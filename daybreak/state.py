"""A small JSON file that remembers things between runs: your LeetCode progress,
which articles you've already been sent, when the last brief went out, etc.

On GitHub Actions the workflow commits this file back to your repo after each
run. That's the simplest free way to keep state without a database.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT = {
    "last_morning": None,
    "last_evening": None,
    "telegram_offset": None,
    "leetcode": {"done": [], "skipped": [], "streak": 0, "last_done_date": None},
    "seen_articles": [],
    "recent_assignments": [],
    "today": {},       # what was sent this morning (used by the evening check-in)
}


def load(path: Path) -> dict:
    data = json.loads(path.read_text()) if path.exists() else {}
    merged = json.loads(json.dumps(DEFAULT))
    merged.update(data)
    merged["leetcode"] = {**DEFAULT["leetcode"], **data.get("leetcode", {})}
    return merged


def save(path: Path, state: dict) -> None:
    # Keep the lists from growing forever.
    state["seen_articles"] = state["seen_articles"][-400:]
    state["recent_assignments"] = state["recent_assignments"][-40:]
    path.write_text(json.dumps(state, indent=2, default=str) + "\n")
