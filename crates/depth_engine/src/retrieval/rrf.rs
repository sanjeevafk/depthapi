use std::collections::{HashMap, HashSet};

/// High-throughput Reciprocal Rank Fusion (RRF) with optional Mosaic Negative Query Algebra.
///
/// Merges dense and lexical candidate rankings into a unified score:
/// RRF_score = (1.0 / (k + rank_dense)) + (1.0 / (k + rank_lexical))
///
/// If `negative_terms` are provided and match candidate text or IDs,
/// Mosaic soft penalty (lambda = 0.5) downweights the score.
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

    let mut all_ids: HashSet<String> = HashSet::with_capacity(dense_map.len() + lex_map.len());
    for key in dense_map.keys() {
        all_ids.insert(key.clone());
    }
    for key in lex_map.keys() {
        all_ids.insert(key.clone());
    }

    let normalized_negatives: Vec<String> = negative_terms
        .unwrap_or_default()
        .into_iter()
        .map(|t| t.trim().to_lowercase())
        .filter(|t| !t.is_empty())
        .collect();

    let texts = candidate_texts.unwrap_or_default();

    let mut results: Vec<(String, f64)> = Vec::with_capacity(all_ids.len());

    for id in all_ids {
        let v_rank = dense_map.get(&id).copied().unwrap_or(1_000_000) as f64;
        let b_rank = lex_map.get(&id).copied().unwrap_or(1_000_000) as f64;

        let mut score = (1.0 / (k + v_rank)) + (1.0 / (k + b_rank));

        // Mosaic Negative Query Algebra: soft downweighting penalty (lambda = 0.5)
        if !normalized_negatives.is_empty() {
            let mut is_negated = false;
            if let Some(text) = texts.get(&id) {
                let text_lower = text.to_lowercase();
                for neg in &normalized_negatives {
                    if text_lower.contains(neg) {
                        is_negated = true;
                        break;
                    }
                }
            } else {
                let id_lower = id.to_lowercase();
                for neg in &normalized_negatives {
                    if id_lower.contains(neg) {
                        is_negated = true;
                        break;
                    }
                }
            }

            if is_negated {
                score *= 0.5; // Soft penalty lambda = 0.5
            }
        }

        results.push((id, score));
    }

    // Sort descending by score, tie-break by ID for determinism
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
        // doc3: (1/62) + (1/1000060) = ~0.016129
        // doc4: (1/1000060) + (1/62) = ~0.016129
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
}
