"""The main flow: read replies → gather everything → write the brief → send it."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import ai, brief, github, ical, leetcode, news, telegram
from . import state as state_mod

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Ctx:
    cfg: dict
    tz: ZoneInfo
    now: datetime
    state: dict
    dry_run: bool = False
    # These let tests swap in sample data instead of the internet:
    fetch_ics: object = ical.fetch_ics
    fetch_feed: object = news.fetch_feed
    gh_getter: object = None
    env: dict = field(default_factory=lambda: dict(os.environ))

    @property
    def today(self) -> date:
        return self.now.date()

    def secret(self, name: str) -> str:
        return (self.env.get(name) or "").strip()


def log(msg: str) -> None:
    print(msg, flush=True)


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def _hhmm(value: str) -> time:
    h, m = str(value).split(":")
    return time(int(h), int(m))


# ─────────────────────────────────────────────────────────── telegram replies ──

HELP = ("Commands: /done (solved today's LeetCode), /skip (skip it), /help.\n"
        "I read replies at the next check-in.")


def process_replies(ctx: Ctx) -> list[str]:
    """Handle /done, /skip, etc. Returns little notices to show in the next message."""
    token, chat = ctx.secret("TELEGRAM_BOT_TOKEN"), ctx.secret("TELEGRAM_CHAT_ID")
    if not (token and chat) or ctx.dry_run:
        return []
    try:
        commands, offset = telegram.read_commands(token, chat, ctx.state.get("telegram_offset"))
    except Exception as exc:
        log(f"  ! couldn't read Telegram replies: {exc}")
        return []
    ctx.state["telegram_offset"] = offset
    return apply_commands(ctx, commands)


def apply_commands(ctx: Ctx, commands: list[str]) -> list[str]:
    notices = []
    lc_cfg = ctx.cfg.get("leetcode", {})
    problems = leetcode.load_list(lc_cfg.get("list", "blind75"), lc_cfg.get("include_premium", False))
    for cmd in commands:
        if cmd in ("/done", "/skip"):
            # Credit the problem that was actually sent (falls back to the current one).
            slug = ctx.state.get("today", {}).get("leetcode")
            prob = next((p for p in problems if p["slug"] == slug), None) or \
                leetcode.current(problems, ctx.state["leetcode"])
            if not prob:
                continue
            how = "done" if cmd == "/done" else "skipped"
            already = prob["slug"] in ctx.state["leetcode"].get(how, [])
            # A /done reply sent late at night still counts for the day the problem was sent.
            sent_day = ctx.state.get("today", {}).get("date")
            credit_day = date.fromisoformat(sent_day) if sent_day else ctx.today
            leetcode.mark(ctx.state["leetcode"], prob["slug"], how, max(credit_day, ctx.today - timedelta(days=1)))
            if not already:
                notices.append(("✅ Solved " if how == "done" else "⏭ Skipped ") + prob["title"])
        elif cmd in ("/help", "/start"):
            notices.append(HELP)
    return notices


# ───────────────────────────────────────────────────────────────── gathering ──

def gather_calendars(ctx: Ctx, start: datetime, end: datetime) -> tuple[list, list]:
    """Returns (calendar events, canvas due items)."""
    events, due = [], []
    for cal in ctx.cfg.get("calendars", []):
        url = ctx.secret(cal.get("secret", ""))
        if not url:
            continue
        name = cal.get("name", "Calendar")
        try:
            items = ical.parse_ics(ctx.fetch_ics(url), ctx.tz, start, end, source=name)
        except Exception as exc:
            log(f"  ! {name} calendar failed: {exc}")
            continue
        (due if name.lower() == "canvas" else events).extend(items)
    return events, due


def gather_projects(ctx: Ctx) -> list[github.ProjectStatus]:
    token = ctx.secret("GH_PAT") or ctx.secret("GITHUB_TOKEN") or None
    projects = list(ctx.cfg.get("projects") or [])
    getter = ctx.gh_getter or github._get
    if not projects and ctx.cfg.get("github_user"):
        try:
            repos = [r["full_name"] for r in getter(
                f"/users/{ctx.cfg['github_user']}/repos", token, {"sort": "pushed", "per_page": 20})
                if not r.get("fork") and not r.get("archived")][:3]
            projects = [{"name": r.split("/")[-1], "repo": r} for r in repos]
        except Exception as exc:
            log(f"  ! couldn't list GitHub repos: {exc}")
    return [github.status(p, token, getter=getter) for p in projects]


# ─────────────────────────────────────────────────────────────────── morning ──

def morning(ctx: Ctx, notices: list[str]) -> str:
    cfg, today = ctx.cfg, ctx.today
    lookahead = int(cfg.get("due_lookahead_days", 3))
    day_start, day_end = ical.day_bounds(today, ctx.tz)

    log("• Reading calendars…")
    all_events, due = gather_calendars(ctx, day_start, day_start + timedelta(days=lookahead + 1))
    todays = [e for e in all_events if e.start < day_end and (e.end > day_start or e.start >= day_start)]
    # "Due soon" = from now on (not things due earlier this morning that already passed).
    due = sorted([d for d in due if d.start >= ctx.now - timedelta(hours=1) or d.all_day and d.start.date() >= today],
                 key=lambda e: e.start)

    log("• Reading news feeds…")
    goals = cfg.get("goals", [])
    candidates = news.collect(goals, set(ctx.state["seen_articles"]), ctx.now,
                              fetcher=ctx.fetch_feed, log=log)

    log("• Checking projects…")
    projects = gather_projects(ctx)

    log("• Planning the day…")
    plan = ai.plan(goals, candidates, projects, brief.schedule_summary(todays, due),
                   ctx.state["recent_assignments"], today,
                   api_key=ctx.secret("GEMINI_API_KEY") or None, log=log)

    lc_cfg = cfg.get("leetcode", {})
    problem, progress, streak, lc_url = None, (0, 0), 0, ""
    if lc_cfg.get("enabled", True):
        problems = leetcode.load_list(lc_cfg.get("list", "blind75"), lc_cfg.get("include_premium", False))
        problem = leetcode.current(problems, ctx.state["leetcode"])
        progress = leetcode.progress(problems, ctx.state["leetcode"])
        streak = leetcode.live_streak(ctx.state["leetcode"], today)
        lc_url = leetcode.url(problem) if problem else ""

    text = brief.morning(cfg.get("name", ""), today, ctx.now, todays, due, plan, problem, lc_url,
                         progress, streak, notices, lookahead)

    # Remember what we sent so we don't repeat it and the evening check-in knows today's problem.
    ctx.state["seen_articles"] += [a["article"].link for a in plan["articles"]]
    ctx.state["recent_assignments"] += [f"{a.get('goal')}: {a['task']}" for a in plan["assignments"]]
    ctx.state["today"] = {"date": today.isoformat(), "leetcode": problem["slug"] if problem else None}
    log(f"• Done ({'AI' if plan.get('ai') else 'fallback'} plan, {len(todays)} events, "
        f"{len(due)} due, {len(plan['articles'])} articles)")
    return text


# ─────────────────────────────────────────────────────────────────── evening ──

def evening(ctx: Ctx, notices: list[str]) -> str | None:
    today = ctx.today
    _, due = gather_calendars(ctx, ctx.now, ical.day_bounds(today + timedelta(days=1), ctx.tz)[1])
    lc_cfg = ctx.cfg.get("leetcode", {})
    problem, lc_url, solved_today = None, "", False
    if lc_cfg.get("enabled", True):
        problems = leetcode.load_list(lc_cfg.get("list", "blind75"), lc_cfg.get("include_premium", False))
        slug = ctx.state.get("today", {}).get("leetcode")
        problem = next((p for p in problems if p["slug"] == slug), None)
        solved_today = ctx.state["leetcode"].get("last_done_date") == today.isoformat()
        if problem and problem["slug"] in ctx.state["leetcode"].get("skipped", []):
            problem = None
        lc_url = leetcode.url(problem) if problem else ""
    streak = leetcode.live_streak(ctx.state["leetcode"], today)
    return brief.evening(today, ctx.now, due, problem, lc_url, solved_today, streak, notices)


# ──────────────────────────────────────────────────────────────── entrypoint ──

def decide_mode(ctx: Ctx) -> str | None:
    """For scheduled runs: send the morning brief once after morning_time (until noon),
    and the evening check-in once after evening_time."""
    t, today = ctx.now.time(), ctx.today.isoformat()
    if _hhmm(ctx.cfg.get("morning_time", "07:00")) <= t < time(12, 0) \
            and ctx.state.get("last_morning") != today:
        return "morning"
    if ctx.cfg.get("evening_enabled", True) and t >= _hhmm(ctx.cfg.get("evening_time", "20:30")) \
            and ctx.state.get("last_evening") != today:
        return "evening"
    return None


def run(mode: str = "auto", config_path: Path = ROOT / "config.yaml",
        state_path: Path = ROOT / "state.json", dry_run: bool = False,
        now: datetime | None = None, **overrides) -> str | None:
    cfg = load_config(config_path)
    tz = ZoneInfo(cfg.get("timezone", "America/Chicago"))
    ctx = Ctx(cfg=cfg, tz=tz, now=(now or datetime.now(tz)).astimezone(tz),
              state=state_mod.load(state_path), dry_run=dry_run, **overrides)

    if mode == "auto":
        mode = decide_mode(ctx)
        if not mode:
            log(f"Nothing to send at {ctx.now:%H:%M} (morning/evening already sent or not time yet).")
            return None
    log(f"Daybreak: {mode} run for {ctx.today} at {ctx.now:%H:%M %Z}")

    notices = process_replies(ctx)
    text = morning(ctx, notices) if mode == "morning" else evening(ctx, notices)
    ctx.state[f"last_{mode}"] = ctx.today.isoformat()

    if dry_run:
        log("\n──────── preview (not sent) ────────\n" + (text or "(nothing to send)"))
        return text
    if text:
        token, chat = ctx.secret("TELEGRAM_BOT_TOKEN"), ctx.secret("TELEGRAM_CHAT_ID")
        if not (token and chat):
            raise SystemExit("Missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID. See README step 3.")
        telegram.send(token, chat, text)
        log("• Sent to Telegram ✔")
    state_mod.save(state_path, ctx.state)
    return text
