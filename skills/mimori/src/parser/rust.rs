use crate::model::{Symbol, SymbolKind};
use anyhow::Result;
use tree_sitter::{Node, Parser};

pub fn parse_rust(file: &str, content: &str) -> Result<Vec<Symbol>> {
    let mut parser = Parser::new();
    let language = tree_sitter_rust::LANGUAGE.into();
    parser.set_language(&language)?;

    let tree = parser
        .parse(content, None)
        .ok_or_else(|| anyhow::anyhow!("Failed to parse Rust source"))?;

    let mut symbols = Vec::new();
    let root = tree.root_node();
    walk_rust_node(root, content, file, None, &mut symbols);
    let external_imports = collect_file_external_imports(root, content);
    for s in &mut symbols {
        s.external_imports = external_imports.clone();
    }

    Ok(symbols)
}

fn walk_rust_node(
    node: Node,
    content: &str,
    file: &str,
    parent_type: Option<&str>,
    symbols: &mut Vec<Symbol>,
) {
    let kind = node.kind();
    match kind {
        "function_item" => {
            if let Some(name_node) = node.child_by_field_name("name") {
                let name = node_text(name_node, content);
                let full_name = match parent_type {
                    Some(pt) => format!("{}::{}", pt, name),
                    None => name.to_string(),
                };

                let sym_kind = if parent_type.is_some() {
                    SymbolKind::Method
                } else {
                    SymbolKind::Function
                };

                let symbol = create_symbol(node, content, file, full_name, sym_kind);
                symbols.push(symbol);
            }
        }
        "struct_item" => {
            if let Some(name_node) = node.child_by_field_name("name") {
                let name = node_text(name_node, content);
                let symbol =
                    create_symbol(node, content, file, name.to_string(), SymbolKind::Struct);
                symbols.push(symbol);
            }
        }
        "enum_item" => {
            if let Some(name_node) = node.child_by_field_name("name") {
                let name = node_text(name_node, content);
                let symbol = create_symbol(node, content, file, name.to_string(), SymbolKind::Enum);
                symbols.push(symbol);
            }
        }
        "trait_item" => {
            if let Some(name_node) = node.child_by_field_name("name") {
                let name = node_text(name_node, content);
                let symbol =
                    create_symbol(node, content, file, name.to_string(), SymbolKind::Trait);
                symbols.push(symbol);

                if let Some(body) = node.child_by_field_name("body") {
                    let mut cursor = body.walk();
                    for child in body.children(&mut cursor) {
                        walk_rust_node(child, content, file, Some(name), symbols);
                    }
                }
                return;
            }
        }
        "impl_item" => {
            let type_name = node
                .child_by_field_name("type")
                .map(|t| node_text(t, content));

            if let Some(body) = node.child_by_field_name("body") {
                let mut cursor = body.walk();
                for child in body.children(&mut cursor) {
                    walk_rust_node(child, content, file, type_name, symbols);
                }
                return;
            }
        }
        "type_item" => {
            if let Some(name_node) = node.child_by_field_name("name") {
                let name = node_text(name_node, content);
                let symbol =
                    create_symbol(node, content, file, name.to_string(), SymbolKind::TypeAlias);
                symbols.push(symbol);
            }
        }
        "const_item" | "static_item" => {
            if let Some(name_node) = node.child_by_field_name("name") {
                let name = node_text(name_node, content);
                let full_name = match parent_type {
                    Some(pt) => format!("{}::{}", pt, name),
                    None => name.to_string(),
                };
                let symbol = create_symbol(node, content, file, full_name, SymbolKind::Constant);
                symbols.push(symbol);
            }
        }
        _ => {}
    }

    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        walk_rust_node(child, content, file, parent_type, symbols);
    }
}

fn create_symbol(node: Node, content: &str, file: &str, name: String, kind: SymbolKind) -> Symbol {
    let start_pos = node.start_position();
    let end_pos = node.end_position();
    let body = node_text(node, content).to_string();
    let signature = extract_signature(&body, &kind);

    let mut calls = Vec::new();
    let mut mentions = Vec::new();
    let mut call_counts = std::collections::HashMap::new();
    let mut member_calls = Vec::new();
    collect_references(
        node,
        content,
        &mut calls,
        &mut mentions,
        &mut call_counts,
        &mut member_calls,
    );

    Symbol {
        name,
        kind,
        file: file.to_string(),
        start_line: start_pos.row + 1,
        end_line: end_pos.row + 1,
        signature,
        body,
        centrality: 0.0,
        calls,
        mentions,
        call_counts,
        member_calls,
        external_imports: Vec::new(),
    }
}

fn collect_references(
    node: Node,
    content: &str,
    calls: &mut Vec<String>,
    mentions: &mut Vec<String>,
    counts: &mut std::collections::HashMap<String, u32>,
    member_calls: &mut Vec<String>,
) {
    fn push_call(
        calls: &mut Vec<String>,
        counts: &mut std::collections::HashMap<String, u32>,
        member_calls: &mut Vec<String>,
        name: &str,
        is_member: bool,
    ) {
        let name = name.trim();
        if name.is_empty() {
            return;
        }
        *counts.entry(name.to_string()).or_insert(0) += 1;
        if !calls.contains(&name.to_string()) {
            calls.push(name.to_string());
        }
        if is_member && !member_calls.contains(&name.to_string()) {
            member_calls.push(name.to_string());
        }
    }
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "call_expression" {
            if let Some(func_node) = child.child_by_field_name("function") {
                let text = node_text(func_node, content);
                // A `field_expression` function (`self.save`, `x.build()`)
                // is a member call: file-local only. Bare paths may
                // resolve workspace-wide.
                let is_member = func_node.kind() == "field_expression";
                let func_name = text.rsplit(['.', ':']).next().unwrap_or(text).trim();
                push_call(calls, counts, member_calls, func_name, is_member);
                // P0 parity: `S::new()` / `S::default()` constructs `S`.
                // Qualified refs are bare evidence (exact-name resolution).
                if func_name == "new" || func_name == "default" {
                    if let Some(sep) = text.rfind("::").or_else(|| text.rfind('.')) {
                        let ty = text[..sep].rsplit([':', '.']).next().unwrap_or("").trim();
                        if !ty.is_empty() && ty != "self" && ty != "Self" {
                            push_call(calls, counts, member_calls, ty, false);
                            push_call(
                                calls,
                                counts,
                                member_calls,
                                &format!("{}::{}", ty, func_name),
                                false,
                            );
                        }
                    }
                }
            }
        } else if child.kind() == "type_identifier" {
            let tname = node_text(child, content).trim();
            if !tname.is_empty() && !mentions.contains(&tname.to_string()) {
                mentions.push(tname.to_string());
            }
        }
        collect_references(child, content, calls, mentions, counts, member_calls);
    }
}

fn extract_signature(body: &str, kind: &SymbolKind) -> String {
    let first_line = body.lines().next().unwrap_or("").trim();

    // A const or static has no body to split off, and its initializer may well
    // contain a brace inside a string literal. The other three parsers already
    // special-case these; rust.rs ignored `kind` entirely and truncated
    // `const B: &str = "{ .. }"` to `const B: &str = "`.
    if matches!(kind, SymbolKind::Constant | SymbolKind::Variable) {
        return first_line.to_string();
    }

    if let Some(idx) = body.find('{') {
        let sig = body[..idx].trim().replace('\n', " ");
        if !sig.is_empty() {
            return sig;
        }
    }
    first_line.to_string()
}

fn node_text<'a>(node: Node, content: &'a str) -> &'a str {
    &content[node.start_byte()..node.end_byte()]
}

/// Local names bound by `use` to anything outside the current crate's
/// namespace (`crate::`, `self::`, `super::` stay local). `std::` and third-
/// party crates can never be workspace symbols, so calls matching these
/// names are external, never resolved locally.
fn collect_file_external_imports(root: Node, content: &str) -> Vec<String> {
    let mut out = Vec::new();
    collect_uses(root, content, &mut out);
    out
}

fn collect_uses(node: Node, content: &str, out: &mut Vec<String>) {
    if node.kind() == "use_declaration" {
        let mut text = node_text(node, content).trim();
        if let Some(rest) = text.strip_prefix("pub") {
            text = rest.trim();
            if let Some(rest_paren) = text.strip_prefix('(') {
                if let Some((_, after)) = rest_paren.split_once(')') {
                    text = after.trim();
                }
            }
        }
        let tree = text
            .strip_prefix("use")
            .unwrap_or(text)
            .trim()
            .trim_end_matches(';')
            .trim();
        if !(tree.starts_with("crate")
            || tree.starts_with("self")
            || tree.starts_with("super"))
        {
            collect_use_tree_names(tree, out);
        }
        return;
    }
    let mut cursor = node.walk();
    for c in node.children(&mut cursor) {
        collect_uses(c, content, out);
    }
}

fn split_top_level_comma(s: &str) -> Vec<&str> {
    let mut parts = Vec::new();
    let mut depth = 0;
    let mut start = 0;
    for (i, c) in s.char_indices() {
        match c {
            '{' => depth += 1,
            '}' => {
                if depth > 0 {
                    depth -= 1;
                }
            }
            ',' if depth == 0 => {
                parts.push(&s[start..i]);
                start = i + 1;
            }
            _ => {}
        }
    }
    if start <= s.len() {
        parts.push(&s[start..]);
    }
    parts
}

/// Last-segment binding of each imported path; `{a, b as c}` enumerates.
/// Glob (`*`) binds nothing enumerable — its names stay resolvable, a
/// documented recall-over-precision choice for globs.
fn collect_use_tree_names(tree: &str, out: &mut Vec<String>) {
    let tree = tree.trim();
    if let Some((_head, rest)) = tree.split_once('{') {
        let rest = rest.trim_end_matches('}');
        for part in split_top_level_comma(rest) {
            let part = part.trim();
            if part.is_empty() || part == "*" {
                continue;
            }
            if let Some((_, alias)) = part.split_once(" as ") {
                push_import(out, alias.trim());
            } else if part.contains('{') {
                collect_use_tree_names(part, out);
            } else {
                let last = part.rsplit("::").next().unwrap_or(part).trim();
                push_import(out, last);
            }
        }
        return;
    }
    if tree == "*" {
        return;
    }
    if let Some((_path, alias)) = tree.split_once(" as ") {
        push_import(out, alias.trim());
    } else {
        let last = tree.rsplit("::").next().unwrap_or(tree).trim();
        push_import(out, last);
    }
}

fn push_import(out: &mut Vec<String>, name: &str) {
    let name = name.trim();
    if !name.is_empty() && name != "*" && !out.contains(&name.to_string()) {
        out.push(name.to_string());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn refs_of(src: &str, symbol: &str) -> Vec<String> {
        parse_rust("t.rs", src)
            .unwrap()
            .into_iter()
            .find(|s| s.name == symbol)
            .unwrap_or_else(|| panic!("no symbol {symbol}"))
            .calls
    }

    #[test]
    fn method_calls_are_recorded_as_references() {
        // Regression M4: `self.save(1)` recorded the literal "self.save",
        // which matched no symbol, so method calls produced no edges at all.
        let src = "struct S; impl S { fn save(&self) {} fn run(&self) { self.save(); } }";
        assert!(
            refs_of(src, "S::run").contains(&"save".to_string()),
            "got {:?}",
            refs_of(src, "S::run")
        );
    }

    #[test]
    fn const_signatures_are_not_cut_at_a_brace_in_a_string() {
        // Regression S4: extract_signature ignored `kind` in this parser only,
        // truncating at the first '{' wherever it appeared.
        let src = r#"pub const BRACE: &str = "{ not a body }";"#;
        let sym = parse_rust("t.rs", src)
            .unwrap()
            .into_iter()
            .find(|s| s.name == "BRACE")
            .unwrap();
        assert!(
            sym.signature.contains("not a body"),
            "got: {}",
            sym.signature
        );
    }

    #[test]
    fn function_signatures_still_stop_at_the_body() {
        let src = "pub fn real(x: u64) -> u64 { x }";
        let sym = parse_rust("t.rs", src)
            .unwrap()
            .into_iter()
            .find(|s| s.name == "real")
            .unwrap();
        assert_eq!(sym.signature, "pub fn real(x: u64) -> u64");
    }

    #[test]
    fn path_calls_still_resolve_to_the_final_segment() {
        let src = "fn f() { crate::db::query_user(); }";
        assert!(refs_of(src, "f").contains(&"query_user".to_string()));
    }

    #[test]
    fn chained_method_calls_record_the_called_method() {
        let src = "fn f() { let x = builder().with_name().finish(); }";
        let refs = refs_of(src, "f");
        assert!(refs.contains(&"finish".to_string()), "got {refs:?}");
    }

    #[test]
    fn nested_use_trees_extract_clean_identifiers() {
        let src = "use std::{collections::{HashMap, HashSet}, io};\nfn f() { let _ = HashMap::new(); }";
        let syms = parse_rust("t.rs", src).unwrap();
        let f = syms.iter().find(|s| s.name == "f").unwrap();
        assert!(f.external_imports.contains(&"HashMap".to_string()), "got {:?}", f.external_imports);
        assert!(f.external_imports.contains(&"HashSet".to_string()), "got {:?}", f.external_imports);
        assert!(f.external_imports.contains(&"io".to_string()), "got {:?}", f.external_imports);
        assert!(!f.external_imports.iter().any(|s| s.contains('}')), "trailing brace found: {:?}", f.external_imports);
    }

    #[test]
    fn pub_use_crate_is_not_external_import() {
        let src = "pub use crate::db::query_user;\npub(crate) use crate::model::User;\nuse anyhow::Result;\nfn f() {}";
        let syms = parse_rust("t.rs", src).unwrap();
        let f = syms.iter().find(|s| s.name == "f").unwrap();
        assert!(!f.external_imports.contains(&"query_user".to_string()), "crate export misflagged as external: {:?}", f.external_imports);
        assert!(!f.external_imports.contains(&"User".to_string()), "crate export misflagged as external: {:?}", f.external_imports);
        assert!(f.external_imports.contains(&"Result".to_string()), "external anyhow::Result missing: {:?}", f.external_imports);
    }
}

