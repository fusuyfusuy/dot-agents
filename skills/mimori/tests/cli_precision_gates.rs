use assert_cmd::Command;
use predicates::prelude::*;
use std::fs;
use tempfile::tempdir;

/// End-to-end primaryKey gate: the only call site imports from an external
/// package, so the local Variable draws zero callers and the map header
/// discloses the external skip.
#[test]
fn external_import_blocks_local_resolution_end_to_end() {
    let dir = tempdir().unwrap();
    fs::write(
        dir.path().join("schema.ts"),
        "export const primaryKey = makeKey(\"pk\");\n",
    )
    .unwrap();
    fs::write(
        dir.path().join("usage.ts"),
        "import { primaryKey } from \"drizzle-orm\";\nexport function buildSchema() {\n  return primaryKey(\"id\");\n}\n",
    )
    .unwrap();

    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(dir.path())
        .args(["up", "schema.ts:primaryKey"])
        .assert()
        .success()
        .stdout(predicate::str::contains("0 callers"));

    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(dir.path())
        .args(["map"])
        .assert()
        .success()
        .stdout(predicate::str::contains("external"));
}

/// The absence gate: every lane file carries the Remote branch except
/// web-server.ts — unaskable in up/down/blast, trivial for `missing`.
#[test]
fn missing_verb_lists_only_files_lacking_the_pattern() {
    let dir = tempdir().unwrap();
    let lane_dir = dir.path().join("utils").join("traefik");
    fs::create_dir_all(&lane_dir).unwrap();
    fs::write(
        lane_dir.join("lanes.ts"),
        "export const a = 1; // Remote branch\n",
    )
    .unwrap();
    fs::write(
        lane_dir.join("remote.ts"),
        "export const b = 2; // serverId branch\n",
    )
    .unwrap();
    fs::write(
        lane_dir.join("web-server.ts"),
        "export const c = 3; // local branch\n",
    )
    .unwrap();

    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(dir.path())
        .args(["missing", "Remote|serverId", "--scope", "utils/traefik"])
        .assert()
        .success()
        .stdout(predicate::str::contains("web-server.ts"))
        .stdout(predicate::str::contains("1 files lack it"))
        .stdout(predicate::str::contains("lanes.ts").not())
        .stdout(predicate::str::contains("remote.ts").not());
}

/// `--defines` restricts the sweep to marker files: other.ts lacks the
/// pattern too, but carries no lane marker, so it stays out.
#[test]
fn missing_defines_prefilters_to_marker_files() {
    let dir = tempdir().unwrap();
    let lane_dir = dir.path().join("utils").join("traefik");
    fs::create_dir_all(&lane_dir).unwrap();
    fs::write(lane_dir.join("web-server.ts"), "lane local\n").unwrap();
    fs::write(lane_dir.join("other.ts"), "unrelated helper\n").unwrap();

    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(dir.path())
        .args([
            "missing",
            "Remote|serverId",
            "--scope",
            "utils/traefik",
            "--defines",
            "lane",
        ])
        .assert()
        .success()
        .stdout(predicate::str::contains("web-server.ts"))
        .stdout(predicate::str::contains("other.ts").not());
}

/// Passing `--scope .` or `--scope ./` must sweep the workspace, not silently zero out.
#[test]
fn missing_scope_dot_sweeps_entire_workspace() {
    let dir = tempdir().unwrap();
    fs::write(dir.path().join("a.ts"), "const target = 1;\n").unwrap();
    fs::write(dir.path().join("b.ts"), "const other = 2;\n").unwrap();

    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(dir.path())
        .args(["missing", "target", "--scope", "."])
        .assert()
        .success()
        .stdout(predicate::str::contains("b.ts"))
        .stdout(predicate::str::contains("1 files lack it"))
        .stdout(predicate::str::contains("a.ts").not());
}

/// Bare `new` must skip fuzzy tiers (Tier 2 same-dir and Tier 3 global).
/// A bare `new()` in one directory must never resolve to an unrelated `fn new()`
/// in another directory even when it is the only `new` in the workspace.
#[test]
fn bare_new_skips_global_resolution() {
    let dir = tempdir().unwrap();
    let mod_a = dir.path().join("alpha");
    let mod_b = dir.path().join("beta");
    fs::create_dir_all(&mod_a).unwrap();
    fs::create_dir_all(&mod_b).unwrap();
    // Only one constructor `new` in the entire workspace
    fs::write(
        mod_a.join("service.rs"),
        "pub struct Service;\nimpl Service {\n    pub fn new() -> Self { Service }\n}\n",
    )
    .unwrap();
    // In another module, a function calls bare `new()` without qualification
    fs::write(
        mod_b.join("client.rs"),
        "pub fn connect() {\n    let _ = new();\n}\n",
    )
    .unwrap();

    // Service::new should have 0 callers because bare `new()` in client.rs skips global tier
    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(dir.path())
        .args(["up", "alpha/service.rs:new"])
        .assert()
        .success()
        .stdout(predicate::str::contains("0 callers"));
}
