use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use sha2::{Digest, Sha256};

use std::collections::HashMap;

pub mod chunker;
pub mod models;
pub mod parser;
pub mod retrieval;

use chunker::{chunk_markdown as rust_chunk_markdown, ChunkerParams};
use models::{
    IngestResultRecord, ParsedDocumentRecord, ENGINE_VERSION, PARSER_NAME,
};
use parser::parse_to_markdown;

fn sha256_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    format!("{:x}", hasher.finalize())
}

#[pymodule]
mod depth_engine {
    use super::*;

    #[pyfunction]
    fn engine_version() -> &'static str {
        ENGINE_VERSION
    }

    /// Convert document bytes (.pdf, .docx, .xlsx, .pptx, .md, .html, etc.) to GitHub-Flavored Markdown.
    #[pyfunction]
    #[pyo3(signature = (bytes, filename_or_ext=None, mime_type=None))]
    fn to_markdown(
        py: Python,
        bytes: &[u8],
        filename_or_ext: Option<&str>,
        mime_type: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        match parse_to_markdown(bytes, filename_or_ext, mime_type) {
            Ok(result) => {
                let dict = serde_json::json!({
                    "markdown": result.markdown,
                    "format": result.format,
                    "confidence": result.confidence,
                    "warnings": result.warnings,
                });
                let bound = pythonize::pythonize(py, &dict)
                    .map_err(|e| PyValueError::new_err(e.to_string()))?;
                Ok(bound.unbind())
            }
            Err(e) => Err(PyValueError::new_err(e)),
        }
    }

    /// Chunk markdown text into structured Chunk records.
    #[pyfunction]
    #[pyo3(signature = (
        markdown,
        doc_id,
        source_name,
        source_url=None,
        dataset_version="v1",
        dataset_namespace=None,
        source_content_hash="",
        parser_version="depth-engine@0.1.0",
        parser_name="depth-engine",
        extraction_confidence=1.0,
        max_tokens=480,
        min_tokens=50
    ))]
    fn chunk_markdown(
        py: Python,
        markdown: &str,
        doc_id: &str,
        source_name: &str,
        source_url: Option<&str>,
        dataset_version: &str,
        dataset_namespace: Option<&str>,
        source_content_hash: &str,
        parser_version: &str,
        parser_name: &str,
        extraction_confidence: f64,
        max_tokens: usize,
        min_tokens: usize,
    ) -> PyResult<Py<PyAny>> {
        let params = ChunkerParams {
            doc_id,
            source_name,
            source_url,
            dataset_version,
            dataset_namespace,
            source_content_hash,
            parser_version,
            parser_name,
            extraction_confidence,
            max_tokens,
            min_tokens,
        };

        let chunks = rust_chunk_markdown(markdown, &params);
        let bound = pythonize::pythonize(py, &chunks)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(bound.unbind())
    }

    /// Complete pipeline: parse bytes of any supported format and chunk into validated chunk records.
    #[pyfunction]
    #[pyo3(signature = (
        doc_id,
        raw_bytes,
        filename_or_ext=None,
        source_url=None,
        source_name=None,
        dataset_version="v1",
        dataset_namespace=None,
        max_tokens=480,
        min_tokens=50
    ))]
    fn parse_and_chunk(
        py: Python,
        doc_id: &str,
        raw_bytes: &[u8],
        filename_or_ext: Option<&str>,
        source_url: Option<&str>,
        source_name: Option<&str>,
        dataset_version: &str,
        dataset_namespace: Option<&str>,
        max_tokens: usize,
        min_tokens: usize,
    ) -> PyResult<Py<PyAny>> {
        let source_content_hash = sha256_hex(raw_bytes);
        let parse_res = match parse_to_markdown(raw_bytes, filename_or_ext, None) {
            Ok(res) => res,
            Err(e) => return Err(PyValueError::new_err(e)),
        };

        let parser_version = format!("{PARSER_NAME}@{ENGINE_VERSION}");
        let s_name = source_name.or(filename_or_ext).unwrap_or("document");

        let params = ChunkerParams {
            doc_id,
            source_name: s_name,
            source_url,
            dataset_version,
            dataset_namespace,
            source_content_hash: &source_content_hash,
            parser_version: &parser_version,
            parser_name: PARSER_NAME,
            extraction_confidence: parse_res.confidence,
            max_tokens,
            min_tokens,
        };

        let chunks = rust_chunk_markdown(&parse_res.markdown, &params);

        let result = IngestResultRecord {
            parsed_doc: ParsedDocumentRecord {
                doc_id: doc_id.to_string(),
                source_uri: source_url.unwrap_or(filename_or_ext.unwrap_or("direct")).to_string(),
                markdown_content: parse_res.markdown,
                format: parse_res.format,
                extraction_confidence: parse_res.confidence,
                parser_name: PARSER_NAME.to_string(),
                parser_version,
                source_content_hash,
                warnings: parse_res.warnings,
            },
            chunks,
        };

        let bound = pythonize::pythonize(py, &result)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(bound.unbind())
    }

    /// Reorder contexts into a U-shaped distribution to prevent lost-in-the-middle degradation.
    #[pyfunction]
    fn reorder_lost_in_the_middle<'py>(
        py: Python<'py>,
        contexts: &Bound<'py, pyo3::types::PySequence>,
    ) -> PyResult<Bound<'py, pyo3::types::PyList>> {
        let len = contexts.len()?;
        if len <= 2 {
            return contexts.to_list();
        }
        let mut items = Vec::with_capacity(len);
        for i in 0..len {
            items.push(contexts.get_item(i)?);
        }
        let reordered = retrieval::ordering::reorder_lost_in_the_middle(items);
        pyo3::types::PyList::new(py, reordered)
    }

    /// High-throughput Reciprocal Rank Fusion with optional Mosaic Negative Query Algebra.
    #[pyfunction]
    #[pyo3(signature = (dense_ranks, lex_ranks, k=60.0, negative_terms=None, candidate_texts=None))]
    fn fuse_rrf(
        dense_ranks: Vec<String>,
        lex_ranks: Vec<String>,
        k: f64,
        negative_terms: Option<Vec<String>>,
        candidate_texts: Option<HashMap<String, String>>,
    ) -> Vec<(String, f64)> {
        retrieval::rrf::fuse_rrf(dense_ranks, lex_ranks, k, negative_terms, candidate_texts)
    }

    /// Fast query intent routing using compiled RegexSet DFA automaton (<1 µs).
    #[pyfunction]
    fn detect_graph_hops(query: &str) -> u32 {
        retrieval::router::detect_graph_hops(query)
    }

    /// Evaluate retrieval confidence (CRAG - Corrective RAG gating).
    #[pyfunction]
    #[pyo3(signature = (candidates, is_reranked=false))]
    fn evaluate_confidence(
        py: Python,
        candidates: Bound<'_, PyAny>,
        is_reranked: bool,
    ) -> PyResult<Py<PyAny>> {
        let eval = if let Ok(scores) = candidates.extract::<Vec<f64>>() {
            retrieval::crag::evaluate_confidence(&scores, is_reranked)
        } else {
            let json_vals: Vec<serde_json::Value> = pythonize::depythonize(&candidates)
                .map_err(|e| PyValueError::new_err(format!("Invalid candidates for confidence evaluation: {e}")))?;
            retrieval::crag::evaluate_contexts_confidence(&json_vals)
        };
        let bound = pythonize::pythonize(py, &eval)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(bound.unbind())
    }

    /// Normalize context text while preserving technical citations.
    #[pyfunction]
    #[pyo3(signature = (text, max_chars=1000))]
    fn normalize_context_text(text: &str, max_chars: usize) -> String {
        retrieval::context::normalize_context_text(text, max_chars)
    }

    /// Compress contexts and enforce total prompt budget.
    #[pyfunction]
    #[pyo3(signature = (contexts, max_contexts=3, max_chars_per_context=1000, max_total_chars=3000))]
    fn compress_contexts(
        py: Python,
        contexts: Bound<'_, PyAny>,
        max_contexts: usize,
        max_chars_per_context: usize,
        max_total_chars: usize,
    ) -> PyResult<Py<PyAny>> {
        let json_vals: Vec<serde_json::Value> = pythonize::depythonize(&contexts)
            .map_err(|e| PyValueError::new_err(format!("Invalid contexts list: {e}")))?;
        let compressed = retrieval::context::compress_contexts(
            json_vals,
            max_contexts,
            max_chars_per_context,
            max_total_chars,
        );
        let bound = pythonize::pythonize(py, &compressed)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(bound.unbind())
    }

    /// High-speed AST parsing and deterministic concept extraction.
    #[pyfunction]
    #[pyo3(signature = (raw_text, chunks=None, document_title=None, user_metadata=None, known_entities=None))]
    fn extract_concepts_and_edges(
        py: Python,
        raw_text: &str,
        chunks: Option<Bound<'_, PyAny>>,
        document_title: Option<&str>,
        user_metadata: Option<Bound<'_, PyAny>>,
        known_entities: Option<Vec<String>>,
    ) -> PyResult<Py<PyAny>> {
        let chunks_json: Option<Vec<serde_json::Value>> = match chunks {
            Some(c) => Some(pythonize::depythonize(&c).map_err(|e| PyValueError::new_err(e.to_string()))?),
            None => None,
        };
        let user_meta_json: Option<HashMap<String, serde_json::Value>> = match user_metadata {
            Some(m) => Some(pythonize::depythonize(&m).map_err(|e| PyValueError::new_err(e.to_string()))?),
            None => None,
        };
        let graph = retrieval::concept_extractor::extract_concepts_and_edges(
            raw_text,
            chunks_json.as_deref(),
            document_title,
            user_meta_json.as_ref(),
            known_entities.as_deref(),
        );
        let bound = pythonize::pythonize(py, &graph)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(bound.unbind())
    }

    /// High-speed markdown vault scanner detecting broken [[WikiLinks]], orphan concept nodes, and cycles.
    #[pyfunction]
    fn lint_wiki_vault(py: Python, vault_dir: &str) -> PyResult<Py<PyAny>> {
        match retrieval::linter::lint_wiki_vault(vault_dir) {
            Ok(report) => {
                let bound = pythonize::pythonize(py, &report)
                    .map_err(|e| PyValueError::new_err(e.to_string()))?;
                Ok(bound.unbind())
            }
            Err(e) => Err(PyValueError::new_err(e)),
        }
    }
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_engine_version() {
        assert_eq!(ENGINE_VERSION, "0.1.0");
    }

    #[test]
    fn test_parse_markdown() {
        let raw = b"# Title\n\nContent here.";
        let res = parse_to_markdown(raw, Some("test.md"), None).expect("failed to parse");
        assert_eq!(res.format, "markdown");
        assert!(res.markdown.contains("# Title"));
    }

    #[test]
    fn test_parse_csv() {
        let raw = b"col1,col2\nval1,val2\n";
        let res = parse_to_markdown(raw, Some("data.csv"), None).expect("failed to parse");
        assert_eq!(res.format, "csv");
        assert!(res.markdown.contains("| col1 | col2 |"));
    }

    #[test]
    fn test_chunking_and_quality() {
        let md = "# Heading 1\n\nFirst paragraph.\n\n## Heading 2\n\n```python\nprint(1)\n```\n";
        let params = ChunkerParams {
            doc_id: "doc-1",
            source_name: "test",
            source_url: None,
            dataset_version: "v1",
            dataset_namespace: None,
            source_content_hash: "hash",
            parser_version: "depth-engine@0.1.0",
            parser_name: "depth-engine",
            extraction_confidence: 1.0,
            max_tokens: 480,
            min_tokens: 5,
        };
        let chunks = rust_chunk_markdown(md, &params);
        assert!(!chunks.is_empty());
        assert_eq!(chunks[0].doc_id, "doc-1");
        assert!(chunks[0].quality_score > 0.8);
    }
}

