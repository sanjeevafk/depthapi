use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ConfidenceEvaluation {
    pub confidence: String,
    pub is_insufficient: bool,
    pub max_score: Option<f64>,
}

/// Evaluates retrieval confidence (CRAG - Corrective RAG gating).
///
/// Thresholds:
/// - Empty candidates: "insufficient" (triggers refusal / fallback)
/// - Reranked candidates:
///     max_score < -2.0 -> "low"
///     max_score < 0.0  -> "medium"
///     max_score >= 0.0 -> "high"
/// - Dense/Sparse candidates:
///     max_score < 0.012 -> "low"
///     max_score < 0.020 -> "medium"
///     max_score >= 0.020 -> "high"
pub fn evaluate_confidence(scores: &[f64], is_reranked: bool) -> ConfidenceEvaluation {
    if scores.is_empty() {
        return ConfidenceEvaluation {
            confidence: "insufficient".to_string(),
            is_insufficient: true,
            max_score: None,
        };
    }

    let max_score = scores.iter().cloned().fold(f64::NEG_INFINITY, f64::max);

    let tier = if is_reranked {
        if max_score < -2.0 {
            "low"
        } else if max_score < 0.0 {
            "medium"
        } else {
            "high"
        }
    } else if max_score < 0.012 {
        "low"
    } else if max_score < 0.020 {
        "medium"
    } else {
        "high"
    };

    ConfidenceEvaluation {
        confidence: tier.to_string(),
        is_insufficient: false,
        max_score: Some(max_score),
    }
}

/// Helper to evaluate confidence directly from a vector of context dictionaries.
pub fn evaluate_contexts_confidence(contexts: &[serde_json::Value]) -> ConfidenceEvaluation {
    if contexts.is_empty() {
        return ConfidenceEvaluation {
            confidence: "insufficient".to_string(),
            is_insufficient: true,
            max_score: None,
        };
    }

    let has_rerank = contexts.iter().any(|c| c.get("rerank_score").is_some());

    if has_rerank {
        let scores: Vec<f64> = contexts
            .iter()
            .filter_map(|c| c.get("rerank_score").and_then(|v| v.as_f64()))
            .collect();
        evaluate_confidence(&scores, true)
    } else {
        let scores: Vec<f64> = contexts
            .iter()
            .filter_map(|c| c.get("score").and_then(|v| v.as_f64()))
            .collect();
        evaluate_confidence(&scores, false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_crag_empty() {
        let res = evaluate_confidence(&[], false);
        assert_eq!(res.confidence, "insufficient");
        assert!(res.is_insufficient);
        assert_eq!(res.max_score, None);
    }

    #[test]
    fn test_crag_reranked() {
        let high = evaluate_confidence(&[2.5, 1.2], true);
        assert_eq!(high.confidence, "high");
        assert!(!high.is_insufficient);
        assert_eq!(high.max_score, Some(2.5));

        let medium = evaluate_confidence(&[-0.5, -1.0], true);
        assert_eq!(medium.confidence, "medium");

        let low = evaluate_confidence(&[-3.5, -4.0], true);
        assert_eq!(low.confidence, "low");
    }

    #[test]
    fn test_crag_unreranked() {
        let high = evaluate_confidence(&[0.025, 0.015], false);
        assert_eq!(high.confidence, "high");

        let medium = evaluate_confidence(&[0.018, 0.010], false);
        assert_eq!(medium.confidence, "medium");

        let low = evaluate_confidence(&[0.005, 0.002], false);
        assert_eq!(low.confidence, "low");
    }
}
