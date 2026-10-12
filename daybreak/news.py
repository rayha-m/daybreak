"""Reads RSS / Atom news feeds.

RSS is a free, standard format almost every news site and blog publishes, so
there's no API key, sign-up, or rate limit to deal with. You choose the feeds
per goal in config.yaml.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime


@dataclass
class Article:
    title: str
    link: str
    summary: str
    published: datetime | None
    source: str
    goal: str = ""


_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str | None, limit: int = 300) -> str:
    text = html.unescape(_TAG_RE.sub(" ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]   # drop the XML namespace


def _find(el: ET.Element, *names: str) -> ET.Element | None:
    for child in el:
        if _local(child.tag) in names:
            return child
    return None


def _date(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    try:
        dt = parsedate_to_datetime(text)                  # RSS style
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))   # Atom style
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_feed(xml_text: str, source: str = "") -> list[Article]:
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    items = [el for el in root.iter() if _local(el.tag) in ("item", "entry")]
    feed_title = ""
    channel = _find(root, "channel")
    t = _find(channel if channel is not None else root, "title")
    if t is not None and t.text:
        feed_title = t.text.strip()

    articles = []
    for it in items:
        title_el = _find(it, "title")
        link = ""
        link_el = _find(it, "link")
        if link_el is not None:
            link = (link_el.text or link_el.get("href") or "").strip()
        summ_el = _find(it, "description", "summary", "content", "encoded")
        date_el = _find(it, "pubDate", "published", "updated", "date")
        title = _clean(title_el.text if title_el is not None else "", 200)
        if not title or not link:
            continue
        articles.append(Article(
            title=title,
            link=link,
            summary=_clean(summ_el.text if summ_el is not None else ""),
            published=_date(date_el.text if date_el is not None else None),
            source=source or feed_title,
        ))
    return articles


def fetch_feed(url: str, timeout: int = 15) -> str:
    import requests
    resp = requests.get(url, timeout=timeout, headers={
        "User-Agent": "Mozilla/5.0 (Daybreak morning brief; personal use)"})
    resp.raise_for_status()
    return resp.content.decode(resp.encoding or "utf-8", errors="replace")


def collect(goals: list[dict], seen_links: set[str], now: datetime,
            max_age_hours: int = 72, per_goal: int = 12,
            fetcher=fetch_feed, log=print) -> dict[str, list[Article]]:
    """Fresh, not-yet-sent articles for each goal, newest first."""
    out: dict[str, list[Article]] = {}
    cutoff = now - timedelta(hours=max_age_hours)
    for goal in goals:
        name = goal["name"]
        pool: list[Article] = []
        for feed in goal.get("feeds", []):
            url = feed["url"] if isinstance(feed, dict) else feed
            label = feed.get("name", "") if isinstance(feed, dict) else ""
            try:
                pool += parse_feed(fetcher(url), source=label)
            except Exception as exc:   # one broken feed shouldn't ruin the brief
                log(f"  ! feed failed ({url}): {exc}")
        fresh, dupes = [], set()
        for a in pool:
            if a.link in seen_links or a.link in dupes:
                continue
            if a.published and a.published < cutoff:
                continue
            dupes.add(a.link)
            a.goal = name
            fresh.append(a)
        fresh.sort(key=lambda a: a.published or cutoff, reverse=True)
        out[name] = fresh[:per_goal]
    return out
