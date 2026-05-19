# Technical Debt Report

## Current Gaps

- Supabase chunk metadata is inconsistent and often missing `upstream_license`, `retrieved_at`, and stable document provenance.
- The local `data/rag/trusted/chunks.json` snapshot is a partial corpus and does not represent the 240k-chunk production source.
- Existing retrieval evaluation is a lightweight offline scorer, not a benchmark harness for multiple retriever families.
- Legacy export logic assumes a flat dataset shape and does not emit governance artifacts.

## Recommended Follow-up

- Backfill provenance fields into `knowledge_documents` and `knowledge_chunks`.
- Add embedding-based semantic dedup once the preferred local or remote embedding provider is fixed.
- Replace placeholder benchmark scoring with live retriever adapters and recorded experiment outputs.
