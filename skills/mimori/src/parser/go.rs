use crate::model::{Symbol, SymbolKind};
use anyhow::Result;
use tree_sitter::{Node, Parser};

pub fn parse_go(file: &str, content: &str) -> Result<Vec<Symbol>> {
    let mut parser = Parser::new();
    let language = tree_sitter_go::LANGUAGE.into();
    parser.set_language(&language)?;

    let tree = parser
        .parse(content, None)
        .ok_or_else(|| anyhow::anyhow!("Failed to parse Go source"))?;

    let mut symbols = Vec::new();
    let root = tree.root_node();
    walk_go_node(root, content, file, &mut symbols);
    let external_imports = collect_file_external_imports(root, content);
    for s in &mut symbols {
        s.external_imports = external_imports.clone();
    }

    Ok(symbols)
}

fn walk_go_node(node: Node, content: &str, file: &str, symbols: &mut Vec<Symbol>) {
    let kind = node.kind();
    match kind {
        "function_declaration" => {
            if let Some(name_node) = node.child_by_field_name("name") {
                let name = node_text(name_node, content);
                let symbol =
                    create_symbol(node, content, file, name.to_string(), SymbolKind::Function);
                symbols.push(symbol);
            }
        }
        "method_declaration" => {
            if let Some(name_node) = node.child_by_field_name("name") {
                let name = node_text(name_node, content);
                let receiver_type = extract_receiver_type(node, content);
                let full_name = match receiver_type {
                    Some(rt) => format!("{}::{}", rt, name),
                    None => name.to_string(),
                };

                let symbol = create_symbol(node, content, file, full_name, SymbolKind::Method);
                symbols.push(symbol);
            }
        }
        "type_declaration" => {
            let mut cursor = node.walk();
            for child in node.children(&mut cursor) {
                if child.kind() == "type_spec" {
                    if let Some(name_node) = child.child_by_field_name("name") {
                        let name = node_text(name_node, content);
                        let type_node = child.child_by_field_name("type");
                        let sym_kind = match type_node.map(|t| t.kind()) {
                            Some("struct_type") => SymbolKind::Struct,
                            Some("interface_type") => SymbolKind::Interface,
                            _ => SymbolKind::TypeAlias,
                        };

                        let symbol = create_symbol(node, content, file, name.to_string(), sym_kind);
                        symbols.push(symbol);
                    }
                }
            }
        }
        "const_declaration" => {
            let mut cursor = node.walk();
            for child in node.children(&mut cursor) {
                if child.kind() == "const_spec" {
                    if let Some(name_node) = child.child_by_field_name("name") {
                        let name = node_text(name_node, content);
                        let symbol = create_symbol(
                            child,
                            content,
                            file,
                            name.to_string(),
                            SymbolKind::Constant,
                        );
                        symbols.push(symbol);
                    }
                }
            }
        }
        _ => {}
    }

    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        walk_go_node(child, content, file, symbols);
    }
}

fn extract_receiver_type<'a>(node: Node<'a>, content: &'a str) -> Option<&'a str> {
    let receiver = node.child_by_field_name("receiver")?;
    let mut cursor = receiver.walk();
    for child in receiver.children(&mut cursor) {
        if child.kind() == "parameter_declaration" {
            if let Some(type_node) = child.child_by_field_name("type") {
                let mut t = node_text(type_node, content);
                if t.starts_with('*') {
                    t = &t[1..];
                }
                return Some(t);
            }
        }
    }
    None
}

fn create_symbol(node: Node, content: &str, file: &str, name: String, kind: SymbolKind) -> Symbol {
    let start_pos = node.start_position();
    let end_pos = node.end_position();
    let body = node_text(node, content).to_string();
    let signature = extract_signature(&body);

    let mut calls = Vec::new();
    let mentions = Vec::new();
    let mut call_counts = std::collections::HashMap::new();
    let mut member_calls = Vec::new();
    collect_references(node, content, &mut calls, &mut call_counts, &mut member_calls);

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
    counts: &mut std::collections::HashMap<String, u32>,
    member_calls: &mut Vec<String>,
) {
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "call_expression" {
            if let Some(func_node) = child.child_by_field_name("function") {
                // `x.F()` (selector) is a member call: file-local only.
                let is_member = func_node.kind() == "selector_expression";
                let text = node_text(func_node, content);
                let func_name = text.rsplit('.').next().unwrap_or(text).trim();
                if !func_name.is_empty() {
                    *counts.entry(func_name.to_string()).or_insert(0) += 1;
                    if !calls.contains(&func_name.to_string()) {
                        calls.push(func_name.to_string());
                    }
                    if is_member && !member_calls.contains(&func_name.to_string()) {
                        member_calls.push(func_name.to_string());
                    }
                }
            }
        }
        collect_references(child, content, calls, counts, member_calls);
    }
}


fn extract_signature(body: &str) -> String {
    let first_line = body.lines().next().unwrap_or("").trim();
    if let Some(idx) = body.find('{') {
        let sig = body[..idx].trim().replace('\n', " ");
        if !sig.is_empty() {
            return sig;
        }
    }
    first_line.to_string()
}

/// Local names bound to stdlib (dotless-path) imports. `import "fmt"`
/// binds `fmt`; dotted paths (module + third-party) stay resolvable —
/// the local package may itself be indexed.
fn collect_file_external_imports(root: Node, content: &str) -> Vec<String> {
    let mut out = Vec::new();
    collect_go_imports(root, content, &mut out);
    out
}

fn collect_go_imports(node: Node, content: &str, out: &mut Vec<String>) {
    if node.kind() == "import_spec" {
        let mut name: Option<&str> = None;
        let mut path: Option<&str> = None;
        let mut cursor = node.walk();
        for c in node.children(&mut cursor) {
            match c.kind() {
                "package_identifier" | "identifier" | "blank_identifier" | "dot" => {
                    name = Some(node_text(c, content).trim());
                }
                "interpreted_string_literal" | "raw_string_literal" => {
                    path = Some(node_text(c, content).trim_matches(['"', '`', ' ']));
                }
                _ => {}
            }
        }
        if let Some(p) = path {
            if !p.contains('.') {
                match name {
                    Some("_") | Some(".") | None => {
                        if name.is_none() {
                            push_import(out, p.rsplit('/').next().unwrap_or(p));
                        }
                    }
                    Some(n) => push_import(out, n),
                }
            }
        }
        return;
    }
    let mut cursor = node.walk();
    for c in node.children(&mut cursor) {
        collect_go_imports(c, content, out);
    }
}

fn push_import(out: &mut Vec<String>, name: &str) {
    let name = name.trim();
    if !name.is_empty() && !out.contains(&name.to_string()) {
        out.push(name.to_string());
    }
}

fn node_text<'a>(node: Node, content: &'a str) -> &'a str {
    &content[node.start_byte()..node.end_byte()]
}
