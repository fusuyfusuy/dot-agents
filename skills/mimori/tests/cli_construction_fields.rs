use assert_cmd::Command;
use predicates::prelude::*;
use std::fs;
use tempfile::tempdir;

fn write_exec_repo(root: &std::path::Path) {
    let src = root.join("src");
    fs::create_dir_all(&src).unwrap();
    fs::write(
        src.join("ExecError.ts"),
        r#"export class ExecError extends Error {
  command: string;
  constructor(command: string, message: string) {
    super(message);
    this.command = command;
  }
  getDetailedMessage() { return `${this.command}: ${this.message}`; }
}
"#,
    )
    .unwrap();
    fs::write(
        src.join("deploy.ts"),
        r#"import { ExecError } from "./ExecError";
async function execAsync(cmd: string) { return cmd; }
export async function deployApplication(cmd: string) {
  const logPath = "/tmp/x.log";
  const commandWithLog = `(${cmd}) >> ${logPath} 2>&1`;
  await execAsync(commandWithLog);
  throw new ExecError(cmd, "fail");
}
export function f1() { throw new ExecError("a", "1"); }
export function useField(e: ExecError) { console.log(e.command); return e.command; }
"#,
    )
    .unwrap();
    fs::write(
        src.join("queue.ts"),
        r#"import { ExecError } from "./ExecError";
export function serializer(error: ExecError) { return { cmd: error.command }; }
"#,
    )
    .unwrap();
}

/// P0: `new X(...)` incl. `throw new X` is a caller edge to the class, and
/// `up`/`blast` on the class fold construction sites in.
#[test]
fn up_on_class_folds_in_new_expression_sites() {
    let dir = tempdir().unwrap();
    write_exec_repo(dir.path());

    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(dir.path())
        .args(["up", "ExecError"])
        .assert()
        .success()
        .stdout(predicate::str::contains("deployApplication"))
        .stdout(predicate::str::contains("f1"));

    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(dir.path())
        .args(["blast", "ExecError"])
        .assert()
        .success()
        .stdout(predicate::str::contains("deployApplication"))
        .stdout(predicate::str::contains("f1"));
}

/// P0: pointing at the constructor directly also lists construction sites.
#[test]
fn up_on_constructor_lists_new_sites() {
    let dir = tempdir().unwrap();
    write_exec_repo(dir.path());

    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(dir.path())
        .args(["up", "ExecError::constructor"])
        .assert()
        .success()
        .stdout(predicate::str::contains("deployApplication"))
        .stdout(predicate::str::contains("f1"));
}

/// P0b: one hit per matching *line* — three `new ExecError` inside a single
/// function must report 3, not 1 (the old per-symbol collapse).
#[test]
fn find_literal_reports_every_line() {
    let dir = tempdir().unwrap();
    let src = dir.path().join("src");
    fs::create_dir_all(&src).unwrap();
    fs::write(
        src.join("multi.ts"),
        "import { ExecError } from \"./ExecError\";\n\
         export function multi() {\n\
         \x20 if (1) throw new ExecError(\"a\", \"1\");\n\
         \x20 if (2) throw new ExecError(\"b\", \"2\");\n\
         \x20 return new ExecError(\"c\", \"3\");\n\
         }\n",
    )
    .unwrap();
    fs::write(
        src.join("ExecError.ts"),
        "export class ExecError { constructor(a: string, b: string) {} }\n",
    )
    .unwrap();

    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(dir.path())
        .args(["find", "new ExecError"])
        .assert()
        .success()
        .stdout(predicate::str::contains("3 literal matches"))
        .stdout(predicate::str::contains("multi.ts"))
        .stdout(predicate::str::contains("(L3)"))
        .stdout(predicate::str::contains("(L4)"))
        .stdout(predicate::str::contains("(L5)"));
}

/// P1: member-access reads are mentions, not call edges: `uses
/// ExecError::command` reaches the method that formats it, the queue
/// serializer, and the console sinks — `up` shows the same readers as
/// labeled value-use rows beneath its (empty) caller list.
#[test]
fn uses_on_field_lists_member_access_readers() {
    let dir = tempdir().unwrap();
    write_exec_repo(dir.path());

    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(dir.path())
        .args(["uses", "ExecError::command"])
        .assert()
        .success()
        .stdout(predicate::str::contains("getDetailedMessage"))
        .stdout(predicate::str::contains("serializer"))
        .stdout(predicate::str::contains("useField"));
}

/// Recall hole from the P0-1 split: template interpolation is (correctly) a
/// mention, but `up` on the interpolated variable must still surface the
/// interpolator — as a labeled weakest-tier row, not a call edge.
#[test]
fn up_on_template_var_lists_interpolator_as_value_use() {
    let dir = tempdir().unwrap();
    write_exec_repo(dir.path());

    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(dir.path())
        .args(["up", "commandWithLog"])
        .assert()
        .success()
        .stdout(predicate::str::contains("Value Uses"))
        .stdout(predicate::str::contains("deployApplication"))
        .stdout(predicate::str::contains("[Value Use]"));
}

/// Same hole through `blast`: the interpolation site joins the radius as a
/// value-use row, untraversed and clearly marked.
#[test]
fn blast_on_template_var_lists_value_uses() {
    let dir = tempdir().unwrap();
    write_exec_repo(dir.path());

    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(dir.path())
        .args(["blast", "commandWithLog"])
        .assert()
        .success()
        .stdout(predicate::str::contains("Value Uses"))
        .stdout(predicate::str::contains("deployApplication"))
        .stdout(predicate::str::contains("[Value Use]"));
}

/// P1b: template interpolation is a mention — `down deployApplication`
/// surfaces the `execAsync` sink (a true call) without the
/// `commandWithLog` intermediate, which `uses` reaches instead.
#[test]
fn down_surfaces_true_callee_not_template_var() {
     let dir = tempdir().unwrap();
    write_exec_repo(dir.path());

    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(dir.path())
        .args(["down", "deployApplication"])
        .assert()
        .success()
        .stdout(predicate::str::contains("execAsync"));

    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(dir.path())
        .args(["uses", "commandWithLog"])
        .assert()
        .success()
        .stdout(predicate::str::contains("deployApplication"));
}

/// P2: zero callers on a class points at the constructor and the confirming rg.
#[test]
fn zero_caller_up_hints_at_constructor() {
    let dir = tempdir().unwrap();
    let src = dir.path().join("src");
    fs::create_dir_all(&src).unwrap();
    fs::write(
        src.join("Lonely.ts"),
        "export class Lonely { lonelyMethod() { return 1; } }\n",
    )
    .unwrap();

    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(dir.path())
        .args(["up", "Lonely"])
        .assert()
        .success()
        .stdout(predicate::str::contains("No upstream callers found."))
        .stdout(predicate::str::contains("Lonely::constructor"))
        .stdout(predicate::str::contains("new Lonely"));
}

/// P3: `blast --with-sinks` appends the literal sweep in the same call.
#[test]
fn blast_with_sinks_lists_literal_hits() {
    let dir = tempdir().unwrap();
    write_exec_repo(dir.path());

    Command::cargo_bin("mimori")
        .unwrap()
        .current_dir(dir.path())
        .args([
            "blast",
            "ExecError",
            "--with-sinks",
            "console.,logPath,getDetailedMessage",
        ])
        .assert()
        .success()
        .stdout(predicate::str::contains("sink hits"))
        .stdout(predicate::str::contains("logPath"))
        .stdout(predicate::str::contains("console."));
}
