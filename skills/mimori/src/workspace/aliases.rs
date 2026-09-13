//! `tsconfig.json` path aliases, so first-party imports are not mistaken for
//! npm packages.
//!
//! The classifier in `parser/typescript.rs` used a two-way test — relative or
//! package — for a three-way world. Every monorepo alias (`@dokploy/server`,
//! `@/components`, anything in `compilerOptions.paths`) landed in the package
//! branch, so its call edges were discarded at `graph/mod.rs`'s external gate
//! and `up`/`blast`/`doctor` answered from a graph missing them.
//!
//! Only the specifier -> first-party *decision* is needed here: the graph
//! resolves a reference by name and directory, never by import path, so an
//! alias does not have to be rewritten to a file location — it only has to
//! stop being called external.

use serde_json::Value;
use std::collections::HashSet;
use std::path::{Path, PathBuf};

/// One `compilerOptions.paths` key. TypeScript allows a single `*`, anywhere.
#[derive(Debug, Clone, PartialEq, Eq)]
struct AliasPattern {
    prefix: String,
    suffix: String,
    wildcard: bool,
}

impl AliasPattern {
    fn matches(&self, spec: &str) -> bool {
        if !self.wildcard {
            return spec == self.prefix;
        }
        spec.len() >= self.prefix.len() + self.suffix.len()
            && spec.starts_with(&self.prefix)
            && spec.ends_with(&self.suffix)
    }
}

/// The workspace's alias patterns, the names of its own packages, and a
/// fingerprint of the configs that produced them so an edited `tsconfig.json`
/// or a new workspace package invalidates the parse cache.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct AliasSet {
    patterns: Vec<AliasPattern>,
    /// `package.json` names of workspace members. `@acme/core` is first-party
    /// code even though no `tsconfig` alias declares it: pnpm links it.
    packages: Vec<String>,
    fingerprint: String,
}

impl AliasSet {
    pub fn is_empty(&self) -> bool {
        self.patterns.is_empty()
    }

    pub fn pattern_count(&self) -> usize {
        self.patterns.len()
    }

    /// Fingerprint of the config contents this set came from. Stored in the
    /// db: adding an alias or a workspace package must force a re-parse, not
    /// serve a stale alias-blind index.
    pub fn fingerprint(&self) -> &str {
        &self.fingerprint
    }

    /// True when `spec` is the workspace's own code rather than an installed
    /// package: a declared `paths` alias, or a workspace member addressed by
    /// its `package.json` name.
    pub fn matches(&self, spec: &str) -> bool {
        if self.patterns.iter().any(|p| p.matches(spec)) {
            return true;
        }
        self.packages.iter().any(|p| {
            spec == p
                || (spec.len() > p.len()
                    && spec.starts_with(p.as_str())
                    && spec.as_bytes()[p.len()] == b'/')
        })
    }

    /// A set built from raw `paths` keys, for parser tests with no tsconfig
    /// on disk to walk.
    #[cfg(test)]
    pub fn for_test(keys: &[&str]) -> AliasSet {
        AliasSet {
            patterns: keys
                .iter()
                .filter_map(|k| pattern_for(k, &[format!("./{k}")]))
                .collect(),
            packages: Vec::new(),
            fingerprint: String::new(),
        }
    }

    /// Walk the workspace for `tsconfig*.json` / `jsconfig.json` /
    /// `package.json`, then collect: workspace package names, and every
    /// `compilerOptions.paths` key reachable through the `extends` chains.
    pub fn collect(root: &Path) -> AliasSet {
        let manifests = super::walker::discover_package_manifests(root);
        let packages = read_package_names(&manifests);
        let configs = super::walker::discover_tsconfigs(root);

        let mut set = AliasSet {
            packages: packages.iter().map(|(name, _)| name.clone()).collect(),
            ..Default::default()
        };
        let mut hash: u64 = 0xcbf29ce484222325;
        // Content-hashed, not field-extracted: which field of a config matters
        // is exactly the kind of reasoning that lets a stale index through.
        for path in manifests.iter().chain(configs.iter()) {
            let rel = path.strip_prefix(root).unwrap_or(path);
            hash = crate::workspace::walker::fnv1a_hash(rel.to_string_lossy().as_bytes())
                ^ hash.wrapping_mul(0x100000001b3);
            let content = std::fs::read_to_string(path).unwrap_or_default();
            hash = crate::workspace::walker::fnv1a_hash(content.as_bytes())
                ^ hash.wrapping_mul(0x100000001b3);
        }
        for config in &configs {
            for (key, replacements) in read_paths(config, &packages) {
                if let Some(pattern) = pattern_for(&key, &replacements) {
                    if !set.patterns.contains(&pattern) {
                        set.patterns.push(pattern);
                    }
                }
            }
        }
        set.fingerprint = format!("{hash:016x}");
        set
    }
}

/// `package.json` `name` -> the directory that declares it.
fn read_package_names(manifests: &[std::path::PathBuf]) -> Vec<(String, std::path::PathBuf)> {
    let mut out = Vec::new();
    for path in manifests {
        let Some(dir) = path.parent() else {
            continue;
        };
        let Ok(raw) = std::fs::read_to_string(path) else {
            continue;
        };
        // Deliberately strict: a malformed manifest must be reported, because
        // silently skipping it means its package's imports stay "external" and
        // the edges to them vanish without a trace.
        match serde_json::from_str::<Value>(&raw) {
            Ok(json) => {
                if let Some(name) = json.get("name").and_then(Value::as_str) {
                    if !name.is_empty() {
                        out.push((name.to_string(), dir.to_path_buf()));
                    }
                }
            }
            Err(e) => eprintln!(
                "mimori: {} is unreadable ({e}); imports of it stay external",
                path.display()
            ),
        }
    }
    out.sort();
    out
}

/// A key is a usable alias only when it cannot swallow the whole npm registry
/// and does not simply point back into `node_modules`:
///
/// - `"*"` (no non-wildcard prefix) matches `react` as readily as `@app/x`.
/// - `"lodash": ["./node_modules/lodash"]` is a redirect to a package, so the
///   gate must keep treating it as external.
fn pattern_for(key: &str, replacements: &[String]) -> Option<AliasPattern> {
    if replacements.iter().any(|r| r.contains("node_modules")) {
        return None;
    }
    match key.split_once('*') {
        Some((prefix, suffix)) => {
            if prefix.is_empty() {
                return None;
            }
            Some(AliasPattern {
                prefix: prefix.to_string(),
                suffix: suffix.to_string(),
                wildcard: true,
            })
        }
        None => Some(AliasPattern {
            prefix: key.to_string(),
            suffix: String::new(),
            wildcard: false,
        }),
    }
}

/// `compilerOptions.paths` of one config, following `extends` (string or the
/// TS 5.0 array form) with `extends`-targets inheriting before overrides.
///
/// `packages` resolves the `extends` form that a pnpm workspace actually uses:
/// `"@acme/tsconfig/base.json"` is a workspace member, not an npm dependency,
/// so a shared base that declares `paths` must not be invisible here.
fn read_paths(config: &Path, packages: &[(String, PathBuf)]) -> Vec<(String, Vec<String>)> {
    let mut out = Vec::new();
    let mut visited = HashSet::new();
    collect_paths(config, packages, &mut visited, &mut out);
    out
}

fn collect_paths(
    config: &Path,
    packages: &[(String, PathBuf)],
    visited: &mut HashSet<PathBuf>,
    out: &mut Vec<(String, Vec<String>)>,
) {
    // Symlinked configs and `a extends b extends a` cycles both land here.
    if !visited.insert(config.to_path_buf()) {
        return;
    }
    let Ok(raw) = std::fs::read_to_string(config) else {
        return;
    };
    let Some(json) = parse_jsonc(&raw) else {
        eprintln!(
            "mimori: {} could not be parsed; its path aliases are not applied",
            config.display()
        );
        return;
    };

    match json.get("extends") {
        Some(Value::String(parent)) => follow_extends(config, parent, packages, visited, out),
        Some(Value::Array(parents)) => {
            for parent in parents.iter().filter_map(Value::as_str) {
                follow_extends(config, parent, packages, visited, out);
            }
        }
        _ => {}
    }

    let Some(paths) = json
        .get("compilerOptions")
        .and_then(|c| c.get("paths"))
        .and_then(Value::as_object)
    else {
        return;
    };
    for (key, replacements) in paths {
        let values: Vec<String> = match replacements {
            Value::Array(items) => items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect(),
            _ => Vec::new(),
        };
        out.push((key.clone(), values));
    }
}

fn follow_extends(
    config: &Path,
    parent: &str,
    packages: &[(String, PathBuf)],
    visited: &mut HashSet<PathBuf>,
    out: &mut Vec<(String, Vec<String>)>,
) {
    let target = if parent.starts_with('.') || parent.starts_with('/') {
        let mut target = config.parent().unwrap_or(Path::new("")).join(parent);
        if target.extension().is_none() {
            target.set_extension("json");
        }
        target
    } else {
        // A bare specifier is either an npm shareable config
        // (`@tsconfig/node18`, cut here) or a workspace member's shared base
        // (`@acme/tsconfig/base.json`), which is ours and must be followed.
        let Some((name, rest)) = split_package_spec(parent) else {
            return;
        };
        let Some((_, dir)) = packages.iter().find(|(n, _)| *n == name) else {
            return;
        };
        let mut target = dir.join(rest);
        if target.extension().is_none() {
            target.set_extension("json");
        }
        target
    };
    collect_paths(&target, packages, visited, out);
}

/// `@scope/name/sub/path` -> `("@scope/name", "sub/path")`;
/// `name/sub` -> `("name", "sub")`. `None` when there is no subpath: a lone
/// segment (`@tsconfig/node18`) is an npm shareable config, not a workspace
/// member.
fn split_package_spec(spec: &str) -> Option<(String, String)> {
    let split_at = if spec.starts_with('@') {
        spec.match_indices('/').nth(1).map(|(i, _)| i)
    } else {
        spec.find('/')
    }?;
    let name = &spec[..split_at];
    let rest = spec[split_at + 1..].trim_start_matches('/');
    if name.is_empty() || rest.is_empty() {
        return None;
    }
    Some((name.to_string(), rest.to_string()))
}

/// JSON with the two things `tsconfig.json` is allowed to contain: `//` and
/// `/* */` comments, and trailing commas.
fn parse_jsonc(raw: &str) -> Option<Value> {
    serde_json::from_str(&strip_trailing_commas(&strip_comments(raw))).ok()
}

fn strip_comments(raw: &str) -> String {
    let mut out = String::with_capacity(raw.len());
    let mut chars = raw.chars().peekable();
    let mut in_string = false;
    while let Some(c) = chars.next() {
        if in_string {
            out.push(c);
            if c == '\\' {
                out.extend(chars.next());
            } else if c == '"' {
                in_string = false;
            }
            continue;
        }
        match c {
            '"' => {
                in_string = true;
                out.push(c);
            }
            '/' if chars.peek() == Some(&'/') => {
                for c in chars.by_ref() {
                    if c == '\n' {
                        out.push('\n');
                        break;
                    }
                }
            }
            '/' if chars.peek() == Some(&'*') => {
                chars.next();
                let mut prev = '\0';
                for c in chars.by_ref() {
                    if prev == '*' && c == '/' {
                        break;
                    }
                    prev = c;
                }
            }
            _ => out.push(c),
        }
    }
    out
}

fn strip_trailing_commas(raw: &str) -> String {
    let mut out = String::with_capacity(raw.len());
    let mut chars = raw.chars().peekable();
    let mut in_string = false;
    while let Some(c) = chars.next() {
        if in_string {
            out.push(c);
            if c == '\\' {
                out.extend(chars.next());
            } else if c == '"' {
                in_string = false;
            }
            continue;
        }
        match c {
            '"' => {
                in_string = true;
                out.push(c);
            }
            ',' => {
                // Keep the comma unless only whitespace precedes the next
                // structural character.
                let mut lookahead = chars.clone();
                let mut next = None;
                while let Some(&n) = lookahead.peek() {
                    if n.is_whitespace() {
                        lookahead.next();
                    } else {
                        next = Some(n);
                        break;
                    }
                }
                if !matches!(next, Some('}') | Some(']')) {
                    out.push(c);
                }
            }
            _ => out.push(c),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wildcard_and_exact_patterns_match_their_own_specifiers_only() {
        let set = AliasSet {
            patterns: vec![
                AliasPattern {
                    prefix: "@app/server/".to_string(),
                    suffix: String::new(),
                    wildcard: true,
                },
                AliasPattern {
                    prefix: "@/".to_string(),
                    suffix: String::new(),
                    wildcard: true,
                },
                AliasPattern {
                    prefix: "shim".to_string(),
                    suffix: String::new(),
                    wildcard: false,
                },
            ],
            packages: vec!["@acme/core".to_string()],
            fingerprint: String::new(),
        };
        assert!(set.matches("@app/server/services/permission"));
        assert!(set.matches("@/components/button"));
        assert!(set.matches("shim"));
        assert!(!set.matches("shimmer"));
        assert!(!set.matches("react"));
        assert!(!set.matches("drizzle-orm"));
        assert!(!set.matches("@app/other"));
        // A workspace member is first-party by its package name, with or
        // without a subpath; a name that merely shares a prefix is not.
        assert!(set.matches("@acme/core"));
        assert!(set.matches("@acme/core/i18n"));
        assert!(!set.matches("@acme/core-utils"));
        assert!(!set.matches("@acme"));
    }

    #[test]
    fn bare_wildcard_key_is_rejected() {
        assert!(pattern_for("*", &["./src/*".to_string()]).is_none());
    }

    #[test]
    fn key_pointing_back_into_node_modules_is_external() {
        assert!(pattern_for("lodash", &["./node_modules/lodash".to_string()]).is_none());
    }

    #[test]
    fn wildcard_in_the_middle_is_honoured() {
        let p = pattern_for("@app/*/utils", &["./src/*/utils".to_string()]).unwrap();
        assert!(p.matches("@app/server/utils"));
        assert!(!p.matches("@app/server/lib"));
        assert!(!p.matches("@app/utils"));
    }

    #[test]
    fn jsonc_comments_and_trailing_commas_parse() {
        let raw = r#"{
  // a comment
  /* another */
  "compilerOptions": {
    "paths": {
      "@app/*": ["./src/*"], // trailing comment
    },
  },
}"#;
        let json = parse_jsonc(raw).expect("jsonc must parse");
        assert!(json["compilerOptions"]["paths"]["@app/*"].is_array());
    }

    #[test]
    fn package_specs_split_into_name_and_subpath() {
        assert_eq!(
            split_package_spec("@acme/tsconfig/base.json"),
            Some(("@acme/tsconfig".into(), "base.json".into()))
        );
        assert_eq!(
            split_package_spec("tsconfig/base.json"),
            Some(("tsconfig".into(), "base.json".into()))
        );
        assert_eq!(
            split_package_spec("pkg/a/b"),
            Some(("pkg".into(), "a/b".into()))
        );
        // Lone segments are npm packages, not workspace members.
        assert_eq!(split_package_spec("@tsconfig/node18"), None);
        assert_eq!(split_package_spec("react"), None);
    }

    /// The pnpm-monorepo shape: apps extend a shared base addressed by
    /// package name, and the aliases live in that base.
    #[test]
    fn extends_through_a_workspace_package_name_is_followed() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        std::fs::create_dir_all(root.join("packages/tsconfig")).unwrap();
        std::fs::write(
            root.join("packages/tsconfig/package.json"),
            r#"{ "name": "@acme/tsconfig" }"#,
        )
        .unwrap();
        std::fs::write(
            root.join("packages/tsconfig/base.json"),
            r#"{ "compilerOptions": { "paths": { "@acme/shared/*": ["./src/*"] } } }"#,
        )
        .unwrap();
        std::fs::create_dir_all(root.join("packages/core")).unwrap();
        std::fs::write(
            root.join("packages/core/package.json"),
            r#"{ "name": "@acme/core" }"#,
        )
        .unwrap();
        std::fs::create_dir_all(root.join("apps/web")).unwrap();
        std::fs::write(
            root.join("apps/web/tsconfig.json"),
            r#"{ "extends": "@acme/tsconfig/base.json" }"#,
        )
        .unwrap();

        let set = AliasSet::collect(root);
        assert!(
            set.matches("@acme/shared/utils"),
            "aliases declared in a package-named base must be applied: {set:?}"
        );
        assert!(
            set.matches("@acme/core"),
            "workspace member must be internal"
        );
        assert!(set.matches("@acme/core/i18n"));
        assert!(!set.matches("react"));
    }

    #[test]
    fn package_manifest_changes_move_the_fingerprint() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        std::fs::write(root.join("package.json"), r#"{ "name": "root" }"#).unwrap();
        let first = AliasSet::collect(root);

        std::fs::create_dir_all(root.join("packages/a")).unwrap();
        std::fs::write(
            root.join("packages/a/package.json"),
            r#"{ "name": "@acme/a" }"#,
        )
        .unwrap();
        let second = AliasSet::collect(root);

        assert!(second.matches("@acme/a"));
        assert_ne!(
            first.fingerprint(),
            second.fingerprint(),
            "a new workspace package must invalidate the parse cache"
        );
    }

    #[test]
    fn slashes_inside_strings_survive_comment_stripping() {
        let json = parse_jsonc(r#"{ "url": "https://example.com//x" }"#).unwrap();
        assert_eq!(json["url"], "https://example.com//x");
    }

    #[test]
    fn extends_chain_inherits_aliases_relative_to_the_extended_config() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        std::fs::write(
            root.join("tsconfig.base.json"),
            r#"{ "compilerOptions": { "paths": { "@base/*": ["./src/*"] } } }"#,
        )
        .unwrap();
        std::fs::create_dir_all(root.join("packages/app")).unwrap();
        std::fs::write(
            root.join("packages/app/tsconfig.json"),
            r#"{ "extends": "../../tsconfig.base",
                 "compilerOptions": { "paths": { "@app/*": ["./src/*"] } } }"#,
        )
        .unwrap();

        let set = AliasSet::collect(root);
        assert!(set.matches("@base/utils"), "extends alias missing: {set:?}");
        assert!(set.matches("@app/utils"));
        assert!(!set.matches("react"));
        assert_eq!(set.pattern_count(), 2);
    }

    #[test]
    fn tsconfig_cycle_terminates_and_fingerprint_tracks_content() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        std::fs::write(
            root.join("tsconfig.json"),
            r#"{ "extends": "./tsconfig.loop.json",
                 "compilerOptions": { "paths": { "@a/*": ["./src/*"] } } }"#,
        )
        .unwrap();
        std::fs::write(
            root.join("tsconfig.loop.json"),
            r#"{ "extends": "./tsconfig.json" }"#,
        )
        .unwrap();

        let first = AliasSet::collect(root);
        assert!(first.matches("@a/x"));

        std::fs::write(
            root.join("tsconfig.json"),
            r#"{ "extends": "./tsconfig.loop.json",
                 "compilerOptions": { "paths": { "@b/*": ["./src/*"] } } }"#,
        )
        .unwrap();
        let second = AliasSet::collect(root);
        assert!(second.matches("@b/x"));
        assert_ne!(
            first.fingerprint(),
            second.fingerprint(),
            "an edited tsconfig must change the fingerprint"
        );
    }
}
