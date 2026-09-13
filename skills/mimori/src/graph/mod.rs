pub mod blast;
pub mod doctor;
pub mod map;
pub mod missing;
pub mod pagerank;

use crate::model::{Coordinate, SliceResult, Symbol, FOLLOW_LOCAL_MARKER};
use anyhow::{bail, Context, Result};
use std::collections::HashMap;
use std::fs;
use std::path::Path;

/// Edge-resolution accounting. Silence was the bug: multi-candidate
/// non-callable names used to vanish without a trace.
#[derive(Debug, Clone, Default)]
pub struct ResolveStats {
    pub resolved: usize,
    pub ambiguous_dropped: usize,
    pub unresolved: usize,
    /// Calls matching a name the caller's file imports from an external
    /// (non-relative) module: never resolved locally, by design.
    pub external: usize,
}

/// Only these kinds may receive call edges. A call landing on a Variable
/// or Field is a graph type error (same class as the old serverId fan-in):
/// data is never callable.
pub fn is_callable_kind(kind: &crate::model::SymbolKind) -> bool {
    use crate::model::SymbolKind::*;
    matches!(kind, Function | Method | Class | Struct | Enum)
}

/// Outbound-edge weight penalty for test-file callers. Test-only call
/// density otherwise outranks the application's actual hubs; the edges
/// stay (recall), they just stop dominating rank (precision).
pub const TEST_EDGE_WEIGHT: f64 = 0.25;

/// Coverage honesty for unindexed repos: what the walker crawled vs indexed.
#[derive(Debug, Clone, Default)]
pub struct CoverageStats {
    pub indexed_files: usize,
    pub crawled_files: usize,
    pub unindexed_exts: Vec<String>,
}

/// Sublinear multiplicity weight: a 50-callsite dependency outweighs a
/// 1-callsite one, but not 50x. Measured shape, cap 8.
pub fn multiplicity_weight(count: u32) -> f64 {
    (count.max(1) as f64).sqrt().min(8.0)
}

/// Counting-unit legend: one static string on every read header so
/// distinct-symbol counts never read as call-site counts.
pub const COUNT_LEGEND: &str =
    "*Units*: N = distinct symbols (callers/affected/mentioners); edge weights sum call-site multiplicity min(sqrt(n),8); blast affected = transitive closure over call edges only.";

#[derive(Debug, Clone)]
pub struct SymbolGraph {
    pub symbols: Vec<Symbol>,
    pub callers_map: HashMap<usize, Vec<usize>>,
    pub callees_map: HashMap<usize, Vec<usize>>,
    /// Exact-name → symbol indices, including unqualified short names so
    /// `resolve` is O(candidates) instead of O(symbols). Built once in `new`.
    pub name_to_indices: HashMap<String, Vec<usize>>,
    /// Full `file:name` coordinate → symbol index. Last writer wins on
    /// collision; kept for diagnostics and future fast paths, never used
    /// alone for resolution so duplicate coordinates still report Ambiguous.
    pub coord_to_index: HashMap<String, usize>,
    pub resolve_stats: ResolveStats,
    pub pagerank_iters: usize,
    pub pagerank_converged: bool,
    pub edge_weights: HashMap<(usize, usize), f64>,
    pub out_weights: Vec<f64>,
    pub coverage: Option<CoverageStats>,
}

impl SymbolGraph {
    pub fn new(mut symbols: Vec<Symbol>) -> Self {
        let _p = crate::Phase::start("  name index");
        let mut name_to_indices: HashMap<String, Vec<usize>> = HashMap::new();
        let mut coord_to_index: HashMap<String, usize> = HashMap::new();

        for (idx, sym) in symbols.iter().enumerate() {
            name_to_indices
                .entry(sym.name.clone())
                .or_default()
                .push(idx);
            coord_to_index.insert(sym.coordinate(), idx);

            // Also index unqualified names for both `::` and `.` qualified
            // symbols (e.g. Type::method -> method, Class.method -> method),
            // matching `name_matches` so indexed resolve stays complete.
            if let Some(short_name) = sym.name.rsplit("::").next() {
                if short_name != sym.name {
                    name_to_indices
                        .entry(short_name.to_string())
                        .or_default()
                        .push(idx);
                }
            }
            if let Some(short_dot) = sym.name.rsplit('.').next() {
                if short_dot != sym.name && !short_dot.contains("::") {
                    name_to_indices
                        .entry(short_dot.to_string())
                        .or_default()
                        .push(idx);
                }
            }
        }

        drop(_p);
        let _p = crate::Phase::start("  edge resolve");
        let mut callers_map: HashMap<usize, Vec<usize>> = HashMap::new();
        let mut callees_map: HashMap<usize, Vec<usize>> = HashMap::new();
        let mut edge_weights: HashMap<(usize, usize), f64> = HashMap::new();
        let mut stats = ResolveStats::default();

        // Intern file paths so the same-file test is a u32 compare.
        let mut file_ids: HashMap<&str, u32> = HashMap::new();
        let sym_file_id: Vec<u32> = symbols
            .iter()
            .map(|s| {
                let next = file_ids.len() as u32;
                *file_ids.entry(s.file.as_str()).or_insert(next)
            })
            .collect();
        // Directory ids for the same-directory tier (P2): file's parent dir.
        let sym_dir: Vec<String> = symbols
            .iter()
            .map(|s| {
                Path::new(&s.file)
                    .parent()
                    .map(|p| p.to_string_lossy().into_owned())
                    .unwrap_or_default()
            })
            .collect();

        // Order each candidate list by file so the same-file probe is a binary
        // search rather than a scan. Without this, a name defined once per file
        // costs O(files) per reference, which is quadratic in workspace size --
        // and names like `new`, `build` or `Config` are defined in most files.
        let mut by_name: HashMap<&str, Vec<(u32, usize)>> =
            HashMap::with_capacity(name_to_indices.len());
        for (name, indices) in &name_to_indices {
            let mut v: Vec<(u32, usize)> = indices.iter().map(|&i| (sym_file_id[i], i)).collect();
            v.sort_unstable();
            by_name.insert(name.as_str(), v);
        }

        // Calls only. Mentions (properties, types, arg identifiers) never
        // become call edges — that fan-out put non-callables atop centrality.
        for u_idx in 0..symbols.len() {
            let u_file = sym_file_id[u_idx];
            let calls = symbols[u_idx].calls.clone();
            let member_set: std::collections::HashSet<&str> = symbols[u_idx]
                .member_calls
                .iter()
                .map(|s| s.as_str())
                .collect();
            let external_set: std::collections::HashSet<&str> = symbols[u_idx]
                .external_imports
                .iter()
                .map(|s| s.as_str())
                .collect();
            // Test-file callers keep their edges (recall) at quarter weight
            // (precision): test-only call density must not outrank the app.
            let test_penalty = if blast::is_test_symbol(&symbols[u_idx]) {
                TEST_EDGE_WEIGHT
            } else {
                1.0
            };
            for ref_name in &calls {
                // External first: imported from a non-relative module, never
                // resolved locally — counted, not silent.
                if external_set.contains(ref_name.as_str()) {
                    stats.external += 1;
                    continue;
                }
                let count = symbols[u_idx]
                    .call_counts
                    .get(ref_name)
                    .copied()
                    .unwrap_or(1);
                let w = multiplicity_weight(count) * test_penalty;
                let Some(candidates) = by_name.get(ref_name.as_str()) else {
                    stats.unresolved += 1;
                    continue;
                };
                // Callable-kind gate: data is never a call target. A name
                // resolving only to Variables/Fields has no edge to give.
                let callable: Vec<(u32, usize)> = candidates
                    .iter()
                    .filter(|&&(_, v)| is_callable_kind(&symbols[v].kind))
                    .copied()
                    .collect();
                if callable.is_empty() {
                    stats.unresolved += 1;
                    continue;
                }

                // Tier 1: same-file. Multiple same-file candidates is
                // ambiguous — drop and count, never pick index order.
                let pos = callable.partition_point(|&(f, _)| f < u_file);
                let mut same_file: Vec<usize> = Vec::new();
                let mut i = pos;
                while i < callable.len() && callable[i].0 == u_file {
                    same_file.push(callable[i].1);
                    i += 1;
                }
                same_file.retain(|&v| v != u_idx);
                if same_file.len() == 1 {
                    add_weighted_edge(u_idx, same_file[0], w, &mut callers_map, &mut callees_map, &mut edge_weights);
                    stats.resolved += 1;
                    continue;
                } else if same_file.len() > 1 {
                    stats.ambiguous_dropped += 1;
                    continue;
                }
                // Member-call receiver rule: `X.foo()` without a resolvable
                // receiver is the weakest evidence in the graph — same-file
                // or drop, never a fuzzy tier. Short-name unique-global on
                // a member call is how 576 false callers happened.
                if member_set.contains(ref_name.as_str()) {
                    stats.ambiguous_dropped += 1;
                    continue;
                }
                // Tier 2: same-directory (P2). Sits between same-file and
                // unique-global; matters as more languages arrive.
                // Bare `new`/`default` skip fuzzy tiers: every file calls
                // `String::new`/`Vec::new`, and the short-name index makes
                // all `X::new` candidates — one same-dir `::new` would
                // collect them all. Qualified `S::new`/`S` refs (recorded
                // alongside by the parsers) carry the real edge.
                let fuzzy_ok = ref_name != "new" && ref_name != "default";
                let u_dir = &sym_dir[u_idx];
                let mut same_dir: Vec<usize> = if fuzzy_ok {
                    callable
                        .iter()
                        .filter(|&&(_, v)| v != u_idx && &sym_dir[v] == u_dir)
                        .map(|&(_, v)| v)
                        .collect()
                } else {
                    Vec::new()
                };
                same_dir.sort_unstable();
                same_dir.dedup();
                if same_dir.len() == 1 {
                    add_weighted_edge(u_idx, same_dir[0], w, &mut callers_map, &mut callees_map, &mut edge_weights);
                    stats.resolved += 1;
                    continue;
                } else if same_dir.len() > 1 {
                    stats.ambiguous_dropped += 1;
                    continue;
                }
                // Bare new/default skip all fuzzy tiers (Tier 2 and Tier 3).
                if !fuzzy_ok {
                    stats.ambiguous_dropped += 1;
                    continue;
                }
                // Tier 3: unique-global (bare identifiers only — member
                // calls never reach here). Anything multi-candidate
                // (`new`, `build`, `Config`) drops — counted, not silent.
                if callable.len() == 1 {
                    let v_idx = callable[0].1;
                    if u_idx != v_idx {
                        add_weighted_edge(u_idx, v_idx, w, &mut callers_map, &mut callees_map, &mut edge_weights);
                        stats.resolved += 1;
                    }
                } else {
                    // Self never breaks a tie: resolve among the others.
                    let non_self: Vec<usize> = callable
                        .iter()
                        .filter(|&&(_, v)| v != u_idx)
                        .map(|&(_, v)| v)
                        .collect();
                    if non_self.len() == 1 {
                        add_weighted_edge(u_idx, non_self[0], w, &mut callers_map, &mut callees_map, &mut edge_weights);
                        stats.resolved += 1;
                    } else {
                        stats.ambiguous_dropped += 1;
                    }
                }
            }
        }

        drop(_p);
        let _p = crate::Phase::start("  pagerank");
        let pr = pagerank::compute_weighted_pagerank(
            &mut symbols,
            &callers_map,
            &callees_map,
            &edge_weights,
            None,
        );

        drop(_p);
        let out_weights: Vec<f64> = (0..symbols.len())
            .map(|u| match callees_map.get(&u) {
                Some(callees) => callees
                    .iter()
                    .map(|&v| edge_weights.get(&(u, v)).copied().unwrap_or(1.0))
                    .sum(),
                None => 0.0,
            })
            .collect();
        SymbolGraph {
            symbols,
            callers_map,
            callees_map,
            name_to_indices,
            coord_to_index,
            resolve_stats: stats,
            pagerank_iters: pr.iters,
            pagerank_converged: pr.converged,
            edge_weights,
            out_weights,
            coverage: None,
        }
    }

    /// Bias the ranking toward symbols whose name or file contains `term`.
    ///
    /// `--seed` parsed and was discarded before this existed, while three
    /// documents described it as working.
    pub fn seed_indices(&self, term: &str) -> Vec<usize> {
        let needle = term.to_lowercase();
        self.symbols
            .iter()
            .enumerate()
            .filter(|(_, s)| {
                s.name.to_lowercase().contains(&needle) || s.file.to_lowercase().contains(&needle)
            })
            .map(|(idx, _)| idx)
            .collect()
    }

    pub fn apply_personalization(&mut self, indices: &[usize]) {
        let pr = pagerank::compute_weighted_pagerank(
            &mut self.symbols,
            &self.callers_map,
            &self.callees_map,
            &self.edge_weights,
            Some(indices),
        );
        self.pagerank_iters = pr.iters;
        self.pagerank_converged = pr.converged;
    }

    pub fn compute_personalized_pagerank(&mut self, focus: &Coordinate) {
        let focus_indices = self.resolve_all(focus);
        let pr = pagerank::compute_weighted_pagerank(
            &mut self.symbols,
            &self.callers_map,
            &self.callees_map,
            &self.edge_weights,
            Some(&focus_indices),
        );
        self.pagerank_iters = pr.iters;
        self.pagerank_converged = pr.converged;
    }

    /// Non-call mentioners of a target: symbols whose `mentions` contain the
    /// target name (or its unqualified short name). Never a call edge —
    /// backs the `uses` verb so mention data stays queryable.
    pub fn mentioners(&self, coord: &Coordinate) -> Vec<&Symbol> {
        let resolved = self.resolve_all(coord);
        let mut out: Vec<&Symbol> = if !resolved.is_empty() {
            let mut indices: Vec<usize> = resolved
                .into_iter()
                .flat_map(|idx| self.mentioner_indices(idx))
                .collect::<std::collections::HashSet<_>>()
                .into_iter()
                .collect();
            indices.sort_unstable();
            indices.into_iter().map(|idx| &self.symbols[idx]).collect()
        } else {
            // Unresolvable target (e.g. a local): fall back to the raw name so
            // `uses` still answers instead of going silent.
            let Some(name) = coord.name() else {
                return Vec::new();
            };
            let short = name.rsplit([':', '.']).next().unwrap_or(name);
            let mut out = Vec::new();
            for sym in &self.symbols {
                if sym.mentions.iter().any(|m| m == name || m == short)
                    && !out.iter().any(|s: &&Symbol| s.coordinate() == sym.coordinate())
                {
                    out.push(sym);
                }
            }
            out
        };
        out.sort_by(|a, b| {
            a.file
                .cmp(&b.file)
                .then(a.start_line.cmp(&b.start_line))
                .then(a.name.cmp(&b.name))
        });
        out
    }

    /// Index-level mentioner lookup: indices whose `mentions` name the
    /// target symbol (full name or unqualified short name).
    pub fn mentioner_indices(&self, target_idx: usize) -> Vec<usize> {
        let name = self.symbols[target_idx].name.as_str();
        let short = name.rsplit([':', '.']).next().unwrap_or(name);
        self.symbols
            .iter()
            .enumerate()
            .filter(|(i, s)| {
                *i != target_idx && s.mentions.iter().any(|m| m == name || m == short)
            })
            .map(|(i, _)| i)
            .collect()
    }

    /// Upstream rows for `up`: strong callers plus weak value-use
    /// mentioners. A symbol that both calls and mentions appears once, as
    /// a caller — the call edge wins.
    pub fn upstream(&self, coord: &Coordinate) -> (Vec<&Symbol>, Vec<&Symbol>) {
        let callers = self.callers(coord);
        let weak: Vec<&Symbol> = self
            .mentioners(coord)
            .into_iter()
            .filter(|m| {
                !callers
                    .iter()
                    .any(|c| c.coordinate() == m.coordinate())
            })
            .collect();
        (callers, weak)
    }

    /// Resolve a coordinate to every symbol in the winning match tier.
    ///
    /// Used by up/down/blast/focus, which legitimately want all matches for a
    /// bare name.
    pub fn resolve_all(&self, coord: &Coordinate) -> Vec<usize> {
        match self.resolve(coord) {
            Resolution::Unique(idx) => vec![idx],
            Resolution::Ambiguous(indices) => indices,
            Resolution::NotFound => Vec::new(),
        }
    }

    /// Resolve a coordinate, distinguishing "one match" from "several".
    ///
    /// Matching is tiered and the first tier that produces candidates is the
    /// answer -- but more than one candidate in a tier is Ambiguous, never a
    /// silent pick. Previously an exact coordinate could match another file by
    /// basename and `build_slice` would take `indices[0]`, returning a
    /// different file's source under the requested path.
    pub fn resolve(&self, coord: &Coordinate) -> Resolution {
        let Some(name) = coord.name() else {
            return Resolution::NotFound;
        };

        // O(candidates) via the persisted index built in `new`, not O(symbols).
        // The index holds exact names plus `::`/`.` short names, covering
        // every `name_matches` case except a multi-segment suffix query like
        // `Type::method` against a stored `Module::Type::method`.
        let mut by_name: Vec<usize> = self.name_to_indices.get(name).cloned().unwrap_or_default();

        if by_name.is_empty() && (name.contains("::") || name.contains('.')) {
            by_name = self
                .symbols
                .iter()
                .enumerate()
                .filter(|(_, s)| name_matches(&s.name, name))
                .map(|(idx, _)| idx)
                .collect();
        }

        if by_name.is_empty() {
            return Resolution::NotFound;
        }

        let Some(target_file) = coord.file() else {
            return Resolution::from(by_name);
        };

        // Tier 1: the exact workspace-relative path.
        // Tier 2: a path suffix on a component boundary.
        // Tier 3: the basename alone.
        for tier in [
            |a: &Path, b: &Path| a == b,
            component_suffix_match,
            basename_match,
        ] {
            let hits: Vec<usize> = by_name
                .iter()
                .copied()
                .filter(|&idx| tier(Path::new(&self.symbols[idx].file), target_file))
                .collect();
            if !hits.is_empty() {
                return Resolution::from(hits);
            }
        }

        Resolution::NotFound
    }

    /// Upstream targets for `up`/`blast`: the resolved symbols plus, when a
    /// target is a class/struct, its `::constructor` / `::new` — so `up X`
    /// folds in `new X(...)` construction sites (P0). Pointing directly at
    /// `X::constructor` needs no folding; it resolves to the ctor itself.
    pub fn resolve_upstream_targets(&self, coord: &Coordinate) -> Vec<usize> {
        let mut indices = self.resolve_all(coord);
        let mut extra = Vec::new();
        for &idx in &indices {
            let sym = &self.symbols[idx];
            if matches!(
                sym.kind,
                crate::model::SymbolKind::Class | crate::model::SymbolKind::Struct
            ) {
                for suffix in ["::constructor", "::new"] {
                    let ctor = format!("{}{}", sym.name, suffix);
                    if let Some(more) = self.name_to_indices.get(&ctor) {
                        extra.extend(more.iter().copied());
                    }
                }
            }
        }
        for e in extra {
            if !indices.contains(&e) {
                indices.push(e);
            }
        }
        indices
    }

    pub fn callers(&self, coord: &Coordinate) -> Vec<&Symbol> {
        let indices = self.resolve_upstream_targets(coord);
        let mut caller_symbols = Vec::new();

        for target_idx in indices {
            if let Some(callers) = self.callers_map.get(&target_idx) {
                for &caller_idx in callers {
                    let sym = &self.symbols[caller_idx];
                    if !caller_symbols
                        .iter()
                        .any(|s: &&Symbol| s.coordinate() == sym.coordinate())
                    {
                        caller_symbols.push(sym);
                    }
                }
            }
        }

        caller_symbols
    }

    pub fn callees(&self, coord: &Coordinate) -> Vec<&Symbol> {
        let indices = self.resolve_all(coord);
        let mut callee_symbols = Vec::new();

        for target_idx in indices {
            if let Some(callees) = self.callees_map.get(&target_idx) {
                for &callee_idx in callees {
                    let sym = &self.symbols[callee_idx];
                    if !callee_symbols
                        .iter()
                        .any(|s: &&Symbol| s.coordinate() == sym.coordinate())
                    {
                        callee_symbols.push(sym);
                    }
                }
            }
        }

        callee_symbols
    }

    pub fn build_slice(
        &self,
        coord: &Coordinate,
        follow_local: bool,
        with_imports: bool,
    ) -> Result<SliceResult> {
        if let Coordinate::Lines { file, start, end } = coord {
            return slice_line_coordinate(file, *start, *end, with_imports);
        }

        let target_idx = match self.resolve(coord) {
            Resolution::Unique(idx) => idx,
            Resolution::NotFound => bail!(
                "Symbol '{}' not found in workspace.\nTry: mimori find '{}'",
                coord,
                coord
            ),
            Resolution::Ambiguous(indices) => {
                let mut matches: Vec<&Symbol> = indices.iter().map(|&i| &self.symbols[i]).collect();
                matches.sort_by(|a, b| {
                    b.centrality
                        .partial_cmp(&a.centrality)
                        .unwrap_or(std::cmp::Ordering::Equal)
                });
                let coords: Vec<String> = matches
                    .iter()
                    .map(|s| {
                        format!(
                            "  - `{}` ({}) [rank: {:.4}]",
                            s.coordinate(),
                            s.kind.as_str(),
                            s.centrality
                        )
                    })
                    .collect();
                let retries: Vec<String> = matches
                    .iter()
                    .map(|s| format!("  mimori slice '{}'", s.coordinate()))
                    .collect();
                bail!(
                    "Ambiguous symbol '{}'. Multiple matches found, please specify full coordinate:\n{}\nRetry with one of:\n{}",
                    coord,
                    coords.join("\n"),
                    retries.join("\n")
                );
            }
        };

        let sym = &self.symbols[target_idx];

        let callers: Vec<String> = self
            .callers(coord)
            .into_iter()
            .map(|s| s.coordinate())
            .collect();

        let callee_syms = self.callees(coord);
        let callees: Vec<String> = callee_syms.iter().map(|s| s.coordinate()).collect();

        let mut body = truncate_body(&sym.body, sym.end_line - sym.start_line);

        if follow_local {
            let local_callees: Vec<&&Symbol> =
                callee_syms.iter().filter(|c| c.file == sym.file).collect();

            if !local_callees.is_empty() {
                body.push_str(FOLLOW_LOCAL_MARKER);
                for lc in local_callees {
                    body.push_str(&format!(
                        "\n// Symbol: `{}` (L{}-L{})\n{}\n",
                        lc.name, lc.start_line, lc.end_line, lc.body
                    ));
                }
            }
        }

        let imports = if with_imports {
            let imps = extract_file_imports(Path::new(&sym.file));
            (!imps.is_empty()).then_some(imps)
        } else {
            None
        };

        Ok(SliceResult {
            coordinate: sym.coordinate(),
            file: sym.file.clone(),
            symbol: Some(sym.clone()),
            line_range: Some((sym.start_line, sym.end_line)),
            content: body,
            callers,
            callees,
            total_lines: sym.end_line - sym.start_line + 1,
            imports,
        })
    }
}

/// How a coordinate resolved against the index.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Resolution {
    Unique(usize),
    Ambiguous(Vec<usize>),
    NotFound,
}

impl From<Vec<usize>> for Resolution {
    fn from(mut hits: Vec<usize>) -> Self {
        match hits.len() {
            0 => Resolution::NotFound,
            1 => Resolution::Unique(hits.remove(0)),
            _ => Resolution::Ambiguous(hits),
        }
    }
}

fn name_matches(symbol_name: &str, target: &str) -> bool {
    symbol_name == target
        || symbol_name.ends_with(&format!("::{}", target))
        || symbol_name.ends_with(&format!(".{}", target))
}

/// True when one path is a suffix of the other on a component boundary.
fn component_suffix_match(a: &Path, b: &Path) -> bool {
    let av: Vec<_> = a.components().collect();
    let bv: Vec<_> = b.components().collect();
    let n = av.len().min(bv.len());
    n > 0 && av[av.len() - n..] == bv[bv.len() - n..]
}

fn basename_match(a: &Path, b: &Path) -> bool {
    match (a.file_name(), b.file_name()) {
        (Some(x), Some(y)) => x == y,
        _ => false,
    }
}

/// Cap very large bodies, keeping the head and tail.
fn truncate_body(body: &str, span: usize) -> String {
    const LIMIT: usize = 250;
    const HEAD: usize = 200;
    const TAIL: usize = 30;

    let lines: Vec<&str> = body.lines().collect();
    if span <= LIMIT || lines.len() <= HEAD + TAIL {
        return body.to_string();
    }

    format!(
        "{}\n\n// ... [{} lines truncated for token efficiency] ...\n\n{}",
        lines[..HEAD].join("\n"),
        lines.len() - HEAD - TAIL,
        lines[lines.len() - TAIL..].join("\n")
    )
}

pub fn extract_file_imports(file_path: &Path) -> Vec<String> {
    let Ok(content) = fs::read_to_string(file_path) else {
        return Vec::new();
    };

    let mut imports = Vec::new();
    let mut in_multiline_import = false;
    let mut multiline_buf = String::new();

    // Scan the header generously: a 100-line cap silently dropped imports in
    // files with long licence blocks or large import lists.
    for line in content.lines().take(400) {
        let trimmed = line.trim();

        if in_multiline_import {
            multiline_buf.push_str(line);
            multiline_buf.push('\n');
            if trimmed.contains(')') || trimmed.contains('}') || trimmed.ends_with(';') {
                in_multiline_import = false;
                imports.push(multiline_buf.trim_end().to_string());
                multiline_buf.clear();
            }
            continue;
        }

        if trimmed.starts_with("use ")
            || trimmed.starts_with("pub use ")
            || trimmed.starts_with("extern crate ")
        {
            if trimmed.ends_with(';') {
                imports.push(trimmed.to_string());
            } else {
                in_multiline_import = true;
                multiline_buf.push_str(line);
                multiline_buf.push('\n');
            }
        } else if trimmed.starts_with("import ")
            || trimmed.starts_with("import{")
            || trimmed.starts_with("import type ")
        {
            if trimmed.ends_with(';') || trimmed.ends_with('\'') || trimmed.ends_with('"') {
                imports.push(trimmed.to_string());
            } else {
                in_multiline_import = true;
                multiline_buf.push_str(line);
                multiline_buf.push('\n');
            }
        } else if trimmed.starts_with("from ") && trimmed.contains(" import ") {
            if trimmed.ends_with('\\') || (trimmed.contains('(') && !trimmed.contains(')')) {
                in_multiline_import = true;
                multiline_buf.push_str(line);
                multiline_buf.push('\n');
            } else {
                imports.push(trimmed.to_string());
            }
        } else if (trimmed.starts_with("const ") || trimmed.starts_with("let "))
            && trimmed.contains("= require(")
        {
            imports.push(trimmed.to_string());
        }
    }

    imports
}

fn add_edge(
    caller_idx: usize,
    callee_idx: usize,
    callers_map: &mut HashMap<usize, Vec<usize>>,
    callees_map: &mut HashMap<usize, Vec<usize>>,
) {
    let callers = callers_map.entry(callee_idx).or_default();
    if !callers.contains(&caller_idx) {
        callers.push(caller_idx);
    }

    let callees = callees_map.entry(caller_idx).or_default();
    if !callees.contains(&callee_idx) {
        callees.push(callee_idx);
    }
}

fn add_weighted_edge(
    caller_idx: usize,
    callee_idx: usize,
    weight: f64,
    callers_map: &mut HashMap<usize, Vec<usize>>,
    callees_map: &mut HashMap<usize, Vec<usize>>,
    edge_weights: &mut HashMap<(usize, usize), f64>,
) {
    add_edge(caller_idx, callee_idx, callers_map, callees_map);
    // Multiple call names resolving to one callee sum: combined evidence.
    *edge_weights.entry((caller_idx, callee_idx)).or_insert(0.0) += weight;
}

/// Slice a line range straight off disk. Needs no index.
pub fn slice_line_coordinate(
    file: &Path,
    start: usize,
    end: usize,
    with_imports: bool,
) -> Result<SliceResult> {
    if !file.exists() {
        bail!("File not found: {}", file.display());
    }

    let content = fs::read_to_string(file)
        .with_context(|| format!("Failed to read file: {}", file.display()))?;

    let lines: Vec<&str> = content.lines().collect();
    let total_lines = lines.len();

    // Normalize: accept reversed ranges, floor at line 1, fail past end of file.
    let (start, end) = if start > end {
        (end, start)
    } else {
        (start, end)
    };
    let start = start.max(1);
    if start > total_lines {
        bail!(
            "Line {} is past the end of {} ({} lines).",
            start,
            file.display(),
            total_lines
        );
    }

    let start_idx = start - 1;
    let end_idx = end.min(total_lines);

    let mut sliced = String::new();
    for (i, line) in lines[start_idx..end_idx].iter().enumerate() {
        sliced.push_str(&format!("{:4} | {}\n", start + i, line));
    }

    let imports = if with_imports {
        let imps = extract_file_imports(file);
        (!imps.is_empty()).then_some(imps)
    } else {
        None
    };

    Ok(SliceResult {
        coordinate: format!("{}:#L{}-{}", file.display(), start, end),
        file: file.display().to_string(),
        symbol: None,
        line_range: Some((start, end)),
        content: sliced,
        callers: Vec::new(),
        callees: Vec::new(),
        total_lines: end_idx - start_idx,
        imports,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::SymbolKind;
    use std::io::Write;

    fn sym(file: &str, name: &str) -> Symbol {
        Symbol {
            name: name.into(),
            kind: SymbolKind::Function,
            file: file.into(),
            start_line: 1,
            end_line: 1,
            signature: String::new(),
            body: format!("fn {name}() {{ /* {file} */ }}"),
            centrality: 0.0,
            calls: vec![],
            mentions: vec![],
            call_counts: Default::default(),
            member_calls: vec![],
            external_imports: vec![],
        }
    }

    fn graph_of(files: &[(&str, &str)]) -> SymbolGraph {
        SymbolGraph::new(files.iter().map(|(f, n)| sym(f, n)).collect())
    }

    fn at(g: &SymbolGraph, raw: &str) -> Resolution {
        g.resolve(&Coordinate::parse(raw).unwrap())
    }

    #[test]
    fn exact_path_beats_a_basename_collision() {
        // Regression M1: `alpha/mod.rs:handler` returned beta's body, because
        // basename equality was one of four equal-weight OR'd conditions and
        // build_slice then took indices[0].
        let g = graph_of(&[
            ("src/alpha/mod.rs", "handler"),
            ("src/beta/mod.rs", "handler"),
        ]);

        let Resolution::Unique(i) = at(&g, "src/alpha/mod.rs:handler") else {
            panic!("exact path must resolve uniquely");
        };
        assert_eq!(g.symbols[i].file, "src/alpha/mod.rs");

        let Resolution::Unique(j) = at(&g, "src/beta/mod.rs:handler") else {
            panic!("exact path must resolve uniquely");
        };
        assert_eq!(g.symbols[j].file, "src/beta/mod.rs");
    }

    #[test]
    fn a_basename_collision_is_ambiguous_not_a_guess() {
        let g = graph_of(&[
            ("src/alpha/mod.rs", "handler"),
            ("src/beta/mod.rs", "handler"),
        ]);
        assert!(matches!(at(&g, "mod.rs:handler"), Resolution::Ambiguous(v) if v.len() == 2));
    }

    #[test]
    fn a_unique_basename_still_resolves() {
        let g = graph_of(&[("src/auth_service.rs", "login"), ("src/other.rs", "logout")]);
        assert!(matches!(
            at(&g, "auth_service.rs:login"),
            Resolution::Unique(_)
        ));
    }

    #[test]
    fn a_path_suffix_resolves_on_component_boundaries() {
        let g = graph_of(&[
            ("src/alpha/mod.rs", "handler"),
            ("src/beta/mod.rs", "handler"),
        ]);
        assert!(matches!(
            at(&g, "alpha/mod.rs:handler"),
            Resolution::Unique(_)
        ));

        // "ha/mod.rs" is a string suffix of "alpha/mod.rs" but not a component
        // suffix, so the suffix tier must not resolve it. It falls through to
        // the basename tier, which sees both files and reports ambiguity rather
        // than guessing.
        assert!(matches!(
            at(&g, "ha/mod.rs:handler"),
            Resolution::Ambiguous(_)
        ));
    }

    #[test]
    fn component_suffix_ignores_mid_component_string_suffixes() {
        assert!(component_suffix_match(
            Path::new("src/alpha/mod.rs"),
            Path::new("alpha/mod.rs")
        ));
        assert!(!component_suffix_match(
            Path::new("src/alpha/mod.rs"),
            Path::new("ha/mod.rs")
        ));
        assert!(component_suffix_match(
            Path::new("mod.rs"),
            Path::new("src/alpha/mod.rs")
        ));
    }

    #[test]
    fn qualified_bare_names_resolve_through_the_name_tier() {
        // Regression P17: "Store::save" parsed as file "Store", name ":save".
        let g = graph_of(&[("src/lib.rs", "Store::save"), ("src/lib.rs", "other")]);
        assert!(matches!(at(&g, "Store::save"), Resolution::Unique(_)));
        assert!(matches!(at(&g, "save"), Resolution::Unique(_)));
    }

    fn sym_with_refs(file: &str, name: &str, refs: &[&str]) -> Symbol {
        let mut s = sym(file, name);
        s.calls = refs.iter().map(|r| r.to_string()).collect();
        for r in refs {
            s.call_counts.insert(r.to_string(), 1);
        }
        s
    }

    /// Caller with explicit member/bare split and external imports.
    fn caller(
        file: &str,
        name: &str,
        calls: &[&str],
        member: &[&str],
        external: &[&str],
    ) -> Symbol {
        let mut s = sym_with_refs(file, name, calls);
        s.member_calls = member.iter().map(|r| r.to_string()).collect();
        s.external_imports = external.iter().map(|r| r.to_string()).collect();
        s
    }

    fn method(file: &str, name: &str) -> Symbol {
        let mut s = sym(file, name);
        s.kind = SymbolKind::Method;
        s
    }

    fn variable(file: &str, name: &str) -> Symbol {
        let mut s = sym(file, name);
        s.kind = SymbolKind::Variable;
        s
    }

    #[test]
    fn a_reference_prefers_a_match_in_its_own_file() {
        let g = SymbolGraph::new(vec![
            sym_with_refs("a.rs", "caller", &["target"]),
            sym("a.rs", "target"),
            sym("b.rs", "target"),
        ]);
        let callees = g.callees(&Coordinate::parse("a.rs:caller").unwrap());
        assert_eq!(callees.len(), 1);
        assert_eq!(callees[0].file, "a.rs");
    }

    #[test]
    fn a_unique_name_links_across_files() {
        let g = SymbolGraph::new(vec![
            sym_with_refs("a.rs", "caller", &["only_one"]),
            sym("b.rs", "only_one"),
        ]);
        let callees = g.callees(&Coordinate::parse("a.rs:caller").unwrap());
        assert_eq!(callees.len(), 1, "a unique cross-file name must still link");
    }

    #[test]
    fn an_ambiguous_name_links_to_nothing_rather_than_everything() {
        // Regression M7: one call to `new` used to wire the caller to every
        // `new` in the workspace, inflating centrality and blast radius.
        let g = SymbolGraph::new(vec![
            sym_with_refs("a.rs", "caller", &["new"]),
            sym("b.rs", "new"),
            sym("c.rs", "new"),
            sym("d.rs", "new"),
        ]);
        let callees = g.callees(&Coordinate::parse("a.rs:caller").unwrap());
        assert!(callees.is_empty(), "got {} spurious edges", callees.len());
    }

    #[test]
    fn mentions_never_become_call_edges() {
         // P0-1 gate: property/type/arg mentions must not fan out to Field
        // candidates. 12 readers mention `serverId`, defined as a field in
        // 3 files — fan-in on every candidate must stay 0, not 12.
        fn reader(file: &str, name: &str) -> Symbol {
            let mut s = sym(file, name);
            s.mentions = vec!["serverId".to_string()];
            s
        }
        fn field(file: &str) -> Symbol {
            Symbol {
                name: "Store::serverId".into(),
                kind: SymbolKind::Field,
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
        let mut syms = vec![field("a.rs"), field("b.rs"), field("c.rs")];
        for i in 0..12 {
            syms.push(reader(&format!("r{i}.rs"), &format!("reader{i}")));
        }
        let g = SymbolGraph::new(syms);
        for f in ["a.rs", "b.rs", "c.rs"] {
            let coord = Coordinate::parse(&format!("{f}:serverId")).unwrap();
            assert!(
                g.callers(&coord).is_empty(),
                "mention fan-in on {f}:serverId must be 0"
            );
        }
        // The data is kept, just mislabeled no more: `uses` finds readers.
        let coord = Coordinate::parse("serverId").unwrap();
        assert_eq!(g.mentioners(&coord).len(), 12);
    }

    #[test]
    fn upstream_partitions_callers_from_value_uses_with_call_wins() {
        // A symbol that both calls and mentions the target appears once,
        // as a caller; a pure mentioner lands in the weak tier.
        let mut both = sym_with_refs("a.rs", "both", &["target"]);
        both.mentions = vec!["target".to_string()];
        let mut pure = sym("a.rs", "pure");
        pure.mentions = vec!["target".to_string()];
        let g = SymbolGraph::new(vec![both, pure, sym("b.rs", "target")]);
        let coord = Coordinate::parse("target").unwrap();
        let (callers, weak) = g.upstream(&coord);
        assert_eq!(callers.len(), 1);
        assert_eq!(callers[0].name, "both");
        assert_eq!(weak.len(), 1);
        assert_eq!(weak[0].name, "pure");
    }

    #[test]
    fn fields_vanish_from_top_ranks() {
        // P0-1 gate: with mentions out of the edge set, a true hub outranks
        // every field; the top-20-row slice of this fixture holds no fields.
        fn reader_of(name: &str, calls: &[&str], mentions: &[&str]) -> Symbol {
            let mut s = sym("r.rs", name);
            s.calls = calls.iter().map(|r| r.to_string()).collect();
            for c in calls {
                s.call_counts.insert(c.to_string(), 1);
            }
            s.mentions = mentions.iter().map(|r| r.to_string()).collect();
            s
        }
        let mut syms = vec![
            sym("hub.rs", "realHub"),
            Symbol {
                name: "Store::input".into(),
                kind: SymbolKind::Field,
                file: "s.rs".into(),
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
            },
        ];
        for i in 0..10 {
            syms.push(reader_of(&format!("caller{i}"), &["realHub"], &["input"]));
        }
        let g = SymbolGraph::new(syms);
        let mut ranked = g.symbols.clone();
        ranked.sort_by(|a, b| {
            b.centrality
                .partial_cmp(&a.centrality)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        // Mechanism, not tiny-graph ordering noise: the field draws zero
        // call edges (the old fan-out gave it 10) and the true hub outranks
        // it. On a real corpus the baseline gap puts fields far outside the
        // top-20; in a 12-node fixture every isolated node shares the
        // dangling baseline, so only the hub-vs-field gap is asserted.
        let top: Vec<&Symbol> = ranked.iter().take(5).collect();
        assert_eq!(top[0].name, "realHub");
        let field_coord = Coordinate::parse("s.rs:Store::input").unwrap();
        assert!(g.callers(&field_coord).is_empty());
        let hub_c = ranked.iter().find(|s| s.name == "realHub").unwrap().centrality;
        let field_c = ranked
            .iter()
            .find(|s| s.name == "Store::input")
            .unwrap()
            .centrality;
        assert!(hub_c > field_c, "hub {hub_c} field {field_c}");
    }

    #[test]
    fn resolver_counts_what_it_drops() {
        // P0-2: ambiguous multi-candidate names are counted, not silent.
        let g = SymbolGraph::new(vec![
            sym_with_refs("a.rs", "caller", &["new", "missing", "only_one"]),
            sym("b.rs", "new"),
            sym("c.rs", "new"),
            sym("d.rs", "only_one"),
        ]);
        assert_eq!(g.resolve_stats.resolved, 1);
        assert_eq!(g.resolve_stats.ambiguous_dropped, 1);
        assert_eq!(g.resolve_stats.unresolved, 1);
    }

    #[test]
    fn same_file_ambiguity_drops_instead_of_picking_index_order() {
         // Two same-file `target` candidates: no edge, counted ambiguous.
        let g = SymbolGraph::new(vec![
            sym_with_refs("a.rs", "caller", &["target"]),
            sym("a.rs", "target"),
            Symbol {
                name: "M::target".into(),
                kind: SymbolKind::Function,
                file: "a.rs".into(),
                start_line: 9,
                end_line: 9,
                signature: String::new(),
                body: String::new(),
                centrality: 0.0,
                calls: vec![],
                mentions: vec![],
                call_counts: Default::default(),
                member_calls: vec![],
                external_imports: vec![],
            },
        ]);
        let callees = g.callees(&Coordinate::parse("a.rs:caller").unwrap());
        assert!(callees.is_empty());
        assert_eq!(g.resolve_stats.ambiguous_dropped, 1);
    }

    #[test]
    fn same_directory_beats_unique_global() {
        // P2: the same-directory tier sits between same-file and global.
        let g = SymbolGraph::new(vec![
            sym_with_refs("src/a/caller.rs", "caller", &["helper"]),
            sym("src/a/helper.rs", "helper"),
            sym("src/b/helper.rs", "helper"),
        ]);
        let callees = g.callees(&Coordinate::parse("src/a/caller.rs:caller").unwrap());
        assert_eq!(callees.len(), 1);
        assert_eq!(callees[0].file, "src/a/helper.rs");
    }

    #[test]
    fn pagerank_converges_and_reports() {
        // P1-1: small cyclic graphs converge well before the cap, and the
        // graph surfaces the exit it took.
        let g = SymbolGraph::new(vec![
            sym_with_refs("a.rs", "x", &["y"]),
            sym_with_refs("a.rs", "y", &["x"]),
        ]);
        assert!(g.pagerank_converged, "iters: {}", g.pagerank_iters);
        assert!(g.pagerank_iters >= 1 && g.pagerank_iters <= 100);
    }

    #[test]
    fn edge_weights_scale_sublinearly_with_multiplicity() {
        // P1-2: 50 callsites outweigh 1 callsite, but not 50x (cap 8).
        assert!((multiplicity_weight(1) - 1.0).abs() < 1e-9);
        assert!((multiplicity_weight(50) - 50f64.sqrt()).abs() < 1e-9);
        assert!((multiplicity_weight(1000) - 8.0).abs() < 1e-9);
        let mut heavy = sym_with_refs("a.rs", "caller", &["hot", "cold"]);
        heavy.call_counts.insert("hot".to_string(), 50);
        heavy.call_counts.insert("cold".to_string(), 1);
        let g = SymbolGraph::new(vec![heavy, sym("b.rs", "hot"), sym("c.rs", "cold")]);
        let u = 0;
        let hot_w = g.edge_weights.get(&(u, 1)).copied().unwrap_or(0.0);
        let cold_w = g.edge_weights.get(&(u, 2)).copied().unwrap_or(0.0);
        assert!(hot_w > cold_w, "hot {hot_w} cold {cold_w}");
        assert!((hot_w - 50f64.sqrt()).abs() < 1e-9);
        assert!((cold_w - 1.0).abs() < 1e-9);
    }

    #[test]
    fn graph_construction_is_deterministic() {
        // P2: byte-identical determinism gate — same input, same edges and
        // ranks, twice in a row.
        fn build() -> (Vec<(usize, usize)>, Vec<f64>) {
            let g = SymbolGraph::new(vec![
                sym_with_refs("a.rs", "caller", &["target", "other"]),
                sym("a.rs", "target"),
                sym("b.rs", "other"),
            ]);
            let mut edges: Vec<(usize, usize)> = g
                .callees_map
                .iter()
                .flat_map(|(u, vs)| vs.iter().map(|v| (*u, *v)))
                .collect();
            edges.sort();
            let ranks: Vec<f64> = g.symbols.iter().map(|s| s.centrality).collect();
            (edges, ranks)
        }
        assert_eq!(build(), build());
    }

    #[test]
    fn an_unknown_symbol_is_not_found() {
        let g = graph_of(&[("src/lib.rs", "handler")]);
        assert!(matches!(at(&g, "nope"), Resolution::NotFound));
        assert!(matches!(at(&g, "src/lib.rs:nope"), Resolution::NotFound));
    }

    #[test]
    fn build_slice_refuses_to_guess_between_ambiguous_matches() {
        let g = graph_of(&[
            ("src/alpha/mod.rs", "handler"),
            ("src/beta/mod.rs", "handler"),
        ]);
        let err = g
            .build_slice(&Coordinate::parse("mod.rs:handler").unwrap(), false, false)
            .unwrap_err()
            .to_string();
        assert!(err.contains("Ambiguous"), "got: {err}");
        assert!(err.contains("alpha") && err.contains("beta"), "got: {err}");
        assert!(
            err.contains("mimori slice 'src/alpha/mod.rs:handler'")
                && err.contains("mimori slice 'src/beta/mod.rs:handler'"),
            "retry commands missing, got: {err}"
        );
    }

    fn fixture(lines: usize) -> tempfile::NamedTempFile {
        let mut f = tempfile::NamedTempFile::new().unwrap();
        for i in 1..=lines {
            writeln!(f, "line {i}").unwrap();
        }
        f.flush().unwrap();
        f
    }

    #[test]
    fn line_zero_does_not_underflow() {
        // (start - 1) on line 0 wrapped to usize::MAX. Regression: M3/graph.rs:361.
        let f = fixture(10);
        let res = slice_line_coordinate(f.path(), 0, 5, false).unwrap();
        assert_eq!(res.line_range, Some((1, 5)));
        assert!(res.content.contains("line 1"));
    }

    #[test]
    fn reversed_ranges_are_normalized() {
        // lines[7..2] panicked. Regression: M3/graph.rs:365.
        let f = fixture(10);
        let res = slice_line_coordinate(f.path(), 8, 2, false).unwrap();
        assert_eq!(res.line_range, Some((2, 8)));
        assert!(res.content.contains("line 2") && res.content.contains("line 8"));
    }

    #[test]
    fn start_past_end_of_file_is_an_error() {
        let f = fixture(10);
        let err = slice_line_coordinate(f.path(), 400, 500, false)
            .unwrap_err()
            .to_string();
        assert!(err.contains("past the end"), "got: {err}");
    }

    #[test]
    fn end_past_end_of_file_clamps() {
        let f = fixture(10);
        let res = slice_line_coordinate(f.path(), 8, 500, false).unwrap();
        assert!(res.content.contains("line 10"));
    }

    #[test]
    fn member_calls_resolve_same_file_only() {
        // `rl.input()` in another file must not ride the short name to a
        // unique-global target; the same-file member call still links.
        let g = SymbolGraph::new(vec![
            caller("a.ts", "localUser", &["input"], &["input"], &[]),
            method("a.ts", "input"),
            method("b.ts", "input"),
            caller("c.ts", "remoteUser", &["input"], &["input"], &[]),
        ]);
        let local = g.callees(&Coordinate::parse("a.ts:localUser").unwrap());
        assert_eq!(local.len(), 1);
        assert_eq!(local[0].file, "a.ts");
        let remote = g.callees(&Coordinate::parse("c.ts:remoteUser").unwrap());
        assert!(remote.is_empty(), "member call escaped its file: {remote:?}");
        assert_eq!(
            g.callers(&Coordinate::parse("b.ts:input").unwrap()).len(),
            0,
            "cross-file member fan-in must be 0"
        );
    }

    #[test]
    fn bare_calls_still_resolve_unique_global() {
        // The member rule must not starve bare identifiers: `cn(...)`
        // called bare keeps its global edge.
        let g = SymbolGraph::new(vec![
            caller("a.ts", "comp", &["cn"], &[], &[]),
            sym("b.ts", "cn"),
        ]);
        let callees = g.callees(&Coordinate::parse("a.ts:comp").unwrap());
        assert_eq!(callees.len(), 1);
        assert_eq!(callees[0].name, "cn");
    }

    #[test]
    fn non_callable_targets_receive_no_edges() {
        // 158 callers on a Variable is a graph type error: data is never
        // callable, so the edge drops instead of building a false hub.
        let g = SymbolGraph::new(vec![
            caller("a.ts", "schema", &["primaryKey"], &[], &[]),
            variable("b.ts", "primaryKey"),
        ]);
        assert!(g
            .callers(&Coordinate::parse("b.ts:primaryKey").unwrap())
            .is_empty());
        assert_eq!(g.resolve_stats.resolved, 0);
        assert_eq!(g.resolve_stats.unresolved, 1);
    }

    #[test]
    fn callable_kind_gate_covers_every_kind() {
        use SymbolKind::*;
        for kind in [Function, Method, Class, Struct, Enum] {
            assert!(is_callable_kind(&kind), "{kind:?} must be callable");
        }
        for kind in [
            Variable,
            Constant,
            Field,
            Interface,
            Trait,
            TypeAlias,
            Module,
        ] {
            assert!(!is_callable_kind(&kind), "{kind:?} must not be callable");
        }
    }

    #[test]
    fn external_imports_never_resolve_locally() {
        // `import { primaryKey } from "drizzle-orm"` marks the call
        // external even though a local Variable shares the name.
        let g = SymbolGraph::new(vec![
            caller("a.ts", "schema", &["primaryKey"], &[], &["primaryKey"]),
            variable("b.ts", "primaryKey"),
        ]);
        assert!(g
            .callers(&Coordinate::parse("b.ts:primaryKey").unwrap())
            .is_empty());
        assert_eq!(g.resolve_stats.external, 1);
        assert_eq!(g.resolve_stats.resolved, 0);
    }

    #[test]
    fn test_file_edges_weigh_a_quarter() {
        // Quarantine: same call, test-file caller weighs 1/4 of product.
        let g = SymbolGraph::new(vec![
            caller("tests/t.rs", "t", &["hub"], &[], &[]),
            caller("src/p.rs", "p", &["hub"], &[], &[]),
            sym("src/hub.rs", "hub"),
        ]);
        let idx = |f: &str, n: &str| {
            g.symbols
                .iter()
                .position(|s| s.file == f && s.name == n)
                .unwrap()
        };
        let hub = idx("src/hub.rs", "hub");
        let wt = g.edge_weights.get(&(idx("tests/t.rs", "t"), hub)).copied().unwrap();
        let wp = g.edge_weights.get(&(idx("src/p.rs", "p"), hub)).copied().unwrap();
        assert!((wp - 1.0).abs() < 1e-9, "product weight {wp}");
        assert!((wt - TEST_EDGE_WEIGHT).abs() < 1e-9, "test weight {wt}");
    }
}
