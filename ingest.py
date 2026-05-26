import chromadb
import json
import os
import glob

client = chromadb.PersistentClient(path=’/workspaces/CF-BACKEND/chromadb’)
fb = client.get_or_create_collection(‘foundational_books’)
ms = client.get_or_create_collection(‘meaning_first_startups’)
lc = client.get_or_create_collection(‘live_courses’)

def ingest(folder, collection, label):
files = glob.glob(f’{folder}/*.jsonl’)
count = 0
skipped = 0
for f in files:
with open(f) as fh:
for i, line in enumerate(fh):
try:
d = json.loads(line)
text = d.get(‘content’) or d.get(‘text’, ‘’)
if not text.strip():
skipped += 1
continue
uid = str(d.get(‘id’, f’{os.path.basename(f)}_{i}’))[:512]
collection.add(documents=[text], ids=[uid])
count += 1
except Exception as e:
skipped += 1
print(f’{label}: {count} chunks ingested, {skipped} skipped’)

base = ‘/workspaces/CF-BACKEND/corpus’
print(‘Starting ingestion…’)
ingest(f’{base}/foundational_books’, fb, ‘foundational_books’)
ingest(f’{base}/meaning_first_startups’, ms, ‘meaning_first_startups’)
ingest(f’{base}/live_courses’, lc, ‘live_courses’)
print(‘ALL DONE’)