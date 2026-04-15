import sys
sys.path.insert(0, '/opt/dermato_rag/src')
from dermato_rag.ingestion import PDFIngester
from dermato_rag.embeddings import EmbeddingManager
from dermato_rag.retrieval import QdrantRetriever
from dermato_rag.config import config
import json, time

print('=== INGESTION 85 PDFs ===', flush=True)
ingester = PDFIngester(config.DATA_DIR)
chunks = ingester.ingest()
print(f'Total chunks: {len(chunks)}', flush=True)

with open('/tmp/dermato_chunks.json', 'w') as f:
    json.dump(chunks, f)
print('Chunks sauvegardés sur disque', flush=True)

print('=== BGE-M3 EMBEDDINGS ===', flush=True)
em = EmbeddingManager()
texts = [c['text'] for c in chunks]
embeddings = em.encode_corpus(texts)
print(f'Embeddings OK: {len(embeddings)} vecteurs', flush=True)

print('=== QDRANT INDEXATION ===', flush=True)
retriever = QdrantRetriever()
retriever.index_documents(chunks, embeddings)
print('INDEXATION COMPLETE - RAG PRET', flush=True)
