"""Builds a brief for a website visitor, on demand.

The scheduled bot (run.py) reads YOUR settings from config.yaml and GitHub
Secrets. This file is the "try it" version: everything comes from the web form,
nothing is saved, and the visitor can bring their own Gemini key.

It reuses the same pieces as the bot (calendar parser, news reader, LeetCode
list, GitHub check, AI planner, message formatter), so the website shows exactly
what the real bot would send.
"""

from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from zoneinfo import ZoneInfo

import yaml

from . import ai, brief, github, ical, leetcode, news

ROOT = Path(__file__).resolve().parent.parent
MAX_BYTES = 3_000_000          # don't download anything bigger than ~3 MB
MAX_GOALS = 4


LEETCODE_WORDS = ("leetcode", "leet code", "coding interview", "blind 75", "neetcode", "dsa")
PRACTICE_TASKS = [
    "Spend 20 focused minutes on one small skill and repeat it until it feels easier.",
    "Review what you practiced last time for 5 minutes, then try something slightly harder for 15.",
    "Set a 25-minute timer and work on the part you usually avoid.",
    "Practice for 15 minutes, then write one sentence about what improved.",
]


class BadInput(ValueError):
    """Something the visitor typed that we can't use (shown to them as-is)."""


# ───────────────────────────────────────────────────────── safe downloading ──
# The server downloads links that visitors paste in. To stop someone from using
# that to poke at private/internal addresses, only allow normal public websites.

def _check_public_url(url: str) -> str:
    url = url.strip()
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise BadInput(f"That doesn't look like a web link: {url[:60]}")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        raise BadInput(f"Couldn't find the website {parsed.hostname}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise BadInput("Links must point to a public website.")
    return url


def safe_get(url: str, timeout: int = 10) -> str:
    import requests
    from urllib.parse import urljoin
    for _ in range(5):                        # follow redirects ourselves, checking each hop
        url = _check_public_url(url)
        r = requests.get(url, timeout=timeout, stream=True, allow_redirects=False,
                         headers={"User-Agent": "Mozilla/5.0 (Daybreak demo)"})
        if r.is_redirect:
            url = urljoin(url, r.headers.get("Location", ""))
            r.close()
            continue
        break
    else:
        raise BadInput("That link redirects too many times.")
    with r:
        r.raise_for_status()
        body = b""
        for chunk in r.iter_content(65536):
            body += chunk
            if len(body) > MAX_BYTES:
                raise BadInput("That link returned a file that's too large.")
        return body.decode(r.encoding or "utf-8", errors="replace")


# ─────────────────────────────────────────────────────────────── the brief ──

def _preset_feeds() -> dict[str, list]:
    """Hand-picked feeds from config.yaml, used when a visitor's goal matches one."""
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text()) or {}
    return {g["name"].strip().lower(): g.get("feeds", []) for g in cfg.get("goals", [])}


def google_news_feed(topic: str) -> dict:
    # Google News makes an RSS feed for any search, so ANY goal gets fresh articles.
    q = quote_plus(f"{topic} when:3d")
    return {"name": "", "url": f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"}


def _tidy_google_titles(articles: dict[str, list]) -> None:
    # Google News titles look like "Headline - Publisher"; split them apart.
    for items in articles.values():
        for a in items:
            if "news.google.com" in a.link and " - " in a.title:
                a.title, a.source = a.title.rsplit(" - ", 1)


def build(form: dict, fetch=safe_get, gh_getter=None, now: datetime | None = None,
          log=lambda m: None) -> dict:
    """form keys: name, timezone, goals [{name, description}], calendar_url,
    assignments_url, practice, leetcode_done, projects, github_user, gemini_key.
    Returns {html, notes, ai}."""
    try:
        tz = ZoneInfo(form.get("timezone") or "America/Chicago")
    except Exception:
        tz = ZoneInfo("America/Chicago")
    now = (now or datetime.now(tz)).astimezone(tz)
    today = now.date()
    notes: list[str] = []

    goals = [g for g in (form.get("goals") or []) if (g.get("name") or "").strip()][:MAX_GOALS]
    if not goals:
        raise BadInput("Add at least one learning goal.")
    presets = _preset_feeds()
    for g in goals:
        g["name"] = g["name"].strip()[:40]
        g["description"] = (g.get("description") or "").strip()[:400]
        g["feeds"] = presets.get(g["name"].lower(), [])[:2] + [google_news_feed(g["name"])]

    # Download every feed and calendar at the same time (much faster than one by one).
    urls = {f["url"] for g in goals for f in g["feeds"]}
    assignments_url = form.get("assignments_url") or form.get("canvas_url")
    cal_links = [("Calendar", (form.get("calendar_url") or "").strip()),
                 ("Assignments", (assignments_url or "").strip())]
    urls |= {u for _, u in cal_links if u}
    results: dict[str, str | Exception] = {}

    def grab(u):
        try:
            return u, fetch(u)
        except Exception as exc:
            return u, exc
    with ThreadPoolExecutor(max_workers=8) as pool:
        for u, res in pool.map(grab, urls):
            results[u] = res

    def cached(u):
        res = results[u]
        if isinstance(res, Exception):
            raise res
        return res

    # Calendars
    lookahead = 3
    day_start, day_end = ical.day_bounds(today, tz)
    events, due = [], []
    for name, url in cal_links:
        if not url:
            continue
        try:
            # "Canvas" mode also tidies Canvas-style "[COURSE-101]" tags; harmless for other schools' feeds.
            items = ical.parse_ics(cached(url), tz, day_start, day_start + timedelta(days=lookahead + 1),
                                   "Canvas" if name == "Assignments" else name)
        except BadInput as exc:
            notes.append(f"{name}: {exc}")
            continue
        except Exception:
            notes.append(f"{name}: couldn't read that link. Make sure it's the iCal/feed link (ends in .ics).")
            continue
        if name == "Assignments":
            due += items
        else:
            events += [e for e in items if e.start < day_end and (e.end > day_start or e.start >= day_start)]
    due = sorted([d for d in due if d.start >= now - timedelta(hours=1) or
                  (d.all_day and d.start.date() >= today)], key=lambda e: e.start)

    # Daily practice. Coding-interview practice uses the Blind 75 tracker;
    # anything else (Spanish, piano, running…) gets a short daily exercise.
    practice = (form.get("practice") or "").strip()[:60]
    use_leetcode = any(w in practice.lower() for w in LEETCODE_WORDS)
    practice_goal = None
    if practice and not use_leetcode:
        practice_goal = {
            "name": practice,
            "description": f"Daily practice of {practice}. No article needed: give one concrete, "
                           "15-30 minute exercise that builds on basics and gets a bit harder over time.",
            "feeds": [],
            "tasks": PRACTICE_TASKS,
        }

    # News
    candidates = news.collect(goals, set(), now, fetcher=cached, log=log, per_goal=10)
    _tidy_google_titles(candidates)
    for g in goals:
        if not candidates.get(g["name"]):
            notes.append(f"No recent articles found for “{g['name']}”. Try a broader topic.")

    # Projects: whatever they typed, plus recent GitHub repos if they gave a username.
    projects = []
    for line in (form.get("projects") or "").splitlines()[:5]:
        line = line.strip(" -•\t")
        if not line:
            continue
        name, _, notes_text = line.partition(":")
        projects.append(github.ProjectStatus(name=name.strip()[:40], notes=notes_text.strip()[:200]))
    user = (form.get("github_user") or "").strip().lstrip("@")
    if user:
        getter = gh_getter or github._get
        try:
            repos = [r["full_name"] for r in getter(f"/users/{quote_plus(user)}/repos", None,
                                                    {"sort": "pushed", "per_page": 20})
                     if not r.get("fork") and not r.get("archived")][:3]
            projects += [github.status({"name": r.split("/")[-1], "repo": r}, None, getter=getter)
                        for r in repos]
            if not repos:
                notes.append(f"GitHub user {user} has no public repos to look at.")
        except Exception:
            notes.append(f"Couldn't load GitHub repos for {user}.")

    # AI plan (only with the visitor's own key)
    key = (form.get("gemini_key") or "").strip() or None
    all_goals = goals + ([practice_goal] if practice_goal else [])
    plan = ai.plan(all_goals, candidates, projects, brief.schedule_summary(events, due), [], today,
                   api_key=key, log=log, quick=True)
    if key and not plan.get("ai"):
        notes.append("Gemini didn't respond (busy or invalid key), so this uses the built-in assignments.")

    # LeetCode tracker (only if their practice is coding interviews)
    problems, lc_state, problem = [], {"done": [], "skipped": []}, None
    if use_leetcode:
        problems = leetcode.load_list()
        try:
            done_count = max(0, min(int(form.get("leetcode_done") or 0), len(problems)))
        except ValueError:
            done_count = 0
        lc_state = {"done": [p["slug"] for p in problems[:done_count]], "skipped": []}
        problem = leetcode.current(problems, lc_state)

    html = brief.morning(
        (form.get("name") or "").strip()[:40], today, now, events, due, plan, problem,
        leetcode.url(problem) if problem else "",
        leetcode.progress(problems, lc_state) if problems else (0, 0),
        0, [], lookahead)
    return {"html": html, "notes": notes, "ai": bool(plan.get("ai"))}
