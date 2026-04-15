#!/usr/bin/env python3
"""Rebuild propre d'une collection Qdrant pour DermatoRAG.

Ce script remplace les 5 `run_*.py` ad-hoc qui s'étaient accumulés dans
`/opt/dermato_rag/` et qui s'écrasaient les uns les autres (cf. audit).

Il fait, dans l'ordre :
  1. Vérifie que la collection cible **n'existe pas** encore (sécurité).
  2. Scanne tous les PDFs de `--source` et les chunke via PDFIngester.
  3. Encode les chunks avec BGE-M3 (batch adapté au CPU).
  4. Crée la collection à la bonne dimension et upserte les points avec IDs
     déterministes (uuid5) → idempotent.
  5. Affiche un rapport final détaillé (par doc_type, par source, par langue).

Usage :
    python scripts/rebuild_collection.py \\
        --source /root/dermato_upload/Onco \\
        --collection dermato_rag_v3 \\
        --qdrant-url http://localhost:6335 \\
        --embedding-model BAAI/bge-m3

Pour un run de test sur un sous-ensemble :
    python scripts/rebuild_collection.py --source /root/dermato_upload/Onco \\
        --collection dermato_rag_v3_test --limit 3
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from pathlib import Path

# Permet d'être lancé depuis /opt/dermato_rag sans installer le package
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dermato_rag.config import config  # noqa: E402
from dermato_rag.embeddings import embedding_manager, BGE_M3_DIM, BGE_M3_NAME  # noqa: E402
from dermato_rag.ingestion import PDFIngester, detect_doc_type  # noqa: E402
from dermato_rag.retrieval import QdrantRetriever  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", type=Path, default=None, help="Dossier contenant les PDFs (défaut: config.DATA_DIR)")
    p.add_argument("--collection", type=str, default="dermato_rag_v3", help="Nom de la collection Qdrant cible")
    p.add_argument("--qdrant-url", type=str, default=None, help="URL Qdrant (défaut: config.QDRANT_URL)")
    p.add_argument("--embedding-model", type=str, default=BGE_M3_NAME, help="Modèle d'embedding")
    p.add_argument("--batch-size", type=int, default=8, help="Batch size d'encodage (CPU: 8, GPU: 32-64)")
    p.add_argument("--limit", type=int, default=0, help="Ne traiter que les N premiers PDFs (debug)")
    p.add_argument("--force", action="store_true", help="Autoriser l'upsert dans une collection existante")
    p.add_argument("--dry-run", action="store_true", help="Tout faire SAUF l'upsert final dans Qdrant")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    source_dir = args.source or config.DATA_DIR
    if not source_dir.exists():
        print(f"❌ Source introuvable : {source_dir}", file=sys.stderr)
        return 2

    # Impose BGE-M3 si aucune autre option
    os.environ.setdefault("EMBEDDING_MODEL", args.embedding_model)

    print("=" * 70)
    print(f"  Source         : {source_dir}")
    print(f"  Collection     : {args.collection}")
    print(f"  Qdrant URL     : {args.qdrant_url or config.QDRANT_URL}")
    print(f"  Embedding      : {args.embedding_model}")
    print(f"  Batch size     : {args.batch_size}")
    print(f"  Limit          : {args.limit or 'ALL'}")
    print(f"  Dry-run        : {args.dry_run}")
    print("=" * 70)

    # ------------------------------------------------------------------
    # Étape 1 — vérifie la collection cible
    # ------------------------------------------------------------------
    retriever = QdrantRetriever(
        qdrant_url=args.qdrant_url,
        collection_name=args.collection,
    )
    existing = {c.name for c in retriever.client.get_collections().collections}
    if args.collection in existing and not args.force:
        print(
            f"\n⚠️  La collection '{args.collection}' existe déjà. "
            f"Utilisez --force pour upsert dedans, ou supprimez-la d'abord :"
        )
        print(
            f"    curl -X DELETE {args.qdrant_url or config.QDRANT_URL}/collections/{args.collection}"
        )
        return 1

    # ------------------------------------------------------------------
    # Étape 2 — extraction + chunking
    # ------------------------------------------------------------------
    print("\n▶ Étape 1/3 — extraction des PDFs et chunking")
    ingester = PDFIngester(data_dir=source_dir)

    pdf_files = sorted(source_dir.glob("*.pdf"))
    if args.limit:
        pdf_files = pdf_files[: args.limit]
    if not pdf_files:
        print(f"❌ Aucun PDF trouvé dans {source_dir}", file=sys.stderr)
        return 2

    # Affichage pré-extraction (useful pour sanity check)
    print(f"\n  {len(pdf_files)} PDFs à traiter :")
    type_preview: Counter = Counter()
    for pdf in pdf_files:
        dt = detect_doc_type(pdf.name)
        type_preview[dt] += 1
        print(f"    [{dt:10s}] {pdf.name}")
    print(f"\n  Pré-classification : {dict(type_preview)}")

    all_chunks = []
    t0 = time.time()
    for pdf in pdf_files:
        chunks = ingester.ingest_one(pdf)
        all_chunks.extend(chunks)
        if chunks:
            print(
                f"    {pdf.name} → {len(chunks)} chunks (type={chunks[0]['doc_type']}, "
                f"~{sum(c['tokens'] for c in chunks) // max(len(chunks),1)} tok/chunk)"
            )
    print(f"\n  Extraction terminée en {time.time() - t0:.1f}s. Total : {len(all_chunks)} chunks.")

    if not all_chunks:
        print("❌ Aucun chunk généré", file=sys.stderr)
        return 3

    # ------------------------------------------------------------------
    # Étape 3 — encodage avec BGE-M3
    # ------------------------------------------------------------------
    print("\n▶ Étape 2/3 — encodage BGE-M3 (CPU)")
    embedding_manager.load_model()
    print(f"  Dim BGE-M3 = {embedding_manager.dim}")
    assert embedding_manager.dim == BGE_M3_DIM, (
        f"Mauvaise dim : {embedding_manager.dim} (attendu {BGE_M3_DIM})"
    )

    texts = [c["text"] for c in all_chunks]
    t0 = time.time()
    embeddings = embedding_manager.encode_corpus(texts, batch_size=args.batch_size)
    elapsed = time.time() - t0
    rate = len(texts) / elapsed if elapsed > 0 else 0
    print(
        f"  Encodé {len(texts)} chunks en {elapsed:.1f}s "
        f"({rate:.1f} chunks/s, shape={embeddings.shape})"
    )

    # ------------------------------------------------------------------
    # Étape 4 — upsert Qdrant
    # ------------------------------------------------------------------
    if args.dry_run:
        print("\n⏭  --dry-run : skip de l'upsert Qdrant.")
    else:
        print("\n▶ Étape 3/3 — upsert dans Qdrant")
        n = retriever.index_documents(all_chunks, embeddings)
        print(f"  {n} points upsertés dans '{args.collection}'.")

    # ------------------------------------------------------------------
    # Rapport final
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  RAPPORT FINAL")
    print("=" * 70)
    types = Counter(c["doc_type"] for c in all_chunks)
    sources = Counter(c["source"] for c in all_chunks)
    langs = Counter(c["lang"] for c in all_chunks)
    tokens_total = sum(c["tokens"] for c in all_chunks)

    print(f"  PDFs traités        : {len(pdf_files)}")
    print(f"  Sources distinctes  : {len(sources)}")
    print(f"  Chunks générés      : {len(all_chunks)}")
    print(f"  Tokens totaux       : {tokens_total:,}")
    print(f"  Par doc_type        : {dict(types)}")
    print(f"  Par langue          : {dict(langs)}")

    print("\n  Top 15 sources par nb de chunks :")
    for src, n in sources.most_common(15):
        print(f"    {n:5d}  {src}")

    print("\n✅ Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
