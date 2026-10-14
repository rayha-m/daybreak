"""The "smart" part: Gemini picks the best articles for your goals, writes a
small daily assignment for each goal, and suggests a next step per project.

Why Gemini: its API has a free tier (no credit card), and one request per day is
far under its limits. Everything here also has a non-AI fallback, so the brief
still arrives if the key is missing or the API is having a bad day.
"""

from __future__ import annotations

import json
import os
from datetime import date

from .github import ProjectStatus
from .news import Article

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-flash-latest"        # alias Google points at its newest Flash model
# Tried in order if the main model is overloaded or retired. All are on the free tier.
BACKUP_MODELS = ["gemini-3.5-flash", "gemini-flash-lite-latest"]


def build_prompt(goals: list[dict], candidates: dict[str, list[Article]],
                 projects: list[ProjectStatus], day_summary: str,
                 recent_assignments: list[str], today: date) -> tuple[str, dict[str, Article]]:
    """Returns the prompt and an id->Article map so the model only refers to real links."""
    by_id: dict[str, Article] = {}
    lines = [
        "You are writing part of a student's personal morning brief.",
        f"Today is {today:%A, %B %d, %Y}.",
        "",
        "TODAY'S SCHEDULE (so you can size tasks to how busy the day is):",
        day_summary or "(nothing scheduled)",
        "",
        "LEARNING GOALS:",
    ]
    for g in goals:
        lines.append(f"- {g['name']}: {g.get('description', '')}")
    lines += ["", "CANDIDATE ARTICLES (pick by id only; never invent links):"]
    for g in goals:
        for i, a in enumerate(candidates.get(g["name"], [])):
            aid = f"{g['name'][:3].lower()}{i}"
            by_id[aid] = a
            lines.append(f"[{aid}] ({g['name']}) {a.title} | {a.source} | {a.summary[:200]}")
    lines += ["", "PROJECTS:"]
    for p in projects:
        bits = [f"- {p.name}"]
        if p.notes:
            bits.append(f"notes: {p.notes}")
        if p.last_commit:
            bits.append(f"last commit: '{p.last_commit}'")
        if p.open_issues:
            bits.append("open issues: " + "; ".join(p.open_issues))
        lines.append(" | ".join(bits))
    if recent_assignments:
        lines += ["", "ASSIGNMENTS GIVEN RECENTLY (don't repeat these):"]
        lines += [f"- {a}" for a in recent_assignments[-15:]]
    lines += [
        "",
        "Return JSON with exactly these keys:",
        '{"articles": [{"id": "...", "why": "one short sentence: what it teaches or why it matters today"}],',
        ' "assignments": [{"goal": "...", "task": "a concrete task doable in 15-30 minutes"}],',
        ' "projects": [{"name": "...", "next_step": "one specific, doable-today next step"}],',
        ' "tip": "one short motivating or practical line for the day"}',
        "Rules: pick 1 article per goal (2 if the day is light), preferring pieces that",
        "explain something or cover an important current event over listicles or ads.",
        "One assignment per goal; build on the chosen article when it fits. If the",
        "schedule is packed, make tasks smaller. Plain text only, no markdown.",
    ]
    return "\n".join(lines), by_id


def call_gemini(prompt: str, api_key: str, model: str | None = None, timeout: int = 60,
                sleep=None, log=print, waits=(0, 8, 20)) -> dict:
    """Asks Gemini, retrying when Google's servers are busy.

    429 = too many requests, 500/503 = Google overloaded. Those usually clear up
    within seconds, so we wait and try again, then try the backup models.
    """
    import time
    import requests
    sleep = sleep or time.sleep
    models = [model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL, *BACKUP_MODELS]
    last_error = None
    for m in dict.fromkeys(models):            # removes duplicates, keeps order
        for attempt, wait in enumerate(waits):
            if wait:
                log(f"  … Gemini busy ({last_error}), retrying {m} in {wait}s")
                sleep(wait)
            resp = requests.post(
                GEMINI_URL.format(model=m),
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.7},
                },
                timeout=timeout,
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = resp.status_code
                continue                        # busy: wait and retry
            if resp.status_code == 404:
                last_error = 404
                break                           # model doesn't exist: go to backup model
            resp.raise_for_status()             # anything else (bad key, etc.) is a real error
            parts = resp.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            if text.startswith("```"):   # just in case the model wraps it in a code block
                text = text.strip("`").removeprefix("json").strip()
            return json.loads(text)
    raise RuntimeError(f"Gemini unavailable after retries (last error {last_error})")


# ---------------------------------------------------------------- fallback --

_FALLBACK_TASKS = [
    "Read today's {goal} article and write 3 bullet points on what you learned.",
    "Find one term in today's {goal} article you didn't know; explain it in 2 sentences.",
    "Write down one question today's {goal} reading raised, and spend 15 min answering it.",
    "Summarize today's {goal} article in one tweet-length sentence.",
    "Connect today's {goal} article to something from class or a project you're working on.",
]


def fallback(goals: list[dict], candidates: dict[str, list[Article]],
             projects: list[ProjectStatus], today: date) -> dict:
    articles, assignments, steps = [], [], []
    for g in goals:
        pool = candidates.get(g["name"], [])
        if pool:
            articles.append({"article": pool[0], "why": ""})
        tasks = g.get("tasks") or _FALLBACK_TASKS
        assignments.append({"goal": g["name"],
                            "task": tasks[today.toordinal() % len(tasks)].format(goal=g["name"])})
    for p in projects:
        if p.open_issues:
            step = f"Work on: {p.open_issues[0]}"
        elif p.last_commit:
            step = f"Pick up where you left off ({p.last_commit})"
        elif p.notes:
            step = p.notes.split(";")[0].strip()
        else:
            continue
        steps.append({"name": p.name, "next_step": step})
    return {"articles": articles, "assignments": assignments, "projects": steps, "tip": ""}


def plan(goals, candidates, projects, day_summary, recent_assignments, today,
         api_key: str | None, log=print, quick: bool = False) -> dict:
    """Returns {'articles': [{'article': Article, 'why'}], 'assignments', 'projects', 'tip', 'ai': bool}"""
    if api_key:
        prompt, by_id = build_prompt(goals, candidates, projects, day_summary,
                                     recent_assignments, today)
        try:
            # quick=True (the website) tries each model once so visitors aren't kept waiting.
            raw = call_gemini(prompt, api_key, log=log,
                              **({"waits": (0,), "timeout": 25} if quick else {}))
            arts = [{"article": by_id[a["id"]], "why": a.get("why", "")}
                    for a in raw.get("articles", []) if a.get("id") in by_id]
            result = {
                "articles": arts,
                "assignments": [a for a in raw.get("assignments", []) if a.get("task")],
                "projects": [p for p in raw.get("projects", []) if p.get("next_step")],
                "tip": raw.get("tip", ""),
                "ai": True,
            }
            # If the model returned nothing usable for a section, fill it from the fallback.
            fb = fallback(goals, candidates, projects, today)
            for key in ("articles", "assignments", "projects"):
                if not result[key]:
                    result[key] = fb[key]
            return result
        except Exception as exc:
            log(f"  ! Gemini failed, using fallback: {exc}")
    result = fallback(goals, candidates, projects, today)
    result["ai"] = False
    return result
