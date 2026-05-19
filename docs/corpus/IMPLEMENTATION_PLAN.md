# Implementation Plan

1. Stabilize source export from Supabase with a deterministic JSONL snapshot.
2. Normalize governance metadata so each chunk carries provenance and license fields.
3. Replace heuristic chunking with deterministic markdown-aware chunking.
4. Add a three-layer dedup pass and keep audit artifacts for removed chunks.
5. Add quality validation and corpus health reports.
6. Publish benchmark assets plus a reproducible evaluation harness.
7. Package the pipeline with `Makefile`, `Dockerfile`, and CI.
