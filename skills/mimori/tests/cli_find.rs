use assert_cmd::Command;
use predicates::prelude::*;
use std::fs;
use tempfile::tempdir;

#[test]
fn test_cli_find_symbols_across_files() {
    let dir = tempdir().unwrap();

    // Create a multi-file, multi-language project
    let src_dir = dir.path().join("src");
    fs::create_dir_all(&src_dir).unwrap();

    let rust_file = src_dir.join("auth.rs");
    fs::write(
        &rust_file,
        "pub fn authenticate_user(token: &str) -> bool { true }\npub struct AuthSession;\n",
    )
    .unwrap();

    let ts_file = src_dir.join("payment.ts");
    fs::write(
        &ts_file,
        "export function process_payment(amount: number) {}\nexport class Authenticator {}\n",
    )
    .unwrap();

    let py_file = src_dir.join("utils.py");
    fs::write(&py_file, "def authenticate_admin(): pass\n").unwrap();

    // Find symbols with pattern "auth"
    let mut cmd = Command::cargo_bin("mimori").unwrap();
    cmd.current_dir(dir.path()).arg("find").arg("auth");

    cmd.assert()
        .success()
        .stdout(predicate::str::contains("authenticate_user"))
        .stdout(predicate::str::contains("AuthSession"))
        .stdout(predicate::str::contains("Authenticator"))
        .stdout(predicate::str::contains("authenticate_admin"));
}

#[test]
fn test_cli_find_symbols_only_vs_files_only() {
    let dir = tempdir().unwrap();
    let src_dir = dir.path().join("src");
    fs::create_dir_all(&src_dir).unwrap();

    let auth_file = src_dir.join("auth_service.rs");
    fs::write(&auth_file, "pub fn login() {}\n").unwrap();

    // -s should find the login symbol when searching login
    let mut cmd_s = Command::cargo_bin("mimori").unwrap();
    cmd_s
        .current_dir(dir.path())
        .arg("find")
        .arg("login")
        .arg("-s");
    cmd_s
        .assert()
        .success()
        .stdout(predicate::str::contains("login"))
        .stdout(predicate::str::contains("auth_service.rs"));

    // -f should find file matching auth_service
    let mut cmd_f = Command::cargo_bin("mimori").unwrap();
    cmd_f
        .current_dir(dir.path())
        .arg("find")
        .arg("auth_service")
        .arg("-f");
    cmd_f
        .assert()
        .success()
        .stdout(predicate::str::contains("auth_service.rs"));
}

#[test]
fn test_cli_find_respects_ignores() {
    let dir = tempdir().unwrap();
    let target_dir = dir.path().join("target").join("debug");
    fs::create_dir_all(&target_dir).unwrap();

    let ignored_file = target_dir.join("ignored.rs");
    fs::write(&ignored_file, "pub fn secret_ignored_symbol() {}\n").unwrap();

    let mut cmd = Command::cargo_bin("mimori").unwrap();
    cmd.current_dir(dir.path())
        .arg("find")
        .arg("secret_ignored_symbol");

    cmd.assert()
        .success()
        .stdout(predicate::str::contains("0 matches"))
        .stdout(predicate::str::contains("No matches found"));
}

#[test]
fn test_cli_find_json_output() {
    let dir = tempdir().unwrap();
    let src_dir = dir.path().join("src");
    fs::create_dir_all(&src_dir).unwrap();

    let rs_file = src_dir.join("calc.rs");
    fs::write(&rs_file, "pub fn calculate_total() {}\n").unwrap();

    let mut cmd = Command::cargo_bin("mimori").unwrap();
    cmd.current_dir(dir.path())
        .arg("find")
        .arg("calculate")
        .arg("--json");

    let assert = cmd.assert().success();
    let stdout = String::from_utf8(assert.get_output().stdout.clone()).unwrap();
    let json: serde_json::Value = serde_json::from_str(&stdout).expect("Valid JSON output");
    assert!(json["matches"].is_array());
    assert_eq!(json["matches"][0]["name"], "calculate_total");
}
