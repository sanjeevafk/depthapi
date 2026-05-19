# Supabase Namespace Audit & Remediation Plan
**Date:** 19 May 2026  
**Environment:** Local Supabase Instance  
**Status:** Ready for Action

---

## Executive Summary

| Task | Status | Finding |
|------|--------|---------|
| **1. Postmortem Ingestion Status** | ❌ NOT INGESTED | No postmortem data in database; system_design namespace exists but empty |
| **2. Generic Collection Names** | ⚠️  CRITICAL | 3 duplicate/generic names: "DepthAPI Trusted Corpus" (2x) + "DepthAPI_Trusted_Corpus" (1x) |
| **3. Redundant Single-Chunk Namespaces** | ✓ NONE FOUND | Database is empty (0 chunks); no single-chunk namespaces to consolidate |

---

## Current State: Collections

```
Database: knowledge_collections (4 records)
├── [Generic] DepthAPI Trusted Corpus        (id: e1fea9c7-396d-4a09-a208-f0c956da884a)
├── [Generic] DepthAPI Trusted Corpus        (id: 51fced1a-f5e7-4873-bf5c-f2098bd3affa)  ← DUPLICATE
├── [Generic] DepthAPI_Trusted_Corpus        (id: 49ffa1e8-f508-42b3-bf49-6680ccddc962)  ← VARIANT
└── [OK] system_design                       (id: adc432bd-29c4-4f99-b96a-b45f13716a99)
```

### Issues Identified

1. **Exact Duplicates**: Two collections with identical name "DepthAPI Trusted Corpus"
2. **Generic Naming**: All non-system_design names are generic (depthapi + trusted)
3. **No Data**: knowledge_chunks table is empty (0 chunks)
4. **Inconsistent Naming Convention**: Mix of spaces, underscores ("DepthAPI_Trusted_Corpus" vs "DepthAPI Trusted Corpus")

---

## Task 1: Postmortem Ingestion Status

### Current Status
- **Database Status**: No postmortem data found
- **Expected Namespace**: `system_design` (exists but empty)
- **Ingest Script**: `scripts/ingest_corpus/ingest_postmortems.py` (ready to run)
- **Datasets Present**: `/datasets/post-mortems/` cloned from danluu/post-mortems

### Next Steps

**Step 1 — Run postmortem ingestion:**
```bash
cd /home/sanjeev/Downloads/depthapi
python3 scripts/ingest_corpus/ingest_postmortems.py
```

**Step 2 — Verify ingestion:**
```bash
python3 scripts/ingest_corpus/diagnose_supabase_namespaces.py
# Should show:
# ✅ Postmortem Status: INGESTED
# ℹ️  system_design namespace: ~1200-1800 chunks
```

**Step 3 — Backfill to Supabase (optional, requires embeddings):**
```bash
python3 scripts/ingest_corpus/backfill_supabase_rag.py \
  --collection-name "Technical Corpus - Postmortems" \
  --chunk-file data/rag/trusted/chunks.json
```

### Documentation References
- Full pipeline: [POSTMORTEM_INGESTION.md](scripts/ingest_corpus/POSTMORTEM_INGESTION.md)
- Base ingestor: [base_ingestor.py](scripts/ingest_corpus/base_ingestor.py)
- Dataset source: [datasets/post-mortems/](datasets/post-mortems/)

---

## Task 2: Rename Generic Collection Names

### Recommended Mapping

| Current Name | Severity | Suggested Name | Rationale |
|---|---|---|---|
| DepthAPI Trusted Corpus | 🔴 CRITICAL | Technical Corpus - MDN | Primary data source (if ingested) |
| DepthAPI Trusted Corpus | 🔴 CRITICAL | REMOVE (duplicate) | Delete once data migrated |
| DepthAPI_Trusted_Corpus | 🟡 HIGH | Technical Corpus - Python | Inconsistent naming/variant form |
| system_design | ✓ OK | Keep as-is | Contextually appropriate; hosts postmortems |

### Data-Aware Naming Strategy

New names should follow this pattern:
```
Technical Corpus - {Primary Source}
  Examples:
  - Technical Corpus - MDN Documentation
  - Technical Corpus - Kubernetes
  - Technical Corpus - System Design Primers
  - Technical Corpus - Postmortems
  - Technical Corpus - Python Docs
```

### Implementation

#### Option A: Direct SQL (Quickest)

```sql
-- 1. Rename the first generic collection
UPDATE knowledge_collections 
SET name = 'Technical Corpus - Trusted Knowledge'
WHERE id = 'e1fea9c7-396d-4a09-a208-f0c956da884a';

-- 2. Rename the variant naming
UPDATE knowledge_collections 
SET name = 'Technical Corpus - Duplicate (Archive)'
WHERE id = 'e1fea9c7-396d-4a09-a208-f0c956da884a';

-- 3. Rename the underscore variant
UPDATE knowledge_collections 
SET name = 'Technical Corpus - Legacy Format'
WHERE id = 'cf49ffa1e8-f508-42b3-bf49-6680ccddc962';
```

#### Option B: Python Script

```python
import asyncio
from api.auth import get_supabase_admin

async def rename_collections():
    supabase = get_supabase_admin()
    
    updates = [
        {
            "id": "e1fea9c7-396d-4a09-a208-f0c956da884a",
            "new_name": "Technical Corpus - Trusted Knowledge"
        },
        {
            "id": "51fced1a-f5e7-4873-bf5c-f2098bd3affa", 
            "new_name": "Technical Corpus - Postmortems & Archived"
        },
        {
            "id": "49ffa1e8-f508-42b3-bf49-6680ccddc962",
            "new_name": "Technical Corpus - Legacy (Underscore Format)"
        }
    ]
    
    for update in updates:
        await supabase.table("knowledge_collections").update({
            "name": update["new_name"]
        }).eq("id", update["id"]).execute()
        print(f"✓ Renamed {update['id']} → {update['new_name']}")

asyncio.run(rename_collections())
```

### Verification After Renaming

```bash
# Run diagnostic again
python3 scripts/ingest_corpus/diagnose_supabase_namespaces.py

# Expected output:
# [*] Fetching knowledge_collections...
#   ✓ Technical Corpus - Trusted Knowledge (id: e1fea9c7-...)
#   ✓ Technical Corpus - Postmortems & Archived (id: 51fced1a-...)
#   ✓ Technical Corpus - Legacy (id: 49ffa1e8-...)
#   ✓ system_design (id: adc432bd-...)
```

---

## Task 3: Remove/Relocate Redundant Single-Chunk Namespaces

### Current Finding
✅ **No single-chunk namespaces found** — Database is currently empty (0 chunks)

### What This Means
- Once postmortem data is ingested, monitor for single-chunk namespaces
- These typically occur from:
  - Stray markdown files with no siblings
  - One-off documentation pages
  - Incomplete ingestion batches

### Detection & Prevention Strategy

**After ingestion, identify single-chunk namespaces:**
```bash
# Run diagnostic with JSON output
python3 scripts/ingest_corpus/diagnose_supabase_namespaces.py --json > report.json

# Look for:
# "total_chunks": 1
```

**Consolidation Options:**

1. **Merge into 'Misc' namespace** (if intentional orphans):
   ```sql
   UPDATE knowledge_chunks 
   SET metadata = jsonb_set(metadata, '{namespace}', '"miscellaneous"'::jsonb)
   WHERE (metadata->>'namespace' = 'orphaned_page');
   ```

2. **Classify by source, then consolidate**:
   ```sql
   -- For single-chunk namespaces from MDN documentation
   UPDATE knowledge_chunks 
   SET metadata = jsonb_set(metadata, '{namespace}', '"system_design"'::jsonb)
   WHERE (metadata->>'namespace' IN ('single_page_1', 'single_page_2'));
   ```

3. **Archive instead of delete** (soft delete):
   ```sql
   UPDATE knowledge_chunks 
   SET deleted_at = now()
   WHERE (metadata->>'namespace')::text IN (
       SELECT namespace, COUNT(*) as cnt 
       FROM knowledge_chunks 
       GROUP BY namespace 
       HAVING count = 1
   );
   ```

### Going Forward

Add this pre-ingestion validation to `base_ingestor.py`:

```python
def _validate_namespace_distribution(chunks: list[dict]) -> dict[str, int]:
    """Warn about single-chunk namespaces before persisting."""
    ns_counts = Counter(c.get("metadata", {}).get("namespace", "trusted") for c in chunks)
    singles = {ns: count for ns, count in ns_counts.items() if count == 1}
    
    if singles:
        log.warning(f"Single-chunk namespaces detected: {singles}")
        log.warning("Consider consolidating these into a parent or misc category")
    
    return dict(ns_counts)
```

---

## Execution Timeline

### Phase 1: Immediate (Today)
- [x] Diagnose current state → **COMPLETE**
- [ ] **Task 1**: Run `ingest_postmortems.py`
- [ ] **Task 2**: Execute collection renames (Option A or B)
- [ ] Verify with `diagnose_supabase_namespaces.py`

### Phase 2: Post-Ingestion (After postmortems loaded)
- [ ] **Task 3**: Monitor for single-chunk namespaces with diagnostic
- [ ] Consolidate any orphans found
- [ ] Update ingestion pipeline to auto-detect suspicious patterns

---

## Hugging Face Split Semantics

### Why the viewer shows only `postmortems_backfill`

The Hugging Face dataset viewer groups rows by the **split name** used at upload time. In this repo, the exporter defaults to a single split:

- `postmortems_backfill`

That means the viewer will show **one split only**, even if the rows inside it come from many different namespaces or collections.

### What is actually inside the split

Each row still keeps its own metadata:

- `namespace`
- `collection_id`
- `collection_name`
- `source_name`
- `source_url`

So the data is not “only postmortems” in the content sense. The split label is just the container name.

### Simple way to think about it

- **Split** = folder label on Hugging Face
- **Row** = one chunk of text
- **Namespace / collection** = metadata attached to the row

### Why the row count looks right

The row count is correct because the exporter uploaded the rows successfully. The split name does **not** change the row content; it only names the bucket where those rows live.

### How namespace renames show up

Namespace or collection renames in Supabase are reflected in the export only if the exporter reads the updated tables and writes those names into the row metadata.

This exporter does that by loading:

- `knowledge_collections` → `collection_name`
- `knowledge_documents` → `collection_id`

So if the Supabase names change, the **next export** will carry the new names into the dataset rows.

### Why the existing dataset is not disturbed

The exporter is append-oriented:

- it dedupes by `content_hash`
- it uploads to a separate split (`postmortems_backfill`)
- it skips README updates unless explicitly requested

That means it adds new rows without rewriting the existing `train` split.

### ELI12 version

Think of Hugging Face like a library shelf:

- the **split name** is the shelf label
- the **rows** are the books
- the **namespace** is the sticker inside each book

You can move the sticker text in Supabase, and the next export will print the new sticker text inside the books.
But the shelf label stays `postmortems_backfill` unless you change the exporter to write into another split.

### Phase 3: Cleanup (Optional, 1-2 weeks)
- [ ] Audit duplicate collection data
- [ ] Soft-delete unused/test collections
- [ ] Document final naming convention in project README

---

## Files Created/Modified

- **Created**: `scripts/ingest_corpus/diagnose_supabase_namespaces.py` — Diagnostic harness (re-runnable)
- **Reference**: Postmortem ingest pipeline (ready to execute)
- **Backup**: Original migration exists in `supabase/migrations/202604230003_*.sql`

---

## Next Action

Run this to execute immediate tasks:

```bash
cd /home/sanjeev/Downloads/depthapi

# 1. Ingest postmortems
python3 scripts/ingest_corpus/ingest_postmortems.py

# 2. Rename collections (Option A: SQL) or (Option B: Python)
# [Choose one from Implementation section above]

# 3. Verify
python3 scripts/ingest_corpus/diagnose_supabase_namespaces.py
```

---

## Support

- **Ingest issues**: Check [POSTMORTEM_INGESTION.md](scripts/ingest_corpus/POSTMORTEM_INGESTION.md)
- **Database issues**: Review migrations in `supabase/migrations/`
- **Re-run diagnostics**: `python3 scripts/ingest_corpus/diagnose_supabase_namespaces.py [--json]`
