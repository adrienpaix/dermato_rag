#!/bin/bash
set -e

cd /opt/dermato_rag

# Activer l'environnement virtuel
source .venv/bin/activate

echo "=== Ingestion des PDFs ==="
python -c "
from dermato_rag.ingestion import PDFIngester
from dermato_rag.embeddings import embedding_manager
from dermato_rag.retrieval import qdrant_retriever

ingester = PDFIngester()
chunks = ingester.ingest()
if chunks:
    print(f'Nombre de chunks extraits: {len(chunks)}')
    embeddings = embedding_manager.encode_corpus([c['text'] for c in chunks])
    qdrant_retriever.index_documents(chunks, embeddings)
    print('Ingestion et indexation terminées.')
else:
    print('Aucun chunk généré.')
"

echo "=== Démarrage de l'API FastAPI ==="
nohup .venv/bin/uvicorn dermato_rag.api:app --host 0.0.0.0 --port 8001 > /tmp/dermato_api.log 2>&1 &
echo "API démarrée sur http://0.0.0.0:8001"
echo "Logs: /tmp/dermato_api.log"
echo "Pour vérifier: curl http://localhost:8001/health"

