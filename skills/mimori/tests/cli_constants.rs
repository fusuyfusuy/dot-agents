use assert_cmd::Command;
use predicates::prelude::*;
use std::fs;
use tempfile::tempdir;

#[test]
fn test_cli_polyglot_constants_indexing() {
    let dir = tempdir().unwrap();
    let root = dir.path();

    // TypeScript exported constant
    fs::write(
        root.join("config.ts"),
        "export const CLEANUP_CRON_JOB = \"0 * * * *\";\n",
    )
    .unwrap();

    // Rust const
    fs::write(
        root.join("constants.rs"),
        "pub const MAX_BUFFER_SIZE: usize = 1024 * 1024;\n",
    )
    .unwrap();

    // Go const
    fs::write(
        root.join("constants.go"),
        "package main\n\nconst DefaultPort = 8080\n",
    )
    .unwrap();

    // Python uppercase const
    fs::write(root.join("settings.py"), "SESSION_TIMEOUT_SECONDS = 3600\n").unwrap();

    // 1. Find TS constant
    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(root)
        .args(["find", "CLEANUP_CRON_JOB"])
        .assert()
        .success()
        .stdout(predicate::str::contains("CLEANUP_CRON_JOB"))
        .stdout(predicate::str::contains("constant"));

    // 2. Find Rust constant
    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(root)
        .args(["find", "MAX_BUFFER_SIZE"])
        .assert()
        .success()
        .stdout(predicate::str::contains("MAX_BUFFER_SIZE"))
        .stdout(predicate::str::contains("constant"));

    // 3. Find Go constant
    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(root)
        .args(["find", "DefaultPort"])
        .assert()
        .success()
        .stdout(predicate::str::contains("DefaultPort"))
        .stdout(predicate::str::contains("constant"));

    // 4. Find Python constant
    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(root)
        .args(["find", "SESSION_TIMEOUT_SECONDS"])
        .assert()
        .success()
        .stdout(predicate::str::contains("SESSION_TIMEOUT_SECONDS"))
        .stdout(predicate::str::contains("constant"));
}
