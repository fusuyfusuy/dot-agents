use assert_cmd::Command;
use predicates::prelude::*;
use std::fs;
use tempfile::tempdir;

#[test]
fn test_cli_find_fallback_to_literal_matches() {
    let dir = tempdir().unwrap();
    let root = dir.path();

    let service_code = r#"export function scheduleTask(jobType: string) {
    if (jobType === "create-backup") {
        triggerBackupJob();
    }
}
"#;

    fs::write(root.join("service.ts"), service_code).unwrap();

    // Query "create-backup" is not a symbol name or a file name, so AST match yields 0.
    // It should fall back to matching the literal string inside scheduleTask.
    let mut cmd = Command::cargo_bin("mimori").unwrap();
    cmd.current_dir(root)
        .args(["find", "create-backup"])
        .assert()
        .success()
        .stdout(
            predicate::str::contains("literal matches")
                .or(predicate::str::contains("literal match")),
        )
        .stdout(predicate::str::contains("service.ts"))
        .stdout(predicate::str::contains(
            "if (jobType === \"create-backup\")",
        ));
}
