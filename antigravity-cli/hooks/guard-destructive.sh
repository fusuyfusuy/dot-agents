#!/usr/bin/env bash
# guard-destructive.sh — PreToolUse safety gate for Antigravity (agy)
# Blocks catastrophic commands and protects sensitive paths.

set -euo pipefail

COMMAND="${1:-}"

# If no arg passed, try reading from stdin
if [[ -z "$COMMAND" ]] && ! [ -t 0 ]; then
  COMMAND=$(cat - || true)
fi

# 1. Catastrophic root/disk destruction patterns
BLOCKED_PATTERNS=(
  "rm -rf /"
  "rm -rf /*"
  "rm -rf ~"
  "rm -rf /home"
  "mkfs"
  ":(){ :|:& };:"
  "dd if=/dev/zero"
  "> /dev/sda"
  "> /dev/nvme"
  "chmod -R 777 /"
  "chown -R"
)

for pattern in "${BLOCKED_PATTERNS[@]}"; do
  if [[ "$COMMAND" == *"$pattern"* ]]; then
    echo "❌ [guard-destructive] Blocked dangerous command matching '$pattern'" >&2
    exit 1
  fi
done

# 2. Protected credential and system files
PROTECTED_PATHS=(
  ".ssh/id_"
  "/etc/shadow"
  "/etc/passwd"
  ".aws/credentials"
)

for path in "${PROTECTED_PATHS[@]}"; do
  if [[ "$COMMAND" == *"$path"* ]] && [[ "$COMMAND" == *"rm "* || "$COMMAND" == *">"* ]]; then
    echo "❌ [guard-destructive] Blocked destructive operation on protected path '$path'" >&2
    exit 1
  fi
done

exit 0
