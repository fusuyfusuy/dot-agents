//! Path-alias imports (`tsconfig.json` -> `compilerOptions.paths`) are
//! first-party code, not external packages.
//!
//! `reports/call-graph-edge-loss-path-aliases.md` §9 states the alias
//! classification was never confirmed by a minimal fixture. These are that
//! fixture, and the guards that keep the fix from over-correcting.

use assert_cmd::Command;
use predicates::prelude::*;
use std::fs;
use std::path::Path;
use tempfile::tempdir;

fn write(root: &Path, rel: &str, content: &str) {
    let path = root.join(rel);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, content).unwrap();
}

fn mimori(root: &Path, args: &[&str]) -> assert_cmd::assert::Assert {
    let mut cmd = Command::cargo_bin("mimori").unwrap();
    cmd.current_dir(root).args(args);
    cmd.assert()
}

/// The Dokploy shape: a server package consumed through `@app/server/*`,
/// called from an app directory. Before the fix this reports 0 callers.
#[test]
fn alias_imported_call_creates_caller_edge() {
    let dir = tempdir().unwrap();
    let root = dir.path();

    // JSONC in the wild: comments and a trailing comma.
    write(
        root,
        "tsconfig.json",
        r#"{
  // monorepo root
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "@app/server/*": ["./packages/server/src/*"],
    },
  },
}"#,
    );
    write(
        root,
        "packages/server/src/services/permission.ts",
        r#"export function checkServicePermissionAndAccess(serviceId: string): boolean {
    return serviceId.length > 0;
}
"#,
    );
    write(
        root,
        "apps/dokploy/server/api/routers/application.ts",
        r#"import { checkServicePermissionAndAccess } from "@app/server/services/permission";

export function deployApplication(serviceId: string): boolean {
    return checkServicePermissionAndAccess(serviceId);
}
"#,
    );

    let coordinate = "packages/server/src/services/permission.ts:checkServicePermissionAndAccess";

    mimori(root, &["up", coordinate])
        .success()
        .stdout(predicate::str::contains("deployApplication"));

    mimori(root, &["blast", coordinate, "-d", "2"])
        .success()
        .stdout(predicate::str::contains("deployApplication"))
        .stdout(predicate::str::contains("isolated root").not());
}

/// F5 in the report: mixed relative/alias importers must not report a
/// plausible-looking partial count. Both callers, or the number is fiction.
#[test]
fn mixed_relative_and_alias_callers_are_both_reported() {
    let dir = tempdir().unwrap();
    let root = dir.path();

    write(
        root,
        "tsconfig.json",
        r#"{ "compilerOptions": { "paths": { "@app/*": ["./packages/*"] } } }"#,
    );
    write(
        root,
        "packages/server/src/exec.ts",
        r#"export async function execAsyncRemote(cmd: string): Promise<string> {
    return cmd;
}
"#,
    );
    write(
        root,
        "packages/server/src/relative-caller.ts",
        r#"import { execAsyncRemote } from "./exec";

export function viaRelative(): Promise<string> {
    return execAsyncRemote("a");
}
"#,
    );
    write(
        root,
        "apps/api/src/alias-caller.ts",
        r#"import { execAsyncRemote } from "@app/server/src/exec";

export function viaAlias(): Promise<string> {
    return execAsyncRemote("b");
}
"#,
    );

    mimori(root, &["up", "packages/server/src/exec.ts:execAsyncRemote"])
        .success()
        .stdout(predicate::str::contains("viaRelative"))
        .stdout(predicate::str::contains("viaAlias"));
}

/// A pnpm/Turborepo workspace addresses its own packages by `package.json`
/// name (`@acme/core`), with no tsconfig alias involved. That is first-party
/// code: measured on a real monorepo, this import form is the difference
/// between 0 and 21 reported callers.
#[test]
fn workspace_package_import_creates_caller_edge() {
    let dir = tempdir().unwrap();
    let root = dir.path();

    write(
        root,
        "package.json",
        r#"{ "name": "acme-monorepo", "workspaces": ["packages/*", "apps/*"] }"#,
    );
    write(
        root,
        "packages/core/package.json",
        r#"{ "name": "@acme/core" }"#,
    );
    write(root, "apps/web/package.json", r#"{ "name": "@acme/web" }"#);
    write(
        root,
        "packages/core/src/i18n.ts",
        r#"export function getTranslation(lang: string): string {
    return lang;
}
"#,
    );
    write(
        root,
        "apps/web/src/App.tsx",
        r#"import { getTranslation } from "@acme/core";

export function App(): string {
    return getTranslation("tr");
}
"#,
    );

    mimori(root, &["up", "packages/core/src/i18n.ts:getTranslation"])
        .success()
        .stdout(predicate::str::contains("App"));
}

/// Adding an alias to `tsconfig.json` must invalidate the parse cache. The
/// tsconfig is not an indexed file, so its change is invisible to
/// content-hash invalidation -- without the alias fingerprint the second
/// command answers from the first run's alias-blind parse.
#[test]
fn editing_tsconfig_invalidates_the_cached_parse() {
    let dir = tempdir().unwrap();
    let root = dir.path();

    write(root, "tsconfig.json", r#"{ "compilerOptions": {} }"#);
    write(
        root,
        "packages/server/src/permission.ts",
        r#"export function canAccess(id: string): boolean { return id.length > 0; }"#,
    );
    write(
        root,
        "apps/api/src/router.ts",
        r#"import { canAccess } from "@app/server/src/permission";

export function route(id: string): boolean { return canAccess(id); }"#,
    );

    let coordinate = "packages/server/src/permission.ts:canAccess";

    // First run builds the index with no alias declared: no edge.
    mimori(root, &["up", coordinate])
        .success()
        .stdout(predicate::str::contains("route").not());

    // Declare the alias. Source files are untouched.
    write(
        root,
        "tsconfig.json",
        r#"{ "compilerOptions": { "paths": { "@app/*": ["./packages/*"] } } }"#,
    );

    mimori(root, &["up", coordinate])
        .success()
        .stdout(predicate::str::contains("route"));
}

#[test]
fn package_import_still_suppresses_edge_with_aliases_configured() {
    let dir = tempdir().unwrap();
    let root = dir.path();

    write(
        root,
        "tsconfig.json",
        r#"{
  "compilerOptions": {
    "paths": {
      "@app/*": ["./packages/*"]
    }
  }
}"#,
    );
    write(
        root,
        "packages/server/src/local-utils.ts",
        r#"export function parseLogs(raw: string): string {
    return raw.trim();
}
"#,
    );
    write(
        root,
        "apps/consumer.ts",
        r#"import { parseLogs } from "some-npm-lib";

export function run(): string {
    return parseLogs("x");
}
"#,
    );

    mimori(
        root,
        &["up", "packages/server/src/local-utils.ts:parseLogs"],
    )
    .success()
    .stdout(predicate::str::contains("run").not());
}
