use crate::model::{Symbol, SymbolKind};
use crate::workspace::AliasSet;
use anyhow::Result;
use tree_sitter::{Node, Parser};

pub fn parse_typescript(
    file: &str,
    content: &str,
    is_tsx: bool,
    aliases: &AliasSet,
) -> Result<Vec<Symbol>> {
    let mut parser = Parser::new();
    let language = if is_tsx {
        tree_sitter_typescript::LANGUAGE_TSX.into()
    } else {
        tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into()
    };
    parser.set_language(&language)?;

    let tree = parser
        .parse(content, None)
        .ok_or_else(|| anyhow::anyhow!("Failed to parse TypeScript source"))?;

    let mut symbols = Vec::new();
    let root = tree.root_node();
    walk_ts_node(root, content, file, None, &mut symbols);
    // File-level facts stamp every symbol identically; threading them
    // through the walker would churn a dozen call sites for no gain.
    let external_imports = collect_external_imports(root, content, aliases);
    for s in &mut symbols {
        s.external_imports = external_imports.clone();
    }

    Ok(symbols)
}

fn walk_ts_node(
    node: Node,
    content: &str,
    file: &str,
    parent_class: Option<&str>,
    symbols: &mut Vec<Symbol>,
) {
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
        "class_declaration" => {
            if let Some(name_node) = node.child_by_field_name("name") {
                let name = node_text(name_node, content);
                let symbol =
                    create_symbol(node, content, file, name.to_string(), SymbolKind::Class);
                symbols.push(symbol);

                if let Some(body) = node.child_by_field_name("body") {
                    let mut cursor = body.walk();
                    for child in body.children(&mut cursor) {
                        walk_ts_node(child, content, file, Some(name), symbols);
                    }
                }
                return;
            }
        }
        "interface_declaration" => {
            if let Some(name_node) = node.child_by_field_name("name") {
                let name = node_text(name_node, content);
                let symbol =
                    create_symbol(node, content, file, name.to_string(), SymbolKind::Interface);
                symbols.push(symbol);
            }
        }
        "type_alias_declaration" => {
            if let Some(name_node) = node.child_by_field_name("name") {
                let name = node_text(name_node, content);
                let symbol =
                    create_symbol(node, content, file, name.to_string(), SymbolKind::TypeAlias);
                symbols.push(symbol);
            }
        }
        "enum_declaration" => {
            if let Some(name_node) = node.child_by_field_name("name") {
                let name = node_text(name_node, content);
                let symbol = create_symbol(node, content, file, name.to_string(), SymbolKind::Enum);
                symbols.push(symbol);
            }
        }
        "method_definition" => {
            if let Some(name_node) = node.child_by_field_name("name") {
                let name = node_text(name_node, content);
                let full_name = match parent_class {
                    Some(pc) => format!("{}::{}", pc, name),
                    None => name.to_string(),
                };
                let symbol = create_symbol(node, content, file, full_name, SymbolKind::Method);
                symbols.push(symbol);
                // P1: `constructor(private cmd: ...)` declares a field.
                // Tree-sitter marks these as required/optional parameters
                // carrying an accessibility modifier.
                if name == "constructor" {
                    if let Some(pc) = parent_class {
                        extract_ctor_param_fields(node, content, file, pc, symbols);
                    }
                }
            }
        }
        "variable_declarator" => {
            if let (Some(name_node), Some(value_node)) = (
                node.child_by_field_name("name"),
                node.child_by_field_name("value"),
            ) {
                let name = node_text(name_node, content);
                let val_kind = value_node.kind();
                if val_kind == "arrow_function" || val_kind == "function" {
                    let symbol =
                        create_symbol(node, content, file, name.to_string(), SymbolKind::Function);
                    symbols.push(symbol);
                } else if val_kind == "call_expression" {
                    let symbol =
                        create_symbol(node, content, file, name.to_string(), SymbolKind::Variable);
                    symbols.push(symbol);

                    if let Some(args_node) = value_node.child_by_field_name("arguments") {
                        let mut arg_cursor = args_node.walk();
                        for arg in args_node.children(&mut arg_cursor) {
                            if arg.kind() == "object" {
                                extract_object_literal_members(arg, content, file, name, symbols);
                            }
                        }
                        // Return as the class_declaration arm does. Falling
                        // through let the generic recursion revisit the same
                        // method_definition nodes with parent_class = None,
                        // emitting every shorthand method a second time.
                        return;
                    }
                } else if val_kind == "template_string" {
                    // P1b: `const commandWithLog = `(${cmd}) >> ...`` is a
                    // coarse dataflow node: its template mentions flow into
                    // it, and call args carrying it flow out to exec sinks.
                    let symbol =
                        create_symbol(node, content, file, name.to_string(), SymbolKind::Variable);
                    symbols.push(symbol);
                } else if is_top_level_const(node, content) {
                    let symbol =
                        create_symbol(node, content, file, name.to_string(), SymbolKind::Constant);
                    symbols.push(symbol);
                }
            }
        }
        _ if kind.ends_with("field_definition") => {
            // P1: `command: string;` / `readonly x = 1` inside a class body.
            // SEXP: (public_field_definition name: (property_identifier)).
            if let Some(pc) = parent_class {
                if let Some(name_node) = node.child_by_field_name("name") {
                    let fname = node_text(name_node, content).trim();
                    if !fname.is_empty() {
                        let full_name = format!("{}::{}", pc, fname);
                        let symbol =
                            create_symbol(node, content, file, full_name, SymbolKind::Field);
                        symbols.push(symbol);
                    }
                }
            }
        }
        _ => {}
    }

    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        walk_ts_node(child, content, file, parent_class, symbols);
    }
}

/// Index `constructor(private cmd: string)` parameter properties as fields.
fn extract_ctor_param_fields(
    ctor_node: Node,
    content: &str,
    file: &str,
    parent_class: &str,
    symbols: &mut Vec<Symbol>,
) {
    let Some(params) = ctor_node.child_by_field_name("parameters") else {
        return;
    };
    let mut cursor = params.walk();
    for param in params.children(&mut cursor) {
        let pk = param.kind();
        if pk != "required_parameter" && pk != "optional_parameter" {
            continue;
        }
        // A parameter property carries an explicit accessibility modifier
        // (`private`/`public`/`protected`/`readonly`). Plain parameters do not
        // become fields and must not be indexed.
        let mut has_modifier = false;
        let mut pcursor = param.walk();
        for c in param.children(&mut pcursor) {
            if c.kind() == "accessibility_modifier" || c.kind() == "readonly" {
                has_modifier = true;
                break;
            }
        }
        if !has_modifier {
            continue;
        }
        if let Some(pat) = param.child_by_field_name("pattern") {
            let fname = node_text(pat, content).trim();
            if !fname.is_empty() {
                let full_name = format!("{}::{}", parent_class, fname);
                let symbol = create_symbol(param, content, file, full_name, SymbolKind::Field);
                symbols.push(symbol);
            }
        }
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
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "call_expression" {
            if let Some(func_node) = child.child_by_field_name("function") {
                // `X.foo()` is a member call: weakest evidence, file-local
                // only. Bare `foo()` may resolve workspace-wide.
                let is_member = func_node.kind() == "member_expression";
                let text = node_text(func_node, content);
                let func_name = text.rsplit('.').next().unwrap_or(text).trim();
                push_call(calls, counts, member_calls, func_name, is_member);
            }
            // Call args are value mentions, not call edges.
            if let Some(args_node) = child.child_by_field_name("arguments") {
                collect_value_mentions(args_node, content, mentions);
            }
        } else if child.kind() == "new_expression" {
            // `new ExecError(...)` is a caller edge to the class and ctor.
            // Constructor position, not a receiver call: bare evidence.
            if let Some(ctor) = child.child_by_field_name("constructor") {
                let text = node_text(ctor, content);
                let short = text.rsplit(['.', ':']).next().unwrap_or(text).trim();
                if !short.is_empty() && short != "super" {
                    push_call(calls, counts, member_calls, short, false);
                    push_call(
                        calls,
                        counts,
                        member_calls,
                        &format!("{}::constructor", short),
                        false,
                    );
                }
            }
        } else if child.kind() == "member_expression" {
            // `error.command` outside a call is a property mention, not a
            // call edge. Edges to fields inflated fan-in (serverId/input).
            if let Some(prop) = child.child_by_field_name("property") {
                push_mention(mentions, node_text(prop, content).trim());
            }
        } else if child.kind() == "template_substitution" {
            // Template interpolation identifiers are mentions, not calls.
            collect_value_mentions(child, content, mentions);
        } else if child.kind() == "type_identifier" {
            let tname = node_text(child, content).trim();
            push_mention(mentions, tname);
        }
        collect_references(child, content, calls, mentions, counts, member_calls);
    }
}

fn push_call(
    calls: &mut Vec<String>,
    counts: &mut std::collections::HashMap<String, u32>,
    member_calls: &mut Vec<String>,
    name: &str,
    is_member: bool,
) {
    let name = name.trim();
    if name.is_empty() || name == "this" || name == "super" {
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

fn push_mention(mentions: &mut Vec<String>, name: &str) {
    let name = name.trim();
    if name.is_empty() || name == "this" || name == "super" {
        return;
    }
    if !mentions.contains(&name.to_string()) {
        mentions.push(name.to_string());
    }
}

/// Collect identifier and member-property mentions in a value position
/// (call arguments, template interpolations). Walks the subtree pushing
/// bare `identifier` names and `member_expression` properties, so
/// `execAsync(commandWithLog)` records `commandWithLog` and
/// `f(error.command)` records `command`.
fn collect_value_mentions(node: Node, content: &str, mentions: &mut Vec<String>) {
    let kind = node.kind();
    if kind == "identifier" {
        push_mention(mentions, node_text(node, content).trim());
    } else if kind == "member_expression" {
        if let Some(prop) = node.child_by_field_name("property") {
            push_mention(mentions, node_text(prop, content).trim());
        }
    }
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        collect_value_mentions(child, content, mentions);
    }
}

fn extract_object_literal_members(
    object_node: Node,
    content: &str,
    file: &str,
    parent_name: &str,
    symbols: &mut Vec<Symbol>,
) {
    let mut cursor = object_node.walk();
    for child in object_node.children(&mut cursor) {
        if child.kind() == "pair" {
            if let Some(key_node) = child.child_by_field_name("key") {
                let key_name = node_text(key_node, content)
                    .trim_matches('\'')
                    .trim_matches('"');
                if !key_name.is_empty() {
                    // `input: fileStream` is data, not a callable: only a
                    // function-valued property keeps Method kind, so
                    // non-callables can never be call targets.
                    let callable = child.child_by_field_name("value").is_some_and(|v| {
                        matches!(
                            v.kind(),
                            "arrow_function" | "function_expression" | "function"
                        )
                    });
                    let full_name = format!("{}::{}", parent_name, key_name);
                    let kind = if callable {
                        SymbolKind::Method
                    } else {
                        SymbolKind::Field
                    };
                    let symbol = create_symbol(child, content, file, full_name, kind);
                    symbols.push(symbol);
                }
            }
        } else if child.kind() == "method_definition" {
            if let Some(name_node) = child.child_by_field_name("name") {
                let key_name = node_text(name_node, content)
                    .trim_matches('\'')
                    .trim_matches('"');
                if !key_name.is_empty() {
                    let full_name = format!("{}::{}", parent_name, key_name);
                    let symbol = create_symbol(child, content, file, full_name, SymbolKind::Method);
                    symbols.push(symbol);
                }
            }
        }
    }
}

fn is_top_level_const(node: Node, content: &str) -> bool {
    if let Some(parent) = node.parent() {
        if parent.kind() == "lexical_declaration" {
            let decl_text = node_text(parent, content).trim_start();
            if decl_text.starts_with("const ") {
                if let Some(grandparent) = parent.parent() {
                    return grandparent.kind() == "export_statement"
                        || grandparent.kind() == "program";
                }
                return true;
            }
        }
    }
    false
}

fn extract_signature(body: &str, kind: &SymbolKind) -> String {
    let first_line = body.lines().next().unwrap_or("").trim();
    match kind {
        SymbolKind::Constant | SymbolKind::Variable => first_line.to_string(),
        _ => {
            if let Some(idx) = body.find('{') {
                let sig = body[..idx].trim().replace('\n', " ");
                if !sig.is_empty() && sig.len() < 120 {
                    return sig;
                }
            }
            first_line.to_string()
        }
    }
}

fn node_text<'a>(node: Node, content: &'a str) -> &'a str {
    &content[node.start_byte()..node.end_byte()]
}

/// Local names bound to modules outside this workspace in this file.
/// `import { primaryKey } from "drizzle-orm"` binds `primaryKey`;
/// `import x from "./local"` binds nothing (resolvable locally), and neither
/// does `import { y } from "@app/server/y"` when `@app/*` is a declared
/// `compilerOptions.paths` alias -- an alias is this workspace's own code.
fn collect_external_imports(root: Node, content: &str, aliases: &AliasSet) -> Vec<String> {
    let mut out = Vec::new();
    let mut cursor = root.walk();
    for child in root.children(&mut cursor) {
        if child.kind() != "import_statement" {
            continue;
        }
        let Some(source) = child.child_by_field_name("source") else {
            continue;
        };
        let spec = node_text(source, content).trim_matches(['\'', '"', ' ']);
        if spec.starts_with('.') || spec.starts_with('/') {
            continue;
        }
        if aliases.matches(spec) {
            continue;
        }
        let mut ccursor = child.walk();
        for item in child.children(&mut ccursor) {
            collect_import_binding(item, content, &mut out);
        }
    }
    out
}

fn collect_import_binding(node: Node, content: &str, out: &mut Vec<String>) {
    match node.kind() {
        "default_import" | "namespace_import" | "identifier" => {
            push_unique(out, node_text(node, content).trim());
        }
        "import_specifier" => {
            // `import { a as b }`: the local binding is the alias.
            let local = node
                .child_by_field_name("alias")
                .or_else(|| node.child_by_field_name("name"));
            if let Some(n) = local {
                push_unique(out, node_text(n, content).trim());
            }
        }
        _ => {
            let mut cursor = node.walk();
            for c in node.children(&mut cursor) {
                collect_import_binding(c, content, out);
            }
        }
    }
}

fn push_unique(out: &mut Vec<String>, name: &str) {
    let name = name.trim();
    if !name.is_empty() && !out.contains(&name.to_string()) {
        out.push(name.to_string());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// No aliases configured: relative and package importers only.
    fn no_aliases() -> AliasSet {
        AliasSet::default()
    }

    #[test]
    fn data_pairs_are_fields_not_methods() {
        // `input: fileStream` is data — only function-valued properties
        // keep Method kind, so non-callables are never call targets.
        let src =
            "export const r = makeRouter({\n  input: fileStream,\n  run: async () => {},\n});";
        let syms = parse_typescript("r.ts", src, false, &no_aliases()).unwrap();
        let kind_of = |n: &str| {
            syms.iter()
                .find(|s| s.name == n)
                .unwrap_or_else(|| panic!("no {n}"))
                .kind
                .clone()
        };
        assert_eq!(kind_of("r::input"), SymbolKind::Field);
        assert_eq!(kind_of("r::run"), SymbolKind::Method);
    }

    #[test]
    fn member_calls_are_flagged_bare_calls_are_not() {
        let src = "export function f(x: any) { x.input(); return bare(); }";
        let syms = parse_typescript("f.ts", src, false, &no_aliases()).unwrap();
        let f = syms.iter().find(|s| s.name == "f").unwrap();
        assert!(
            f.calls.contains(&"input".to_string()),
            "calls: {:?}",
            f.calls
        );
        assert!(
            f.calls.contains(&"bare".to_string()),
            "calls: {:?}",
            f.calls
        );
        assert!(f.member_calls.contains(&"input".to_string()));
        assert!(!f.member_calls.contains(&"bare".to_string()));
    }

    /// Three-way, not two-way: relative, path alias, package. The old
    /// two-way version of this test passed while every monorepo alias was
    /// misread as an npm package (see
    /// `reports/call-graph-edge-loss-path-aliases.md`).
    #[test]
    fn import_classification_is_three_way() {
        let src = concat!(
            "import { primaryKey } from \"drizzle-orm\";\n",
            "import { x } from \"./local\";\n",
            "import { aliased } from \"@app/server/services/permission\";\n",
            "export function f() { return primaryKey(); }"
        );
        let aliases = AliasSet::for_test(&["@app/server/*"]);
        let syms = parse_typescript("f.ts", src, false, &aliases).unwrap();
        let f = syms.iter().find(|s| s.name == "f").unwrap();
        let imported = &f.external_imports;
        assert!(
            imported.contains(&"primaryKey".to_string()),
            "package binding must stay external: {imported:?}"
        );
        assert!(
            !imported.contains(&"x".to_string()),
            "relative binding must stay internal: {imported:?}"
        );
        assert!(
            !imported.contains(&"aliased".to_string()),
            "path-alias binding must be internal: {imported:?}"
        );
    }

    /// Without the alias declaration the same import is a package again —
    /// the gate is driven by tsconfig, not by the specifier's shape.
    #[test]
    fn alias_binding_is_external_when_no_alias_is_declared() {
        let src = "import { aliased } from \"@app/server/services/permission\";\nexport function f() { return aliased(); }";
        let syms = parse_typescript("f.ts", src, false, &no_aliases()).unwrap();
        let f = syms.iter().find(|s| s.name == "f").unwrap();
        assert!(f.external_imports.contains(&"aliased".to_string()));
    }
}
