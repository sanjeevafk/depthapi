use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Concept {
    pub name: String,
    pub concept_type: String,
    pub description: Option<String>,
    pub metadata: HashMap<String, Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ConceptEdge {
    pub source_concept: String,
    pub target_concept: String,
    pub relation_type: String,
    pub weight: f64,
    pub metadata: HashMap<String, Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChunkConceptLink {
    pub chunk_index: usize,
    pub concept_name: String,
    pub confidence: f64,
    pub metadata: HashMap<String, Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ExtractedGraph {
    pub concepts: Vec<Concept>,
    pub edges: Vec<ConceptEdge>,
    pub chunk_links: Vec<ChunkConceptLink>,
}

static WIKILINK_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\[\[([^\]\|]+)(?:\|([^\]]+))?\]\]").unwrap());
static HEADING_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^(#{1,6})\s+(.+)$").unwrap());
static CLEAN_PREFIX_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[0-9\.\-\s]+|^[#*_`\s]+|[#*_`\s]+$").unwrap());

pub fn normalize_concept_name(name: &str) -> String {
    let cleaned = CLEAN_PREFIX_PATTERN.replace_all(name, "").trim().to_string();
    if cleaned.is_empty() {
        name.trim().to_string()
    } else {
        cleaned
    }
}

struct GraphBuilder {
    concepts_by_key: HashMap<String, Concept>,
    edges_set: HashSet<(String, String, String)>,
    edges: Vec<ConceptEdge>,
    chunk_links: Vec<ChunkConceptLink>,
}

impl GraphBuilder {
    fn new() -> Self {
        Self {
            concepts_by_key: HashMap::new(),
            edges_set: HashSet::new(),
            edges: Vec::new(),
            chunk_links: Vec::new(),
        }
    }

    fn add_concept(&mut self, name: &str, c_type: &str, desc: Option<&str>) -> String {
        let norm = normalize_concept_name(name);
        if norm.is_empty() {
            return String::new();
        }
        let key = norm.to_lowercase();
        if !self.concepts_by_key.contains_key(&key) {
            let mut meta = HashMap::new();
            meta.insert("canonical_key".to_string(), Value::String(key.clone()));
            self.concepts_by_key.insert(
                key.clone(),
                Concept {
                    name: norm.clone(),
                    concept_type: c_type.to_string(),
                    description: desc.map(|s| s.to_string()),
                    metadata: meta,
                },
            );
        }
        self.concepts_by_key.get(&key).unwrap().name.clone()
    }

    fn add_edge(&mut self, source: &str, target: &str, rel: &str, weight: f64) {
        let s_norm = self.add_concept(source, "topic", None);
        let t_norm = self.add_concept(target, "topic", None);
        if s_norm.is_empty()
            || t_norm.is_empty()
            || s_norm.to_lowercase() == t_norm.to_lowercase()
        {
            return;
        }

        let edge_key = (
            s_norm.to_lowercase(),
            t_norm.to_lowercase(),
            rel.to_string(),
        );
        if self.edges_set.insert(edge_key) {
            self.edges.push(ConceptEdge {
                source_concept: s_norm,
                target_concept: t_norm,
                relation_type: rel.to_string(),
                weight,
                metadata: HashMap::new(),
            });
        }
    }
}

pub fn extract_concepts_and_edges(
    raw_text: &str,
    chunks: Option<&[Value]>,
    document_title: Option<&str>,
    _user_metadata: Option<&HashMap<String, Value>>,
    known_entities: Option<&[String]>,
) -> ExtractedGraph {
    let mut builder = GraphBuilder::new();

    // 1. Add document root concept if provided
    let mut root_name = String::new();
    if let Some(title) = document_title {
        root_name = builder.add_concept(title, "document", None);
    }

    // 2. Extract heading hierarchy and structural edges
    let mut heading_stack: Vec<(usize, String)> = Vec::new();
    if !root_name.is_empty() {
        heading_stack.push((0, root_name.clone()));
    }

    for line in raw_text.lines() {
        let line_str = line.trim();
        if let Some(captures) = HEADING_PATTERN.captures(line_str) {
            let level = captures.get(1).map_or(1, |m| m.as_str().len());
            let heading_text = captures.get(2).map_or("", |m| m.as_str()).trim();
            let concept_name = builder.add_concept(heading_text, "section", None);

            while let Some(top) = heading_stack.last() {
                if top.0 >= level {
                    heading_stack.pop();
                } else {
                    break;
                }
            }

            if let Some(parent) = heading_stack.last() {
                let p_name = parent.1.clone();
                builder.add_edge(&p_name, &concept_name, "contains", 1.0);
            } else if !root_name.is_empty() && concept_name != root_name {
                builder.add_edge(&root_name, &concept_name, "contains", 1.0);
            }

            heading_stack.push((level, concept_name));
        }

        // Cross-references like [[Target]]
        let current_context = heading_stack
            .last()
            .map(|h| h.1.clone())
            .unwrap_or_else(|| root_name.clone());

        for wiki_match in WIKILINK_PATTERN.captures_iter(line_str) {
            let target_raw = wiki_match.get(1).map_or("", |m| m.as_str()).trim();
            let target_name = builder.add_concept(target_raw, "entity", None);
            if !current_context.is_empty() && !target_name.is_empty() {
                let line_lower = line_str.to_lowercase();
                let ctx_lower = current_context.to_lowercase();
                let is_dependency = line_lower.contains("depend")
                    || line_lower.contains("require")
                    || ctx_lower.contains("depend")
                    || ctx_lower.contains("require");
                let rel = if is_dependency {
                    "depends_on"
                } else {
                    "references"
                };
                builder.add_edge(&current_context, &target_name, rel, 1.0);
            }
        }
    }

    // 3. Match known entity mentions across text
    if let Some(entities) = known_entities {
        let raw_lower = raw_text.to_lowercase();
        let context_concept = heading_stack
            .last()
            .map(|h| h.1.clone())
            .unwrap_or_else(|| root_name.clone());

        for entity in entities {
            let ent_norm = normalize_concept_name(entity);
            if ent_norm.len() < 3 {
                continue;
            }
            if !root_name.is_empty() && ent_norm.to_lowercase() == root_name.to_lowercase() {
                continue;
            }
            if raw_lower.contains(&ent_norm.to_lowercase()) {
                let target_name = builder.add_concept(&ent_norm, "entity", None);
                if !context_concept.is_empty() && !target_name.is_empty() {
                    builder.add_edge(&context_concept, &target_name, "references", 1.0);
                }
            }
        }
    }

    // 4. Map chunks to concepts
    if !builder.concepts_by_key.is_empty() {
        if let Some(chunks_slice) = chunks {
            for (idx, chunk) in chunks_slice.iter().enumerate() {
                let mut linked_for_chunk: HashSet<String> = HashSet::new();

                let c_content = chunk
                    .get("content")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let meta = chunk.get("metadata");

                let hierarchy = meta
                    .and_then(|m| m.get("hierarchy"))
                    .and_then(|h| h.as_array());

                if let Some(h_arr) = hierarchy {
                    if !h_arr.is_empty() {
                        for (h_level, h_item) in h_arr.iter().enumerate() {
                            let h_str = h_item.as_str().unwrap_or("");
                            let c_name = builder.add_concept(h_str, "section", None);
                            if !c_name.is_empty() && linked_for_chunk.insert(c_name.clone()) {
                                let confidence = if h_level == h_arr.len() - 1 { 1.0 } else { 0.8 };
                                builder.chunk_links.push(ChunkConceptLink {
                                    chunk_index: idx,
                                    concept_name: c_name,
                                    confidence,
                                    metadata: HashMap::new(),
                                });
                            }
                        }
                    }
                } else if !root_name.is_empty() {
                    builder.chunk_links.push(ChunkConceptLink {
                        chunk_index: idx,
                        concept_name: root_name.clone(),
                        confidence: 1.0,
                        metadata: HashMap::new(),
                    });
                }

                // Detect inline wikilinks in chunk content
                for wiki_match in WIKILINK_PATTERN.captures_iter(c_content) {
                    let target_raw = wiki_match.get(1).map_or("", |m| m.as_str()).trim();
                    let target_name = builder.add_concept(target_raw, "entity", None);
                    if !target_name.is_empty() && linked_for_chunk.insert(target_name.clone()) {
                        let mut l_meta = HashMap::new();
                        l_meta.insert(
                            "source".to_string(),
                            Value::String("inline_wikilink".to_string()),
                        );
                        builder.chunk_links.push(ChunkConceptLink {
                            chunk_index: idx,
                            concept_name: target_name,
                            confidence: 0.7,
                            metadata: l_meta,
                        });
                    }
                }

                // Detect mentions of known entities in chunk content
                if let Some(entities) = known_entities {
                    let c_lower = c_content.to_lowercase();
                    for entity in entities {
                        let ent_norm = normalize_concept_name(entity);
                        if ent_norm.len() < 3 {
                            continue;
                        }
                        if !root_name.is_empty() && ent_norm.to_lowercase() == root_name.to_lowercase() {
                            continue;
                        }
                        let ent_lower = ent_norm.to_lowercase();
                        if !linked_for_chunk.contains(&ent_lower) && c_lower.contains(&ent_lower) {
                            let target_name = builder.add_concept(&ent_norm, "entity", None);
                            if !target_name.is_empty() {
                                linked_for_chunk.insert(ent_lower);
                                let mut l_meta = HashMap::new();
                                l_meta.insert(
                                    "source".to_string(),
                                    Value::String("entity_mention".to_string()),
                                );
                                builder.chunk_links.push(ChunkConceptLink {
                                    chunk_index: idx,
                                    concept_name: target_name,
                                    confidence: 0.75,
                                    metadata: l_meta,
                                });
                            }
                        }
                    }
                }
            }
        }
    }

    ExtractedGraph {
        concepts: builder.concepts_by_key.into_values().collect(),
        edges: builder.edges,
        chunk_links: builder.chunk_links,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_concepts_basic() {
        let md = "# System Architecture\n\n## Database\n\nDepends on [[PostgreSQL]].\n";
        let graph = extract_concepts_and_edges(md, None, Some("Core Architecture"), None, None);

        assert!(!graph.concepts.is_empty());
        assert!(graph.concepts.iter().any(|c| c.name == "System Architecture"));
        assert!(graph.concepts.iter().any(|c| c.name == "PostgreSQL"));

        assert!(graph.edges.iter().any(|e| e.relation_type == "depends_on" && e.target_concept == "PostgreSQL"));
    }
}
