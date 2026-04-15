import sys
sys.path.insert(0, '/opt/dermato_rag/src')
from dermato_rag.embeddings import EmbeddingManager
from dermato_rag.retrieval import QdrantRetriever
import json

print('Chargement des 5294 chunks depuis disque...', flush=True)
with open('/tmp/dermato_chunks.json') as f:
    chunks = json.load(f)
print(f'Chunks chargés: {len(chunks)}', flush=True)

print('BGE-M3 encodage (peut prendre 15-20 min)...', flush=True)
em = EmbeddingManager()
texts = [c['text'] for c in chunks]
embeddings = em.encode_corpus(texts)
print(f'Embeddings OK: {len(embeddings)} vecteurs dim={len(embeddings[0])}', flush=True)

print('Suppression ancienne collection + réindexation...', flush=True)
retriever = QdrantRetriever()
retriever.index_documents(chunks, embeddings)
print('INDEXATION COMPLETE - RAG PRET', flush=True)
