use crate::models::{
    ChunkRecord, QualityInputs, CHUNKER_VERSION, SCHEMA_VERSION,
};
use sha2::{Digest, Sha256};
use std::collections::HashMap;

fn sha256_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    format!("{:x}", hasher.finalize())
}

pub fn rough_token_count(text: &str) -> usize {
    std::cmp::max(1, text.chars().count() / 4)
}

/// A parsed markdown block.
#[derive(Debug, Clone)]
enum BlockType {
    Heading(usize, String),
    Code(String),
    Paragraph(String),
}


/// Classify markdown into atomic blocks.
fn classify_blocks(text: &str) -> Vec<BlockType> {
    let mut blocks = Vec::new();
    let mut current_para = Vec::new();
    let mut in_code_fence = false;
    let mut code_lines = Vec::new();

    for line in text.lines() {
        let trimmed = line.trim();

        // Handle code fences
        if trimmed.starts_with("```") {
            if in_code_fence {
                code_lines.push(line);
                blocks.push(BlockType::Code(code_lines.join("\n")));
                code_lines.clear();
                in_code_fence = false;
            } else {
                if !current_para.is_empty() {
                    blocks.push(BlockType::Paragraph(current_para.join("\n")));
                    current_para.clear();
                }
                code_lines.push(line);
                in_code_fence = true;
            }
            continue;
        }

        if in_code_fence {
            code_lines.push(line);
            continue;
        }

        // Blank line ends current paragraph
        if trimmed.is_empty() {
            if !current_para.is_empty() {
                blocks.push(BlockType::Paragraph(current_para.join("\n")));
                current_para.clear();
            }
            continue;
        }

        // Headings
        if trimmed.starts_with('#') {
            if !current_para.is_empty() {
                blocks.push(BlockType::Paragraph(current_para.join("\n")));
                current_para.clear();
            }
            let level = trimmed.chars().take_while(|&c| c == '#').count();
            blocks.push(BlockType::Heading(level, line.to_string()));
            continue;
        }

        // Tables
        if trimmed.starts_with('|') && trimmed.ends_with('|') {
            current_para.push(line);
            continue;
        }

        current_para.push(line);
    }

    if in_code_fence && !code_lines.is_empty() {
        blocks.push(BlockType::Code(code_lines.join("\n")));
    }
    if !current_para.is_empty() {
        blocks.push(BlockType::Paragraph(current_para.join("\n")));
    }

    blocks
}

/// Convert blocks to raw text representation
fn block_to_text(block: &BlockType) -> &str {
    match block {
        BlockType::Heading(_, text) => text,
        BlockType::Code(text) => text,
        BlockType::Paragraph(text) => text,
    }
}


pub struct ChunkerParams<'a> {
    pub doc_id: &'a str,
    pub source_name: &'a str,
    pub source_url: Option<&'a str>,
    pub dataset_version: &'a str,
    pub dataset_namespace: Option<&'a str>,
    pub source_content_hash: &'a str,
    pub parser_version: &'a str,
    pub parser_name: &'a str,
    pub extraction_confidence: f64,
    pub max_tokens: usize,
    pub min_tokens: usize,
}

pub fn chunk_markdown(
    markdown: &str,
    params: &ChunkerParams,
) -> Vec<ChunkRecord> {
    let clean_md = markdown.trim();
    if clean_md.is_empty() {
        return Vec::new();
    }

    let blocks = classify_blocks(clean_md);
    let mut chunks: Vec<ChunkRecord> = Vec::new();
    let mut current_blocks: Vec<String> = Vec::new();
    let mut current_tokens: usize = 0;
    let mut chunk_order: usize = 0;

    let flush_chunk = |chunks: &mut Vec<ChunkRecord>,
                       current_blocks: &mut Vec<String>,
                       current_tokens: &mut usize,
                       order: &mut usize| {
        if current_blocks.is_empty() {
            return;
        }

        let content = current_blocks.join("\n\n").trim().to_string();
        if content.is_empty() {
            current_blocks.clear();
            *current_tokens = 0;
            return;
        }

        let token_count = rough_token_count(&content);
        let content_hash = sha256_hex(content.as_bytes());
        let chunk_id_key = format!("{}:{}:{}", params.doc_id, *order, content_hash);
        let chunk_id = sha256_hex(chunk_id_key.as_bytes());

        let quality_inputs = QualityInputs {
            extraction_confidence: params.extraction_confidence,
            markdown_cleanliness: 1.0,
            header_continuity: 0.9,
            ocr_corruption_rate: 0.0,
            code_block_preservation: if content.contains("```") { 1.0 } else { 0.9 },
            token_validity: if token_count >= params.min_tokens && token_count <= params.max_tokens {
                1.0
            } else {
                0.7
            },
            layout_retention: 1.0,
            table_extraction_success: 1.0,
        };
        let quality_score = quality_inputs.compute_score();

        chunks.push(ChunkRecord {
            chunk_id,
            doc_id: params.doc_id.to_string(),
            content,
            token_count,
            chunk_order: *order,
            schema_version: SCHEMA_VERSION.to_string(),
            parser_version: params.parser_version.to_string(),
            chunker_version: CHUNKER_VERSION.to_string(),
            middleware_versions: HashMap::new(),
            source_name: params.source_name.to_string(),
            source_url: params.source_url.map(|s| s.to_string()),
            dataset_version: params.dataset_version.to_string(),
            dataset_namespace: params.dataset_namespace.map(|s| s.to_string()),
            source_content_hash: params.source_content_hash.to_string(),
            content_hash,
            quality_inputs: Some(quality_inputs),
            quality_score,
            duplicate_score: 0.0,
            structural_confidence: 1.0,
            extraction_method: "direct_parse".to_string(),
            is_fallback_result: false,
            parser_name: params.parser_name.to_string(),
            metadata: serde_json::json!({}),
        });

        *order += 1;
        current_blocks.clear();
        *current_tokens = 0;
    };

    for block in blocks {
        let block_text = block_to_text(&block);
        let block_tokens = rough_token_count(block_text);

        // If this is a top-level heading (# or ##) and we already have minimum tokens, flush
        if let BlockType::Heading(level, _) = block {
            if level <= 2 && current_tokens >= params.min_tokens {
                flush_chunk(&mut chunks, &mut current_blocks, &mut current_tokens, &mut chunk_order);
            }
        }

        // If adding this block exceeds max_tokens and we already have content, flush
        if current_tokens + block_tokens > params.max_tokens && !current_blocks.is_empty() {
            flush_chunk(&mut chunks, &mut current_blocks, &mut current_tokens, &mut chunk_order);
        }

        current_blocks.push(block_text.to_string());
        current_tokens += block_tokens;
    }

    // Flush remaining
    flush_chunk(&mut chunks, &mut current_blocks, &mut current_tokens, &mut chunk_order);

    // Fallback: If no chunks (e.g. content was filtered), emit one chunk with clean_md
    if chunks.is_empty() && !clean_md.is_empty() {
        let content = clean_md.to_string();
        let token_count = rough_token_count(&content);
        let content_hash = sha256_hex(content.as_bytes());
        let chunk_id_key = format!("{}:{}:{}", params.doc_id, 0, content_hash);
        let chunk_id = sha256_hex(chunk_id_key.as_bytes());

        let quality_inputs = QualityInputs {
            extraction_confidence: params.extraction_confidence,
            markdown_cleanliness: 1.0,
            header_continuity: 1.0,
            ocr_corruption_rate: 0.0,
            code_block_preservation: 1.0,
            token_validity: 1.0,
            layout_retention: 1.0,
            table_extraction_success: 1.0,
        };

        chunks.push(ChunkRecord {
            chunk_id,
            doc_id: params.doc_id.to_string(),
            content,
            token_count,
            chunk_order: 0,
            schema_version: SCHEMA_VERSION.to_string(),
            parser_version: params.parser_version.to_string(),
            chunker_version: CHUNKER_VERSION.to_string(),
            middleware_versions: HashMap::new(),
            source_name: params.source_name.to_string(),
            source_url: params.source_url.map(|s| s.to_string()),
            dataset_version: params.dataset_version.to_string(),
            dataset_namespace: params.dataset_namespace.map(|s| s.to_string()),
            source_content_hash: params.source_content_hash.to_string(),
            content_hash,
            quality_inputs: Some(quality_inputs.clone()),
            quality_score: quality_inputs.compute_score(),
            duplicate_score: 0.0,
            structural_confidence: 1.0,
            extraction_method: "direct_parse".to_string(),
            is_fallback_result: false,
            parser_name: params.parser_name.to_string(),
            metadata: serde_json::json!({}),
        });
    }

    chunks
}
