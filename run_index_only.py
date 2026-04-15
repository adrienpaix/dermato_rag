import sys, json
import numpy as np
sys.path.insert(0, "/opt/dermato_rag/src")
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
import uuid

QDRANT_URL = "http://localhost:6335"
COLLECTION = "dermato_rag"

print("Chargement chunks + embeddings...", flush=True)
with open("/tmp/dermato_chunks.json") as f:
    chunks = json.load(f)
embeddings = np.load("/tmp/dermato_fast_embeddings.npy")
print(f"Chunks: {len(chunks)}, Embeddings: {embeddings.shape}", flush=True)

client = QdrantClient(url=QDRANT_URL, check_compatibility=False)

# Supprime si existe
try:
    client.delete_collection(COLLECTION)
    print("Ancienne collection supprimée", flush=True)
except:
    pass

# Crée
client.create_collection(
    collection_name=COLLECTION,
    vectors_config=VectorParams(size=384, distance=Distance.COSINE)
)
print("Collection créée (dim=384, COSINE)", flush=True)

# Indexation par batches
BATCH = 200
total = len(chunks)
for i in range(0, total, BATCH):
    batch_chunks = chunks[i:i+BATCH]
    batch_emb = embeddings[i:i+BATCH]
    pts = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=emb.tolist(),
            payload={"text": c["text"], "source": c["source"], "page": c.get("page", 0)}
        )
        for c, emb in zip(batch_chunks, batch_emb)
    ]
    client.upsert(collection_name=COLLECTION, points=pts)
    print(f"  {min(i+BATCH, total)}/{total} indexés", flush=True)

info = client.get_collection(COLLECTION)
print(f"INDEXATION COMPLETE - {info.points_count} points dans Qdrant", flush=True)
