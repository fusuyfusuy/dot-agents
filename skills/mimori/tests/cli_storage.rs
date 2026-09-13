use assert_cmd::Command;
use predicates::prelude::*;
use std::fs;
use tempfile::tempdir;

#[test]
fn test_cli_sqlite_cache_creation_and_query() {
    let dir = tempdir().unwrap();
    let src = dir.path().join("src");
    fs::create_dir_all(&src).unwrap();

    let file1 = src.join("app.rs");
    fs::write(
        &file1,
        "pub fn startup() { init_db(); }\npub fn init_db() {}\n",
    )
    .unwrap();

    // First invocation: builds SQLite cache in .mimori/index.db
    let mut cmd1 = Command::cargo_bin("mimori").unwrap();
    cmd1.current_dir(dir.path()).arg("find").arg("startup");
    cmd1.assert()
        .success()
        .stdout(predicate::str::contains("startup"));

    // Check .mimori/index.db exists
    let db_path = dir.path().join(".mimori").join("index.db");
    assert!(
        db_path.exists(),
        ".mimori/index.db should exist after indexing"
    );

    // Second invocation: instant lookup from SQLite
    let mut cmd2 = Command::cargo_bin("mimori").unwrap();
    cmd2.current_dir(dir.path())
        .arg("slice")
        .arg("src/app.rs:startup");
    cmd2.assert()
        .success()
        .stdout(predicate::str::contains("startup()"))
        .stdout(predicate::str::contains("init_db"));
}

#[test]
fn test_cli_incremental_cache_update_on_edit() {
    let dir = tempdir().unwrap();
    let src = dir.path().join("src");
    fs::create_dir_all(&src).unwrap();

    let file = src.join("feature.rs");
    fs::write(&file, "pub fn old_feature() {}\n").unwrap();

    // Index original
    let mut cmd1 = Command::cargo_bin("mimori").unwrap();
    cmd1.current_dir(dir.path()).arg("find").arg("old_feature");
    cmd1.assert()
        .success()
        .stdout(predicate::str::contains("old_feature"));

    // Modify file: rename symbol
    fs::write(&file, "pub fn new_feature() {}\n").unwrap();

    // Re-run: should incrementally re-index and find new_feature
    let mut cmd2 = Command::cargo_bin("mimori").unwrap();
    cmd2.current_dir(dir.path()).arg("find").arg("new_feature");
    cmd2.assert()
        .success()
        .stdout(predicate::str::contains("new_feature"));

    // old_feature should no longer be present
    let mut cmd3 = Command::cargo_bin("mimori").unwrap();
    cmd3.current_dir(dir.path()).arg("find").arg("old_feature");
    cmd3.assert()
        .success()
        .stdout(predicate::str::contains("0 matches"));
}

#[test]
fn test_cli_clean_command_purges_cache() {
    let dir = tempdir().unwrap();
    let src = dir.path().join("src");
    fs::create_dir_all(&src).unwrap();
    fs::write(src.join("lib.rs"), "pub fn test_fn() {}\n").unwrap();

    // Warm up cache
    let mut cmd1 = Command::cargo_bin("mimori").unwrap();
    cmd1.current_dir(dir.path()).arg("find").arg("test_fn");
    cmd1.assert().success();

    let db_path = dir.path().join(".mimori").join("index.db");
    assert!(db_path.exists());

    // Run clean
    let mut cmd_clean = Command::cargo_bin("mimori").unwrap();
    cmd_clean.current_dir(dir.path()).arg("clean");
    cmd_clean.assert().success();

    assert!(
        !db_path.exists(),
        ".mimori/index.db should be purged after clean"
    );
}

#[test]
fn test_cli_init_reports_failure_when_it_creates_nothing() {
    // Regression S1: init printed success and exited 0 even when
    // create_dir_all failed, and it is the first step of the documented
    // warmup, so the failure cascaded silently.
    let dir = tempdir().unwrap();
    // A regular file at .mimori makes create_dir_all fail.
    fs::write(dir.path().join(".mimori"), b"").unwrap();

    let mut cmd = Command::cargo_bin("mimori").unwrap();
    cmd.current_dir(dir.path()).arg("init");
    cmd.assert().failure();
}
