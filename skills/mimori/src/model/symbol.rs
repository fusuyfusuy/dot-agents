use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum SymbolKind {
    Function,
    Method,
    Struct,
    Class,
    Interface,
    Trait,
    Enum,
    TypeAlias,
    Variable,
    Module,
    Constant,
    Field,
}

impl SymbolKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            SymbolKind::Function => "function",
            SymbolKind::Method => "method",
            SymbolKind::Field => "field",
            SymbolKind::Struct => "struct",
            SymbolKind::Class => "class",
            SymbolKind::Interface => "interface",
            SymbolKind::Trait => "trait",
            SymbolKind::Enum => "enum",
            SymbolKind::TypeAlias => "type",
            SymbolKind::Variable => "variable",
            SymbolKind::Module => "module",
            SymbolKind::Constant => "constant",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Symbol {
    pub name: String,
    pub kind: SymbolKind,
    pub file: String,
    pub start_line: usize,
    pub end_line: usize,
    pub signature: String,
    pub body: String,
    #[serde(default)]
    pub centrality: f64,
    /// True call edges (call/new expressions). Alone feeds PageRank/up/blast.
    #[serde(default)]
    pub calls: Vec<String>,
    /// Non-call mentions (member properties, type names, call-arg and
    /// template-interpolation identifiers). Backs `uses`, never call edges.
    #[serde(default)]
    pub mentions: Vec<String>,
    /// Per-call-name occurrence count (multiplicity before dedup).
    /// Weight = min(sqrt(n), 8).
    #[serde(default)]
    pub call_counts: std::collections::HashMap<String, u32>,
    /// Calls made through a receiver (`X.foo()`): weakest evidence, never
    /// leaves the file. Subset of `calls`.
    #[serde(default)]
    pub member_calls: Vec<String>,
    /// Local names bound by this file to external (non-relative) modules.
    /// Calls matching one are external — never resolved locally.
    #[serde(default)]
    pub external_imports: Vec<String>,
}

impl Symbol {
    pub fn coordinate(&self) -> String {
        format!("{}:{}", self.file, self.name)
    }

    pub fn line_coordinate(&self) -> String {
        format!("{}:#L{}-{}", self.file, self.start_line, self.end_line)
    }
}
