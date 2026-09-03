use regex::Regex;
use serde_json::{Map, Value};
use std::collections::HashSet;
use std::sync::LazyLock;

static DECORATIVE_MD_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?m)^\s*(?:-{3,}|\*{3,}|_{3,})\s*$").unwrap());
static MD_LINK_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\[([^\]]+)\]\(([^)]+)\)").unwrap());
static HEADING_PREFIX_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?m)^\s{0,3}#{1,6}\s+").unwrap());
static BLOCKQUOTE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?m)^\s*>\s?").unwrap());
static REPEATED_WS_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"[ \t]{2,}").unwrap());
static MULTI_BLANK_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\n{3,}").unwrap());
static NON_WORD_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\W+").unwrap());

static DOC_PREFIX_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^doc(?:ument)?[_:\-\s]+").unwrap());
static CHUNK_PREFIX_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^chunk[_:\-\s]+").unwrap());
static SEP_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"[\s/|:]+").unwrap());
static INVALID_ID_CHARS_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"[^a-z0-9#._-]+").unwrap());
static MULTI_DASH_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"-{2,}").unwrap());

pub fn rough_token_count(text: &str) -> usize {
    (text.len() / 4).max(1)
}

/// Normalize document/chunk IDs for exact-match retrieval metrics.
pub fn canonical_id(value: &str) -> String {
    let ascii: String = value
        .chars()
        .filter(|c| c.is_ascii())
        .collect::<String>()
        .to_lowercase();
    let mut s = ascii.trim().to_string();

    s = DOC_PREFIX_RE.replace(&s, "").to_string();
    s = CHUNK_PREFIX_RE.replace(&s, "").to_string();
    s = SEP_RE.replace_all(&s, "-").to_string();
    s = INVALID_ID_CHARS_RE.replace_all(&s, "").to_string();
    s = MULTI_DASH_RE.replace_all(&s, "-").to_string();
    s.trim_matches(|c| c == '-' || c == '_' || c == '.').to_string()
}

fn split_sentences(text: &str) -> Vec<&str> {
    let mut parts = Vec::new();
    let mut last = 0;
    let bytes = text.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'.' || bytes[i] == b'!' || bytes[i] == b'?' {
            let punct_pos = i;
            let mut j = i + 1;
            let mut has_ws = false;
            while j < bytes.len()
                && (bytes[j] == b' ' || bytes[j] == b'\t' || bytes[j] == b'\n' || bytes[j] == b'\r')
            {
                has_ws = true;
                j += 1;
            }
            if has_ws {
                parts.push(&text[last..=punct_pos]);
                last = j;
                i = j;
                continue;
            }
        }
        i += 1;
    }
    if last < text.len() {
        let rem = text[last..].trim();
        if !rem.is_empty() {
            parts.push(&text[last..]);
        }
    }
    parts
}

/// Compress context while preserving technical meaning and citations.
pub fn normalize_context_text(text: &str, max_chars: usize) -> String {
    let s = text.to_string();
    let s = DECORATIVE_MD_RE.replace_all(&s, "");
    let s = MD_LINK_RE.replace_all(&s, "$1 ($2)");
    let s = HEADING_PREFIX_RE.replace_all(&s, "");
    let s = BLOCKQUOTE_RE.replace_all(&s, "");
    let s = REPEATED_WS_RE.replace_all(&s, " ");
    let s = MULTI_BLANK_RE.replace_all(&s, "\n\n");
    let clean = s.trim();

    let mut seen: HashSet<String> = HashSet::new();
    let mut deduped: Vec<&str> = Vec::new();

    for part in split_sentences(clean) {
        let norm = NON_WORD_RE.replace_all(part, " ").trim().to_lowercase();
        if !norm.is_empty() && seen.insert(norm) {
            deduped.push(part.trim());
        }
    }

    let joined = deduped.join(" ");

    if joined.len() <= max_chars {
        return joined;
    }

    let cut = joined[..max_chars].trim_end();
    let r_nl = cut.rfind('\n').unwrap_or(0);
    let r_dot = cut.rfind(". ").unwrap_or(0);
    let r_semi = cut.rfind("; ").unwrap_or(0);
    let r_sp = cut.rfind(' ').unwrap_or(0);

    let boundary = r_nl.max(r_dot).max(r_semi).max(r_sp);
    let threshold = (max_chars as f64 * 0.65) as usize;

    if boundary > threshold {
        format!("{}...", cut[..=boundary].trim_end())
    } else {
        format!("{cut}...")
    }
}

/// Normalize selected contexts and enforce total prompt budget.
pub fn compress_contexts(
    contexts: Vec<Value>,
    max_contexts: usize,
    max_chars_per_context: usize,
    max_total_chars: usize,
) -> Vec<Value> {
    let mut compressed: Vec<Value> = Vec::new();
    let mut total_chars = 0;
    let mut seen_texts: HashSet<String> = HashSet::new();
    let mut seen_docs: HashSet<String> = HashSet::new();

    for context in contexts {
        if compressed.len() >= max_contexts || total_chars >= max_total_chars {
            break;
        }

        let raw_text = context
            .get("content")
            .or_else(|| context.get("text"))
            .and_then(|v| v.as_str())
            .unwrap_or("");

        let remaining = max_total_chars.saturating_sub(total_chars);
        let max_chars = 250.max(max_chars_per_context.min(remaining));
        let text = normalize_context_text(raw_text, max_chars);

        let prefix_sample = if text.len() > 500 { &text[..500] } else { &text };
        let fingerprint = NON_WORD_RE
            .replace_all(prefix_sample, " ")
            .trim()
            .to_lowercase();

        let doc_id_raw = context
            .get("document_id")
            .or_else(|| context.get("doc_id"))
            .or_else(|| context.get("source_name"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let doc_id = canonical_id(doc_id_raw);

        if !fingerprint.is_empty() && seen_texts.contains(&fingerprint) {
            continue;
        }
        if !doc_id.is_empty() && seen_docs.contains(&doc_id) && !compressed.is_empty() {
            continue;
        }

        let mut item = match context {
            Value::Object(map) => map,
            _ => Map::new(),
        };

        let token_count = item
            .get("token_count")
            .and_then(|v| v.as_u64())
            .map(|v| v as usize)
            .unwrap_or_else(|| rough_token_count(&text));

        item.insert("content".to_string(), Value::String(text.clone()));
        item.insert(
            "token_count".to_string(),
            Value::Number(serde_json::Number::from(token_count)),
        );

        total_chars += text.len();
        if !fingerprint.is_empty() {
            seen_texts.insert(fingerprint);
        }
        if !doc_id.is_empty() {
            seen_docs.insert(doc_id);
        }

        compressed.push(Value::Object(item));
    }

    compressed
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_canonical_id() {
        assert_eq!(canonical_id("document_123"), "123");
        assert_eq!(canonical_id("chunk:456"), "456");
        assert_eq!(canonical_id("doc_API-Guide/v1.0"), "api-guide-v1.0");
    }

    #[test]
    fn test_normalize_context() {
        let raw = "## Section 1\n\n[Python](https://python.org)\n\nDuplicate sentence. Duplicate sentence. Unique!";
        let normalized = normalize_context_text(raw, 1000);
        assert!(!normalized.contains("##"));
        assert!(normalized.contains("Python (https://python.org)"));
        assert!(normalized.contains("Unique!"));
    }
}
