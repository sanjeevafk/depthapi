use regex::Regex;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::Path;
use std::sync::LazyLock;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BrokenLink {
    pub source_file: String,
    pub target: String,
    pub line: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct VaultLintReport {
    pub total_notes: usize,
    pub broken_links: Vec<BrokenLink>,
    pub orphan_nodes: Vec<String>,
    pub cycles: Vec<Vec<String>>,
    pub valid: bool,
}

static WIKILINK_REGEX: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\[\[([^\]\|#]+)(?:#[^\]\|]*)?(?:\|[^\]]*)?\]\]").unwrap()
});

static HEADING_REGEX: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^#\s+(.+)$").unwrap()
});

fn canonical_slug(s: &str) -> String {
    s.trim()
        .to_lowercase()
        .chars()
        .filter(|c| c.is_alphanumeric())
        .collect()
}

#[derive(Debug)]
struct NoteInfo {
    rel_path: String,
    stem: String,
    title: Option<String>,
    lines: Vec<String>,
}

fn collect_markdown_files(dir: &Path, base_dir: &Path, notes: &mut Vec<NoteInfo>) -> Result<(), String> {
    if !dir.exists() {
        return Err(format!("Vault directory does not exist: {}", dir.display()));
    }

    let entries = fs::read_dir(dir).map_err(|e| format!("Failed to read dir {}: {e}", dir.display()))?;

    for entry in entries {
        let entry = entry.map_err(|e| format!("Failed to read entry: {e}"))?;
        let path = entry.path();

        if path.is_dir() {
            // Ignore hidden directories like .git or .obsidian
            if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
                if name.starts_with('.') {
                    continue;
                }
            }
            collect_markdown_files(&path, base_dir, notes)?;
        } else if path.is_file() {
            if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
                if ext.eq_ignore_ascii_case("md") {
                    let rel_path = match path.strip_prefix(base_dir) {
                        Ok(p) => p.to_string_lossy().replace('\\', "/"),
                        Err(_) => path.to_string_lossy().replace('\\', "/"),
                    };
                    let stem = path
                        .file_stem()
                        .and_then(|s| s.to_str())
                        .unwrap_or("")
                        .to_string();

                    let content = fs::read_to_string(&path).unwrap_or_default();
                    let mut title = None;
                    let mut lines = Vec::new();

                    for line in content.lines() {
                        if title.is_none() {
                            if let Some(caps) = HEADING_REGEX.captures(line) {
                                title = Some(caps[1].trim().to_string());
                            }
                        }
                        lines.push(line.to_string());
                    }

                    notes.push(NoteInfo {
                        rel_path,
                        stem,
                        title,
                        lines,
                    });
                }
            }
        }
    }

    Ok(())
}

fn canonicalize_cycle(cycle: &[String]) -> Vec<String> {
    if cycle.is_empty() {
        return Vec::new();
    }
    // Drop the closing duplicate node for rotation
    let nodes = if cycle.len() > 1 && cycle.first() == cycle.last() {
        &cycle[..cycle.len() - 1]
    } else {
        cycle
    };

    if nodes.is_empty() {
        return Vec::new();
    }

    // Find index of lexicographically smallest node
    let mut min_idx = 0;
    for (i, node) in nodes.iter().enumerate() {
        if node < &nodes[min_idx] {
            min_idx = i;
        }
    }

    // Rotate slice
    let mut rotated = Vec::with_capacity(nodes.len() + 1);
    for i in 0..nodes.len() {
        rotated.push(nodes[(min_idx + i) % nodes.len()].clone());
    }
    // Close cycle
    rotated.push(rotated[0].clone());
    rotated
}

pub fn lint_wiki_vault(vault_dir: &str) -> Result<VaultLintReport, String> {
    let base_path = Path::new(vault_dir);
    let mut notes = Vec::new();

    collect_markdown_files(base_path, base_path, &mut notes)?;

    // Sort notes for determinism
    notes.sort_by(|a, b| a.rel_path.cmp(&b.rel_path));

    let total_notes = notes.len();
    if total_notes == 0 {
        return Ok(VaultLintReport {
            total_notes: 0,
            broken_links: Vec::new(),
            orphan_nodes: Vec::new(),
            cycles: Vec::new(),
            valid: true,
        });
    }

    // Map targets to note indices
    // Keys: canonical slug of stem, canonical slug of title, rel_path, rel_path without .md
    let mut target_map: HashMap<String, usize> = HashMap::new();
    let mut note_id_to_idx: HashMap<String, usize> = HashMap::new();

    for (idx, note) in notes.iter().enumerate() {
        note_id_to_idx.insert(note.rel_path.clone(), idx);

        // Map rel_path
        target_map.insert(note.rel_path.to_lowercase(), idx);
        if let Some(stripped) = note.rel_path.strip_suffix(".md") {
            target_map.insert(stripped.to_lowercase(), idx);
            target_map.insert(canonical_slug(stripped), idx);
        }

        // Map stem
        target_map.insert(note.stem.to_lowercase(), idx);
        target_map.insert(canonical_slug(&note.stem), idx);

        // Map title if present
        if let Some(ref title) = note.title {
            target_map.insert(title.to_lowercase(), idx);
            target_map.insert(canonical_slug(title), idx);
        }
    }

    let mut broken_links = Vec::new();
    let mut in_degrees: Vec<usize> = vec![0; total_notes];
    let mut adj_list: Vec<HashSet<usize>> = vec![HashSet::new(); total_notes];

    for (src_idx, note) in notes.iter().enumerate() {
        for (line_idx, line) in note.lines.iter().enumerate() {
            for cap in WIKILINK_REGEX.captures_iter(line) {
                let raw_target = cap[1].trim();
                if raw_target.is_empty() {
                    continue;
                }

                let target_lookup = raw_target.to_lowercase();
                let target_slug = canonical_slug(raw_target);

                let matched_idx = target_map
                    .get(&target_lookup)
                    .or_else(|| target_map.get(&target_slug))
                    .copied();

                match matched_idx {
                    Some(tgt_idx) => {
                        // Avoid counting multiple identical links from the same source file to same target
                        if adj_list[src_idx].insert(tgt_idx) {
                            if src_idx != tgt_idx {
                                in_degrees[tgt_idx] += 1;
                            }
                        }
                    }
                    None => {
                        broken_links.push(BrokenLink {
                            source_file: note.rel_path.clone(),
                            target: raw_target.to_string(),
                            line: line_idx + 1,
                        });
                    }
                }
            }
        }
    }

    // Orphan detection:
    // Notes with in_degree == 0, excluding index.md and log.md
    let mut orphan_nodes = Vec::new();
    for (idx, note) in notes.iter().enumerate() {
        let is_root_file = note.rel_path.eq_ignore_ascii_case("index.md")
            || note.rel_path.eq_ignore_ascii_case("log.md")
            || note.stem.eq_ignore_ascii_case("index")
            || note.stem.eq_ignore_ascii_case("log");

        if !is_root_file && in_degrees[idx] == 0 {
            orphan_nodes.push(note.rel_path.clone());
        }
    }
    orphan_nodes.sort();

    // Cycle detection using DFS:
    // state: 0 = unvisited, 1 = visiting (in recursion stack), 2 = visited
    let mut state: Vec<u8> = vec![0; total_notes];
    let mut stack: Vec<usize> = Vec::new();
    let mut detected_cycles_set: HashSet<Vec<String>> = HashSet::new();

    fn dfs(
        u: usize,
        adj: &[HashSet<usize>],
        state: &mut [u8],
        stack: &mut Vec<usize>,
        notes: &[NoteInfo],
        cycles_set: &mut HashSet<Vec<String>>,
    ) {
        state[u] = 1;
        stack.push(u);

        // Sort out-neighbors for determinism
        let mut neighbors: Vec<usize> = adj[u].iter().copied().collect();
        neighbors.sort();

        for &v in &neighbors {
            if state[v] == 1 {
                // Found a cycle!
                if let Some(pos) = stack.iter().position(|&node| node == v) {
                    let mut cycle_names: Vec<String> = stack[pos..]
                        .iter()
                        .map(|&idx| notes[idx].stem.clone())
                        .collect();
                    cycle_names.push(notes[v].stem.clone());
                    let canonical = canonicalize_cycle(&cycle_names);
                    if !canonical.is_empty() {
                        cycles_set.insert(canonical);
                    }
                }
            } else if state[v] == 0 {
                dfs(v, adj, state, stack, notes, cycles_set);
            }
        }

        stack.pop();
        state[u] = 2;
    }

    for i in 0..total_notes {
        if state[i] == 0 {
            dfs(i, &adj_list, &mut state, &mut stack, &notes, &mut detected_cycles_set);
        }
    }

    let mut cycles: Vec<Vec<String>> = detected_cycles_set.into_iter().collect();
    cycles.sort();

    let valid = broken_links.is_empty() && cycles.is_empty();

    Ok(VaultLintReport {
        total_notes,
        broken_links,
        orphan_nodes,
        cycles,
        valid,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs::File;
    use std::io::Write;
    use std::path::PathBuf;

    struct TempVault {
        path: PathBuf,
    }

    impl TempVault {
        fn new(name: &str) -> Self {
            let path = std::env::temp_dir().join(format!("depth_vault_test_{}_{}", name, std::process::id()));
            let _ = fs::remove_dir_all(&path);
            fs::create_dir_all(&path).unwrap();
            Self { path }
        }

        fn write_file(&self, rel_path: &str, content: &str) {
            let full_path = self.path.join(rel_path);
            if let Some(parent) = full_path.parent() {
                fs::create_dir_all(parent).unwrap();
            }
            let mut f = File::create(full_path).unwrap();
            f.write_all(content.as_bytes()).unwrap();
        }
    }

    impl Drop for TempVault {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    #[test]
    fn test_empty_vault() {
        let vault = TempVault::new("empty");
        let report = lint_wiki_vault(vault.path.to_str().unwrap()).unwrap();
        assert_eq!(report.total_notes, 0);
        assert!(report.valid);
        assert!(report.broken_links.is_empty());
        assert!(report.orphan_nodes.is_empty());
        assert!(report.cycles.is_empty());
    }

    #[test]
    fn test_clean_vault_with_links() {
        let vault = TempVault::new("clean");
        vault.write_file("index.md", "# Index\n\nSee [[concepts/fastapi]] and [[rust]].\n");
        vault.write_file("concepts/fastapi.md", "# FastAPI\n\nHigh performance web framework. Uses [[pydantic]].\n");
        vault.write_file("concepts/pydantic.md", "# Pydantic\n\nData validation for Python.\n");
        vault.write_file("concepts/rust.md", "# Rust\n\nSystems programming language.\n");

        let report = lint_wiki_vault(vault.path.to_str().unwrap()).unwrap();
        assert_eq!(report.total_notes, 4);
        assert!(report.broken_links.is_empty());
        assert!(report.orphan_nodes.is_empty());
        assert!(report.cycles.is_empty());
        assert!(report.valid);
    }

    #[test]
    fn test_broken_wikilink() {
        let vault = TempVault::new("broken");
        vault.write_file("index.md", "# Index\n\nSee [[concepts/unknown]].\n");
        vault.write_file("concepts/known.md", "# Known\n\nLinks to [[ghost_note]].\n");

        let report = lint_wiki_vault(vault.path.to_str().unwrap()).unwrap();
        assert_eq!(report.broken_links.len(), 2);
        assert!(!report.valid);
    }

    #[test]
    fn test_orphan_node_detection() {
        let vault = TempVault::new("orphan");
        vault.write_file("index.md", "# Index\n\nOnly links to [[connected]].\n");
        vault.write_file("concepts/connected.md", "# Connected\n\nLinked from index.\n");
        vault.write_file("concepts/lonely.md", "# Lonely\n\nNobody links to me.\n");

        let report = lint_wiki_vault(vault.path.to_str().unwrap()).unwrap();
        assert_eq!(report.orphan_nodes, vec!["concepts/lonely.md"]);
    }

    #[test]
    fn test_cycle_detection() {
        let vault = TempVault::new("cycle");
        vault.write_file("index.md", "# Index\n\nLinks to [[a]].\n");
        vault.write_file("concepts/a.md", "# Node A\n\nLinks to [[b]].\n");
        vault.write_file("concepts/b.md", "# Node B\n\nLinks back to [[a]].\n");

        let report = lint_wiki_vault(vault.path.to_str().unwrap()).unwrap();
        assert_eq!(report.cycles.len(), 1);
        assert_eq!(report.cycles[0], vec!["a", "b", "a"]);
        assert!(!report.valid);
    }
}
