use assert_cmd::Command;
use predicates::prelude::*;
use std::fs;
use tempfile::tempdir;

#[test]
fn test_cli_slice_with_imports() {
    let dir = tempdir().unwrap();
    let root = dir.path();

    let ts_code = r#"import { db } from "./db";
import type { User } from "./schema";
import {
    helperOne,
    helperTwo,
} from "./helpers";

export function authenticate(userId: string): User {
    return db.query(userId);
}
"#;

    fs::write(root.join("auth.ts"), ts_code).unwrap();

    // Slicing without --with-imports should not have "Backing Imports"
    let mut cmd1 = Command::cargo_bin("mimori").unwrap();
    cmd1.current_dir(root)
        .args(["slice", "auth.ts:authenticate"])
        .assert()
        .success()
        .stdout(predicate::str::contains("Backing Imports").not());

    // Slicing with --with-imports should include the import statements
    let mut cmd2 = Command::cargo_bin("mimori").unwrap();
    cmd2.current_dir(root)
        .args(["slice", "auth.ts:authenticate", "--with-imports"])
        .assert()
        .success()
        .stdout(predicate::str::contains("Backing Imports"))
        .stdout(predicate::str::contains("import { db } from \"./db\";"))
        .stdout(predicate::str::contains(
            "import type { User } from \"./schema\";",
        ))
        .stdout(predicate::str::contains("helperOne"));

    // Short flag -i
    let mut cmd3 = Command::cargo_bin("mimori").unwrap();
    cmd3.current_dir(root)
        .args(["slice", "auth.ts:authenticate", "-i"])
        .assert()
        .success()
        .stdout(predicate::str::contains("Backing Imports"));
}
