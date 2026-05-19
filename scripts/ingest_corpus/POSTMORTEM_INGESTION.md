# Postmortem Dataset Ingestion & Indexing E2E

**Dataset:** `datasets/postmortems/` — Tech incident postmortem reports (YAML+Markdown)  
**Target Namespace:** `system_design`  
**Expected Chunks:** ~1,200–1,800 chunks (~500K–700K tokens)  
**Redistribution:** ✅ CC-BY-4.0 (safe for HF hub)  
**Effort:** **15–25 minutes** (no embedding; can defer to async job)

---

## Overview

The postmortems dataset contains incident reports, outage analyses, and failure post-mortems from real companies (GitHub, Facebook, AWS, etc.). Each file has YAML frontmatter (company, product, URL) followed by markdown narrative.

**Why separate namespace?**  
Postmortems are system-design–adjacent but distinct from official docs. They serve:
- Query intent: "How did company X handle outage Y?"
- Depth level: `technical` and `expert` (specific incident analysis)
- Complement to: System Design Primer, Architecture knowledge

**Why NOT merge into existing trusted corpus?**  
Different semantics → better filtering if kept separate. RAG queries can explicitly request "system_design" namespace for curated incident learnings.

---

## Pre-Execution Checklist

**✓ Environment**
- [ ] Supabase credentials in `.env.local` (optional; used for backfill later)
- [ ] Python 3.10+ with poetry/pip
- [ ] Working directory: `/home/sanjeev/Downloads/depthapi`

**✓ Dataset Presence**
```bash
test -d datasets/postmortems && echo "✓ postmortems dir exists" || echo "✗ missing"
test -d datasets/postmortems/data && echo "✓ postmortems/data dir exists" || echo "✗ missing"
find datasets/postmortems/data -name "*.md" | head -3
```

**✓ Script Readiness**
```bash
grep -q "def run(" scripts/ingest_corpus/ingest_postmortems.py && echo "✓ ingest_postmortems.py ready" || echo "✗ script broken"
test -f scripts/ingest_corpus/base_ingestor.py && echo "✓ base_ingestor.py exists" || echo "✗ missing"
```

**✓ Dependencies**
```bash
python3 -c "import yaml" && echo "✓ pyyaml installed" || echo "✗ install: pip install pyyaml"
```

---

## Execution: Step 1 — Ingest Postmortems → chunks.json

**Goal:** Parse postmortems, chunk them, and write `data/rag/trusted/chunks.json`

**Command:**
```bash
cd /home/sanjeev/Downloads/depthapi
python3 scripts/ingest_corpus/ingest_postmortems.py
```

**What it does:**
1. Scans `datasets/postmortems/data/**/*.md`
2. Parses YAML frontmatter (company, product, url, etc.)
3. Semantically chunks markdown body (chunk_size=800, overlap=25 words)
4. Adds metadata: `namespace="system_design"`, tags=`["postmortem", "incident", "architecture", "devops", "P2"]`
5. Deduplicates via content SHA-256 hash
6. Writes/appends to `data/rag/trusted/chunks.json`

**Expected Output (log):**
```
[12:34:56] [INFO] ingest – Processing postmortems...
[12:34:58] [INFO] ingest – [Tech Postmortems] processed=42 added=1245 total_chunks_after_flush=225360
```

**On Failure:**
- "pyyaml is required": `pip install pyyaml` and retry
- "Postmortems directory not found": verify `datasets/postmortems/data/` exists and contains `.md` files
- "Chunks already exist in chunks.json": normal — script deduplicates by content hash

---

## Execution: Step 2 — Verify Corpus Quality

**Goal:** Audit chunks.json to ensure postmortems were added correctly

**Command:**
```bash
python3 scripts/ingest_corpus/detailed_audit.py
```

**What it checks:**
- Total chunk count
- Source distribution (should include "Tech Postmortems")
- Namespace spread (should have `system_design` entries)
- Average chunk length (should be 400–600 chars)
- Deduplication (all IDs unique)

**Expected Output (relevant lines):**
```
=== Corpus Audit: chunks.json ===
Total Chunks: 225XXX

Sources:
  - MDN Content EN-US: 127197 (56.5%)
  - Kubernetes Website EN: 42744 (19.0%)
  - CPython Docs: 34923 (15.5%)
  - ...
  - Tech Postmortems: 1245 (0.5%)
  
Deduplication: OK (All IDs unique)
Average Content Length: 450.2 chars
```

**If postmortems not in report:**
- Check: `grep -c '"source_name".*Postmortem' data/rag/trusted/chunks.json`
- If zero: re-run ingest_postmortems.py with verbose logging

---

## Execution: Step 3 — Build Vector + BM25 Index

**Goal:** Generate pgvector embeddings and BM25 index for retrieval

**Note:** This step is only needed if you want Supabase/local retrieval. It is independent of Hugging Face export.

Two options:

### Option 3a: Backfill to Supabase (if local Supabase instance is running)

```bash
python3 scripts/ingest_corpus/backfill_supabase_rag.py \
  --collection-name "DepthAPI Trusted Corpus" \
  --chunk-file data/rag/trusted/chunks.json
```

**Prerequisites:**
- Supabase local instance running (or remote credentials in `.env.local`)
- `SUPABASE_URL`, `SUPABASE_SECRET_KEY` in environment

**What it does:**
1. Reads chunks.json
2. Generates embeddings (batched, with retry logic)
3. Pushes to `knowledge_chunks` table with pgvector embeddings
4. Builds FTS (full-text search) indexes for BM25

**Expected Output:**
```
[HH:MM:SS] [INFO] main – Backfilling Supabase...
[HH:MM:SS] [INFO] main – Embedded 225XXX chunks in YYY batches (ZZ errors)
[HH:MM:SS] [INFO] main – Backfill complete
```

### Option 3b: Build Local Index (no Supabase required)

```bash
python3 scripts/ingest_corpus/build_index.py \
  --chunks-file data/rag/trusted/chunks.json \
  --index-dir data/rag/trusted/indexes
```

**What it does:**
1. Generates embeddings locally
2. Builds FAISS or Annoy index for approximate nearest-neighbor search
3. Builds BM25 inverted index

**Use if:** Testing locally or Supabase unavailable

---

## Execution: Step 4 — Export to Hugging Face (Optional, no embeddings required)

**Goal:** Push postmortems to HF Hub as part of the corpus dataset

**Important:** You can export to Hugging Face immediately after ingestion, before any embedding or vector indexing step. HF upload only needs the chunk text + metadata rows.

**Prerequisites:**
- HuggingFace token in `HF_TOKEN` or `HUGGINGFACE_TOKEN` env var
- Repo ID in `HF_REPO_ID` (defaults to `{username}/depthapi_technical_corpus`)

**Command:**
```bash
HF_TOKEN=hf_your_token_here \
  python3 scripts/release/export_to_hf.py
```

**What it does:**
1. Reads chunks.json
2. Deduplicates against existing HF dataset (if repo exists)
3. Converts to Hugging Face Dataset format (arrow/parquet)
4. Pushes to Hub with commit message
5. Includes `namespace` in every row (used for filtering)

**When to run:**
- Right after ingestion, if you only need to publish the dataset
- After embeddings/backfill, if you want the HF export to reflect the final corpus snapshot

**Expected Output:**
```
Authenticated as HF user: sanjeevafk
Found 225XXX existing chunks in HF repo.
Skipped 0 chunks already present in HF repo.
Pushing to Hugging Face Hub at sanjeev/depthapi_technical_corpus...
Pushed dataset successfully! Creating dataset card...
Dataset card uploaded successfully!
```

**Verify on HF Hub:**
- Visit: `https://huggingface.co/datasets/sanjeevafk/depthapi_technical_corpus`
- Filter rows with `namespace == "system_design"`
- Should see ~1,200 rows with tags `["postmortem", "incident", ...]`

---

## Full Verification: Query Corpus for Postmortem Content

**Test retrieval with a simple query:**

```python
import json

with open("data/rag/trusted/chunks.json") as f:
    chunks = json.load(f)

# Find postmortem chunks
postmortem_chunks = [c for c in chunks if c.get("source_name") == "Tech Postmortems"]
print(f"Postmortem chunks: {len(postmortem_chunks)}")

# Inspect one
if postmortem_chunks:
    sample = postmortem_chunks[0]
    print(f"\nSample chunk:")
    print(f"  Namespace: {sample['metadata'].get('namespace')}")
    print(f"  Company: {sample['metadata'].get('company')}")
    print(f"  Product: {sample['metadata'].get('product')}")
    print(f"  Preview: {sample['content'][:200]}...")
```

**Expected output:**
```
Postmortem chunks: 1245

Sample chunk:
  Namespace: system_design
  Company: GitHub
  Product: Pages
  Preview: Incident Report / Postmortem: GitHub - Pages
Source: https://github.blog/...
```

---

## Rollback / Undo

If something goes wrong and you need to remove postmortems:

```bash
# Option 1: Revert chunks.json to before ingest (if version control)
git checkout HEAD -- data/rag/trusted/chunks.json

# Option 2: Filter out postmortems manually
python3 -c "
import json
with open('data/rag/trusted/chunks.json') as f:
    chunks = json.load(f)
filtered = [c for c in chunks if c.get('source_name') != 'Tech Postmortems']
with open('data/rag/trusted/chunks.json', 'w') as f:
    json.dump(filtered, f)
print(f'Removed postmortems. Remaining: {len(filtered)} chunks')
"

# Option 3: Delete rows from Supabase directly (if backfilled)
# supabase.table('knowledge_chunks').delete().eq('namespace', 'system_design').execute()
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `pyyaml is required` | `pip install pyyaml` |
| No .md files found | Check path: `datasets/postmortems/data/` (should have *.md files) |
| Chunks not appearing in chunks.json | Re-run ingest script; check stdout for errors |
| "Namespace not in metadata" | Post-mortems use `metadata['namespace']` field; verify backfill script reads it |
| HF export fails | Check token: `echo $HF_TOKEN` and verify permissions on repo |

---

## Agent Handoff Prompt

**Starting point for autonomous execution:**

```
You are tasked with ingesting the postmortems dataset into DepthAPI's corpus.

Context:
- Dataset location: datasets/postmortems/data/
- Target namespace: system_design
- Existing chunks: ~224,000 in data/rag/trusted/chunks.json
- Expected result: ~225,000+ chunks after postmortem ingestion

Your task (in order):
1. Execute pre-checks to verify dataset presence, script readiness, and dependencies.
2. Run: python3 scripts/ingest_corpus/ingest_postmortems.py
3. Audit the result with: python3 scripts/ingest_corpus/detailed_audit.py
4. Verify that "Tech Postmortems" appears in the audit output with ~1,200+ chunks.
5. If Supabase is available, backfill embeddings: python3 scripts/ingest_corpus/backfill_supabase_rag.py --collection-name "DepthAPI Trusted Corpus" --chunk-file data/rag/trusted/chunks.json
6. Report success/failure with final chunk counts by namespace.

Success criteria:
- chunks.json contains >1,200 postmortem chunks
- All chunks have namespace="system_design"
- detailed_audit.py shows "Tech Postmortems" in sources list
- (Optional) Supabase backfill completes without embedding errors

Abort if:
- Dataset path does not exist
- Script has syntax errors
- pyyaml is not installed and cannot be installed
- Embedding backfill fails repeatedly (>3 retries)

Report final status with:
- Total chunks added
- Namespace distribution
- Any warnings or partial failures
```

---

## Next Steps

After postmortem ingestion succeeds:

1. **Ingest CIU dataset** — See `CIU_INGESTION.md`
2. **Export to HF Hub** — Run `export_to_hf.py` to push namespace-aware dataset
3. **Query Validation** — Test retrieval with sample incident-related queries
4. **Archive Report** — Save audit logs to `data/rag/reports/` for lineage tracking
