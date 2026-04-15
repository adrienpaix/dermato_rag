import sys, json, numpy as np
sys.path.insert(0, "/opt/dermato_rag/src")
from dermato_rag.retrieval import QdrantRetriever
from dermato_rag.config import config
from sentence_transformers import SentenceTransformer
import os

BATCH = 128
CACHE = "/tmp/dermato_embeddings.npy"

print("Chargement chunks...", flush=True)
with open("/tmp/dermato_chunks.json") as f:
    chunks = json.load(f)
print(f"Chunks: {len(chunks)}", flush=True)

texts = [c["text"] for c in chunks]

if os.path.exists(CACHE):
    print(f"Cache trouvé, chargement...", flush=True)
    embeddings = np.load(CACHE)
    print(f"Cache: {len(embeddings)} vecteurs", flush=True)
else:
    print(f"Chargement SentenceTransformer bge-m3...", flush=True)
    model = SentenceTransformer("BAAI/bge-m3")
    print("Modèle chargé. Encodage par batches...", flush=True)
    all_emb = []
    for i in range(0, len(texts), BATCH):
        batch = texts[i:i+BATCH]
        emb = model.encode(batch, show_progress_bar=False, normalize_embeddings=True)
        all_emb.extend(emb)
        if (i // BATCH) % 5 == 0:
            print(f"  {i+len(batch)}/{len(texts)} encodés", flush=True)
    embeddings = np.array(all_emb)
    np.save(CACHE, embeddings)
    print(f"Embeddings sauvegardés: {embeddings.shape}", flush=True)

print("Indexation Qdrant...", flush=True)
retriever = QdrantRetriever()
retriever.index_documents(chunks, embeddings.tolist())
print(f"INDEXATION COMPLETE - {len(chunks)} points", flush=True)
