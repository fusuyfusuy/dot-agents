#!/usr/bin/env python3
"""Self-check for the quota bucket selection logic in fetch_quotas.py and
antigravity-cli/status.py: run directly with `python3 test_quota_selection.py`.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATUS_PY = os.path.join(HERE, "..", "..", "..", "..", "antigravity-cli", "status.py")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TWO_BUCKETS = {
    "claudeopus": {
        "name": "Claude Opus 4.6 (Thinking)",
        "remaining_percentage": 100.0,
        "reset_time": "2026-01-01T01:00:00Z",
    },
    "geminipro": {
        "name": "Gemini 3.1 Pro (High)",
        "remaining_percentage": 12.86,
        "reset_time": "2026-01-03T23:40:00Z",
    },
}

ONE_BUCKET = {
    "claudeopus": {
        "name": "Claude Opus 4.6 (Thinking)",
        "remaining_percentage": 100.0,
        "reset_time": "2026-01-01T01:00:00Z",
    },
    "geminipro": {
        "name": "Gemini 3.1 Pro (High)",
        "remaining_percentage": 100.0,
        "reset_time": "2026-01-01T01:00:00Z",
    },
}


def check(module_name, path):
    m = load_module(module_name, path)

    # 1. When an active model is specified, its quota is the gating constraint
    gemini_active = m.select_gating_quota(TWO_BUCKETS, "Gemini 3.1 Pro (High)")
    assert gemini_active["name"] == "Gemini 3.1 Pro (High)", f"{module_name}: should pick active Gemini model, got {gemini_active}"

    claude_active = m.select_gating_quota(TWO_BUCKETS, "Claude Opus 4.6 (Thinking)")
    assert claude_active["name"] == "Claude Opus 4.6 (Thinking)", f"{module_name}: should pick active Claude model, got {claude_active}"

    # 2. When no active model is specified, picks the most constrained (lowest remaining %)
    gating_unspecified = m.select_gating_quota(TWO_BUCKETS, "")
    assert gating_unspecified["name"] == "Gemini 3.1 Pro (High)", f"{module_name}: should pick 12.86% over 100%, got {gating_unspecified}"

    # 3. When quotas are tied (100% vs 100%), breaks ties by soonest reset time
    gating_tied = m.select_gating_quota(ONE_BUCKET, "")
    assert gating_tied["name"] == "Claude Opus 4.6 (Thinking)", f"{module_name}: should pick soonest reset on tie, got {gating_tied}"

    print(f"{module_name}: OK")


def check_claude_gating():
    fq = load_module("fetch_quotas", os.path.join(HERE, "fetch_quotas.py"))

    # 5h always gates when present, regardless of which window is tighter
    claude_7d_tighter = {
        "model": "Claude 3.7",
        "five_hour_used_pct": 8.0,
        "five_hour_resets_at": 1787095000,
        "seven_day_used_pct": 62.0,
        "seven_day_resets_at": 1787180000,
    }
    res = fq.select_claude_gating_quota(claude_7d_tighter)
    assert res["window"] == "5h" and res["remaining_pct"] == 92.0, f"Claude gating expected 5h 92%, got {res}"

    claude_5h_tighter = {
        "model": "Claude 3.7",
        "five_hour_used_pct": 90.0,
        "five_hour_resets_at": 1787095000,
        "seven_day_used_pct": 50.0,
        "seven_day_resets_at": 1787180000,
    }
    res = fq.select_claude_gating_quota(claude_5h_tighter)
    assert res["window"] == "5h" and res["remaining_pct"] == 10.0, f"Claude gating expected 5h 10%, got {res}"

    # 5h missing -> falls back to 7d
    claude_5h_missing = {
        "model": "Claude 3.7",
        "seven_day_used_pct": 62.0,
        "seven_day_resets_at": 1787180000,
    }
    res = fq.select_claude_gating_quota(claude_5h_missing)
    assert res["window"] == "7d" and res["remaining_pct"] == 38.0, f"Claude gating expected 7d fallback 38%, got {res}"

    print("claude_gating: OK")


def check_ocgo_gating():
    fq = load_module("fetch_quotas", os.path.join(HERE, "fetch_quotas.py"))

    # 5h always gates when present, regardless of which window is tighter
    ocgo_weekly_tighter = {
        "rolling": {"status": "ok", "percent": 8.0, "resetsAt": "2026-01-02T12:00:00Z"},
        "weekly": {"status": "ok", "percent": 62.0, "resetsAt": "2026-01-03T12:00:00Z"},
        "monthly": {"status": "ok", "percent": 40.0, "resetsAt": "2026-01-10T12:00:00Z"},
    }
    res = fq.select_ocgo_gating_quota(ocgo_weekly_tighter)
    assert res["label"] == "5h" and res["remaining_pct"] == 92.0, f"OCGO gating expected 5h 92%, got {res}"

    ocgo_5h_tighter = {
        "rolling": {"status": "ok", "percent": 90.0, "resetsAt": "2026-01-02T12:00:00Z"},
        "weekly": {"status": "ok", "percent": 50.0, "resetsAt": "2026-01-03T12:00:00Z"},
        "monthly": {"status": "ok", "percent": 30.0, "resetsAt": "2026-01-10T12:00:00Z"},
    }
    res = fq.select_ocgo_gating_quota(ocgo_5h_tighter)
    assert res["label"] == "5h" and res["remaining_pct"] == 10.0, f"OCGO gating expected 5h 10%, got {res}"

    # 5h missing -> falls back to weekly, then monthly
    ocgo_5h_missing = {
        "weekly": {"status": "ok", "percent": 62.0, "resetsAt": "2026-01-03T12:00:00Z"},
        "monthly": {"status": "ok", "percent": 30.0, "resetsAt": "2026-01-10T12:00:00Z"},
    }
    res = fq.select_ocgo_gating_quota(ocgo_5h_missing)
    assert res["label"] == "wk" and res["remaining_pct"] == 38.0, f"OCGO gating expected wk fallback 38%, got {res}"

    print("ocgo_gating: OK")


check("fetch_quotas", os.path.join(HERE, "fetch_quotas.py"))
check("status", STATUS_PY)
check_claude_gating()
check_ocgo_gating()
print("all quota selection self-checks passed")
