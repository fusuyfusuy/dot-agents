use super::symbol::Symbol;
use serde::{Deserialize, Serialize};

/// Marker `SymbolGraph::build_slice` inserts before inlined `--follow-local`
/// bodies. Shared so the budgeted renderer can split them back off.
pub const FOLLOW_LOCAL_MARKER: &str = "\n\n// --- Inlined Local Callees (--follow-local) ---\n";

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SliceResult {
    pub coordinate: String,
    pub file: String,
    pub symbol: Option<Symbol>,
    pub line_range: Option<(usize, usize)>,
    pub content: String,
    pub callers: Vec<String>,
    pub callees: Vec<String>,
    pub total_lines: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub imports: Option<Vec<String>>,
}

/// Rough token estimate, calibrated per language instead of one constant.
/// Heuristic — documented in `--help`, never used for billing, only for
/// packing context. Symbol-dense Rust/Go (~3 ch/tok) tokenize finer than
/// whitespace-heavy Python (~5 ch/tok); TS/JS sit in the middle (~4).
fn chars_per_token_for(file: &str) -> usize {
    match file.rsplit('.').next().unwrap_or("") {
        "rs" | "go" => 3,
        "py" => 5,
        _ => 4,
    }
}

impl SliceResult {
    fn estimate_tokens(&self, s: &str) -> usize {
        s.len().div_ceil(chars_per_token_for(&self.file))
    }

    pub fn to_markdown(&self) -> String {
        format!(
            "{}{}{}{}{}",
            self.header_markdown(),
            self.imports_markdown(),
            self.callers_markdown(),
            self.callees_markdown(),
            self.body_markdown(true)
        )
    }

    /// Render markdown packed to roughly `budget_tokens`. The core slice
    /// (header + body) is never cut; surrounding context drops
    /// lowest-priority first: inlined locals, callees, callers, imports.
    /// A notice names what was dropped so agents can re-request it explicitly.
    pub fn to_markdown_budgeted(&self, budget_tokens: usize) -> String {
        let header = self.header_markdown();
        let core_tokens = self.estimate_tokens(&header) + self.estimate_tokens(&self.body_markdown(false));

        // Optional context, lowest priority first.
        let optional: Vec<(&str, String)> = vec![
            ("inlined local callees", self.locals_markdown()),
            ("callees", self.callees_markdown()),
            ("callers", self.callers_markdown()),
            ("imports", self.imports_markdown()),
        ]
        .into_iter()
        .filter(|(_, text)| !text.is_empty())
        .collect();

        // Drop lowest-priority sections until the budget fits.
        let mut keep = vec![true; optional.len()];
        let mut dropped: Vec<&str> = Vec::new();
        let mut total = core_tokens
            + optional
                .iter()
                .map(|(_, t)| self.estimate_tokens(t))
                .sum::<usize>();
        for (i, (name, text)) in optional.iter().enumerate() {
            if total <= budget_tokens {
                break;
            }
            total -= self.estimate_tokens(text);
            dropped.push(name);
            keep[i] = false;
        }

        let kept = |name: &str| {
            optional
                .iter()
                .position(|(n, _)| *n == name)
                .map(|i| keep[i])
                .unwrap_or(false)
        };
        let text_of = |name: &str| {
            optional
                .iter()
                .find(|(n, _)| *n == name)
                .map(|(_, t)| t.as_str())
                .unwrap_or("")
        };

        // Canonical order matches `to_markdown`: header, imports, callers,
        // callees, body.
        let mut out = header;
        if kept("imports") {
            out.push_str(text_of("imports"));
        }
        if kept("callers") {
            out.push_str(text_of("callers"));
        }
        if kept("callees") {
            out.push_str(text_of("callees"));
        }
        out.push_str(&self.body_markdown(kept("inlined local callees")));

        if total > budget_tokens {
            // Core alone exceeds the budget; everything droppable is gone.
            out.push_str(&format!(
                "\n_Core slice alone (~{total} tokens) exceeds --budget {budget_tokens}; context omitted._\n"
            ));
            if !dropped.is_empty() {
                out.push_str(&format!("_Dropped: {}._\n", dropped.join(", ")));
            }
        } else if !dropped.is_empty() {
            out.push_str(&format!(
                "\n_Truncated to fit --budget {budget_tokens} (~{total} tokens): dropped {}._\n",
                dropped.join(", ")
            ));
        } else {
            return out;
        }
        out
    }

    fn header_markdown(&self) -> String {
        let mut out = String::new();
        out.push_str(&format!("### Slice: `{}`\n\n", self.coordinate));
        out.push_str(&format!("- **File**: `{}`\n", self.file));

        if let Some(sym) = &self.symbol {
            out.push_str(&format!(
                "- **Symbol**: `{}` ({})\n",
                sym.name,
                sym.kind.as_str()
            ));
            out.push_str(&format!(
                "- **Lines**: L{}-L{}\n",
                sym.start_line, sym.end_line
            ));
            if !sym.signature.is_empty() {
                out.push_str(&format!("- **Signature**: `{}`\n", sym.signature));
            }
        } else if let Some((start, end)) = self.line_range {
            out.push_str(&format!("- **Lines**: L{}-L{}\n", start, end));
        }
        out
    }

    fn imports_markdown(&self) -> String {
        let mut out = String::new();
        if let Some(imports) = &self.imports {
            if !imports.is_empty() {
                out.push_str("- **Backing Imports**:\n```\n");
                for imp in imports {
                    out.push_str(imp);
                    out.push('\n');
                }
                out.push_str("```\n");
            }
        }
        out
    }

    fn callers_markdown(&self) -> String {
        let mut out = String::new();
        if !self.callers.is_empty() {
            out.push_str("- **1-Hop Callers**:\n");
            for c in &self.callers {
                out.push_str(&format!("  - `{}`\n", c));
            }
        }
        out
    }

    fn callees_markdown(&self) -> String {
        let mut out = String::new();
        if !self.callees.is_empty() {
            out.push_str("- **1-Hop Callees**:\n");
            for c in &self.callees {
                out.push_str(&format!("  - `{}`\n", c));
            }
        }
        out
    }

    /// Split `content` into the symbol body and the inlined `--follow-local`
    /// block (empty when `-f` was not used).
    fn split_locals(&self) -> (&str, &str) {
        match self.content.find(FOLLOW_LOCAL_MARKER) {
            Some(pos) => self.content.split_at(pos),
            None => (self.content.as_str(), ""),
        }
    }

    fn locals_markdown(&self) -> String {
        self.split_locals().1.to_string()
    }

    fn body_markdown(&self, include_locals: bool) -> String {
        let (main, locals) = self.split_locals();
        let shown = if include_locals {
            self.content.as_str()
        } else {
            main
        };
        let _ = locals;
        let mut out = String::from("\n```\n");
        out.push_str(shown);
        if !shown.ends_with('\n') {
            out.push('\n');
        }
        out.push_str("```\n");
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::SymbolKind;

    fn fixture() -> SliceResult {
        SliceResult {
            coordinate: "src/a.rs:foo".to_string(),
            file: "src/a.rs".to_string(),
            symbol: Some(Symbol {
                name: "foo".into(),
                kind: SymbolKind::Function,
                file: "src/a.rs".into(),
                start_line: 1,
                end_line: 3,
                signature: "pub fn foo()".into(),
                body: "pub fn foo() {\n  bar();\n}".into(),
                centrality: 0.0,
                calls: vec!["bar".into()],
                mentions: vec![],
                call_counts: std::collections::HashMap::from([("bar".to_string(), 1)]),
                member_calls: vec![],
                external_imports: vec![],
            }),
            line_range: Some((1, 3)),
            content: format!(
                "pub fn foo() {{\n  bar();\n}}{} \n// Symbol: `bar` (L10-L12)\nfn bar() {{}}\n",
                FOLLOW_LOCAL_MARKER
            ),
            callers: vec!["src/b.rs:caller".to_string()],
            callees: vec!["src/a.rs:bar".to_string()],
            total_lines: 3,
            imports: Some(vec!["use crate::bar;".to_string()]),
        }
    }

    #[test]
    fn unbudgeted_render_keeps_every_section_in_order() {
        let md = fixture().to_markdown();
        assert!(md.contains("### Slice: `src/a.rs:foo`"));
        assert!(md.contains("- **Backing Imports**:"));
        assert!(md.contains("- **1-Hop Callers**:"));
        assert!(md.contains("- **1-Hop Callees**:"));
        assert!(md.contains("Inlined Local Callees"));
        let imports = md.find("Backing Imports").unwrap();
        let callers = md.find("1-Hop Callers").unwrap();
        let callees = md.find("1-Hop Callees").unwrap();
        // Anchor on `bar();` — the signature line also contains `pub fn foo()`.
        let body = md.find("  bar();").unwrap();
        assert!(imports < callers && callers < callees && callees < body);
    }

    #[test]
    fn generous_budget_is_byte_identical_to_unbudgeted() {
        let s = fixture();
        assert_eq!(s.to_markdown_budgeted(100_000), s.to_markdown());
    }

    #[test]
    fn tight_budget_keeps_core_and_names_what_was_dropped() {
        let s = fixture();
        let full = s.to_markdown();
        // One token under the full estimate in the fixture's own per-language
        // terms: forces exactly the cheapest drop (inlined locals), whatever
        // the exact character counts are.
        let budget = s.estimate_tokens(&full) - 1;
        let md = s.to_markdown_budgeted(budget);
        assert!(md.contains("### Slice: `src/a.rs:foo`"), "core lost: {md}");
        assert!(md.contains("pub fn foo()"), "body lost: {md}");
        assert!(
            md.contains(&format!("Truncated to fit --budget {budget}")),
            "notice missing: {md}"
        );
        assert!(!md.contains("Inlined Local Callees"), "locals kept: {md}");
        assert!(md.contains("1-Hop Callees"), "kept section lost: {md}");
    }

    #[test]
    fn zero_budget_keeps_core_and_reports_it_exceeds() {
        let s = fixture();
        let md = s.to_markdown_budgeted(0);
        assert!(md.contains("### Slice: `src/a.rs:foo`"), "core lost: {md}");
        assert!(md.contains("exceeds --budget 0"), "notice missing: {md}");
        assert!(!md.contains("1-Hop Callers"), "context kept: {md}");
    }
}
