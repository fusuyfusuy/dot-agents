use crate::graph::blast::{is_program_entry, is_test_symbol};
use crate::graph::SymbolGraph;
use crate::model::SymbolKind;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeadSymbol {
    pub name: String,
    pub kind: String,
    pub file: String,
    pub coordinate: String,
    pub centrality: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HubSymbol {
    pub name: String,
    pub kind: String,
    pub coordinate: String,
    pub fan_in: usize,
    pub centrality: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DoctorResult {
    pub files: usize,
    pub symbols: usize,
    pub edges: usize,
    pub isolated: usize,
    /// Isolated free functions/methods: the strongest delete candidates.
    pub likely_dead: Vec<DeadSymbol>,
    /// Isolated constants, types, traits and friends: often exported API
    /// surface, so they need human eyes before any deletion.
    pub needs_review: Vec<DeadSymbol>,
    pub top_hubs: Vec<HubSymbol>,
    /// Edge-resolution accounting (P0-2).
    #[serde(default)]
    pub resolved: usize,
    #[serde(default)]
    pub ambiguous_dropped: usize,
    #[serde(default)]
    pub unresolved: usize,
    /// Calls skipped as external imports.
    #[serde(default)]
    pub external: usize,
    /// PageRank convergence (P1-1).
    #[serde(default)]
    pub pagerank_iters: usize,
    #[serde(default)]
    pub pagerank_converged: bool,
}

impl DoctorResult {
    pub fn to_markdown(&self) -> String {
        let mut out = String::new();
        out.push_str("# Doctor: repository health\n\n");
        out.push_str(&format!(
            "- Files: {}\n- Symbols: {}\n- Edges: {}\n- Isolated (no callers, no callees): {}\n- Resolution: {} resolved, {} ambiguous-dropped, {} unresolved, {} external\n- PageRank: {} iters, {}\n\n",
            self.files,
            self.symbols,
            self.edges,
            self.isolated,
            self.resolved,
            self.ambiguous_dropped,
            self.unresolved,
            self.external,
            self.pagerank_iters,
            if self.pagerank_converged {
                "converged"
            } else {
                "NOT converged"
            }
        ));

        if self.top_hubs.is_empty() {
            out.push_str("No hubs found.\n");
        } else {
            out.push_str("## 🔝 Top hubs by fan-in\n");
            for h in &self.top_hubs {
                out.push_str(&format!(
                    "- **`{}`** ({}) fan-in {} rank {:.4} → `{}`\n",
                    h.name, h.kind, h.fan_in, h.centrality, h.coordinate
                ));
            }
            out.push('\n');
        }

        if self.likely_dead.is_empty() && self.needs_review.is_empty() {
            out.push_str("No dead-weight candidates. Nothing is both uncalled and unconnected.\n");
        } else {
            out.push_str(&format!(
                "## 🍂 Dead-weight candidates ({} likely, {} to review)\n",
                self.likely_dead.len(),
                self.needs_review.len()
            ));
            out.push_str(
                "_Uncalled, unconnected, not `main` and not a test. Low confidence — dynamic entry points and runtime registration produce false positives._\n",
            );
            if !self.likely_dead.is_empty() {
                out.push_str("\n### Likely dead (isolated functions)\n");
                for d in &self.likely_dead {
                    out.push_str(&format!(
                        "- **`{}`** ({}) rank {:.4} → `{}`\n",
                        d.name, d.kind, d.centrality, d.coordinate
                    ));
                }
            }
            if !self.needs_review.is_empty() {
                out.push_str("\n### Needs human review (isolated types/constants)\n");
                for d in &self.needs_review {
                    out.push_str(&format!(
                        "- **`{}`** ({}) rank {:.4} → `{}`\n",
                        d.name, d.kind, d.centrality, d.coordinate
                    ));
                }
            }
        }

        out
    }
}

pub fn run_doctor(graph: &SymbolGraph, limit: Option<usize>) -> DoctorResult {
    let files: HashSet<&str> = graph.symbols.iter().map(|s| s.file.as_str()).collect();
    let edges: usize = graph.callees_map.values().map(|v| v.len()).sum();

    let mut isolated = 0usize;
    let mut likely_dead = Vec::new();
    let mut needs_review = Vec::new();
    for (idx, sym) in graph.symbols.iter().enumerate() {
        let has_callers = graph.callers_map.get(&idx).is_some_and(|v| !v.is_empty());
        let has_callees = graph.callees_map.get(&idx).is_some_and(|v| !v.is_empty());
        if !has_callers && !has_callees {
            isolated += 1;
        }
         // Dead-weight = isolated, not a `main` program entry, not a test,
        // and not a framework-dispatched entry (P1-3): tRPC router
        // procedures, Next.js page/route/layout/middleware/server
        // conventions, and index-barrel re-exports are reachable without a
        // tracked call edge, so flagging them trains users to ignore the list.
        // Note: deliberately NOT using `is_entry_point` here — that treats
        // every uncalled symbol as an entry, which would exclude exactly the
        // isolated symbols we want to flag.
        if !has_callers
            && !has_callees
            && !is_program_entry(sym)
            && !is_test_symbol(sym)
            && !is_framework_entry(sym)
        {
            let dead = DeadSymbol {
                name: sym.name.clone(),
                kind: sym.kind.as_str().to_string(),
                file: sym.file.clone(),
                coordinate: sym.coordinate(),
                centrality: sym.centrality,
            };
            // Free functions/methods are the strongest delete candidates;
            // types, traits and constants are often exported API surface.
            match sym.kind {
                SymbolKind::Function | SymbolKind::Method => likely_dead.push(dead),
                _ => needs_review.push(dead),
            }
        }
    }

/// Framework-dispatched entries: reachable without a tracked call edge.
/// tRPC procedures (`appRouter::*` / `router(...)` objects), Next.js
/// `page|route|layout|middleware|server` files, and symbols re-exported
/// through an index barrel.
pub(crate) fn is_framework_entry(sym: &crate::model::Symbol) -> bool {
    let file = sym.file.replace('\\', "/");
    let stem = file
        .rsplit('/')
        .next()
        .unwrap_or(&file)
        .rsplit('.')
        .nth(1)
        .unwrap_or("");
    if matches!(
        stem,
        "page" | "route" | "layout" | "loading" | "error" | "middleware" | "server"
    ) {
        return true;
    }
    // tRPC-style router objects: `appRouter`, `*Router`, or `router`.
    let leaf = sym.name.rsplit("::").next().unwrap_or(&sym.name);
    let leaf = leaf.rsplit('.').next().unwrap_or(leaf);
    if leaf == "appRouter" || leaf == "router" || leaf.ends_with("Router") {
        return true;
    }
    // Procedures registered on a router object read as `appRouter::name`.
    if sym.name.starts_with("appRouter::") || sym.name.starts_with("appRouter.") {
        return true;
    }
    // Index-barrel re-exports: anything defined in an index file is public
    // surface by construction.
    if stem == "index" || stem == "mod" {
        return true;
    }
    false
}

    let mut by_centrality = |a: &DeadSymbol, b: &DeadSymbol| {
        a.centrality
            .partial_cmp(&b.centrality)
            .unwrap_or(std::cmp::Ordering::Equal)
    };
    likely_dead.sort_by(&mut by_centrality);
    needs_review.sort_by(&mut by_centrality);
    // `--limit` applies per tier (documented in `--help`). Without one, cap
    // the "likely" tier at 50: an unbounded list trains users to ignore it.
    match limit {
        Some(n) => {
            likely_dead.truncate(n);
            needs_review.truncate(n);
        }
        None => {
            likely_dead.truncate(50);
        }
    }

    let mut hubs: Vec<HubSymbol> = graph
        .symbols
        .iter()
        .enumerate()
        .map(|(idx, s)| HubSymbol {
            name: s.name.clone(),
            kind: s.kind.as_str().to_string(),
            coordinate: s.coordinate(),
            fan_in: graph.callers_map.get(&idx).map_or(0, |v| v.len()),
            centrality: s.centrality,
        })
        .collect();
    hubs.sort_by(|a, b| {
        b.fan_in.cmp(&a.fan_in).then_with(|| {
            b.centrality
                .partial_cmp(&a.centrality)
                .unwrap_or(std::cmp::Ordering::Equal)
        })
    });
    hubs.truncate(5);

    DoctorResult {
        files: files.len(),
        symbols: graph.symbols.len(),
        edges,
        isolated,
        likely_dead,
        needs_review,
        top_hubs: hubs,
        resolved: graph.resolve_stats.resolved,
        ambiguous_dropped: graph.resolve_stats.ambiguous_dropped,
        unresolved: graph.resolve_stats.unresolved,
        external: graph.resolve_stats.external,
        pagerank_iters: graph.pagerank_iters,
        pagerank_converged: graph.pagerank_converged,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::SymbolGraph;
    use crate::model::{Symbol, SymbolKind};

    fn sym(file: &str, name: &str, refs: Vec<String>) -> Symbol {
        let mut counts = std::collections::HashMap::new();
        for r in &refs {
            counts.insert(r.clone(), 1);
        }
        Symbol {
            name: name.into(),
            kind: SymbolKind::Function,
            file: file.into(),
            start_line: 1,
            end_line: 5,
            signature: String::new(),
            body: format!("fn {name} {{}}"),
            centrality: 0.0,
            calls: refs,
            mentions: vec![],
            call_counts: counts,
            member_calls: vec![],
            external_imports: vec![],
        }
    }

    #[test]
    fn isolated_non_entry_is_flagged_dead() {
        let g = SymbolGraph::new(vec![
            sym("src/a.rs", "used", vec![]),
            sym("src/a.rs", "caller", vec!["used".into()]),
            sym("src/b.rs", "lonely", vec![]),
        ]);
        let res = run_doctor(&g, None);
        assert_eq!(res.symbols, 3);
        assert!(res.likely_dead.iter().any(|d| d.name == "lonely"));
        assert!(!res.likely_dead.iter().any(|d| d.name == "used"));
        assert!(!res.needs_review.iter().any(|d| d.name == "used"));
    }

    #[test]
    fn isolated_constants_need_review_not_likely_dead() {
        use crate::model::SymbolKind;
        let mut c = sym("src/b.rs", "STRAY_CONST", vec![]);
        c.kind = SymbolKind::Constant;
        let g = SymbolGraph::new(vec![c]);
        let res = run_doctor(&g, None);
        assert!(res.likely_dead.is_empty());
        assert!(res.needs_review.iter().any(|d| d.name == "STRAY_CONST"));
    }

    #[test]
    fn entry_points_and_tests_are_excluded() {
        let g = SymbolGraph::new(vec![
            sym("src/main.rs", "main", vec![]),
            sym("tests/cli.rs", "helper", vec![]),
        ]);
        let res = run_doctor(&g, None);
        assert!(
            res.likely_dead.is_empty() && res.needs_review.is_empty(),
            "main + test helper must not be flagged, got {:?} / {:?}",
            res.likely_dead,
            res.needs_review
        );
    }

    #[test]
    fn framework_dispatched_entries_are_excluded() {
        // P1-3: tRPC procedures, Next.js conventions, index barrels.
        let mut router_proc = sym("src/routers/user.ts", "appRouter::getUser", vec![]);
        router_proc.kind = SymbolKind::Method;
        let g = SymbolGraph::new(vec![
            router_proc,
            sym("src/app/page.tsx", "Page", vec![]),
            sym("src/app/api/route.ts", "GET", vec![]),
            sym("src/middleware.ts", "middleware", vec![]),
            sym("src/index.ts", "publicHelper", vec![]),
        ]);
        let res = run_doctor(&g, None);
        assert!(
            res.likely_dead.is_empty() && res.needs_review.is_empty(),
            "framework entries must not be flagged, got {:?} / {:?}",
            res.likely_dead,
            res.needs_review
        );
    }

    #[test]
    fn likely_dead_caps_at_fifty_without_limit() {
        // P1-3: unbounded lists train users to ignore them.
        let syms: Vec<Symbol> = (0..60)
            .map(|i| sym(&format!("src/f{i}.rs"), &format!("lonely{i}"), vec![]))
            .collect();
        let g = SymbolGraph::new(syms);
        let res = run_doctor(&g, None);
        assert_eq!(res.likely_dead.len(), 50);
        let res_all = run_doctor(&g, Some(60));
        assert_eq!(res_all.likely_dead.len(), 60);
    }

    #[test]
    fn doctor_reports_resolution_and_convergence() {
        // P0-2/P1-1: counts and the pagerank exit surface in the result.
        let g = SymbolGraph::new(vec![
            sym("src/a.rs", "caller", vec!["used".into()]),
            sym("src/a.rs", "used", vec![]),
        ]);
        let res = run_doctor(&g, None);
        assert_eq!(res.resolved, 1);
        assert!(res.pagerank_iters >= 1);
        let md = res.to_markdown();
        assert!(md.contains("ambiguous-dropped"));
        assert!(md.contains("PageRank"));
    }
}
