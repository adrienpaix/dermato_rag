"""Retrieval Qdrant pour DermatoRAG.

Patch d'audit (avr 2026) :
- IDs Qdrant déterministes (uuid5 sur chunk_id) → plus jamais d'écrasement entre
  deux runs d'ingestion (cf. bug d'`id=idx` du code original).
- Méthode `rerank()` qui appelle Cohere Rerank v3.5 multilingue (clé déjà en .env).
- Méthode `search_with_rerank()` qui combine retrieval + reranking + filtre par
  `doc_type` (article / guideline / ebook).
- Le client Qdrant est instancié paresseusement et partagé via singleton.
- Vérification de la dimension de la collection vs celle de l'embedding model :
  on échoue vite avec un message clair en cas de mismatch.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)

from ..config import config
from ..embeddings import embedding_manager

# Namespace stable pour la génération d'UUID déterministes à partir des chunk_ids.
# NE PAS CHANGER après une indexation : ça invaliderait tous les IDs existants.
_QDRANT_ID_NAMESPACE = uuid.UUID("d3f07a13-5b3e-4f8e-9d04-1c0c8d2a5e21")

# Synonymes de payload field pour la source du document.
_SOURCE_KEYS = ("source", "filename", "file", "document")


def _chunk_id_to_uuid(chunk_id: str) -> str:
    """Convertit un chunk_id texte en UUID déterministe (str)."""
    return str(uuid.uuid5(_QDRANT_ID_NAMESPACE, chunk_id))


class QdrantRetriever:
    """Wrapper Qdrant + (optionnel) Cohere Rerank."""

    def __init__(
        self,
        qdrant_url: Optional[str] = None,
        collection_name: Optional[str] = None,
    ):
        self.qdrant_url = qdrant_url or config.QDRANT_URL
        self.collection_name = collection_name or config.COLLECTION_NAME
        self.client = QdrantClient(self.qdrant_url, check_compatibility=False)
        self.embedding_manager = embedding_manager
        self._cohere_client = None  # lazy-loaded

    # ------------------------------------------------------------------
    # Création / vérification de collection
    # ------------------------------------------------------------------

    def ensure_collection(self, vector_size: Optional[int] = None) -> None:
        """Crée la collection si absente, vérifie sa dimension sinon."""
        vector_size = vector_size or self.embedding_manager.dim
        collections = self.client.get_collections().collections
        names = {c.name for c in collections}

        if self.collection_name not in names:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )
            print(
                f"[retrieval] Created collection '{self.collection_name}' "
                f"(size={vector_size}, distance=COSINE)",
                flush=True,
            )
            return

        info = self.client.get_collection(self.collection_name)
        existing_size = info.config.params.vectors.size  # type: ignore[union-attr]
        if existing_size != vector_size:
            raise RuntimeError(
                f"[retrieval] DIMENSION MISMATCH : la collection "
                f"'{self.collection_name}' est en dim={existing_size} alors que "
                f"l'embedding model produit dim={vector_size}. "
                f"C'est exactement le bug du déploiement initial. "
                f"Solution : créer une nouvelle collection (ex: "
                f"'{self.collection_name}_v3') et migrer le COLLECTION_NAME du .env."
            )

    # ------------------------------------------------------------------
    # Indexation
    # ------------------------------------------------------------------

    def index_documents(
        self,
        chunks: Sequence[Dict[str, Any]],
        embeddings: np.ndarray,
        batch_size: int = 100,
    ) -> int:
        """Indexe des chunks dans Qdrant.

        IDs déterministes (uuid5 sur chunk_id) → un re-run d'ingestion sur les
        mêmes chunks fait un *upsert* propre, jamais un écrasement aveugle.
        """
        if len(chunks) != len(embeddings):
            raise ValueError("Nombre de chunks et d'embeddings différent.")

        vector_size = int(embeddings.shape[1])
        self.ensure_collection(vector_size)

        points: List[PointStruct] = []
        for chunk, embedding in zip(chunks, embeddings):
            cid = chunk.get("chunk_id")
            if not cid:
                raise ValueError(f"Chunk sans chunk_id : {chunk!r}")
            points.append(
                PointStruct(
                    id=_chunk_id_to_uuid(cid),
                    vector=embedding.tolist(),
                    payload={
                        "text": chunk["text"],
                        "source": chunk.get("source", ""),
                        "page": chunk.get("page", 0),
                        "chunk_id": cid,
                        "title": chunk.get("title", ""),
                        "doc_type": chunk.get("doc_type", "unknown"),
                        "lang": chunk.get("lang", "unknown"),
                        "tokens": chunk.get("tokens", 0),
                    },
                )
            )

        for i in range(0, len(points), batch_size):
            batch = points[i : i + batch_size]
            self.client.upsert(collection_name=self.collection_name, points=batch)
            print(f"[retrieval] Upserted {i + len(batch)}/{len(points)}", flush=True)

        print(f"[retrieval] Done. {len(points)} points indexés.", flush=True)
        return len(points)

    # ------------------------------------------------------------------
    # Recherche dense
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        doc_types: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Recherche dense (cosine) dans Qdrant.

        `doc_types` (optionnel) : filtre payload, ex. ["article", "guideline"]
        pour booster les sources EBM sur les questions thérapeutiques.
        """
        top_k = top_k or config.TOP_K_RETRIEVAL
        query_embedding = self.embedding_manager.encode_query(query)

        qdrant_filter: Optional[Filter] = None
        if doc_types:
            qdrant_filter = Filter(
                must=[
                    FieldCondition(
                        key="doc_type",
                        match=MatchAny(any=list(doc_types)),
                    )
                ]
            )

        results = self.client.query_points(
            collection_name=self.collection_name,
            query=query_embedding.tolist(),
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
        )

        chunks: List[Dict[str, Any]] = []
        for hit in results.points:
            payload = hit.payload or {}
            src = ""
            for k in _SOURCE_KEYS:
                if payload.get(k):
                    src = payload[k]
                    break
            chunks.append(
                {
                    "text": payload.get("text", ""),
                    "source": src,
                    "page": payload.get("page", 0),
                    "chunk_id": payload.get("chunk_id", str(hit.id)),
                    "title": payload.get("title", ""),
                    "doc_type": payload.get("doc_type", "unknown"),
                    "lang": payload.get("lang", "unknown"),
                    "score": hit.score,
                }
            )
        return chunks

    # ------------------------------------------------------------------
    # Reranking Cohere
    # ------------------------------------------------------------------

    def _get_cohere(self):
        if self._cohere_client is None:
            import cohere

            if not config.COHERE_API_KEY:
                raise RuntimeError(
                    "COHERE_API_KEY manquante dans .env — impossible de rerank."
                )
            self._cohere_client = cohere.Client(config.COHERE_API_KEY)
        return self._cohere_client

    def rerank(
        self,
        query: str,
        chunks: Sequence[Dict[str, Any]],
        top_n: Optional[int] = None,
        model: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Reranke les chunks via Cohere Rerank multilingue.

        Renvoie les top_n chunks par ordre de pertinence Cohere, avec un champ
        `rerank_score` ajouté.
        """
        if not chunks:
            return []
        top_n = top_n or config.TOP_K_RERANK
        model = model or config.RERANKER_MODEL

        co = self._get_cohere()
        documents = [c.get("text", "") for c in chunks]
        try:
            resp = co.rerank(
                model=model,
                query=query,
                documents=documents,
                top_n=min(top_n, len(documents)),
            )
        except Exception as e:
            print(f"[retrieval] Cohere rerank failed: {e}. Falling back to dense order.", flush=True)
            return list(chunks)[:top_n]

        reranked: List[Dict[str, Any]] = []
        for r in resp.results:
            c = dict(chunks[r.index])
            c["rerank_score"] = float(r.relevance_score)
            reranked.append(c)
        return reranked

    def search_with_rerank(
        self,
        query: str,
        top_k_retrieval: Optional[int] = None,
        top_k_rerank: Optional[int] = None,
        doc_types: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Pipeline complet : dense search → Cohere rerank → top_n.

        C'est la méthode à appeler depuis l'API pour les questions EBM.
        """
        top_k_retrieval = top_k_retrieval or config.TOP_K_RETRIEVAL
        top_k_rerank = top_k_rerank or config.TOP_K_RERANK

        candidates = self.search(query, top_k=top_k_retrieval, doc_types=doc_types)
        if not candidates:
            return []

        # Boost de score par type de document avant reranking
        # pour compenser la sous-représentation des ebooks/guidelines
        DOC_TYPE_BOOST = {
            'guideline': 0.15,
            'ebook': 0.10,
            'article': 0.0,
            'unknown': 0.0,
        }
        for chunk in candidates:
            boost = DOC_TYPE_BOOST.get(chunk.get('doc_type', 'unknown'), 0.0)
            chunk['score'] = min(1.0, chunk.get('score', 0.0) + boost)

        # Requête supplémentaire filtrée sur ebook + guideline pour garantir
        # leur présence dans les candidats au reranking
        if not doc_types:
            extra = self.search(
                query,
                top_k=max(top_k_retrieval // 3, 10),
                doc_types=['ebook', 'guideline'],
            )
            # Dédupliquer par chunk_id
            existing_ids = {c['chunk_id'] for c in candidates}
            for c in extra:
                if c['chunk_id'] not in existing_ids:
                    boost = DOC_TYPE_BOOST.get(c.get('doc_type', 'unknown'), 0.0)
                    c['score'] = min(1.0, c.get('score', 0.0) + boost)
                    candidates.append(c)
                    existing_ids.add(c['chunk_id'])

        if not config.USE_RERANKER or not config.COHERE_API_KEY:
            # Trier par score boosté si pas de reranker
            candidates.sort(key=lambda x: x.get('score', 0), reverse=True)
            return candidates[:top_k_rerank]

        return self.rerank(query, candidates, top_n=top_k_rerank)

    # ------------------------------------------------------------------
    # Helpers d'inspection
    # ------------------------------------------------------------------

    def search_for_section(
        self, section_title: str, course_title: str, top_k: int = 15
    ) -> List[Dict[str, Any]]:
        """Recherche dédiée à la rédaction d'une section de cours."""
        query = f"{course_title} {section_title}"
        return self.search(query, top_k=top_k)

    def get_collection_info(self):
        return self.client.get_collection(collection_name=self.collection_name)


# Singleton — instancié une seule fois à l'import.
qdrant_retriever = QdrantRetriever()
