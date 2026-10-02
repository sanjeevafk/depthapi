use std::collections::{HashMap, HashSet};

/// High-throughput Reciprocal Rank Fusion (RRF) with optional Mosaic Negative Query Algebra.
///
/// Merges dense and lexical candidate rankings into a unified score:
///   RRF_score = (1.0 / (k + rank_dense)) + (1.0 / (k + rank_lexical))
///
/// If `negative_terms` are provided and match candidate text or IDs,
/// Mosaic soft penalty (lambda = 0.5) downweights the score.
///
/// ## Performance note (turbopuffer batched-iterator pattern)
///
/// The hot path is split into two passes to let LLVM auto-vectorize the arithmetic:
///
///   Pass 1 — arithmetic (branch-free, SIMD-friendly):
///     Iterate over a contiguous `Vec<&str>` of IDs, compute RRF scores into a
///     parallel `Vec<f64>`.  No branches inside this loop, so the compiler can
///     emit SIMD FP instructions across the whole slice.
///
///   Pass 2 — negation (branch-heavy, small):
///     Walk the same two Vecs and apply the 0.5 penalty where required.
///     Separated from Pass 1 so the branch mispredictions stay out of the hot loop.
///
/// This avoids the "zero-cost iterator" SIMD-prevention issue described in
/// turbopuffer's blog post: each `next()` call creates a recursive dependency
/// that hides the loop shape the compiler needs for vectorization.
fn score_ids(
    ids: &[&str],
    dense_map: &HashMap<String, usize>,
    lex_map: &HashMap<String, usize>,
    k: f64,
) -> Vec<f64> {
    // Branch-free arithmetic loop — LLVM can auto-vectorize this.
    ids.iter()
        .map(|id| {
            let v_rank = dense_map.get(*id).copied().unwrap_or(1_000_000) as f64;
            let b_rank = lex_map.get(*id).copied().unwrap_or(1_000_000) as f64;
            (1.0 / (k + v_rank)) + (1.0 / (k + b_rank))
        })
        .collect()
}

fn apply_negation_pass(
    ids: &[&str],
    scores: &mut [f64],
    normalized_negatives: &[String],
    texts: &HashMap<String, String>,
) {
    for (id, score) in ids.iter().zip(scores.iter_mut()) {
        let is_negated = if let Some(text) = texts.get(*id) {
            let text_lower = text.to_lowercase();
            normalized_negatives.iter().any(|neg| text_lower.contains(neg.as_str()))
        } else {
            let id_lower = id.to_lowercase();
            normalized_negatives.iter().any(|neg| id_lower.contains(neg.as_str()))
        };
        if is_negated {
            *score *= 0.5; // Mosaic soft penalty lambda = 0.5
        }
    }
}

pub fn fuse_rrf(
    dense_ranks: Vec<String>,
    lex_ranks: Vec<String>,
    k: f64,
    negative_terms: Option<Vec<String>>,
    candidate_texts: Option<HashMap<String, String>>,
) -> Vec<(String, f64)> {
    let mut dense_map: HashMap<String, usize> = HashMap::with_capacity(dense_ranks.len());
    for (rank, id) in dense_ranks.into_iter().enumerate() {
        dense_map.entry(id).or_insert(rank);
    }

    let mut lex_map: HashMap<String, usize> = HashMap::with_capacity(lex_ranks.len());
    for (rank, id) in lex_ranks.into_iter().enumerate() {
        lex_map.entry(id).or_insert(rank);
    }

    // Collect into a Vec so iteration is over contiguous memory — a prerequisite
    // for SIMD vectorization in the score pass below.
    let all_ids: HashSet<&str> = dense_map
        .keys()
        .map(String::as_str)
        .chain(lex_map.keys().map(String::as_str))
        .collect();
    let ids: Vec<&str> = all_ids.into_iter().collect();

    // Pass 1: branch-free arithmetic — SIMD-vectorizable.
    let mut scores = score_ids(&ids, &dense_map, &lex_map, k);

    // Pass 2: negation — branch-heavy, kept separate from Pass 1.
    let normalized_negatives: Vec<String> = negative_terms
        .unwrap_or_default()
        .into_iter()
        .map(|t| t.trim().to_lowercase())
        .filter(|t| !t.is_empty())
        .collect();
    let texts = candidate_texts.unwrap_or_default();
    if !normalized_negatives.is_empty() {
        apply_negation_pass(&ids, &mut scores, &normalized_negatives, &texts);
    }

    let mut results: Vec<(String, f64)> = ids
        .into_iter()
        .zip(scores)
        .map(|(id, score)| (id.to_owned(), score))
        .collect();

    // Sort descending by score; tie-break by ID for determinism.
    results.sort_by(|a, b| {
        b.1.partial_cmp(&a.1)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.0.cmp(&b.0))
    });

    results
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_fuse_rrf_basic() {
        let dense = vec!["doc1".to_string(), "doc2".to_string(), "doc3".to_string()];
        let lex = vec!["doc2".to_string(), "doc1".to_string(), "doc4".to_string()];

        let fused = fuse_rrf(dense, lex, 60.0, None, None);
        assert_eq!(fused.len(), 4);

        // doc1: (1/60) + (1/61) = 0.016666 + 0.016393 = 0.033059
        // doc2: (1/61) + (1/60) = 0.033059
        // doc3: (1/62) + (1/1000060) ≈ 0.016129
        // doc4: (1/1000060) + (1/62) ≈ 0.016129
        assert!(fused[0].0 == "doc1" || fused[0].0 == "doc2");
        assert!(fused[1].0 == "doc1" || fused[1].0 == "doc2");
        assert!((fused[0].1 - fused[1].1).abs() < 1e-6);
    }

    #[test]
    fn test_fuse_rrf_negative_penalty() {
        let dense = vec!["doc1".to_string(), "doc2".to_string()];
        let lex = vec!["doc2".to_string(), "doc1".to_string()];

        let mut texts = HashMap::new();
        texts.insert("doc1".to_string(), "Documentation about OAuth2 and auth".to_string());
        texts.insert("doc2".to_string(), "Documentation about API keys".to_string());

        let fused = fuse_rrf(
            dense,
            lex,
            60.0,
            Some(vec!["oauth2".to_string()]),
            Some(texts),
        );

        // doc2 should be ranked higher because doc1 is penalized with lambda = 0.5
        assert_eq!(fused[0].0, "doc2");
        assert_eq!(fused[1].0, "doc1");
        assert!((fused[1].1 - (fused[0].1 * 0.5)).abs() < 1e-6);
    }

    #[test]
    fn test_fuse_rrf_empty() {
        let fused = fuse_rrf(vec![], vec![], 60.0, None, None);
        assert!(fused.is_empty());
    }

    #[test]
    fn test_fuse_rrf_dense_only() {
        let dense = vec!["a".to_string(), "b".to_string()];
        let fused = fuse_rrf(dense, vec![], 60.0, None, None);
        assert_eq!(fused.len(), 2);
        // "a" was ranked 0, "b" ranked 1 — "a" should score higher
        assert_eq!(fused[0].0, "a");
    }

    #[test]
    fn test_fuse_rrf_negation_on_id_fallback() {
        // No candidate_texts provided; negation should fall back to matching on ID.
        let dense = vec!["spam_doc".to_string(), "good_doc".to_string()];
        let lex = vec!["good_doc".to_string(), "spam_doc".to_string()];

        let fused = fuse_rrf(
            dense,
            lex,
            60.0,
            Some(vec!["spam".to_string()]),
            None,
        );

        assert_eq!(fused[0].0, "good_doc");
        assert_eq!(fused[1].0, "spam_doc");
    }
}
