import sys, json, numpy as np
sys.path.insert(0, "/opt/dermato_rag/src")
from dermato_rag.retrieval import QdrantRetriever
from sentence_transformers import SentenceTransformer
import os

CACHE = "/tmp/dermato_fast_embeddings.npy"

print("Chargement chunks...", flush=True)
with open("/tmp/dermato_chunks.json") as f:
    chunks = json.load(f)
print(f"Total: {len(chunks)} chunks", flush=True)

texts = [c["text"] for c in chunks]

if os.path.exists(CACHE):
    print("Cache trouvé, chargement...", flush=True)
    embeddings = np.load(CACHE)
    print(f"Cache: {len(embeddings)} vecteurs dim={embeddings.shape[1]}", flush=True)
else:
    print("Chargement all-MiniLM-L6-v2 (22MB, rapide)...", flush=True)
    model = SentenceTransformer("all-MiniLM-L6-v2")
    print("Encodage...", flush=True)
    embeddings = model.encode(texts, batch_size=128, show_progress_bar=True, normalize_embeddings=True)
    np.save(CACHE, embeddings)
    print(f"OK: {embeddings.shape}", flush=True)

print("Suppression ancienne collection + indexation Qdrant...", flush=True)
retriever = QdrantRetriever()

# Override dim to 384 for MiniLM
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
import uuid

client = QdrantClient(url="http://localhost:6333", check_compatibility=False)
client.recreate_collection(
    collection_name="dermato_rag",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE)
)
print("Collection recréée (dim=384)", flush=True)

BATCH = 200
for i in range(0, len(chunks), BATCH):
    pts = []
    for j, (chunk, emb) in enumerate(zip(chunks[i:i+BATCH], embeddings[i:i+BATCH])):
        pts.append(PointStruct(
            id=str(uuid.uuid4()),
            vector=emb.tolist(),
            payload={k: v for k, v in chunk.items() if k != "chunk_id"}
        ))
    client.upsert(collection_name="dermato_rag", points=pts)
    if (i // BATCH) % 5 == 0:
        print(f"  Indexé {i+len(pts)}/{len(chunks)}", flush=True)

print(f"INDEXATION COMPLETE - {len(chunks)} points", flush=True)
