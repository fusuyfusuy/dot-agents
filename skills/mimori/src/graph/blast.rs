use crate::graph::SymbolGraph;
use crate::model::Coordinate;
use crate::model::Symbol;
use anyhow::{bail, Result};
use serde::{Deserialize, Serialize};
use std::collections::{HashSet, VecDeque};
use std::ffi::OsStr;
use std::path::{Component, Path};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BlastNode {
    pub name: String,
    pub kind: String,
    pub file: String,
    pub coordinate: String,
    pub depth: usize,
    pub is_entry_point: bool,
    pub is_test: bool,
    /// Set for downstream traversals when the node calls nothing tracked.
    /// Upstream traversals leave it false and use `is_entry_point` instead.
    #[serde(default)]
    pub is_sink: bool,
    /// Weakest tier: reached via a non-call mention (property read, type
    /// ref, arg/template identifier), not a call edge. Never traversed
    /// further, never an entry point.
    #[serde(default)]
    pub is_value_use: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BlastResult {
    pub target: String,
    pub depth_limit: usize,
    pub affected: Vec<BlastNode>,
    pub entry_points: Vec<String>,
    pub test_suites: Vec<String>,
    /// `up` (callers) or `down` (callees). Defaults to `up` so cached JSON
    /// without the key still loads.
    #[serde(default = "default_blast_direction")]
    pub direction: String,
    /// Direct mentioners of the target (upstream only): value uses that no
    /// call edge reaches. Call edge wins on overlap — anything already in
    /// `affected` is excluded here.
    #[serde(default)]
    pub value_uses: Vec<BlastNode>,
}

fn default_blast_direction() -> String {
    "up".to_string()
}

impl BlastResult {
    pub fn to_markdown(&self) -> String {
        let mut out = String::new();
        let dir_label = if self.direction == "down" {
            "Downstream"
        } else {
            "Upstream"
        };
        out.push_str(&format!(
            "# Blast Radius ({dir_label}): `{}` (Depth: {}, Affected: {})\n\n",
            self.target,
            self.depth_limit,
            self.affected.len()
        ));
        out.push_str(&format!("{}\n\n", super::COUNT_LEGEND));
        if self.affected.is_empty() && self.value_uses.is_empty() {
            if self.direction == "down" {
                out.push_str("No downstream callees reached. This symbol is a leaf or calls nothing tracked.\n");
            } else {
                out.push_str(
                    "No upstream callers affected. This symbol is an isolated root or leaf.\n",
                );
            }
            return out;
        }

        if !self.entry_points.is_empty() {
            if self.direction == "down" {
                out.push_str("### 🛑 Boundary Sinks\n");
            } else {
                out.push_str("### 🚪 Affected Entry Points\n");
            }
            for ep in &self.entry_points {
                out.push_str(&format!("- 🎯 `{}`\n", ep));
            }
            out.push('\n');
        }

        if !self.test_suites.is_empty() {
            out.push_str("### 🧪 Affected Test Suites\n");
            for ts in &self.test_suites {
                out.push_str(&format!("- 🧪 `{}`\n", ts));
            }
            out.push('\n');
        }

        if !self.affected.is_empty() {
            out.push_str("### 🌊 Transitive Call Tree\n");
            for node in &self.affected {
                let indent = "  ".repeat(node.depth);
                let tag = if node.is_entry_point {
                    " [Entry Point]"
                } else if node.is_sink {
                    " [Sink]"
                } else if node.is_test {
                    " [Test]"
                } else {
                    ""
                };
                out.push_str(&format!(
                    "{}- (d={}) **`{}`** ({}) → `{}`{}\n",
                    indent, node.depth, node.name, node.kind, node.coordinate, tag
                ));
            }
        }
        out.push('\n');

        if !self.value_uses.is_empty() {
            out.push_str("### 📎 Value Uses (mentions — weakest tier, not traversed)\n");
            for node in &self.value_uses {
                out.push_str(&format!(
                    "- **`{}`** ({}) → `{}` [Value Use]\n",
                    node.name, node.kind, node.coordinate
                ));
            }
            out.push('\n');
        }

        out
    }
}

pub fn calculate_blast_radius(
    graph: &SymbolGraph,
    coord: &Coordinate,
    depth_limit: usize,
) -> Result<BlastResult> {
    traverse(graph, coord, depth_limit, Traversal::Upstream)
}

/// Transitive downstream impact: everything `target` calls, directly or
/// transitively, up to `depth_limit`. Answers "is this safe to delete / what
/// does this pull in?" — the mirror of `calculate_blast_radius`.
///
/// `entry_points` here reports reached *sinks* (symbols with no tracked
/// callees) so callers can still eyeball the boundary of the cone.
pub fn calculate_downstream_blast(
    graph: &SymbolGraph,
    coord: &Coordinate,
    depth_limit: usize,
) -> Result<BlastResult> {
    traverse(graph, coord, depth_limit, Traversal::Downstream)
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Traversal {
    Upstream,
    Downstream,
}

fn traverse(
    graph: &SymbolGraph,
    coord: &Coordinate,
    depth_limit: usize,
    direction: Traversal,
) -> Result<BlastResult> {
    // P0: upstream blasts on a class fold in its constructor's callers, so
    // `new X(...)` sites seed the transitive cone.
    let indices = match direction {
        Traversal::Upstream => graph.resolve_upstream_targets(coord),
        Traversal::Downstream => graph.resolve_all(coord),
    };
    if indices.is_empty() {
        bail!("Target symbol '{}' not found in workspace.", coord);
    }

    let mut queue = VecDeque::new();
    let mut visited = HashSet::new();

    for &idx in &indices {
        visited.insert(idx);
        queue.push_back((idx, 0));
    }

    let mut affected = Vec::new();
    let mut entry_points = Vec::new();
    let mut test_suites = Vec::new();

    while let Some((curr_idx, curr_depth)) = queue.pop_front() {
        if curr_depth >= depth_limit {
            continue;
        }

        let neighbors = match direction {
            Traversal::Upstream => graph.callers_map.get(&curr_idx),
            Traversal::Downstream => graph.callees_map.get(&curr_idx),
        };

        if let Some(next) = neighbors {
            for &next_idx in next {
                if !visited.contains(&next_idx) {
                    visited.insert(next_idx);
                    let sym = &graph.symbols[next_idx];
                    let next_depth = curr_depth + 1;

                    let (is_entry_point, is_sink) = match direction {
                        Traversal::Upstream => (is_entry_point(graph, next_idx, sym), false),
                        Traversal::Downstream => (false, is_sink(graph, next_idx)),
                    };
                    let is_test = is_test_symbol(sym);

                    if (is_entry_point || is_sink) && !entry_points.contains(&sym.coordinate()) {
                        entry_points.push(sym.coordinate());
                    }
                    if is_test && !test_suites.contains(&sym.file) {
                        test_suites.push(sym.file.clone());
                    }

                    affected.push(BlastNode {
                        name: sym.name.clone(),
                        kind: sym.kind.as_str().to_string(),
                        file: sym.file.clone(),
                        coordinate: sym.coordinate(),
                        depth: next_depth,
                        is_entry_point,
                        is_test,
                        is_sink,
                        is_value_use: false,
                    });

                    queue.push_back((next_idx, next_depth));
                }
            }
        }
    }

    // Weakest tier (upstream only): direct mentioners of the seed targets.
    // Never traversed, never entry points; anything the call closure
    // already reached stays a call row (call wins).
    let mut value_uses = Vec::new();
    if direction == Traversal::Upstream {
        for &seed in &indices {
            for m_idx in graph.mentioner_indices(seed) {
                if visited.contains(&m_idx) {
                    continue;
                }
                visited.insert(m_idx);
                let sym = &graph.symbols[m_idx];
                if value_uses
                    .iter()
                    .any(|n: &BlastNode| n.coordinate == sym.coordinate())
                {
                    continue;
                }
                value_uses.push(BlastNode {
                    name: sym.name.clone(),
                    kind: sym.kind.as_str().to_string(),
                    file: sym.file.clone(),
                    coordinate: sym.coordinate(),
                    depth: 1,
                    is_entry_point: false,
                    is_test: is_test_symbol(sym),
                    is_sink: false,
                    is_value_use: true,
                });
            }
        }
    }

    Ok(BlastResult {
        target: coord.to_string(),
        depth_limit,
        affected,
        entry_points,
        test_suites,
        direction: match direction {
            Traversal::Upstream => "up".to_string(),
            Traversal::Downstream => "down".to_string(),
        },
        value_uses,
    })
}

/// An entry point is a symbol nothing else calls -- which is what the graph
/// already knows -- or a program entry by name.
///
/// The previous rule matched any name starting with `get_`, `post_` or
/// `handle_`, and any file whose path contained `main.`, `index.` or `app.`,
/// so every getter in the codebase was reported as an affected entry point.
pub(crate) fn is_entry_point(graph: &SymbolGraph, idx: usize, sym: &Symbol) -> bool {
    if sym.name == "main" || sym.name.ends_with("::main") {
        return true;
    }
    graph
        .callers_map
        .get(&idx)
        .is_none_or(|callers| callers.is_empty())
}

/// A program entry by name (`main`). Narrower than `is_entry_point`, which
/// treats every uncalled symbol as an entry — that would exclude the exact
/// isolated symbols doctor wants to flag.
pub(crate) fn is_program_entry(sym: &Symbol) -> bool {
    sym.name == "main" || sym.name.ends_with("::main")
}

/// A sink is a symbol that calls nothing tracked — the downstream mirror of
/// an entry point. Used to mark the boundary of a downstream blast cone.
pub(crate) fn is_sink(graph: &SymbolGraph, idx: usize) -> bool {
    graph
        .callees_map
        .get(&idx)
        .is_none_or(|callees| callees.is_empty())
}

/// Test detection on real conventions, matched against path components and
/// filename suffixes.
///
/// The previous rule was `name.contains("test") || file.contains("test")`,
/// which classified `latest`, `contest` and `attestation` as tests.
pub(crate) fn is_test_symbol(sym: &Symbol) -> bool {
    const TEST_DIRS: &[&str] = &["tests", "test", "__tests__", "spec", "__mocks__"];
    const TEST_SUFFIXES: &[&str] = &[
        "_test.go",
        "_test.py",
        ".test.ts",
        ".test.tsx",
        ".test.js",
        ".test.jsx",
        ".spec.ts",
        ".spec.tsx",
        ".spec.js",
        ".spec.jsx",
    ];

    let path = Path::new(&sym.file);

    if path
        .components()
        .any(|c| matches!(c, Component::Normal(n) if TEST_DIRS.iter().any(|d| OsStr::new(d) == n)))
    {
        return true;
    }

    let name = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or_default();

    if TEST_SUFFIXES.iter().any(|sfx| name.ends_with(sfx))
        || name.starts_with("test_")
        || name.starts_with("Test")
    {
        return true;
    }

    let sym_leaf = sym.name.rsplit("::").next().unwrap_or(&sym.name);
    sym_leaf.starts_with("test_") || sym_leaf.starts_with("Test")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::SymbolKind;

    fn s(file: &str, name: &str) -> Symbol {
        Symbol {
            name: name.into(),
            kind: SymbolKind::Function,
            file: file.into(),
            start_line: 1,
            end_line: 1,
            signature: String::new(),
            body: String::new(),
            centrality: 0.0,
            calls: vec![],
            mentions: vec![],
            call_counts: Default::default(),
            member_calls: vec![],
            external_imports: vec![],
        }
    }

    fn sr(file: &str, name: &str, refs: &[&str]) -> Symbol {
        let mut sym = s(file, name);
        sym.calls = refs.iter().map(|r| r.to_string()).collect();
        for r in refs {
            sym.call_counts.insert(r.to_string(), 1);
        }
        sym
    }

    #[test]
    fn downstream_blast_follows_callees_transitively() {
        use crate::graph::SymbolGraph;
        use crate::model::Coordinate;
        let g = SymbolGraph::new(vec![
            sr("src/a.rs", "top", &["mid"]),
            sr("src/a.rs", "mid", &["leaf"]),
            s("src/a.rs", "leaf"),
        ]);
        let coord = Coordinate::parse("src/a.rs:top").unwrap();
        let res = calculate_downstream_blast(&g, &coord, 3).unwrap();
        assert_eq!(res.direction, "down");
        let names: Vec<&str> = res.affected.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"mid"), "expected mid in {:?}", names);
        assert!(names.contains(&"leaf"), "expected leaf in {:?}", names);
        // Upstream from the leaf mirrors back up.
        let coord = Coordinate::parse("src/a.rs:leaf").unwrap();
        let up = calculate_blast_radius(&g, &coord, 3).unwrap();
        assert_eq!(up.direction, "up");
        let names: Vec<&str> = up.affected.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"mid"), "expected mid in {:?}", names);
    }

    #[test]
    fn test_detection_uses_conventions_not_substrings() {
        assert!(is_test_symbol(&s("tests/cli_map.rs", "anything")));
        assert!(is_test_symbol(&s("src/__tests__/a.ts", "anything")));
        assert!(is_test_symbol(&s("pkg/server_test.go", "TestServe")));
        assert!(is_test_symbol(&s("src/a.spec.ts", "anything")));
        assert!(is_test_symbol(&s("src/lib.rs", "test_parses_input")));

        // Regression M13: these are not tests.
        assert!(!is_test_symbol(&s("src/latest.rs", "latest_version")));
        assert!(!is_test_symbol(&s("src/contest.rs", "run_contest")));
        assert!(!is_test_symbol(&s("src/auth.rs", "attestation")));
        assert!(!is_test_symbol(&s("src/protest/mod.rs", "handler")));
    }
}
