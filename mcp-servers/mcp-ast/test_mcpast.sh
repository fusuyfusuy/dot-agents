#!/usr/bin/env bash
# test_mcpast.sh — runnable assert suite for the mcp-ast MCP server (no framework).
# Run: bash tui-agent-settings/mcp-servers/mcp-ast/test_mcpast.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
NODE="$(command -v node)"
SG_VERSION_OK="$("$HOME/.pi/agent/npm/node_modules/@ast-grep/cli/ast-grep" --version 2>/dev/null | grep -ci ast-grep || true)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
call() { # call <tool> <json-args>
  printf '{"id":1,"method":"tools/call","params":{"name":"%s","arguments":%s}}' "$1" "$2" |
    "$NODE" "$DIR/server.js" | python3 -c 'import json,sys;d=json.load(sys.stdin)["result"];print(("ERR " if d.get("isError") else "OK  ")+d["content"][0]["text"])'
}

printf 'def fmt(x):\n    print(fmt_val(x))\n' >"$TMP/a.py"

# T1: real ast-grep preview shows a diff
OUT=$(call structural_edit '{"filePath":"'"$TMP"'/a.py","pattern":"print($X)","rewrite":"human($X)","preview":true}')
echo "$OUT" | grep -q "ast-grep preview" && echo "$OUT" | grep -q "human(fmt_val(x))" || {
  echo "FAIL T1: $OUT"
  exit 1
}
grep -q "print(fmt_val" "$TMP/a.py" || {
  echo "FAIL T1b: preview must not write"
  exit 1
}
echo "pass T1 preview-diff-no-write"

# T2: apply rewrites structurally
OUT=$(call structural_edit '{"filePath":"'"$TMP"'/a.py","pattern":"print($X)","rewrite":"human($X)"}')
grep -q "human(fmt_val(x))" "$TMP/a.py" && ! grep -q "print(fmt_val" "$TMP/a.py" || {
  echo "FAIL T2: $OUT"
  exit 1
}
echo "pass T2 apply"

# T3: genuine no-match is a clean error
OUT=$(call structural_edit '{"filePath":"'"$TMP"'/a.py","pattern":"zzz($Q)","rewrite":"y($Q)"}')
echo "$OUT" | grep -q "^ERR" && echo "$OUT" | grep -q "no match" || {
  echo "FAIL T3: $OUT"
  exit 1
}
echo "pass T3 clean-no-match"

# T4: ast_edit reports applied vs NOT-applied ops honestly
printf 'const x = 1;\n' >"$TMP/b.js"
OUT=$(call ast_edit '{"filePath":"'"$TMP"'/b.js","operations":[{"op":"add_import","moduleSpecifier":"node:fs"},{"op":"replace","oldText":"const x = 1;","newText":"const x = 2;"},{"op":"replace","oldText":"MISSING NEEDLE","newText":"y"}]}')
echo "$OUT" | grep -q "text replace (literal, not AST)" && echo "$OUT" | grep -q "NOT applied" && echo "$OUT" | grep -q "needle not found" || {
  echo "FAIL T4: $OUT"
  exit 1
}
grep -q "const x = 2;" "$TMP/b.js" && head -1 "$TMP/b.js" | grep -q 'import' || {
  echo "FAIL T4b"
  exit 1
}
echo "pass T4 honest-op-reporting"

# T5: without any ast-grep on the system, fallback is labeled, never silent
if [ "${SG_VERSION_OK:-0}" -ge 1 ]; then
  OUT=$(printf '{"id":1,"method":"tools/call","params":{"name":"structural_edit","arguments":{"filePath":"'"$TMP"'/a.py","pattern":"human(","rewrite":"HUMAN("}}}' |
    env HOME="$TMP/nohome" PATH=/usr/bin:/bin "$NODE" "$DIR/server.js" | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["content"][0]["text"])')
  echo "$OUT" | grep -q "TEXT FALLBACK" || {
    echo "FAIL T5: $OUT"
    exit 1
  }
  echo "pass T5 labeled-fallback"
else
  echo "skip T5 (ast-grep not installed here)"
fi

# T6: tool descriptions carry honesty markers
DESCS=$(printf '{"id":1,"method":"tools/list"}' | "$NODE" "$DIR/server.js")
echo "$DESCS" | grep -qi "literal text" && echo "$DESCS" | grep -qi "no PageRank" || {
  echo "FAIL T6"
  exit 1
}
echo "pass T6 honest-descriptions"

echo "ALL PASS"
