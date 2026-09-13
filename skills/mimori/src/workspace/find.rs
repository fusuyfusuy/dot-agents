use crate::storage::get_or_sync_graph;
use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::path::Path;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FindMatch {
    pub name: String,
    pub kind: String,
    pub file: String,
    pub coordinate: String,
    pub start_line: usize,
    pub end_line: usize,
    pub signature: String,
    pub centrality: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FindResult {
    pub query: String,
    pub matches: Vec<FindMatch>,
}

impl FindResult {
    pub fn to_markdown(&self) -> String {
        let mut out = String::new();
        let is_literal_fallback = self.matches.iter().any(|m| m.kind == "literal");
        if is_literal_fallback {
            out.push_str(&format!(
                "### Find: `{}` ({} literal matches) [fallback]\n\n",
                self.query,
                self.matches.len()
            ));
        } else {
            out.push_str(&format!(
                "### Find: `{}` ({} matches)\n\n",
                self.query,
                self.matches.len()
            ));
        }

        if self.matches.is_empty() {
            out.push_str("No matches found.\n");
            return out;
        }

        for m in &self.matches {
            if m.kind == "file" {
                out.push_str(&format!("- 📄 **`{}`** (file)\n", m.file));
            } else if m.kind == "literal" {
                out.push_str(&format!(
                    "- 🔍 **`{}`** (literal) → `{}` (L{})\n",
                    m.signature, m.file, m.start_line
                ));
            } else {
                out.push_str(&format!(
                    "- 🔹 **`{}`** ({}) [rank: {:.4}] → `{}` (L{}-L{})\n",
                    m.name, m.kind, m.centrality, m.coordinate, m.start_line, m.end_line
                ));
                if !m.signature.is_empty() {
                    out.push_str(&format!("    `{}`\n", m.signature));
                }
            }
        }

        out
    }
}

pub fn execute_find(
    root: &Path,
    query: &str,
    symbols_only: bool,
    files_only: bool,
) -> Result<FindResult> {
    let graph = get_or_sync_graph(root)?;
    let q_lower = query.to_lowercase();
    let mut matches = Vec::new();

    if !symbols_only {
        let mut seen_files = HashSet::new();
        for s in &graph.symbols {
            if !seen_files.contains(&s.file) {
                seen_files.insert(s.file.clone());
                if s.file.to_lowercase().contains(&q_lower) {
                    matches.push(FindMatch {
                        name: Path::new(&s.file)
                            .file_name()
                            .map(|n| n.to_string_lossy().to_string())
                            .unwrap_or_default(),
                        kind: "file".to_string(),
                        file: s.file.clone(),
                        coordinate: s.file.clone(),
                        start_line: 1,
                        end_line: 1,
                        signature: String::new(),
                        centrality: 0.0,
                    });
                }
            }
        }
    }

    if !files_only {
        for s in &graph.symbols {
            if s.name.to_lowercase().contains(&q_lower) {
                matches.push(FindMatch {
                    name: s.name.clone(),
                    kind: s.kind.as_str().to_string(),
                    file: s.file.clone(),
                    coordinate: s.coordinate(),
                    start_line: s.start_line,
                    end_line: s.end_line,
                    signature: s.signature.clone(),
                    centrality: s.centrality,
                });
            }
        }
    }

    // Hybrid fallback: when zero symbols and files match, scan workspace files
    // line by line, emitting one hit per matching line (P0b: `rg -c` parity).
    // The previous per-symbol scan emitted one hit per symbol body, so N
    // occurrences inside one function counted as 1, and code outside any
    // symbol body was invisible entirely.
    if matches.is_empty() {
        let mut files: Vec<&str> = graph
            .symbols
            .iter()
            .map(|s| s.file.as_str())
            .collect();
        files.sort_unstable();
        files.dedup();
        for rel in files {
            let Ok(content) = std::fs::read_to_string(root.join(rel)) else {
                continue;
            };
            for (idx, line) in content.lines().enumerate() {
                if line.to_lowercase().contains(&q_lower) {
                    let line_no = idx + 1;
                    matches.push(FindMatch {
                        name: format!("{} (literal match)", rel),
                        kind: "literal".to_string(),
                        file: rel.to_string(),
                        coordinate: format!("{}:#L{}", rel, line_no),
                        start_line: line_no,
                        end_line: line_no,
                        signature: line.trim().to_string(),
                        centrality: 0.0,
                    });
                }
            }
        }
    }

    matches.sort_by(|a, b| {
        let a_exact = a.name.to_lowercase() == q_lower;
        let b_exact = b.name.to_lowercase() == q_lower;
        b_exact
            .cmp(&a_exact)
            .then_with(|| {
                b.centrality
                    .partial_cmp(&a.centrality)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .then_with(|| a.name.len().cmp(&b.name.len()))
    });

    Ok(FindResult {
        query: query.to_string(),
        matches,
    })
}
