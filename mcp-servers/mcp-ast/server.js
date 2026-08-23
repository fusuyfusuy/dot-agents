#!/usr/bin/env node
/**
 * MCP AST Server — structural editing and code navigation for Antigravity (agy).
 * Exposes ast_edit, structural_edit, soul_find, soul_impact, lsp_diagnostics via Model Context Protocol (stdio).
 *
 * Engine honesty:
 * - structural_edit runs the real ast-grep (`sg`) binary when present (AST-aware,
 *   whitespace-safe). Without it, falls back to LITERAL text replace and says so
 *   in its output.
 * - ast_edit add_import is textual by design; pattern/rewrite ops route through
 *   ast-grep; oldText/newText ops are literal text replace and are labeled as such.
 * - soul_find / soul_impact are heuristics (definition weight + git churn/co-change),
 *   not a PageRank repo graph.
 */

const readline = require("node:readline");
const fs = require("node:fs");
const path = require("node:path");
const { execSync, execFileSync } = require("node:child_process");

// ---------------------------------------------------------------------------
// ast-grep engine (real structural rewriting when available)
// ---------------------------------------------------------------------------

function findSg() {
  const home = process.env.HOME || "";
  const candidates = [
    process.env.AST_GREP_BIN,
    "ast-grep",
    // pi-lens installs @ast-grep/cli under the shared npm dir
    ...["cli", "cli-linux-x64-gnu", "cli-linux-x64-musl"].flatMap((d) => [
      path.join(home, ".pi/agent/npm/node_modules/@ast-grep", d, "ast-grep"),
      path.join(home, ".pi/agent/npm/node_modules/@ast-grep", d, "sg"),
    ]),
    path.join(home, ".pi/agent/bin/sg"),
    "sg", // LAST: on Linux this is often shadow-utils setgroup, not ast-grep
  ].filter(Boolean);
  for (const bin of candidates) {
    try {
      const v = execFileSync(bin, ["--version"], {
        encoding: "utf-8",
        stdio: ["ignore", "pipe", "pipe"],
      });
      if (/ast[- ]?grep/i.test(v)) return bin; // reject same-named non-ast-grep tools
    } catch {}
  }
  return null;
}

// Run `sg run` on one file. preview=true prints a diff; otherwise applies (-U).
// Returns { ok, engine, output } — ok=false means sg found no match or errored.
function runSg(absPath, pattern, rewrite, preview) {
  const sg = findSg();
  if (!sg) return null; // engine absent → caller decides fallback
  const args = ["run", "-p", pattern];
  if (rewrite) args.push("-r", rewrite);
  if (!preview) args.push("-U"); // --update-all: apply without interactive prompt
  args.push(absPath);
  try {
    const out = execFileSync(sg, args, {
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    return { ok: true, engine: "ast-grep", output: out.trim() };
  } catch (e) {
    // Empirically (ast-grep 0.45): no-match exits 1 with EMPTY stdout+stderr;
    // anything else (noisy stderr, other codes) is an engine error.
    const err = String((e.stderr || "") + (e.stdout || "")).trim();
    if (e.status === 1 && !err) {
      return { ok: false, engine: "ast-grep", output: "no match for pattern" };
    }
    return {
      ok: false,
      engine: "ast-grep",
      output: err
        ? err.slice(0, 300)
        : `ast-grep exited ${e.status ?? "by signal"}`,
    };
  }
}

// ---------------------------------------------------------------------------
// Tool Handlers
// ---------------------------------------------------------------------------

async function handleSoulFind(args) {
  const query = String(args.query || "").trim();
  const limit = Math.min(Math.max(Number(args.limit) || 10, 1), 50);
  const cwd = process.cwd();

  if (!query) {
    return { isError: true, text: "Error: query parameter is required" };
  }

  try {
    // 1. Churn score approximation via git log
    const churnMap = new Map();
    try {
      const gitLog = execSync(
        "git log -n 50 --name-only --format='' 2>/dev/null",
        { cwd, encoding: "utf-8" },
      );
      for (const line of gitLog.split("\n")) {
        const f = line.trim();
        if (f) churnMap.set(f, (churnMap.get(f) || 0) + 1);
      }
    } catch {}

    // 2. Ripgrep search for symbol definition
    let rgOutput = "";
    try {
      const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      rgOutput = execSync(
        `rg -n -i --max-count 50 "(def |class |fn |function |export |interface |type |const |let |var ).*${escaped}" 2>/dev/null`,
        { cwd, encoding: "utf-8" },
      );
    } catch {
      // Fallback search
      try {
        rgOutput = execSync(
          `rg -n -i --max-count 30 "${query.replace(/"/g, '\\"')}" 2>/dev/null`,
          { cwd, encoding: "utf-8" },
        );
      } catch {}
    }

    const matches = [];
    for (const line of rgOutput.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const parts = trimmed.split(":");
      if (parts.length < 3) continue;
      const filePath = parts[0];
      const lineNo = parts[1];
      const lineContent = parts.slice(2).join(":").trim();
      const churn = churnMap.get(filePath) || 0;
      const isDef =
        /^(def |class |fn |function |export |interface |type )/i.test(
          lineContent,
        );
      const score = (isDef ? 10 : 1) + churn * 2;
      matches.push({ filePath, lineNo, lineContent, score });
    }

    matches.sort((a, b) => b.score - a.score);
    const topMatches = matches.slice(0, limit);

    if (!topMatches.length) {
      return { text: `No symbols or files matching "${query}" found.` };
    }

    const rows = topMatches.map(
      (m) =>
        `| \`${m.filePath}:${m.lineNo}\` | \`${m.lineContent}\` | score: ${m.score} |`,
    );
    return {
      text: `### Ranked Symbol Search for "${query}"\n\n| Location | Symbol Signature | Rank |\n| :--- | :--- | :--- |\n${rows.join("\n")}`,
    };
  } catch (err) {
    return { isError: true, text: `soul_find failed: ${err.message}` };
  }
}

async function handleSoulImpact(args) {
  const targetPath = String(args.filePath || "").trim();
  const cwd = process.cwd();

  if (!targetPath) {
    return { isError: true, text: "Error: filePath parameter is required" };
  }

  try {
    // 1. Direct Imports Search
    const baseName = path.basename(targetPath).replace(/\.[^.]+$/, "");
    let directImporters = [];
    try {
      const grepRes = execSync(
        `rg -l --max-count 20 "(from ['\\"].*${baseName}['\\"]|require\\(['\\"].*${baseName}['\\"]\\)|import .*${baseName})" 2>/dev/null`,
        { cwd, encoding: "utf-8" },
      );
      directImporters = grepRes
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
    } catch {}

    // 2. Co-change analysis via git history
    const coChanges = new Map();
    try {
      const logOut = execSync(
        `git log -n 40 --name-only --format='COMMIT_SEP' --follow -- "${targetPath}" 2>/dev/null`,
        { cwd, encoding: "utf-8" },
      );
      const commits = logOut.split("COMMIT_SEP");
      for (const c of commits) {
        const files = c
          .split("\n")
          .map((f) => f.trim())
          .filter((f) => f && f !== targetPath);
        for (const f of files) {
          coChanges.set(f, (coChanges.get(f) || 0) + 1);
        }
      }
    } catch {}

    const coChangeList = Array.from(coChanges.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10);

    let report = `### Blast Radius & Impact Analysis for \`${targetPath}\`\n\n`;
    report += `#### 1. Direct Importers (${directImporters.length} files)\n`;
    if (directImporters.length) {
      report += directImporters.map((f) => `- \`${f}\``).join("\n") + "\n";
    } else {
      report += "_No direct importers detected._\n";
    }

    report += `\n#### 2. Frequent Co-Changed Files (Historical Blast Radius)\n`;
    if (coChangeList.length) {
      report +=
        coChangeList
          .map(
            ([f, count]) =>
              `- \`${f}\` (co-changed in ${count} recent commits)`,
          )
          .join("\n") + "\n";
    } else {
      report += "_No git co-change history recorded._\n";
    }

    return { text: report };
  } catch (err) {
    return { isError: true, text: `soul_impact failed: ${err.message}` };
  }
}

async function handleAstEdit(args) {
  const filePath = String(args.filePath || "").trim();
  const ops = Array.isArray(args.operations) ? args.operations : [];

  if (!filePath) {
    return { isError: true, text: "Error: filePath is required" };
  }
  if (!ops.length) {
    return { isError: true, text: "Error: operations array is empty" };
  }

  const absPath = path.isAbsolute(filePath)
    ? filePath
    : path.join(process.cwd(), filePath);
  if (!fs.existsSync(absPath)) {
    return { isError: true, text: `Error: file not found: ${filePath}` };
  }

  let code = fs.readFileSync(absPath, "utf-8");
  const orig = code;
  const applied = [];
  const failed = [];

  for (const op of ops) {
    const opType = op.op || op.type;
    const targetName = op.name || op.targetName;

    if (opType === "add_import") {
      const moduleSpec = op.moduleSpecifier || op.module || op.from;
      const named = op.namedImports || op.named || [];
      const defImport = op.defaultImport || op.default;
      if (moduleSpec) {
        if (code.includes(moduleSpec)) {
          applied.push(`import already present: ${moduleSpec}`);
        } else {
          let importStmt = "";
          if (defImport && named.length) {
            importStmt = `import ${defImport}, { ${named.join(", ")} } from "${moduleSpec}";\n`;
          } else if (defImport) {
            importStmt = `import ${defImport} from "${moduleSpec}";\n`;
          } else if (named.length) {
            importStmt = `import { ${named.join(", ")} } from "${moduleSpec}";\n`;
          } else {
            importStmt = `import "${moduleSpec}";\n`;
          }
          code = importStmt + code;
          applied.push(`add_import: ${moduleSpec}`);
        }
      }
    } else if (opType === "replace" || opType === "set_body") {
      // Structural path: op carries pattern/rewrite → real ast-grep rewrite
      if (op.pattern && op.rewrite !== undefined) {
        const r = runSg(absPath, op.pattern, op.rewrite, false);
        if (r === null) {
          failed.push(`pattern op skipped: ast-grep binary not installed`);
        } else if (r.ok) {
          applied.push(
            `structural rewrite via ast-grep: ${op.pattern.slice(0, 40)}`,
          );
        } else {
          failed.push(`pattern op failed: ${r.output.slice(0, 120)}`);
        }
        continue;
      }
      const oldText = op.oldText || op.target;
      const newText = op.newText || op.body || op.content || "";
      if (oldText && code.includes(oldText)) {
        code = code.replace(oldText, newText);
        applied.push(
          `text replace (literal, not AST) in ${targetName || "code"}`,
        );
      } else {
        failed.push(
          `replace: needle not found${targetName ? ` for '${targetName}'` : ""} — nothing written for this op`,
        );
      }
    } else if (opType === "remove") {
      const target = op.target || targetName;
      if (target && code.includes(target)) {
        code = code.replace(target, "");
        applied.push(`remove: ${target.slice(0, 40)} (literal)`);
      } else {
        failed.push(`remove: target not found`);
      }
    } else {
      failed.push(
        `unsupported op type: ${opType} (supported: add_import, replace/set_body, remove)`,
      );
    }
  }

  if (code !== orig) {
    fs.writeFileSync(absPath, code, "utf-8");
  }
  const lines = [];
  if (applied.length) {
    lines.push(`✓ Applied ${applied.length} op(s) to \`${filePath}\`:`);
    lines.push(...applied.map((a) => `- ${a}`));
  }
  if (failed.length) {
    lines.push(`✗ NOT applied (${failed.length}) — review these:`);
    lines.push(...failed.map((a) => `- ${a}`));
  }
  if (!lines.length) {
    lines.push(`No ops applicable to \`${filePath}\`.`);
  }
  return { text: lines.join("\n") };
}

async function handleStructuralEdit(args) {
  const filePath = String(args.filePath || "").trim();
  const pattern = String(args.pattern || "");
  const rewrite = String(args.rewrite || "");
  const preview = !!args.preview;

  if (!filePath || !pattern) {
    return { isError: true, text: "Error: filePath and pattern are required" };
  }

  const absPath = path.isAbsolute(filePath)
    ? filePath
    : path.join(process.cwd(), filePath);
  if (!fs.existsSync(absPath)) {
    return { isError: true, text: `Error: file not found: ${filePath}` };
  }

  // Preferred path: real ast-grep (AST-aware, whitespace-safe)
  const r = runSg(absPath, pattern, rewrite, preview);
  if (r !== null) {
    if (r.ok) {
      const body = r.output || "(no diff shown)";
      return {
        text: `✓ ast-grep ${preview ? "preview" : "rewrite applied"} on \`${filePath}\`:\n${body.slice(0, 2000)}`,
      };
    }
    return {
      isError: true,
      text: `ast-grep: ${r.output} in \`${filePath}\` (pattern: ${pattern.slice(0, 80)})`,
    };
  }

  // Fallback: literal text replace — labeled honestly
  let code = fs.readFileSync(absPath, "utf-8");
  if (code.includes(pattern)) {
    if (preview) {
      return {
        text: `⚠ TEXT FALLBACK preview (install ast-grep for structural matching): would replace ${code.split(pattern).length - 1} literal occurrence(s) of \`${pattern.slice(0, 40)}\` in \`${filePath}\``,
      };
    }
    code = code.split(pattern).join(rewrite);
    fs.writeFileSync(absPath, code, "utf-8");
    return {
      text: `⚠ Applied via TEXT FALLBACK (ast-grep binary not found — this was a literal string replace, not an AST rewrite): \`${pattern.slice(0, 40)}\` → \`${rewrite.slice(0, 40)}\` in \`${filePath}\``,
    };
  }
  return {
    isError: true,
    text: `Pattern not found in \`${filePath}\`: ${pattern.slice(0, 80)}`,
  };
}

async function handleLspDiagnostics(args) {
  const targetPath = String(args.filePath || "").trim();
  const cwd = process.cwd();
  const absPath = targetPath
    ? path.isAbsolute(targetPath)
      ? targetPath
      : path.join(cwd, targetPath)
    : "";
  const ext = absPath ? path.extname(absPath).toLowerCase() : "";

  let report = "### LSP Diagnostics & Type Verification\n\n";
  let hasErrors = false;

  // 1. Python diagnostic check
  if (!targetPath || ext === ".py") {
    try {
      const pyCmd = absPath
        ? `python3 -m py_compile "${absPath}" 2>&1`
        : "python3 -m compileall -q . 2>&1";
      const out = execSync(pyCmd, { cwd, encoding: "utf-8" });
      if (out && out.trim()) {
        report += `#### Python Compiler Output\n\`\`\`\n${out.trim()}\n\`\`\`\n`;
        hasErrors = true;
      }
    } catch (e) {
      report += `#### ❌ Python Syntax/Compile Errors\n\`\`\`\n${(e.stdout || e.stderr || e.message).trim()}\n\`\`\`\n`;
      hasErrors = true;
    }
  }

  // 2. TypeScript / JavaScript check
  if (!targetPath || [".ts", ".tsx", ".js", ".jsx"].includes(ext)) {
    if (fs.existsSync(path.join(cwd, "tsconfig.json"))) {
      try {
        const tsCmd = "npx -y typescript tsc --noEmit 2>&1";
        const tsOut = execSync(tsCmd, { cwd, encoding: "utf-8" });
        if (tsOut && tsOut.trim()) {
          report += `#### TypeScript Diagnostic Output\n\`\`\`\n${tsOut.trim().slice(0, 2000)}\n\`\`\`\n`;
        }
      } catch (e) {
        const errOut = (e.stdout || e.stderr || e.message || "").trim();
        if (errOut) {
          report += `#### ❌ TypeScript Compilation Errors\n\`\`\`\n${errOut.slice(0, 3000)}\n\`\`\`\n`;
          hasErrors = true;
        }
      }
    }
  }

  // 3. Rust check
  if (!targetPath || ext === ".rs") {
    if (fs.existsSync(path.join(cwd, "Cargo.toml"))) {
      try {
        const cargoOut = execSync("cargo check --message-format=short 2>&1", {
          cwd,
          encoding: "utf-8",
        });
        if (cargoOut && cargoOut.includes("error")) {
          report += `#### ❌ Cargo Diagnostic Errors\n\`\`\`\n${cargoOut.trim()}\n\`\`\`\n`;
          hasErrors = true;
        }
      } catch (e) {
        report += `#### ❌ Cargo Check Errors\n\`\`\`\n${(e.stdout || e.stderr || e.message).trim()}\n\`\`\`\n`;
        hasErrors = true;
      }
    }
  }

  // 4. Go check
  if (!targetPath || ext === ".go") {
    if (fs.existsSync(path.join(cwd, "go.mod"))) {
      try {
        const goOut = execSync("go vet ./... 2>&1", { cwd, encoding: "utf-8" });
        if (goOut && goOut.trim()) {
          report += `#### ❌ Go Vet Errors\n\`\`\`\n${goOut.trim()}\n\`\`\`\n`;
          hasErrors = true;
        }
      } catch (e) {
        report += `#### ❌ Go Vet Errors\n\`\`\`\n${(e.stdout || e.stderr || e.message).trim()}\n\`\`\`\n`;
        hasErrors = true;
      }
    }
  }

  if (!hasErrors) {
    report +=
      "✅ **Zero errors found**: All syntax and compiler diagnostic checks passed cleanly.\n";
  }

  return { text: report, isError: hasErrors };
}

// ---------------------------------------------------------------------------
// MCP Protocol Definitions
// ---------------------------------------------------------------------------

const TOOLS = [
  {
    name: "lsp_diagnostics",
    description:
      "Runs real-time Language Server / compiler diagnostic checks (TypeScript, Python, Rust, Go) to find type and syntax errors before finishing tasks.",
    inputSchema: {
      type: "object",
      properties: {
        filePath: {
          type: "string",
          description: "Optional specific file to run compiler diagnostics on",
        },
      },
    },
  },
  {
    name: "soul_find",
    description:
      "Ranked symbol search using definition-weighting + git churn heuristics (approximates centrality ranking; no PageRank graph). Returns the most relevant symbols/locations for a query.",
    inputSchema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description:
            "Symbol name, function name, class, or identifier to locate",
        },
        limit: {
          type: "integer",
          description: "Maximum number of ranked results (default 10)",
        },
      },
      required: ["query"],
    },
  },
  {
    name: "soul_impact",
    description:
      "Calculates the blast radius of modifying a target file or symbol: finds direct importers and historical git co-change dependencies.",
    inputSchema: {
      type: "object",
      properties: {
        filePath: {
          type: "string",
          description: "Relative or absolute path to the file being modified",
        },
        symbolName: {
          type: "string",
          description:
            "Optional specific symbol or function name within the file",
        },
      },
      required: ["filePath"],
    },
  },
  {
    name: "ast_edit",
    description:
      "Edits TS/JS files atomically: add_import (textual), replace/set_body/remove (literal text match), or pass pattern+rewrite in an op for true ast-grep structural rewriting. Reports which ops did NOT apply.",
    inputSchema: {
      type: "object",
      properties: {
        filePath: {
          type: "string",
          description: "Relative or absolute path to the TS/JS file",
        },
        operations: {
          type: "array",
          description: "List of AST mutation operations to apply atomically",
          items: {
            type: "object",
            properties: {
              op: {
                type: "string",
                enum: [
                  "add_import",
                  "remove_import",
                  "set_body",
                  "replace",
                  "remove",
                ],
              },
              name: { type: "string" },
              module: { type: "string" },
              named: { type: "array", items: { type: "string" } },
              oldText: { type: "string" },
              newText: { type: "string" },
            },
            required: ["op"],
          },
        },
      },
      required: ["filePath", "operations"],
    },
  },
  {
    name: "structural_edit",
    description:
      "Polyglot AST pattern rewriting via ast-grep (Python, Go, Rust, Java, etc.). Falls back to labeled literal text replace if the ast-grep binary is missing. Set preview:true for a diff without writing.",
    inputSchema: {
      type: "object",
      properties: {
        filePath: {
          type: "string",
          description: "Relative or absolute path to the source file",
        },
        pattern: { type: "string", description: "AST pattern string to match" },
        rewrite: { type: "string", description: "Replacement rewrite string" },
      },
      required: ["filePath", "pattern", "rewrite"],
    },
  },
];

// ---------------------------------------------------------------------------
// JSON-RPC stdio Transport
// ---------------------------------------------------------------------------

function sendResponse(id, result, error = null) {
  const msg = { jsonrpc: "2.0", id };
  if (error) {
    msg.error = error;
  } else {
    msg.result = result;
  }
  process.stdout.write(JSON.stringify(msg) + "\n");
}

function handleMessage(line) {
  if (!line || !line.trim()) return;
  let req;
  try {
    req = JSON.parse(line);
  } catch {
    return;
  }

  const { id, method, params } = req;

  if (method === "initialize") {
    sendResponse(id, {
      protocolVersion: "2024-11-05",
      capabilities: { tools: {} },
      serverInfo: { name: "mcp-ast", version: "1.0.0" },
    });
  } else if (method === "notifications/initialized") {
    // Client acknowledgment, no response required
  } else if (method === "ping") {
    sendResponse(id, {});
  } else if (method === "tools/list") {
    sendResponse(id, { tools: TOOLS });
  } else if (method === "tools/call") {
    const name = params?.name;
    const args = params?.arguments || {};

    let handlerPromise;
    if (name === "lsp_diagnostics") handlerPromise = handleLspDiagnostics(args);
    else if (name === "soul_find") handlerPromise = handleSoulFind(args);
    else if (name === "soul_impact") handlerPromise = handleSoulImpact(args);
    else if (name === "ast_edit") handlerPromise = handleAstEdit(args);
    else if (name === "structural_edit")
      handlerPromise = handleStructuralEdit(args);
    else {
      sendResponse(id, {
        content: [{ type: "text", text: `Unknown tool: ${name}` }],
        isError: true,
      });
      return;
    }

    handlerPromise
      .then((res) => {
        sendResponse(id, {
          content: [{ type: "text", text: res.text || "" }],
          isError: !!res.isError,
        });
      })
      .catch((err) => {
        sendResponse(id, {
          content: [{ type: "text", text: `Tool error: ${err.message}` }],
          isError: true,
        });
      });
  } else if (id !== undefined) {
    sendResponse(id, null, {
      code: -32601,
      message: `Method not found: ${method}`,
    });
  }
}

const rl = readline.createInterface({ input: process.stdin, terminal: false });
rl.on("line", handleMessage);
