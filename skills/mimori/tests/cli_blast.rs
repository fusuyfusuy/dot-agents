use assert_cmd::Command;
use predicates::prelude::*;
use std::fs;
use tempfile::tempdir;

#[test]
fn test_cli_blast_radius_transitive_callers() {
    let dir = tempdir().unwrap();
    let src = dir.path().join("src");
    fs::create_dir_all(&src).unwrap();

    // A chain of dependencies: db -> service -> api_controller -> main
    fs::write(src.join("db.rs"), "pub fn execute_sql() -> bool { true }\n").unwrap();

    fs::write(
        src.join("service.rs"),
        "use crate::db::execute_sql;\npub fn save_user() { execute_sql(); }\n",
    )
    .unwrap();

    fs::write(
        src.join("controller.rs"),
        "use crate::service::save_user;\npub fn post_user() { save_user(); }\n",
    )
    .unwrap();

    fs::write(
        src.join("main.rs"),
        "use crate::controller::post_user;\nfn main() { post_user(); }\n",
    )
    .unwrap();

    let tests_dir = dir.path().join("tests");
    fs::create_dir_all(&tests_dir).unwrap();
    fs::write(
        tests_dir.join("test_db.rs"),
        "use crate::db::execute_sql;\n#[test]\nfn test_query() { execute_sql(); }\n",
    )
    .unwrap();

    // Blast execute_sql
    let mut cmd = Command::cargo_bin("mimori").unwrap();
    cmd.current_dir(dir.path()).arg("blast").arg("execute_sql");

    cmd.assert()
        .success()
        .stdout(predicate::str::contains("Blast Radius"))
        .stdout(predicate::str::contains("save_user"))
        .stdout(predicate::str::contains("post_user"))
        .stdout(predicate::str::contains("main"))
        .stdout(predicate::str::contains("test_query"));
}

#[test]
fn test_cli_blast_depth_limit() {
    let dir = tempdir().unwrap();
    let src = dir.path().join("src");
    fs::create_dir_all(&src).unwrap();

    fs::write(src.join("a.rs"), "pub fn leaf() {}\n").unwrap();
    fs::write(
        src.join("b.rs"),
        "use crate::a::leaf;\npub fn hop1() { leaf(); }\n",
    )
    .unwrap();
    fs::write(
        src.join("c.rs"),
        "use crate::b::hop1;\npub fn hop2() { hop1(); }\n",
    )
    .unwrap();
    fs::write(
        src.join("d.rs"),
        "use crate::c::hop2;\npub fn hop3() { hop2(); }\n",
    )
    .unwrap();

    // Blast depth 1 -> should include hop1, but NOT hop3
    let mut cmd = Command::cargo_bin("mimori").unwrap();
    cmd.current_dir(dir.path())
        .arg("blast")
        .arg("leaf")
        .arg("-d")
        .arg("1");

    cmd.assert()
        .success()
        .stdout(predicate::str::contains("hop1"))
        .stdout(predicate::str::contains("hop3").not());
}

#[test]
fn test_cli_blast_json_output() {
    let dir = tempdir().unwrap();
    let src = dir.path().join("src");
    fs::create_dir_all(&src).unwrap();

    fs::write(src.join("db.rs"), "pub fn read() {}\n").unwrap();
    fs::write(
        src.join("app.rs"),
        "use crate::db::read;\npub fn run() { read(); }\n",
    )
    .unwrap();

    let mut cmd = Command::cargo_bin("mimori").unwrap();
    cmd.current_dir(dir.path())
        .arg("blast")
        .arg("read")
        .arg("--json");

    let assert = cmd.assert().success();
    let stdout = String::from_utf8(assert.get_output().stdout.clone()).unwrap();
    let json: serde_json::Value = serde_json::from_str(&stdout).expect("Valid JSON");
    assert!(json["affected"].is_array());
    assert_eq!(json["target"], "read");
}
