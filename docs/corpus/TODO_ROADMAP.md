# Prioritized TODO Roadmap

1. P0: Backfill missing source licenses and retrieval timestamps in Supabase metadata.
2. P0: Run the new pipeline against the full local Supabase instance and inspect `validation_report.json`.
3. P0: Replace placeholder benchmark harness metrics with live BM25, hybrid, dense, and reranker runs.
4. P1: Add MinHash LSH and embedding-backed semantic dedup at full-corpus scale.
5. P1: Add row-group and schema assertions in CI for HF parquet compatibility.
6. P2: Add benchmark slices per source family and per license family.
