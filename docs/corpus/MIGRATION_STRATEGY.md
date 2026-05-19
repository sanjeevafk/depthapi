# Migration Strategy

1. Keep the existing `data/rag/trusted/chunks.json` flow untouched for serving traffic.
2. Build the new corpus in `data/research_corpus/` so artifacts are isolated.
3. Once governance and validation reports are acceptable, export the parquet bundle to the Hugging Face dataset repo.
4. Switch downstream benchmark and release scripts to consume `deduped_chunks.parquet`.
5. After the new path is stable, deprecate the legacy `chunks.json` exporter and backfill missing metadata into Supabase.
