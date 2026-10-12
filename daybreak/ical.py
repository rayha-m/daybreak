"""Tiny iCalendar (.ics) reader.

Both Google Calendar and Canvas give you a private "iCal feed" link. Reading
that link is much simpler than either company's full API (no OAuth sign-in
flow), and it's read-only, so the bot can never change your calendar.

We only need a few fields from each event, so instead of pulling in a big
library this module handles the parts of the format that matter:
line folding, escaped text, time zones, all-day events, and repeating
events (RRULE / EXDATE / moved instances).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr

UTC = ZoneInfo("UTC")

# Canvas puts the course in brackets at the end of the title: "Quiz 3 [CHEM-107-501]"
_COURSE_RE = re.compile(r"\s*\[([^\]]+)\]\s*$")


@dataclass
class Event:
    title: str
    start: datetime            # always timezone-aware, in the user's zone
    end: datetime
    all_day: bool
    location: str = ""
    source: str = ""           # e.g. "Calendar" or "Canvas"
    course: str = ""           # filled in for Canvas items
    url: str = ""

    @property
    def is_assignment(self) -> bool:
        return self.source.lower() == "canvas"


def _unfold(text: str) -> list[str]:
    """iCal wraps long lines by starting the next line with a space/tab."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    return (value.replace("\\n", "\n").replace("\\N", "\n")
                 .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\"))


def _parse_line(line: str) -> tuple[str, dict[str, str], str]:
    """'DTSTART;TZID=America/Chicago:20261012T090000' -> (name, params, value)."""
    # The value starts after the first ':' that is not inside quotes.
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == ":" and not in_quote:
            head, value = line[:i], line[i + 1:]
            break
    else:
        return line.upper(), {}, ""
    parts = head.split(";")
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')
    return parts[0].upper(), params, value


def _parse_dt(value: str, params: dict[str, str], tz: ZoneInfo) -> tuple[datetime, bool]:
    """Return (aware datetime in `tz`, is_all_day)."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        d = datetime.strptime(value[:8], "%Y%m%d").date()
        return datetime.combine(d, time.min, tz), True
    if value.endswith("Z"):
        dt = datetime.strptime(value[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    else:
        fmt = "%Y%m%dT%H%M%S" if len(value) >= 15 else "%Y%m%dT%H%M"
        naive = datetime.strptime(value, fmt)
        zone_name = params.get("TZID")
        try:
            zone = ZoneInfo(zone_name) if zone_name else tz
        except Exception:  # Unknown/Windows-style TZID names: assume the user's zone
            zone = tz
        dt = naive.replace(tzinfo=zone)
    return dt.astimezone(tz), False


def _raw_events(text: str) -> list[dict]:
    events, current = [], None
    for line in _unfold(text):
        if not line.strip():
            continue
        name, params, value = _parse_line(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            current = {"_multi": {}}
        elif name == "END" and value.upper() == "VEVENT":
            if current is not None:
                events.append(current)
            current = None
        elif current is not None:
            if name in ("EXDATE", "RDATE"):
                current["_multi"].setdefault(name, []).append((params, value))
            else:
                current[name] = (params, value)
    return events


def parse_ics(text: str, tz: ZoneInfo, window_start: datetime, window_end: datetime,
              source: str = "Calendar") -> list[Event]:
    """All event occurrences overlapping [window_start, window_end)."""
    raws = _raw_events(text)

    # Instances that were moved/edited ("this event only") carry RECURRENCE-ID.
    overrides: dict[tuple[str, datetime], dict] = {}
    for r in raws:
        if "RECURRENCE-ID" in r:
            p, v = r["RECURRENCE-ID"]
            rid, _ = _parse_dt(v, p, tz)
            overrides[(r.get("UID", ({}, ""))[1], rid)] = r

    out: list[Event] = []
    for r in raws:
        if "DTSTART" not in r or "RECURRENCE-ID" in r:
            continue
        if r.get("STATUS", ({}, ""))[1].upper() == "CANCELLED":
            continue
        start, all_day = _parse_dt(r["DTSTART"][1], r["DTSTART"][0], tz)
        if "DTEND" in r:
            end, _ = _parse_dt(r["DTEND"][1], r["DTEND"][0], tz)
        elif "DURATION" in r:
            end = start + _parse_duration(r["DURATION"][1])
        else:
            end = start + (timedelta(days=1) if all_day else timedelta(0))
        length = end - start
        uid = r.get("UID", ({}, ""))[1]

        starts = [start]
        if "RRULE" in r:
            starts = _expand(r, start, tz, window_start - length, window_end)

        for s in starts:
            occ = overrides.get((uid, s), r)
            if occ is not r:   # use the edited copy of this one occurrence
                if occ.get("STATUS", ({}, ""))[1].upper() == "CANCELLED":
                    continue
                s, all_day_o = _parse_dt(occ["DTSTART"][1], occ["DTSTART"][0], tz)
                e = (_parse_dt(occ["DTEND"][1], occ["DTEND"][0], tz)[0]
                     if "DTEND" in occ else s + length)
            else:
                all_day_o, e = all_day, s + length
            if e <= window_start or s >= window_end:
                # zero-length items (like Canvas due times) still count if they start inside
                if not (s == e and window_start <= s < window_end):
                    continue
            out.append(_make_event(occ, s, e, all_day_o, source))
    out.sort(key=lambda ev: (ev.start, not ev.all_day))
    return out


def _expand(r: dict, start: datetime, tz: ZoneInfo, lo: datetime, hi: datetime) -> list[datetime]:
    rule_text = "RRULE:" + r["RRULE"][1]

    # We expand repeats in local wall-clock time ("every Mon 9:00 AM"), so a class
    # stays at 9:00 even after daylight saving time changes. dateutil then needs
    # UNTIL as a plain local time too, so convert "...Z" (UTC) or date-only values.
    def _local_until(m: re.Match) -> str:
        v = m.group(1)
        if len(v) == 8:
            return f"UNTIL={v}T235959"
        dt = datetime.strptime(v.rstrip("Z"), "%Y%m%dT%H%M%S")
        if v.endswith("Z"):
            dt = dt.replace(tzinfo=UTC).astimezone(tz).replace(tzinfo=None)
        return "UNTIL=" + dt.strftime("%Y%m%dT%H%M%S")

    rule_text = re.sub(r"UNTIL=(\d{8}(?:T\d{6}Z?)?)", _local_until, rule_text)
    try:
        rule = rrulestr(rule_text, dtstart=start.replace(tzinfo=None))
    except Exception:
        return [start]
    found = rule.between(lo.astimezone(tz).replace(tzinfo=None),
                         hi.astimezone(tz).replace(tzinfo=None), inc=True)
    starts = [d.replace(tzinfo=tz) for d in found]

    excluded = set()
    for params, value in r["_multi"].get("EXDATE", []):
        for v in value.split(","):
            excluded.add(_parse_dt(v, params, tz)[0])
    return [s for s in starts if s not in excluded]


def _parse_duration(value: str) -> timedelta:
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip())
    if not m:
        return timedelta(0)
    sign, w, d, h, mi, s = m.groups()
    td = timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0),
                   minutes=int(mi or 0), seconds=int(s or 0))
    return -td if sign == "-" else td


def _make_event(r: dict, start: datetime, end: datetime, all_day: bool, source: str) -> Event:
    title = _unescape(r.get("SUMMARY", ({}, "(no title)"))[1]).strip() or "(no title)"
    course = ""
    if source.lower() == "canvas":
        m = _COURSE_RE.search(title)
        if m:
            # "CHEM-117:534,536,538" -> "CHEM-117"  (drop the list of section numbers)
            course = m.group(1).split(":")[0].strip()
            title = _COURSE_RE.sub("", title)
    return Event(
        title=title,
        start=start,
        end=end,
        all_day=all_day,
        location=_unescape(r.get("LOCATION", ({}, ""))[1]).strip(),
        source=source,
        course=course,
        url=r.get("URL", ({}, ""))[1].strip(),
    )


def fetch_ics(url: str, timeout: int = 20) -> str:
    import requests
    # People often copy the "webcal://" version of the link; it's the same thing over https.
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Daybreak/1.0"})
    resp.raise_for_status()
    return resp.text


def day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tz)
    return start, start + timedelta(days=1)
