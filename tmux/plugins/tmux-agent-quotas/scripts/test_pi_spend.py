#!/usr/bin/env python3
"""Self-check for the pi today-spend logic in fetch_quotas.py:
run directly with `python3 test_pi_spend.py`.
"""
import importlib.util
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FETCH_PY = os.path.join(HERE, "fetch_quotas.py")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fq = load_module("fetch_quotas", FETCH_PY)

# --- entry_cost_total ---
assistant = {
    "type": "message",
    "message": {"role": "assistant", "usage": {"cost": {"input": 0.1, "output": 0.4, "total": 0.5}}},
}
assert fq.entry_cost_total(assistant) == 0.5, "assistant message cost"

user_msg = {"type": "message", "message": {"role": "user", "content": []}}
assert fq.entry_cost_total(user_msg) == 0.0, "user message has no cost"

tool_result = {
    "type": "message",
    "message": {"role": "toolResult", "usage": {"cost": {"total": 0.03}}},
}
assert fq.entry_cost_total(tool_result) == 0.03, "toolResult cost"

compaction = {"type": "compaction", "usage": {"cost": {"total": 0.1}}}
assert fq.entry_cost_total(compaction) == 0.1, "compaction cost"

branch = {"type": "branch_summary", "usage": {"cost": {"total": 0.07}}}
assert fq.entry_cost_total(branch) == 0.07, "branch_summary cost"

no_cost = {"type": "message", "message": {"role": "assistant", "usage": {"cost": {"total": None}}}}
assert fq.entry_cost_total(no_cost) == 0.0, "missing/None cost -> 0"

bad_cost = {"type": "message", "message": {"role": "assistant", "usage": {"cost": {"total": "abc"}}}}
assert fq.entry_cost_total(bad_cost) == 0.0, "non-numeric cost -> 0"

unknown_type = {"type": "model_change", "usage": {"cost": {"total": 9.9}}}
assert fq.entry_cost_total(unknown_type) == 0.0, "non-usage entry types are skipped"

# --- compute_pi_today_spend over a synthetic sessions dir ---
lines_today = [
    json.dumps(assistant),      # 0.50
    json.dumps(user_msg),       # 0.00
    json.dumps(tool_result),    # 0.03
    json.dumps(compaction),     # 0.10
    json.dumps(branch),         # 0.07
    "this is not json {{{",     # skipped
    "",                         # blank line skipped
    json.dumps(bad_cost),       # 0.00
]
expected = 0.5 + 0.03 + 0.1 + 0.07  # 0.70

old_assistant = dict(assistant)
with tempfile.TemporaryDirectory() as root:
    today_file = os.path.join(root, "sub", "2026-08-19T00-00-00-000Z_x.jsonl")
    os.makedirs(os.path.dirname(today_file), exist_ok=True)
    with open(today_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines_today) + "\n")

    old_file = os.path.join(root, "2026-06-11T00-00-00-000Z_y.jsonl")
    with open(old_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(assistant) + "\n")  # same shape, must be excluded
    old_ts = time.time() - 3 * 24 * 3600  # three days ago
    os.utime(old_file, (old_ts, old_ts))

    # stale entry inside today's file still counts (file mtime is the gate)
    with open(os.path.join(root, "README.txt"), "w", encoding="utf-8") as f:
        f.write("not a session")

    total = fq.compute_pi_today_spend(root)
    assert abs(total - expected) < 1e-9, f"expected {expected}, got {total}"
    assert total == fq.compute_pi_today_spend(root), "idempotent"

assert fq.compute_pi_today_spend("/nonexistent/dir") == 0.0, "missing dir -> 0"

# --- format ---
assert fq.format_pi_spend(3.42) == "$3.42"
assert fq.format_pi_spend(0.049) == "$0.05"
assert fq.format_pi_spend(0.0) == "$0.00"

print("pi spend self-checks passed")