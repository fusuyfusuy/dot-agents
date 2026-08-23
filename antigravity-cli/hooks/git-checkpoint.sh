#!/usr/bin/env bash
# git-checkpoint.sh — PreToolUse hook for Antigravity (agy)
# Automatically creates a lightweight checkpoint ref before modifying files.

set -euo pipefail

# Only run if inside a valid git repository
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  exit 0
fi

# Check if there are modified or uncommitted files
if ! git status --porcelain 2>/dev/null | grep -q .; then
  exit 0
fi

# Create a local checkpoint ref
CHECKPOINT_NAME="agy-checkpoint-$(date +%Y%m%d%H%M%S)"
# Update a lightweight ref in refs/checkpoints/ (non-intrusive, does not touch branches or working tree)
COMMIT_HASH=$(git stash create "Auto-checkpoint by agy before edit" 2>/dev/null || true)
if [[ -n "$COMMIT_HASH" ]]; then
  git update-ref "refs/agy-checkpoints/$CHECKPOINT_NAME" "$COMMIT_HASH" 2>/dev/null || true
fi

exit 0
