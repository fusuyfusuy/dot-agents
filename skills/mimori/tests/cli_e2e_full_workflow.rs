use assert_cmd::Command;
use predicates::prelude::*;
use std::fs;
use tempfile::tempdir;

#[test]
fn test_cli_e2e_complete_polyglot_workflow() {
    let dir = tempdir().unwrap();
    let root = dir.path();

    // 1. Setup multi-language project structure
    let core_dir = root.join("src").join("core");
    let client_dir = root.join("src").join("client");
    let worker_dir = root.join("src").join("worker");
    let gateway_dir = root.join("src").join("gateway");
    let test_dir = root.join("tests");

    fs::create_dir_all(&core_dir).unwrap();
    fs::create_dir_all(&client_dir).unwrap();
    fs::create_dir_all(&worker_dir).unwrap();
    fs::create_dir_all(&gateway_dir).unwrap();
    fs::create_dir_all(&test_dir).unwrap();

    // Rust Engine
    fs::write(
        core_dir.join("engine.rs"),
        r#"
pub fn execute_transaction(tx_id: &str) -> bool {
    validate_tx(tx_id)
}

fn validate_tx(tx_id: &str) -> bool {
    !tx_id.is_empty()
}
"#,
    )
    .unwrap();

    // TypeScript Client
    fs::write(
        client_dir.join("api.ts"),
        r#"
export class TransactionClient {
    public submit(txId: string): boolean {
        return execute_transaction(txId);
    }
}
"#,
    )
    .unwrap();

    // Python Worker
    fs::write(
        worker_dir.join("tasks.py"),
        r#"
def background_worker(tx_id: str):
    execute_transaction(tx_id)
"#,
    )
    .unwrap();

    // Go Gateway
    fs::write(
        gateway_dir.join("gateway.go"),
        r#"package gateway

func RouteRequest(txId string) bool {
    return execute_transaction(txId)
}
"#,
    )
    .unwrap();

    // Rust Test
    fs::write(
        test_dir.join("test_engine.rs"),
        r#"
use crate::core::engine::execute_transaction;

#[test]
fn test_tx() {
    assert!(execute_transaction("tx_123"));
}
"#,
    )
    .unwrap();

    // --- STEP 1: Init ---
    let mut cmd_init = Command::cargo_bin("mimori").unwrap();
    cmd_init.current_dir(root).arg("init");
    cmd_init.assert().success();

    // --- STEP 2: Map (Ranked Overview) ---
    let mut cmd_map = Command::cargo_bin("mimori").unwrap();
    cmd_map.current_dir(root).arg("map");
    cmd_map
        .assert()
        .success()
        .stdout(predicate::str::contains("Repository Map"))
        .stdout(predicate::str::contains("execute_transaction"))
        .stdout(predicate::str::contains("TransactionClient"))
        .stdout(predicate::str::contains("background_worker"))
        .stdout(predicate::str::contains("RouteRequest"));

    // --- STEP 3: Find (Polyglot search) ---
    let mut cmd_find = Command::cargo_bin("mimori").unwrap();
    cmd_find.current_dir(root).arg("find").arg("transaction");
    cmd_find
        .assert()
        .success()
        .stdout(predicate::str::contains("execute_transaction"))
        .stdout(predicate::str::contains("TransactionClient"));

    // --- STEP 4: Slice with 1-Hop Neighbors and inlined local callee ---
    let mut cmd_slice = Command::cargo_bin("mimori").unwrap();
    cmd_slice
        .current_dir(root)
        .arg("slice")
        .arg("src/core/engine.rs:execute_transaction")
        .arg("-f");

    cmd_slice
        .assert()
        .success()
        .stdout(predicate::str::contains("pub fn execute_transaction"))
        .stdout(predicate::str::contains("1-Hop Callers"))
        .stdout(predicate::str::contains("1-Hop Callees"))
        .stdout(predicate::str::contains("validate_tx"))
        .stdout(predicate::str::contains("Inlined Local Callees"));

    // --- STEP 5: Up (Callers of execute_transaction) ---
    let mut cmd_up = Command::cargo_bin("mimori").unwrap();
    cmd_up
        .current_dir(root)
        .arg("up")
        .arg("execute_transaction");

    cmd_up
        .assert()
        .success()
        .stdout(predicate::str::contains("submit"))
        .stdout(predicate::str::contains("background_worker"))
        .stdout(predicate::str::contains("RouteRequest"));

    // --- STEP 6: Down (Callees of execute_transaction) ---
    let mut cmd_down = Command::cargo_bin("mimori").unwrap();
    cmd_down
        .current_dir(root)
        .arg("down")
        .arg("execute_transaction");

    cmd_down
        .assert()
        .success()
        .stdout(predicate::str::contains("validate_tx"));

    // --- STEP 7: Blast (Transitive Blast Radius) ---
    let mut cmd_blast = Command::cargo_bin("mimori").unwrap();
    cmd_blast.current_dir(root).arg("blast").arg("validate_tx");

    cmd_blast
        .assert()
        .success()
        .stdout(predicate::str::contains("Blast Radius"))
        .stdout(predicate::str::contains("execute_transaction"));

    // --- STEP 8: JSON Mode on Map and Find ---
    let mut cmd_json = Command::cargo_bin("mimori").unwrap();
    cmd_json
        .current_dir(root)
        .arg("find")
        .arg("execute")
        .arg("--json");
    let assert_json = cmd_json.assert().success();
    let stdout = String::from_utf8(assert_json.get_output().stdout.clone()).unwrap();
    let json: serde_json::Value = serde_json::from_str(&stdout).unwrap();
    assert!(json["matches"].is_array());

    // --- STEP 9: Clean ---
    let db_path = root.join(".mimori").join("index.db");
    assert!(db_path.exists());

    let mut cmd_clean = Command::cargo_bin("mimori").unwrap();
    cmd_clean.current_dir(root).arg("clean");
    cmd_clean.assert().success();

    assert!(!db_path.exists(), "Database should be deleted after clean");
}
