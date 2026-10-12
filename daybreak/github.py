"""Looks at your GitHub projects so the brief can suggest a concrete next step.

Uses GitHub's public REST API. On GitHub Actions, the built-in GITHUB_TOKEN is
passed in automatically, which raises the rate limit. To include PRIVATE repos,
add a personal access token as the GH_PAT secret (see README).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

API = "https://api.github.com"


@dataclass
class ProjectStatus:
    name: str
    repo: str = ""
    notes: str = ""                     # your own description / to-do list from config.yaml
    last_commit: str = ""
    last_commit_when: datetime | None = None
    open_issues: list[str] = field(default_factory=list)
    error: str = ""


def _get(path: str, token: str | None, params: dict | None = None):
    import requests
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Daybreak/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(API + path, headers=headers, params=params or {}, timeout=15)
    r.raise_for_status()
    return r.json()


def recent_repos(user: str, token: str | None, limit: int = 3) -> list[str]:
    """Your most recently pushed-to repos (used when config lists none)."""
    repos = _get(f"/users/{user}/repos", token, {"sort": "pushed", "per_page": 20})
    return [r["full_name"] for r in repos if not r.get("fork") and not r.get("archived")][:limit]


def status(project: dict, token: str | None, getter=_get) -> ProjectStatus:
    st = ProjectStatus(name=project.get("name") or project.get("repo", "project"),
                       repo=project.get("repo", ""), notes=project.get("notes", ""))
    if not st.repo:
        return st
    try:
        commits = getter(f"/repos/{st.repo}/commits", token, {"per_page": 10})
        # Skip Daybreak's own daily save commits; we want YOUR last real change.
        commits = [c for c in commits if "[skip ci]" not in c["commit"]["message"]]
        if commits:
            c = commits[0]["commit"]
            st.last_commit = c["message"].splitlines()[0][:120]
            st.last_commit_when = datetime.fromisoformat(c["committer"]["date"].replace("Z", "+00:00"))
        issues = getter(f"/repos/{st.repo}/issues", token,
                        {"state": "open", "per_page": 5, "sort": "updated"})
        # The issues endpoint also returns pull requests; keep real issues only.
        st.open_issues = [i["title"] for i in issues if "pull_request" not in i][:5]
    except Exception as exc:
        st.error = str(exc)[:120]
    return st
