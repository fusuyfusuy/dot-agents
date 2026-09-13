use assert_cmd::Command;
use predicates::prelude::*;
use std::fs;
use tempfile::tempdir;

#[test]
fn test_cli_up_and_down_dependencies() {
    let dir = tempdir().unwrap();
    let src = dir.path().join("src");
    fs::create_dir_all(&src).unwrap();

    let db_file = src.join("db.rs");
    fs::write(
        &db_file,
        r#"
pub fn query_user(id: &str) -> String {
    format!("user_{}", id)
}
"#,
    )
    .unwrap();

    let service_file = src.join("service.rs");
    fs::write(
        &service_file,
        r#"
use crate::db::query_user;

pub fn get_profile(user_id: &str) -> String {
    query_user(user_id)
}

pub fn handle_request(req: &str) -> String {
    get_profile(req)
}
"#,
    )
    .unwrap();

    // Test 'up' on query_user -> should show get_profile as caller
    let mut cmd_up = Command::cargo_bin("mimori").unwrap();
    cmd_up.current_dir(dir.path()).arg("up").arg("query_user");

    cmd_up
        .assert()
        .success()
        .stdout(predicate::str::contains("get_profile"));

    // Test 'down' on handle_request -> should show get_profile as callee
    let mut cmd_down = Command::cargo_bin("mimori").unwrap();
    cmd_down
        .current_dir(dir.path())
        .arg("down")
        .arg("handle_request");

    cmd_down
        .assert()
        .success()
        .stdout(predicate::str::contains("get_profile"));
}

#[test]
fn test_cli_slice_enriches_1hop_callers_and_callees() {
    let dir = tempdir().unwrap();
    let src = dir.path().join("src");
    fs::create_dir_all(&src).unwrap();

    let file1 = src.join("app.rs");
    fs::write(
        &file1,
        r#"
pub fn helper() -> i32 {
    42
}

pub fn process() -> i32 {
    helper()
}

pub fn main_flow() -> i32 {
    process()
}
"#,
    )
    .unwrap();

    let target = format!("{}:process", file1.to_str().unwrap());
    let mut cmd = Command::cargo_bin("mimori").unwrap();
    cmd.current_dir(dir.path()).arg("slice").arg(&target);

    cmd.assert()
        .success()
        .stdout(predicate::str::contains("1-Hop Callers"))
        .stdout(predicate::str::contains("main_flow"))
        .stdout(predicate::str::contains("1-Hop Callees"))
        .stdout(predicate::str::contains("helper"));
}

#[test]
fn test_cli_slice_follow_local_inlines_callee() {
    let dir = tempdir().unwrap();
    let src = dir.path().join("src");
    fs::create_dir_all(&src).unwrap();

    let file1 = src.join("calc.rs");
    fs::write(
        &file1,
        r#"
fn private_add(a: i32, b: i32) -> i32 {
    a + b
}

pub fn compute(x: i32) -> i32 {
    private_add(x, 10)
}
"#,
    )
    .unwrap();

    let target = format!("{}:compute", file1.to_str().unwrap());
    let mut cmd = Command::cargo_bin("mimori").unwrap();
    cmd.current_dir(dir.path())
        .arg("slice")
        .arg(&target)
        .arg("-f");

    cmd.assert()
        .success()
        .stdout(predicate::str::contains("private_add"))
        .stdout(predicate::str::contains("a + b"));
}
