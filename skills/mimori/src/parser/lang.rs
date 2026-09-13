use std::path::Path;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Language {
    Rust,
    TypeScript,
    TSX,
    JavaScript,
    Python,
    Go,
    Unknown,
}

impl Language {
    pub fn from_path(path: &Path) -> Self {
        match path.extension().and_then(|ext| ext.to_str()) {
            Some("rs") => Language::Rust,
            Some("ts") => Language::TypeScript,
            Some("tsx") => Language::TSX,
            Some("js") | Some("mjs") | Some("cjs") => Language::JavaScript,
            Some("jsx") => Language::TSX,
            Some("py") => Language::Python,
            Some("go") => Language::Go,
            _ => Language::Unknown,
        }
    }
}
