"""Wrapper d'embedding pour DermatoRAG.

Patch d'audit (avr 2026) :
- Force BGE-M3 (1024-dim, multilingue, contrastif). Plus de fallback silencieux
  type BioBERT/MiniLM qui produit des embeddings dégénérés.
- Chargement thread-safe (lock) pour éviter les races dans le ThreadPool de FastAPI.
- Preload optionnel au démarrage de l'API (cf. api.startup).
- normalize_embeddings=True : on travaille en cosine sur des vecteurs unitaires.
- Vérifie la dimension à l'init pour échouer vite si la mauvaise version est en place.
"""

from __future__ import annotations

import threading
from typing import List

import numpy as np
from sentence_transformers import SentenceTransformer

from ..config import config

# BGE-M3 produit des vecteurs de dimension 1024.
# Toute autre dimension est une erreur de configuration et doit faire planter l'app.
BGE_M3_NAME = "BAAI/bge-m3"
BGE_M3_DIM = 1024


class EmbeddingManager:
    """Singleton thread-safe pour BGE-M3."""

    def __init__(self, model_name: str = None):
        self.model_name = model_name or config.EMBEDDING_MODEL
        if self.model_name != BGE_M3_NAME:
            # Ne pas planter ici (on peut vouloir utiliser un autre modèle pour des
            # tests), mais avertir bruyamment.
            print(
                f"[embeddings] WARNING: model_name={self.model_name!r} "
                f"alors que ce projet est calibré pour {BGE_M3_NAME}. "
                f"Si vous voyez du retrieval cassé, c'est probablement ici."
            )
        self.model: SentenceTransformer | None = None
        self._lock = threading.Lock()
        self._dim: int | None = None

    def load_model(self) -> None:
        """Charge le modèle (idempotent, thread-safe)."""
        if self.model is not None:
            return
        with self._lock:
            if self.model is not None:
                return
            print(f"[embeddings] Loading {self.model_name}...", flush=True)
            model = SentenceTransformer(self.model_name)
            # Sanity check : on vérifie la dimension de sortie sur un encode bidon.
            probe = model.encode(["__dim_probe__"], normalize_embeddings=True)
            dim = int(probe.shape[1])
            if self.model_name == BGE_M3_NAME and dim != BGE_M3_DIM:
                raise RuntimeError(
                    f"[embeddings] BGE-M3 a chargé une dimension inattendue : {dim} "
                    f"(attendu {BGE_M3_DIM}). Vérifiez la version de "
                    f"sentence-transformers."
                )
            self.model = model
            self._dim = dim
            print(f"[embeddings] Loaded. dim={dim}", flush=True)

    @property
    def dim(self) -> int:
        """Dimension de sortie du modèle (charge le modèle si nécessaire)."""
        if self._dim is None:
            self.load_model()
        return self._dim  # type: ignore[return-value]

    @property
    def is_loaded(self) -> bool:
        return self.model is not None

    def encode_corpus(self, texts: List[str], batch_size: int = 8) -> np.ndarray:
        """Encode un corpus de documents.

        batch_size=8 par défaut : adapté à du CPU-only sans OOM.
        Pour du GPU, monter à 32-64.
        """
        self.load_model()
        return self.model.encode(  # type: ignore[union-attr]
            texts,
            batch_size=batch_size,
            show_progress_bar=True,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )

    def encode_query(self, text: str) -> np.ndarray:
        """Encode une requête utilisateur en vecteur dense unitaire."""
        self.load_model()
        v = self.model.encode(  # type: ignore[union-attr]
            [text],
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return v[0]


# Singleton importable
embedding_manager = EmbeddingManager()
