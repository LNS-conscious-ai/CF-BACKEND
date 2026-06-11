"""
ingest.py — CF ChromaDB Ingestion Script
LNS Confidential · Day 1/2 · RUNTIME.md v1.1
Reads all JSONL files from corpus/ and loads into ChromaDB.
Dual-schema fix: supports both 'content' and 'text' fields.
"""

import os
import pathlib
import json
import chromadb
from chromadb.utils import embedding_functions

CHROMADB_PATH = os.environ.get('CHROMADB_PATH', '/var/data/chromadb')
CORPUS_PATH   = "/workspaces/CF-BACKEND/corpus"

COLLECTIONS = {
    "foundational_books":    "corpus/foundational_books",
    "meaning_first_startups":"corpus/meaning_first_startups",
    "live_courses":          "corpus/live_courses",
}

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

def get_client():
    return chromadb.PersistentClient(path=CHROMADB_PATH)

def get_embed_fn():
    return embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBED_MODEL
    )

def load_jsonl(filepath):
    chunks = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                # Dual-schema fix: support both 'content' and 'text' fields
                text = d.get("content") or d.get("text", "")
                if text and text.strip():
                    chunks.append({
                        "text": text.strip(),
                        "meta": {
                            "source":      d.get("source", filepath.name),
                            "topic_tag":   d.get("topic_tag", "general"),
                            "source_url":  d.get("source_url", ""),
                            "scraped_date":d.get("scraped_date", ""),
                            "file":        filepath.name,
                        }
                    })
            except json.JSONDecodeError as e:
                print(f"  [SKIP] {filepath.name} line {line_num}: {e}")
    return chunks

def ingest_collection(client, embed_fn, collection_name, folder_path):
    folder = pathlib.Path(folder_path)
    if not folder.exists():
        print(f"[SKIP] Folder not found: {folder_path}")
        return 0

    files = list(folder.glob("*.jsonl"))
    if not files:
        print(f"[SKIP] No .jsonl files in {folder_path}")
        return 0

    print(f"\n[{collection_name}] Found {len(files)} files...")

    # Delete and recreate collection for clean ingest
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass

    col = client.create_collection(
        name=collection_name,
        embedding_function=embed_fn,
        metadata={"hnsw:space": "cosine"}
    )

    total = 0
    for filepath in sorted(files):
        chunks = load_jsonl(filepath)
        if not chunks:
            print(f"  [EMPTY] {filepath.name}")
            continue

        # Batch insert (ChromaDB handles batching internally)
        ids      = [f"{collection_name}_{total + i}" for i in range(len(chunks))]
        texts    = [c["text"] for c in chunks]
        metadatas= [c["meta"] for c in chunks]

        col.add(documents=texts, metadatas=metadatas, ids=ids)
        total += len(chunks)
        print(f"  [OK] {filepath.name} → {len(chunks)} chunks")

    print(f"[DONE] {collection_name}: {total} total chunks")
    return total

def main():
    print("=" * 55)
    print("CF ChromaDB Ingestion — RUNTIME.md v1.1")
    print("=" * 55)

    client   = get_client()
    embed_fn = get_embed_fn()

    grand_total = 0
    for col_name, folder in COLLECTIONS.items():
        count = ingest_collection(client, embed_fn, col_name, folder)
        grand_total += count

    print("\n" + "=" * 55)
    print("FINAL CHUNK COUNTS:")
    for col in client.list_collections():
        c = client.get_collection(col.name)
        print(f"  {col.name}: {c.count()} chunks")
    print(f"\nGRAND TOTAL: {grand_total} chunks")
    print("ALL DONE ✅")
    print("=" * 55)

if __name__ == "__main__":
    main()
