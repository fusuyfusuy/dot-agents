use assert_cmd::Command;
use predicates::prelude::*;
use std::fs;
use tempfile::tempdir;

fn fixture() -> tempfile::TempDir {
    let dir = tempdir().unwrap();
    let src = dir.path().join("src");
    fs::create_dir_all(&src).unwrap();
    fs::write(
        src.join("a.rs"),
        "pub fn used() {}\npub fn caller() { used(); }\npub fn lonely() {}\n",
    )
    .unwrap();
    fs::write(src.join("main.rs"), "fn main() {}\n").unwrap();
    dir
}

#[test]
fn test_cli_doctor_reports_health_and_dead_weight() {
    let dir = fixture();
    let mut cmd = Command::cargo_bin("mimori").unwrap();
    cmd.current_dir(dir.path()).arg("doctor");
    cmd.assert()
        .success()
        .stdout(predicate::str::contains("Doctor"))
        .stdout(predicate::str::contains("lonely"))
        .stdout(predicate::str::contains("Symbols:"));
}

#[test]
fn test_cli_doctor_json_is_structured() {
    let dir = fixture();
    let mut cmd = Command::cargo_bin("mimori").unwrap();
    cmd.current_dir(dir.path()).arg("--json").arg("doctor");
    cmd.assert()
        .success()
        .stdout(predicate::str::contains("likely_dead"))
        .stdout(predicate::str::contains("needs_review"))
        .stdout(predicate::str::contains("top_hubs"));
}

#[test]
fn test_cli_blast_down_follows_callees() {
    let dir = fixture();
    let mut cmd = Command::cargo_bin("mimori").unwrap();
    cmd.current_dir(dir.path())
        .arg("blast")
        .arg("src/a.rs:caller")
        .arg("--down");
    cmd.assert()
        .success()
        .stdout(predicate::str::contains("Downstream"))
        .stdout(predicate::str::contains("used"));
}
