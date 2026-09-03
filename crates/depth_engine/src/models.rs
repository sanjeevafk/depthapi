use serde::{Deserialize, Serialize};
use std::collections::HashMap;

pub const SCHEMA_VERSION: &str = "1.0.0";
pub const ENGINE_VERSION: &str = "0.1.0";
pub const CHUNKER_VERSION: &str = "depth-engine-chunker@0.1.0";
pub const PARSER_NAME: &str = "depth-engine";

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct QualityInputs {
    pub extraction_confidence: f64,
    pub markdown_cleanliness: f64,
    pub header_continuity: f64,
    pub ocr_corruption_rate: f64,
    pub code_block_preservation: f64,
    pub token_validity: f64,
    pub layout_retention: f64,
    pub table_extraction_success: f64,
}

impl QualityInputs {
    pub fn compute_score(&self) -> f64 {
        let weights = [
            (self.extraction_confidence, 0.25),
            (self.markdown_cleanliness, 0.15),
            (self.header_continuity, 0.10),
            (self.code_block_preservation, 0.15),
            (self.token_validity, 0.15),
            (self.layout_retention, 0.10),
            (self.table_extraction_success, 0.10),
        ];
        let sum_weights: f64 = weights.iter().map(|(_, w)| w).sum();
        let raw_score: f64 = weights.iter().map(|(val, w)| val * w).sum::<f64>() / sum_weights;
        let ocr_penalty = self.ocr_corruption_rate * 0.1;
        let score = (raw_score - ocr_penalty).clamp(0.0, 1.0);
        (score * 10000.0).round() / 10000.0
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ChunkRecord {
    pub chunk_id: String,
    pub doc_id: String,
    pub content: String,
    pub token_count: usize,
    pub chunk_order: usize,
    pub schema_version: String,
    pub parser_version: String,
    pub chunker_version: String,
    pub middleware_versions: HashMap<String, String>,
    pub source_name: String,
    pub source_url: Option<String>,
    pub dataset_version: String,
    pub dataset_namespace: Option<String>,
    pub source_content_hash: String,
    pub content_hash: String,
    pub quality_inputs: Option<QualityInputs>,
    pub quality_score: f64,
    pub duplicate_score: f64,
    pub structural_confidence: f64,
    pub extraction_method: String,
    pub is_fallback_result: bool,
    pub parser_name: String,
    pub metadata: serde_json::Value,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ParsedDocumentRecord {
    pub doc_id: String,
    pub source_uri: String,
    pub markdown_content: String,
    pub format: String,
    pub extraction_confidence: f64,
    pub parser_name: String,
    pub parser_version: String,
    pub source_content_hash: String,
    pub warnings: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct IngestResultRecord {
    pub parsed_doc: ParsedDocumentRecord,
    pub chunks: Vec<ChunkRecord>,
}
