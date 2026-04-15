#!/usr/bin/env python3
"""Fast rebuild of dermato_rag_v3 using FlagEmbedding BGE-M3."""
import sys, os, time, uuid, json, hashlib
from pathlib import Path
os.environ["EMBEDDING_MODEL"] = "BAAI/bge-m3"

print("=== Fast Rebuild dermato_rag_v3 ===")
print(f"Start: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}", flush=True)

REPO_DIR = Path("/opt/dermato_rag")
SOURCE_DIR = Path("/root/dermato_upload/Onco")
COLLECTION = "dermato_rag_v3"
QDRANT_URL = "http://localhost:6335"
ENCODE_BATCH = 10   # Small batches for FlagEmbedding progress visibility
MAX_LENGTH = 256    # Truncate to 256 for 2x speedup
BGE_M3_DIM = 1024

sys.path.insert(0, str(REPO_DIR / "src"))
import urllib.request

def qdrant_req(method, path, data=None):
    body = json.dumps(data).encode() if data else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(f"{QDRANT_URL}{path}", data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())

print("\n▶ Checking Qdrant...", flush=True)
cols = qdrant_req("GET", "/collections")
existing = [c["name"] for c in cols.get("result",{}).get("collections",[])]
print(f"  Collections: {existing}")
assert "dermato_rag" in existing and "dermato_rag_v2" in existing
if COLLECTION in existing:
    print(f"  {COLLECTION} already exists, exiting."); sys.exit(1)
print("  ✅ Qdrant OK", flush=True)

print("\n▶ Step 1/3 — PDF extraction...", flush=True)
from dermato_rag.ingestion import PDFIngester
ingester = PDFIngester(data_dir=SOURCE_DIR)
pdfs = sorted(SOURCE_DIR.glob("*.pdf"))
print(f"  {len(pdfs)} PDFs", flush=True)
all_chunks = []
t0 = time.time()
for pdf in pdfs:
    chunks = ingester.ingest_one(pdf)
    all_chunks.extend(chunks)
print(f"  Done in {time.time()-t0:.1f}s. Total: {len(all_chunks)} chunks.", flush=True)

print("\n▶ Step 2/3 — BGE-M3 encoding...", flush=True)
from FlagEmbedding import BGEM3FlagModel
model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False)
print("  Model loaded", flush=True)

texts = [c["text"] for c in all_chunks]
all_embeddings = []
n_batches = (len(texts) + ENCODE_BATCH - 1) // ENCODE_BATCH
t0 = time.time()
for i in range(0, len(texts), ENCODE_BATCH):
    batch = texts[i:i+ENCODE_BATCH]
    out = model.encode(batch, batch_size=ENCODE_BATCH, max_length=MAX_LENGTH)
    all_embeddings.extend(out["dense_vecs"].tolist())
    done = i + len(batch)
    elapsed = time.time() - t0
    rate = done / elapsed if elapsed > 0 else 0
    eta = (len(texts) - done) / rate if rate > 0 else 9999
    print(f"  [{time.strftime('%H:%M:%S')}] Batch {i//ENCODE_BATCH+1}/{n_batches}: {done}/{len(texts)} ({rate:.2f}/s, ETA {eta:.0f}s)", flush=True)

total_enc = time.time() - t0
print(f"  Encoded {len(texts)} in {total_enc:.1f}s ({len(texts)/total_enc:.2f}/s)", flush=True)
assert len(all_embeddings[0]) == BGE_M3_DIM

print(f"\n▶ Step 3/3 — Creating {COLLECTION} and upserting...", flush=True)
qdrant_req("PUT", f"/collections/{COLLECTION}", {"vectors": {"size": BGE_M3_DIM, "distance": "Cosine"}})
print("  Collection created", flush=True)

UPSERT_BATCH = 200
total_up = 0
t0 = time.time()
for i in range(0, len(all_chunks), UPSERT_BATCH):
    bc = all_chunks[i:i+UPSERT_BATCH]
    bv = all_embeddings[i:i+UPSERT_BATCH]
    points = []
    for j,(chunk,vec) in enumerate(zip(bc,bv)):
        h = hashlib.sha1(chunk["text"].encode()).hexdigest()
        pid = str(uuid.uuid5(uuid.NAMESPACE_OID, h))
        points.append({"id":pid,"vector":vec,"payload":{
            "text":chunk["text"],"source":chunk.get("source",""),
            "doc_type":chunk.get("doc_type","unknown"),"chunk_index":chunk.get("chunk_index",i+j)}})
    qdrant_req("PUT", f"/collections/{COLLECTION}/points", {"points": points})
    total_up += len(points)
    print(f"  Upserted {total_up}/{len(all_chunks)}", flush=True)

print(f"  Upsert done in {time.time()-t0:.1f}s", flush=True)
time.sleep(3)
v3 = qdrant_req("GET", f"/collections/{COLLECTION}")
cnt = v3.get("result",{}).get("points_count",0)
print(f"\n  Final count: {cnt} points", flush=True)
print(f"\n✅ REBUILD COMPLETE: {cnt} points at {time.strftime('%H:%M:%S UTC', time.gmtime())}", flush=True)
if cnt < 1000: sys.exit(3)
