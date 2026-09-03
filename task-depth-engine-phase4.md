# Task: Phase 4 — Rust Core Engine (`depth_engine`)

## Status
- **Phase:** 4 (Compiled Engine Core)
- **Branch:** `feat/depth-engine-rust-core`
- **State:** Completed
- **Test Results:** 164 passing (153 regression baseline + 11 new depth_engine tests), 4/4 cargo tests passing

## Completed Objectives
1. **Initialized Rust Crate (`crates/depth_engine`):**
   - Setup root Cargo workspace and `crates/depth_engine` package with `maturin` and `pyo3` 0.29 bindings.
   - Core dependencies: `pyo3`, `pythonize`, `anydoc` 0.2.4, `serde`, `serde_json`, `sha2`.
2. **Multi-Format Ingestion via `anydoc`:**
   - Document conversion to GitHub-Flavored Markdown for `.docx`, `.xlsx`, `.pptx`, `.pdf`, `.md`, `.html`, and `.csv`.
   - Soft-fail extraction error handling: preserves partial text with lineage extraction warnings, fails fast on completely unextractable/garbage documents.
3. **Zero-Copy Structural & Semantic Chunking:**
   - Fast Markdown heading and token-bounded chunking in Rust matching DepthAPI chunk schema (`Chunk` data contract, deterministic `chunk_id` hashing, and deterministic `quality_score` computation).
4. **PyO3 Native Python Bindings:**
   - Exposed `depth_engine` Python module with `parse_and_chunk`, `chunk_markdown`, and `to_markdown`.
   - Implemented `api/services/rag/pipeline/depth_engine_adapter.py` with automatic pure-Python fallback.
   - Integrated into `api/routers/ingest.py` supporting auto-routing and explicit `engine="depth-engine"`.
5. **Verification & Parity:**
   - Native Rust tests: 4 passed in `crates/depth_engine`.
   - Python unit tests: 11 passed in `tests/unit/test_depth_engine.py`.
   - Full test suite: 164 passed across entire repository with 0 regressions.
