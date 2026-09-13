use assert_cmd::Command;
use predicates::prelude::*;
use std::fs;
use tempfile::tempdir;

#[test]
fn test_cli_map_centrality_ranking() {
    let dir = tempdir().unwrap();
    let src = dir.path().join("src");
    fs::create_dir_all(&src).unwrap();

    // core_engine is called by 3 other functions -> should have highest centrality
    let engine_file = src.join("engine.rs");
    fs::write(
        &engine_file,
        r#"
pub fn core_engine() -> i32 {
    100
}
"#,
    )
    .unwrap();

    let callers_file = src.join("handlers.rs");
    fs::write(
        &callers_file,
        r#"
use crate::engine::core_engine;

pub fn handler_one() -> i32 { core_engine() }
pub fn handler_two() -> i32 { core_engine() }
pub fn handler_three() -> i32 { core_engine() }
"#,
    )
    .unwrap();

    let mut cmd = Command::cargo_bin("mimori").unwrap();
    cmd.current_dir(dir.path()).arg("map");

    cmd.assert()
        .success()
        .stdout(predicate::str::contains("Repository Map"))
        .stdout(predicate::str::contains("core_engine"))
        .stdout(predicate::str::contains("handler_one"));
}

#[test]
fn test_cli_map_scope_filter() {
    let dir = tempdir().unwrap();
    let auth_dir = dir.path().join("src").join("auth");
    let pay_dir = dir.path().join("src").join("pay");
    fs::create_dir_all(&auth_dir).unwrap();
    fs::create_dir_all(&pay_dir).unwrap();

    fs::write(auth_dir.join("auth.rs"), "pub fn authenticate() {}\n").unwrap();
    fs::write(pay_dir.join("pay.rs"), "pub fn charge() {}\n").unwrap();

    let mut cmd = Command::cargo_bin("mimori").unwrap();
    cmd.current_dir(dir.path())
        .arg("map")
        .arg("--scope")
        .arg("src/auth");

    cmd.assert()
        .success()
        .stdout(predicate::str::contains("authenticate"))
        .stdout(predicate::str::contains("charge").not());
}

#[test]
fn test_cli_map_json_output() {
    let dir = tempdir().unwrap();
    let src = dir.path().join("src");
    fs::create_dir_all(&src).unwrap();
    fs::write(src.join("lib.rs"), "pub fn main_init() {}\n").unwrap();

    let mut cmd = Command::cargo_bin("mimori").unwrap();
    cmd.current_dir(dir.path()).arg("map").arg("--json");

    let assert = cmd.assert().success();
    let stdout = String::from_utf8(assert.get_output().stdout.clone()).unwrap();
    let json: serde_json::Value = serde_json::from_str(&stdout).expect("Valid JSON");
    assert!(json["modules"].is_array());
}
