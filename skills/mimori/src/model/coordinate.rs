use anyhow::{bail, Context, Result};
use std::fmt;
use std::path::{Component, Path, PathBuf};

/// File extensions that make a `:`-prefix recognizable as a path rather than
/// the first segment of a qualified symbol name.
const SOURCE_EXTENSIONS: &[&str] = &["rs", "ts", "tsx", "js", "jsx", "py", "go"];

/// A parsed target coordinate.
///
/// Parsing used to be four separate `split_once(':')` calls, which read
/// `Store::save` as file `"Store"` plus name `":save"`. That matched nothing,
/// and only resolved at all because the caller fell through to a bare-name
/// search -- so the exact-coordinate path was dead for every qualified symbol.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Coordinate {
    /// `path/file.rs:#L10-50`
    Lines {
        file: PathBuf,
        start: usize,
        end: usize,
    },
    /// `path/file.rs:symbol`
    Symbol { file: PathBuf, name: String },
    /// `symbol` or `Type::method`
    Bare { name: String },
}

impl Coordinate {
    pub fn parse(raw: &str) -> Result<Self> {
        let raw = raw.trim();
        if raw.is_empty() {
            bail!("Empty coordinate.");
        }

        if let Some(idx) = raw.find(":#L") {
            let file = raw[..idx].to_string();
            if file.is_empty() {
                bail!("Line coordinate '{}' has no file part.", raw);
            }
            let (start, end) = parse_line_range(&raw[idx + 3..])?;
            return Ok(Coordinate::Lines {
                file: PathBuf::from(file),
                start,
                end,
            });
        }

        // Split on the first ':' only when what precedes it looks like a path.
        // Otherwise the whole string is a name, which keeps `Store::save` intact.
        if let Some(idx) = raw.find(':') {
            let (head, tail) = (&raw[..idx], &raw[idx + 1..]);
            if looks_like_path(head) {
                if tail.is_empty() {
                    bail!("Coordinate '{}' names a file but no symbol.", raw);
                }
                return Ok(Coordinate::Symbol {
                    file: PathBuf::from(head),
                    name: tail.to_string(),
                });
            }
        }

        Ok(Coordinate::Bare {
            name: raw.to_string(),
        })
    }

    /// Rewrite the file part into the workspace-relative form the index stores,
    /// so an absolute target and a relative one resolve identically.
    pub fn normalize_against(self, root: &Path) -> Self {
        match self {
            // A line range is read straight off disk and never looked up in the
            // index, so the path stays exactly as the user gave it.
            lines @ Coordinate::Lines { .. } => lines,
            Coordinate::Symbol { file, name } => Coordinate::Symbol {
                file: relativize(&file, root),
                name,
            },
            bare => bare,
        }
    }

    pub fn file(&self) -> Option<&Path> {
        match self {
            Coordinate::Lines { file, .. } | Coordinate::Symbol { file, .. } => Some(file),
            Coordinate::Bare { .. } => None,
        }
    }

    pub fn name(&self) -> Option<&str> {
        match self {
            Coordinate::Symbol { name, .. } | Coordinate::Bare { name } => Some(name),
            Coordinate::Lines { .. } => None,
        }
    }

    /// The directory to start searching for the workspace root from, when the
    /// coordinate carries an absolute path.
    pub fn absolute_parent(&self) -> Option<PathBuf> {
        let file = self.file()?;
        if !file.is_absolute() {
            return None;
        }
        file.parent().map(|p| p.to_path_buf())
    }
}

impl fmt::Display for Coordinate {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Coordinate::Lines { file, start, end } => {
                write!(f, "{}:#L{}-{}", file.display(), start, end)
            }
            Coordinate::Symbol { file, name } => write!(f, "{}:{}", file.display(), name),
            Coordinate::Bare { name } => write!(f, "{}", name),
        }
    }
}

fn parse_line_range(range: &str) -> Result<(usize, usize)> {
    match range.split_once('-') {
        Some((s, e)) => Ok((
            s.trim().parse().context("Invalid start line")?,
            e.trim().parse().context("Invalid end line")?,
        )),
        None => {
            let n: usize = range.trim().parse().context("Invalid line number")?;
            Ok((n, n))
        }
    }
}

fn looks_like_path(head: &str) -> bool {
    if head.contains('/') || head.contains('\\') {
        return true;
    }
    Path::new(head)
        .extension()
        .and_then(|e| e.to_str())
        .is_some_and(|ext| SOURCE_EXTENSIONS.contains(&ext))
}

/// Strip the workspace root and any `./` noise, leaving the form stored in the
/// index. A path outside the root is returned unchanged so it can still match
/// by suffix or basename.
fn relativize(file: &Path, root: &Path) -> PathBuf {
    let stripped = file.strip_prefix(root).unwrap_or(file);
    let cleaned: PathBuf = stripped
        .components()
        .filter(|c| !matches!(c, Component::CurDir))
        .collect();
    if cleaned.as_os_str().is_empty() {
        stripped.to_path_buf()
    } else {
        cleaned
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn qualified_names_are_not_mistaken_for_paths() {
        // Regression P17: split_once(':') read this as file "Store", name ":save".
        assert_eq!(
            Coordinate::parse("Store::save").unwrap(),
            Coordinate::Bare {
                name: "Store::save".into()
            }
        );
        assert_eq!(
            Coordinate::parse("handler").unwrap(),
            Coordinate::Bare {
                name: "handler".into()
            }
        );
    }

    #[test]
    fn paths_are_recognized_by_separator_or_extension() {
        assert_eq!(
            Coordinate::parse("src/a.rs:handler").unwrap(),
            Coordinate::Symbol {
                file: "src/a.rs".into(),
                name: "handler".into()
            }
        );
        assert_eq!(
            Coordinate::parse("a.rs:handler").unwrap(),
            Coordinate::Symbol {
                file: "a.rs".into(),
                name: "handler".into()
            }
        );
    }

    #[test]
    fn a_qualified_name_survives_a_file_prefix() {
        assert_eq!(
            Coordinate::parse("src/a.rs:Store::save").unwrap(),
            Coordinate::Symbol {
                file: "src/a.rs".into(),
                name: "Store::save".into()
            }
        );
    }

    #[test]
    fn line_ranges_parse_in_both_forms() {
        assert_eq!(
            Coordinate::parse("a.rs:#L10-50").unwrap(),
            Coordinate::Lines {
                file: "a.rs".into(),
                start: 10,
                end: 50
            }
        );
        assert_eq!(
            Coordinate::parse("a.rs:#L7").unwrap(),
            Coordinate::Lines {
                file: "a.rs".into(),
                start: 7,
                end: 7
            }
        );
    }

    #[test]
    fn absolute_targets_normalize_onto_stored_paths() {
        let root = Path::new("/repo");
        let c = Coordinate::parse("/repo/src/a.rs:handler")
            .unwrap()
            .normalize_against(root);
        assert_eq!(c.file().unwrap(), Path::new("src/a.rs"));

        let c = Coordinate::parse("./src/a.rs:handler")
            .unwrap()
            .normalize_against(root);
        assert_eq!(c.file().unwrap(), Path::new("src/a.rs"));
    }

    #[test]
    fn malformed_coordinates_are_errors_not_guesses() {
        assert!(Coordinate::parse("").is_err());
        assert!(Coordinate::parse("a.rs:").is_err());
        assert!(Coordinate::parse("a.rs:#Lxx").is_err());
    }
}
