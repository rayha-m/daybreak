"""Picks today's LeetCode problem and tracks progress + streak.

LeetCode has no official public API, so instead of scraping it we walk through
a curated list (Blind 75 by default, in data/). You reply /done or /skip to the
bot in Telegram, and the next run moves you forward.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_list(name_or_path: str = "blind75", include_premium: bool = False) -> list[dict]:
    path = Path(name_or_path)
    if not path.suffix:
        path = DATA_DIR / f"{name_or_path}.json"
    problems = json.loads(path.read_text())["problems"]
    return [p for p in problems if include_premium or not p.get("premium")]


def url(problem: dict) -> str:
    return f"https://leetcode.com/problems/{problem['slug']}/"


def current(problems: list[dict], lc_state: dict) -> dict | None:
    finished = set(lc_state.get("done", [])) | set(lc_state.get("skipped", []))
    for p in problems:
        if p["slug"] not in finished:
            return p
    return None   # list complete!


def mark(lc_state: dict, slug: str, how: str, today: date) -> None:
    """how = 'done' or 'skipped'. Updates the streak for 'done'."""
    lst = lc_state.setdefault(how, [])
    if slug not in lst:
        lst.append(slug)
    if how != "done":
        return
    last = lc_state.get("last_done_date")
    today_s = today.isoformat()
    if last == today_s:
        return
    yesterday = (today - timedelta(days=1)).isoformat()
    lc_state["streak"] = lc_state.get("streak", 0) + 1 if last == yesterday else 1
    lc_state["last_done_date"] = today_s


def live_streak(lc_state: dict, today: date) -> int:
    """Streak only counts if you solved one today or yesterday."""
    last = lc_state.get("last_done_date")
    if last in (today.isoformat(), (today - timedelta(days=1)).isoformat()):
        return lc_state.get("streak", 0)
    return 0


def progress(problems: list[dict], lc_state: dict) -> tuple[int, int]:
    slugs = {p["slug"] for p in problems}
    return len(slugs & set(lc_state.get("done", []))), len(problems)
