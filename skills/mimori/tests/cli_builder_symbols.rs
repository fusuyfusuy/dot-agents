use assert_cmd::Command;
use predicates::prelude::*;
use std::fs;
use tempfile::tempdir;

#[test]
fn test_cli_builder_pattern_and_object_literals() {
    let dir = tempdir().unwrap();
    let root = dir.path();

    let router_code = r#"import { createTRPCRouter, protectedProcedure } from "../trpc";

export const deploymentRouter = createTRPCRouter({
    killProcess: protectedProcedure.mutation(async ({ input }) => {
        return { killed: true };
    }),
    getLogs: protectedProcedure.query(async () => {
        return ["log1", "log2"];
    }),
});
"#;

    fs::write(root.join("deployment.ts"), router_code).unwrap();

    // 1. mimori find should find deploymentRouter and its procedures
    let mut find_cmd = Command::cargo_bin("mimori").unwrap();
    find_cmd
        .current_dir(root)
        .args(["find", "killProcess"])
        .assert()
        .success()
        .stdout(predicate::str::contains("deploymentRouter::killProcess"))
        .stdout(predicate::str::contains("field"));

    // 2. mimori slice should be able to slice the specific procedure directly
    let mut slice_cmd = Command::cargo_bin("mimori").unwrap();
    slice_cmd
        .current_dir(root)
        .args(["slice", "deployment.ts:killProcess"])
        .assert()
        .success()
        .stdout(predicate::str::contains(
            "killProcess: protectedProcedure.mutation",
        ))
        .stdout(predicate::str::contains("return { killed: true };"));

    // 3. mimori slice with qualified name
    let mut slice_cmd2 = Command::cargo_bin("mimori").unwrap();
    slice_cmd2
        .current_dir(root)
        .args(["slice", "deployment.ts:deploymentRouter::getLogs"])
        .assert()
        .success()
        .stdout(predicate::str::contains(
            "getLogs: protectedProcedure.query",
        ));
}
