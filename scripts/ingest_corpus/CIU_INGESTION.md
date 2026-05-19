# Coding Interview University Ingestion & Indexing E2E

**Dataset:** `datasets/coding-interview-university/` — Curated CS interview prep guide (Markdown)  
**Target Namespace:** `trusted_learning_resources`  
**Expected Chunks:** ~150–200 chunks (~50K–80K tokens)  
**Redistribution:** ✅ CC-BY-SA-4.0 (safe for HF hub)  
**Effort:** **10–15 minutes** (no PDF extraction; pure markdown parsing)

---

## Overview

Coding Interview University (CIU) is a hand-curated **study roadmap**, not a text corpus. It contains structured CS fundamentals knowledge with links to external resources.

**Content breakdown:**
- `README.md` (136KB) — Main study guide: Big-O, data structures, trees, graphs, algorithms, system design, networking
- `programming-language-resources.md` (8KB) — Language-specific resources and cheat sheets
- Translations (ignore) — 25 non-English variants
- Cheat sheet PDFs (optional) — Big-O, system design, Python essentials

**Why separate namespace?**  
CIU is **learning-focused** (conceptual + study tasks), not **reference documentation** (API docs, official specs). Retrieving CIU for "learn about hash tables" is different from retrieving MDN for "JavaScript Map API".

**Why NOT merge into existing trusted corpus?**  
Different retrieval semantics. Queries like "how does a hash table work?" benefit from CIU's pedagogical structure. Queries like "HTMLElement.addEventListener" should NOT match CIU.

---

## Pre-Execution Checklist

**✓ Environment**
- [ ] Python 3.10+ with poetry/pip
- [ ] Working directory: `/home/sanjeev/Downloads/depthapi`
- [ ] Supabase credentials (optional; used later for backfill)

**✓ Dataset Presence**
```bash
test -d datasets/coding-interview-university && echo "✓ CIU dir exists" || echo "✗ missing"
test -f datasets/coding-interview-university/README.md && echo "✓ README exists" || echo "✗ missing"
test -f datasets/coding-interview-university/programming-language-resources.md && echo "✓ programming-language-resources exists" || echo "✗ missing"
wc -l datasets/coding-interview-university/README.md  # should be ~2,000+ lines
```

**✓ Script Readiness**
```bash
grep -q "def run(" scripts/ingest_corpus/ingest_ciu.py && echo "✓ ingest_ciu.py ready" || echo "✗ script broken"
test -f scripts/ingest_corpus/base_ingestor.py && echo "✓ base_ingestor.py exists" || echo "✗ missing"
grep -q "split_by_header_semantic" scripts/ingest_corpus/ingest_ciu.py && echo "✓ semantic splitter imported" || echo "✗ missing"
```

**✓ Dependencies**
```bash
python3 -c "import re" && echo "✓ re available" || echo "✗ missing (stdlib)"
```

---

## Execution: Step 1 — Ingest CIU → chunks.json

**Goal:** Parse CIU markdown, chunk by headers, and write `data/rag/trusted/chunks.json`

**Command:**
```bash
cd /home/sanjeev/Downloads/depthapi
python3 scripts/ingest_corpus/ingest_ciu.py
```

**What it does:**
1. Reads `datasets/coding-interview-university/README.md` and `programming-language-resources.md`
2. Strips noise: translations metadata, TOC-only lists, repo navigation links
3. Splits on `### ` (level-3 headers) — each subsection becomes a chunk
4. Adds metadata: `namespace="trusted_learning_resources"`, tags=`["cs-fundamentals", "algorithms", "system-design", "big-o", "data-structures", "P1"]`
5. Deduplicates via content SHA-256 hash
6. Writes/appends to `data/rag/trusted/chunks.json`

**Expected Output (log):**
```
[12:34:56] [INFO] ingest – Processing: Coding Interview University – Study Roadmap
[12:34:56] [INFO] ingest –   → extracted 145 candidate chunks
[12:34:57] [INFO] ingest –   → 145 new (after dedup)
[12:34:57] [INFO] ingest – Processing: CIU – Language-Specific Resources
[12:34:57] [INFO] ingest –   → extracted 12 candidate chunks
[12:34:57] [INFO] ingest –   → 12 new (after dedup)
[12:34:57] [INFO] ingest – CIU ingest complete. chunks.json total: 225507
```

**Typical result:**
- README chunks: ~140–160
- Language resources chunks: ~10–15
- **Total new chunks: ~155–170**

**On Failure:**
- "File not found": verify `datasets/coding-interview-university/README.md` exists
- "0 new chunks": likely deduplication (re-run is idempotent; no harm)
- "Chunk validation failed unexpectedly": check base_ingestor.py validators

---

## Execution: Step 2 — Verify Corpus Quality

**Goal:** Audit chunks.json to ensure CIU was added correctly

**Command:**
```bash
python3 scripts/ingest_corpus/detailed_audit.py
```

**What it checks:**
- Total chunk count (should be ~225K + 150–170 from CIU)
- Source distribution (should include "Coding Interview University")
- Namespace spread (should have `trusted_learning_resources` entries)
- Average chunk length (should be 400–600 chars for CIU chunks)
- Deduplication (all IDs unique)

**Expected Output (relevant lines):**
```
=== Corpus Audit: chunks.json ===
Total Chunks: 225XXX

Sources:
  - MDN Content EN-US: 127197 (56.5%)
  - Kubernetes Website EN: 42744 (19.0%)
  - CPython Docs: 34923 (15.5%)
  - Node.js API Docs: 10177 (4.5%)
  - React.dev Content: 8959 (4.0%)
  - Full-Stack FastAPI Template: 692 (0.3%)
  - System Design Primer: 468 (0.2%)
  - Coding Interview University: 155 (0.1%)
  
Deduplication: OK (All IDs unique)
Average Content Length: 425.8 chars
```

**If CIU not in report:**
- Check: `grep -c '"source_name".*Coding Interview' data/rag/trusted/chunks.json`
- If zero: re-run ingest_ciu.py
- If error during ingest: check Python version (3.10+) and re-run

---

## Execution: Step 3 — Inspect CIU Chunks (Quality Spot-Check)

**Goal:** Manually verify that CIU chunks are well-formed and contain expected content

**Command:**
```python
import json
import pprint

with open("data/rag/trusted/chunks.json") as f:
    chunks = json.load(f)

# Find CIU chunks
ciu_chunks = [c for c in chunks if c.get("source_name") == "Coding Interview University"]
print(f"\n✓ CIU chunks found: {len(ciu_chunks)}\n")

# Inspect sample
if ciu_chunks:
    sample = ciu_chunks[0]
    print("Sample CIU chunk:")
    print(f"  ID: {sample.get('id')}")
    print(f"  Source: {sample.get('source_name')}")
    print(f"  Namespace: {sample.get('metadata', {}).get('namespace', 'N/A')}")
    print(f"  Tags: {sample.get('tags', [])}")
    print(f"  Content preview: {sample.get('content', '')[:300]}...")
    print()

# Check namespace distribution
from collections import Counter
namespaces = Counter(
    (c.get("metadata") or {}).get("namespace", "trusted") 
    if isinstance(c.get("metadata"), dict) else "trusted"
    for c in chunks
)
print("Namespace distribution:")
for ns, count in sorted(namespaces.items(), key=lambda x: -x[1]):
    print(f"  {ns}: {count}")
```

**Expected output:**
```
✓ CIU chunks found: 155

Sample CIU chunk:
  ID: abc123def...
  Source: Coding Interview University
  Namespace: trusted_learning_resources
  Tags: ['cs-fundamentals', 'algorithms', 'system-design', 'big-o', 'data-structures', 'P1']
  Content preview: Topic: Data Structures / Subtopic: Arrays
A vector is a mutable array with automatic resizing...
```

---

## Execution: Step 4 — Build Vector + BM25 Index

**Goal:** Generate pgvector embeddings and BM25 index for retrieval

**Two options (same as postmortems):**

### Option 4a: Backfill to Supabase

```bash
python3 scripts/ingest_corpus/backfill_supabase_rag.py \
  --collection-name "DepthAPI Trusted Corpus" \
  --chunk-file data/rag/trusted/chunks.json
```

**Prerequisites:**
- Supabase local instance or remote credentials
- `SUPABASE_URL`, `SUPABASE_SECRET_KEY` in `.env.local`

**Expected output:**
```
[HH:MM:SS] [INFO] main – Backfilling Supabase...
[HH:MM:SS] [INFO] main – Embedded 225XXX chunks in YYY batches (ZZ errors)
[HH:MM:SS] [INFO] main – Backfill complete
```

### Option 4b: Build Local Index

```bash
python3 scripts/ingest_corpus/build_index.py \
  --chunks-file data/rag/trusted/chunks.json \
  --index-dir data/rag/trusted/indexes
```

---

## Execution: Step 5 — Export to Hugging Face (Optional)

**Goal:** Push CIU to HF Hub as part of the corpus dataset

**Prerequisites:**
- HuggingFace token in `HF_TOKEN` or `HUGGINGFACE_TOKEN`
- Repo ID in `HF_REPO_ID` (defaults to `{username}/depthapi_technical_corpus`)

**Command:**
```bash
HF_TOKEN=hf_your_token_here \
  python3 scripts/release/export_to_hf.py
```

**What it does:**
1. Reads chunks.json (now containing both postmortem + CIU chunks)
2. Deduplicates against existing HF dataset
3. Converts to Hugging Face format
4. Pushes to Hub
5. Includes `namespace` in every row (rows now have both `system_design` and `trusted_learning_resources`)

**Verify on HF Hub:**
- Visit: `https://huggingface.co/datasets/sanjeev/depthapi_technical_corpus`
- Filter rows with `namespace == "trusted_learning_resources"`
- Should see ~155 rows with tags `["cs-fundamentals", "algorithms", ...]`

---

## Full Verification: Query Corpus for CIU Content

**Test retrieval with a sample query:**

```python
import json

with open("data/rag/trusted/chunks.json") as f:
    chunks = json.load(f)

# Find CIU chunks about complexity
ciu_chunks = [c for c in chunks if c.get("source_name") == "Coding Interview University"]
complexity_chunks = [c for c in ciu_chunks if "Big-O" in c.get("content", "") or "O(n)" in c.get("content", "")]

print(f"CIU chunks mentioning complexity: {len(complexity_chunks)}")
if complexity_chunks:
    sample = complexity_chunks[0]
    print(f"\nSample:")
    print(f"  Subject: {sample['metadata'].get('section', 'N/A')} / {sample['metadata'].get('subsection', 'N/A')}")
    print(f"  Content:\n{sample['content'][:400]}...\n")
```

**Expected output:**
```
CIU chunks mentioning complexity: >10

Sample:
  Subject: Data Structures / Arrays
  Content:
Topic: Data Structures
Subtopic: Arrays

An array is a mutable sequence with O(1) access...
```

---

## Rollback / Undo

If CIU ingestion needs to be reverted:

```bash
# Option 1: Git revert
git checkout HEAD -- data/rag/trusted/chunks.json

# Option 2: Manual filter
python3 -c "
import json
with open('data/rag/trusted/chunks.json') as f:
    chunks = json.load(f)
filtered = [c for c in chunks if c.get('source_name') != 'Coding Interview University']
with open('data/rag/trusted/chunks.json', 'w') as f:
    json.dump(filtered, f)
print(f'Removed CIU. Remaining: {len(filtered)} chunks')
"

# Option 3: Delete rows from Supabase (if backfilled)
# supabase.table('knowledge_chunks').delete().eq('namespace', 'trusted_learning_resources').execute()
```

---

## Advanced: Including Cheat Sheet PDFs (Optional)

CIU includes 3 high-value cheat sheet PDFs. If you want to include them:

**Note:** This requires `pdfplumber` (already in requirements for other ingests).

**Decision:**
- ✅ Include: `big-o-cheatsheet.pdf`, `system-design.pdf`, `Coding Interview Python Language Essentials.pdf`
- ❌ Skip: C/C++/Java cheat sheets (out of scope), `bits-cheat-sheet.pdf` (niche)

**Integration:** Add to [ingest_ciu.py](ingest_ciu.py) in future iteration (not required for MVP).

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `FileNotFoundError` for README.md | Verify: `ls datasets/coding-interview-university/README.md` |
| 0 new chunks added | Normal if re-running (dedup); check log for actual count |
| Chunks too small | CIU sections are small (~100–300 words); this is expected and intentional |
| Namespace not showing | Verify metadata structure: chunks should have `metadata['namespace'] = "trusted_learning_resources"` |
| HF export fails | Check HF token: `echo $HF_TOKEN` and verify write access |

---

## Agent Handoff Prompt

**Starting point for autonomous execution:**

```
You are tasked with ingesting the Coding Interview University dataset into DepthAPI's corpus.

Context:
- Dataset location: datasets/coding-interview-university/
- Target namespace: trusted_learning_resources
- Existing chunks: ~225,000+ in data/rag/trusted/chunks.json (including postmortems)
- Expected result: ~225,150+ chunks after CIU ingestion

Your task (in order):
1. Execute pre-checks: verify CIU directory, README.md presence, and script readiness.
2. Run: python3 scripts/ingest_corpus/ingest_ciu.py
3. Audit the result: python3 scripts/ingest_corpus/detailed_audit.py
4. Verify that "Coding Interview University" appears in audit output with ~150–170 chunks.
5. Spot-check: run inspect_ciu_chunks.py (provided above in Step 3) to validate chunk structure.
6. If Supabase is available, backfill embeddings: python3 scripts/ingest_corpus/backfill_supabase_rag.py --collection-name "DepthAPI Trusted Corpus" --chunk-file data/rag/trusted/chunks.json
7. Report final chunk counts with namespace breakdown.

Success criteria:
- chunks.json contains >150 CIU chunks
- All CIU chunks have namespace="trusted_learning_resources"
- detailed_audit.py shows "Coding Interview University" in sources list
- Sample CIU chunk content includes CS terminology (Big-O, data structures, etc.)
- (Optional) Supabase backfill completes without embedding errors

Abort if:
- Dataset path does not exist
- README.md is not found
- Script has syntax errors
- Chunk validation fails unexpectedly

Report final status with:
- Total chunks added (CIU contribution)
- Final namespace distribution (all namespaces + counts)
- Any warnings or partial failures
```

---

## Next Steps

After CIU ingestion succeeds:

1. **Cross-Validate Both Datasets** — Run queries that should match CIU and postmortems separately
2. **Export Full Corpus to HF** — Run `export_to_hf.py` to push all namespaces together
3. **Document Dataset Boundaries** — Add to project README: namespace meanings and query patterns
4. **Plan Future Ingestions** — Consider `code_search_net`, `CS-and-Programming-Books`, etc.
5. **Archive Audit Reports** — Save `detailed_audit.py` output to `data/rag/reports/` for lineage
