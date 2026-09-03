use regex::RegexSetBuilder;
use std::sync::LazyLock;

/// Compiled RegexSet DFA automaton for sub-microsecond query intent classification.
static RELATIONAL_PATTERNS: LazyLock<regex::RegexSet> = LazyLock::new(|| {
    let patterns = [
        r"\b(depend(s|ency|encies|ent)?|depends\s+on)\b",
        r"\b(lineage|provenance|origin(s)?)\b",
        r"\b(connect(ed|ion|ions|s)?|link(ed|s)?|relations?(hip|hips)?)\b",
        r"\b(upstream|downstream|caller(s)?|callee(s)?)\b",
        r"\b(impact(s|ed)?|blast\s+radius|break(s)?\s+if)\b",
        r"\b(hierarch(y|ical)|parent|child(ren)?|subclass(es)?|superclass(es)?)\b",
        r"\b(architect(ure)?|flow|pipeline|interact(s|ion|ions)?)\b",
        r"\bhow\s+does\s+.+\s+(relate|connect|interact|affect|interface)",
        r"\b(what|who|which)\s+(uses|calls|imports|requires|references)\b",
        r"\b(between\s+.+\s+and\s+.+)\b",
        r"\b(trace|path\s+between|graph\s+of)\b",
    ];
    RegexSetBuilder::new(patterns)
        .case_insensitive(true)
        .build()
        .expect("Failed to build relational query RegexSet")
});

/// Deterministically detects if a query benefits from graph traversal.
///
/// Returns:
///   1 if relational, dependency, or structural intent is detected.
///   0 for standard factual or entity lookup queries (to prevent topic drift).
pub fn detect_graph_hops(query: &str) -> u32 {
    let clean = query.trim();
    if clean.is_empty() {
        return 0;
    }
    if RELATIONAL_PATTERNS.is_match(clean) {
        1
    } else {
        0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_factual_query() {
        assert_eq!(detect_graph_hops("What is Python?"), 0);
        assert_eq!(detect_graph_hops("How to install Rust"), 0);
        assert_eq!(detect_graph_hops(""), 0);
        assert_eq!(detect_graph_hops("   "), 0);
    }

    #[test]
    fn test_relational_queries() {
        assert_eq!(detect_graph_hops("What dependencies does Auth0 have?"), 1);
        assert_eq!(detect_graph_hops("Show the lineage of this dataset"), 1);
        assert_eq!(detect_graph_hops("How does the ingest pipeline interact with postgres?"), 1);
        assert_eq!(detect_graph_hops("What breaks if I change the schema?"), 1);
        assert_eq!(detect_graph_hops("What uses this function?"), 1);
        assert_eq!(detect_graph_hops("Trace the connection between A and B"), 1);
    }
}
