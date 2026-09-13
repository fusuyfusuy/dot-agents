use ignore::WalkBuilder;
use rayon::prelude::*;
use std::ffi::OsStr;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::time::UNIX_EPOCH;

/// One source file as found on disk: its path relative to the workspace root,
/// its mtime, a content hash, and the content itself.
///
/// The hash is what decides whether a reparse is needed. mtime is carried only
/// so it can be recorded; it is never trusted for invalidation, because any
/// operation that preserves mtime while changing content (`cp -p`, `touch -r`,
/// `rsync -t`) would otherwise leave the index permanently stale.
pub struct FileScan {
    pub rel: PathBuf,
    pub mtime: i64,
    pub hash: String,
    pub content: String,
}

/// Directory names never indexed, matched as whole path components relative to
/// the workspace root. Substring matching on the absolute path would exclude an
/// entire repository that merely lives under e.g. `~/build/`.
const IGNORED_DIRS: &[&str] = &[
    "target",
    "node_modules",
    ".git",
    "dist",
    "build",
    "vendor",
    ".mimori",
];

const SUPPORTED_EXTENSIONS: &[&str] = &["rs", "ts", "tsx", "js", "jsx", "py", "go"];

/// Coverage accounting (P1-4): what the walker crawled vs indexed, so a zero
/// that means "none indexed" never presents as "none exists".
#[derive(Debug, Clone, Default)]
pub struct ScanStats {
    pub indexed_files: usize,
    pub crawled_files: usize,
    pub unindexed_exts: Vec<String>,
}

 /// Walk the workspace, then read and hash every supported file in parallel.
 ///
 /// Reading every file on every run is what makes hash-based invalidation
 /// possible: a content change cannot be detected without reading the content.
 /// Parsing, which dominates the cost, still runs only on files whose hash moved.
 pub fn scan_workspace(root: &Path) -> Vec<FileScan> {
    scan_workspace_with_stats(root).0
}

pub fn scan_workspace_with_stats(root: &Path) -> (Vec<FileScan>, ScanStats) {
     let paths = discover_workspace_files(root);
 
    let stats = ScanStats {
        indexed_files: paths.supported.len(),
        crawled_files: paths.crawled,
        unindexed_exts: paths.unindexed_exts.clone(),
    };

    let scans = paths
        .supported
         .into_par_iter()
         .filter_map(|(rel, full, mtime)| {
            let content = fs::read_to_string(&full).ok()?;
            let hash = format!("{:x}", fnv1a_hash(content.as_bytes()));
            Some(FileScan {
                rel,
                mtime,
                hash,
                content,
            })
         })
        .collect();
    (scans, stats)
 }

struct DiscoveredFiles {
    supported: Vec<(PathBuf, PathBuf, i64)>,
    crawled: usize,
    unindexed_exts: Vec<String>,
}

fn discover_workspace_files(root: &Path) -> DiscoveredFiles {
    let builder = walk_builder(root);

    let mut supported = Vec::new();
    let mut crawled = 0usize;
    let mut ext_counts: std::collections::HashMap<String, usize> = std::collections::HashMap::new();

    for entry in builder.build().flatten() {
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let rel = path.strip_prefix(root).unwrap_or(path).to_path_buf();
        if is_ignored_rel(&rel) {
            continue;
        }
        crawled += 1;
        if !has_supported_extension(path) {
            let ext = path
                .extension()
                .and_then(|e| e.to_str())
                .unwrap_or("(none)")
                .to_string();
            *ext_counts.entry(ext).or_insert(0) += 1;
            continue;
        }

        let mtime = entry
            .metadata()
            .ok()
            .and_then(|m| m.modified().ok())
            .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
            .map(|d| d.as_nanos() as i64)
            .unwrap_or(0);

        supported.push((rel, path.to_path_buf(), mtime));
    }

    let mut unindexed_exts: Vec<String> = ext_counts
        .into_iter()
        .map(|(ext, n)| format!("{ext}×{n}"))
        .collect();
    unindexed_exts.sort();
    DiscoveredFiles {
        supported,
        crawled,
        unindexed_exts,
    }
}

pub fn has_supported_extension(path: &Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .is_some_and(|ext| SUPPORTED_EXTENSIONS.contains(&ext))
}

/// True when any component of the workspace-relative path is an ignored
/// directory name.
/// The one walk configuration for every discovery pass, so `tsconfig`
/// discovery cannot drift from source discovery on ignore rules.
fn walk_builder(root: &Path) -> WalkBuilder {
    let mut builder = WalkBuilder::new(root);
    builder
        .hidden(true)
        .git_ignore(true)
        .git_global(true)
        .git_exclude(true)
        .add_custom_ignore_filename(".mimoriignore");
    builder
}

/// Workspace-owned configs that can declare `compilerOptions.paths`.
/// `node_modules` configs are ignored by the walker: a dependency's aliases
/// say nothing about this workspace's own imports.
pub fn discover_tsconfigs(root: &Path) -> Vec<PathBuf> {
    discover_named(root, |name| {
        name == "jsconfig.json" || (name.starts_with("tsconfig") && name.ends_with(".json"))
    })
}

/// Workspace-owned package manifests. Their `name` fields are how a pnpm /
/// Turborepo / Nx workspace addresses its own packages (`@acme/core`), which
/// is first-party code that no `tsconfig` alias has to declare.
pub fn discover_package_manifests(root: &Path) -> Vec<PathBuf> {
    discover_named(root, |name| name == "package.json")
}

fn discover_named(root: &Path, accept: impl Fn(&str) -> bool) -> Vec<PathBuf> {
    let mut out = Vec::new();
    for entry in walk_builder(root).build().flatten() {
        let path = entry.path();
        if !path.is_file() || is_ignored_rel(path.strip_prefix(root).unwrap_or(path)) {
            continue;
        }
        let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
            continue;
        };
        if accept(name) {
            out.push(path.to_path_buf());
        }
    }
    out.sort();
    out
}

/// True when any component of the workspace-relative path is an ignored
/// directory name.
pub fn is_ignored_rel(rel: &Path) -> bool {
    rel.components().any(|c| match c {
        Component::Normal(name) => IGNORED_DIRS.iter().any(|d| OsStr::new(d) == name),
        _ => false,
    })
}

pub fn fnv1a_hash(bytes: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf29ce484222325;
    for &b in bytes {
        hash ^= b as u64;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    hash
}

/// Find the workspace root for a target.
///
/// 1. Walk up from the target's directory for a `.mimori`, then a VCS root.
/// 2. Otherwise, if the cwd contains the target, use the cwd.
/// 3. Otherwise fall back to the target's own directory.
///
/// The previous rule was step 3 unconditionally, so an absolute coordinate
/// inside a repository silently indexed only that one directory -- hiding
/// callers elsewhere and writing a stray SQLite index into a source folder.
/// Step 3 survives only for a target that lies outside any known workspace.
pub fn find_workspace_root(target_dir: Option<&Path>, cwd: &Path) -> PathBuf {
    let Some(target_dir) = target_dir else {
        return cwd.to_path_buf();
    };

    for marker in [".mimori", ".git"] {
        let mut dir = Some(target_dir);
        while let Some(d) = dir {
            if d.join(marker).exists() {
                return d.to_path_buf();
            }
            dir = d.parent();
        }
    }

    if target_dir.starts_with(cwd) {
        return cwd.to_path_buf();
    }

    target_dir.to_path_buf()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ignores_match_components_not_substrings() {
        assert!(is_ignored_rel(Path::new("target/debug/x.rs")));
        assert!(is_ignored_rel(Path::new("a/node_modules/b/c.js")));
        assert!(is_ignored_rel(Path::new(".mimori/index.rs")));

        // Regression M14: a repo living under a path containing an ignored name
        // must still be indexed. These are workspace-relative, so the parent
        // directory never appears here.
        assert!(!is_ignored_rel(Path::new("src/lib.rs")));
        assert!(!is_ignored_rel(Path::new("src/building/mod.rs")));
        assert!(!is_ignored_rel(Path::new("src/targeting.rs")));
    }

    #[test]
    fn extension_filter_covers_the_supported_set() {
        for ext in SUPPORTED_EXTENSIONS {
            assert!(has_supported_extension(Path::new(&format!("a.{ext}"))));
        }
        assert!(!has_supported_extension(Path::new("a.md")));
        assert!(!has_supported_extension(Path::new("noext")));
    }

    #[test]
    fn workspace_root_walks_up_to_the_marker() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        let nested = root.join("src").join("deep");
        fs::create_dir_all(&nested).unwrap();
        fs::create_dir_all(root.join(".mimori")).unwrap();

        // Regression M5: this used to return `nested` itself.
        assert_eq!(
            find_workspace_root(Some(&nested), Path::new("/nowhere")),
            root
        );
    }

    #[test]
    fn workspace_root_prefers_a_containing_cwd_over_the_file_parent() {
        let tmp = tempfile::tempdir().unwrap();
        let cwd = tmp.path().canonicalize().unwrap();
        let nested = cwd.join("src").join("deep");
        fs::create_dir_all(&nested).unwrap();

        // Regression M5: this used to return `nested`, indexing one directory.
        assert_eq!(find_workspace_root(Some(&nested), &cwd), cwd);
    }

    #[test]
    fn scan_stats_report_indexed_vs_crawled() {
        // P1-4: unindexed extensions are disclosed, not silent.
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        fs::write(root.join("a.rs"), "fn a() {}").unwrap();
        fs::write(root.join("b.md"), "# docs").unwrap();
        fs::write(root.join("c.json"), "{}").unwrap();
        let (scans, stats) = scan_workspace_with_stats(root);
        assert_eq!(scans.len(), 1);
        assert_eq!(stats.indexed_files, 1);
        assert_eq!(stats.crawled_files, 3);
        assert!(stats.unindexed_exts.iter().any(|e| e.starts_with("md")));
        assert!(stats.unindexed_exts.iter().any(|e| e.starts_with("json")));
    }

    #[test]
    fn a_target_outside_any_workspace_roots_at_its_own_directory() {
        let tmp = tempfile::tempdir().unwrap();
        let loose = tmp.path().canonicalize().unwrap();
        let elsewhere = tempfile::tempdir().unwrap();

        // Pointing at a loose file from an unrelated cwd must still work.
        assert_eq!(find_workspace_root(Some(&loose), elsewhere.path()), loose);
    }

    #[test]
    fn hash_distinguishes_same_length_content() {
        // Same byte length, different content: the case mtime+size cannot catch.
        assert_ne!(
            fnv1a_hash(b"pub fn aaa() {}"),
            fnv1a_hash(b"pub fn bbb() {}")
        );
    }
}
