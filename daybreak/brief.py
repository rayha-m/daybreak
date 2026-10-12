"""Turns everything into the Telegram message (Telegram's HTML formatting)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape as _escape


def escape(text: str, quote: bool = False) -> str:
    # Only quote-escape inside href="..."; Telegram shows &#x27; literally in some clients.
    return _escape(text, quote=quote)

from .ical import Event


def _time(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _day_label(d: date, today: date) -> str:
    if d == today:
        return "Today"
    if d == today + timedelta(days=1):
        return "Tomorrow"
    return d.strftime("%a %b %d").replace(" 0", " ")


def schedule_lines(events: list[Event]) -> list[str]:
    lines = []
    for e in events:
        when = "All day" if e.all_day else f"{_time(e.start)}–{_time(e.end)}"
        loc = f" · {escape(e.location)}" if e.location else ""
        lines.append(f"• <b>{when}</b>  {escape(e.title)}{loc}")
    return lines


def schedule_summary(events: list[Event], due: list[Event]) -> str:
    """Plain-text version for the AI prompt."""
    parts = [f"{'all day' if e.all_day else _time(e.start)}: {e.title}" for e in events]
    parts += [f"due {e.start:%a} : {e.title}" for e in due]
    return "\n".join(parts)


def due_lines(due: list[Event], today: date, now: datetime) -> list[str]:
    lines = []
    for e in due:
        d = e.start.date()
        when = _day_label(d, today)
        if not e.all_day:
            when += f" {_time(e.start)}"
        urgent = "⚠️ " if d <= today + timedelta(days=1) else ""
        course = f" <i>({escape(e.course)})</i>" if e.course else ""
        title = escape(e.title)
        if e.url:
            title = f'<a href="{escape(e.url, quote=True)}">{title}</a>'
        lines.append(f"• {urgent}<b>{when}</b> — {title}{course}")
    return lines


def morning(name: str, today: date, now: datetime, events: list[Event], due: list[Event],
            plan: dict, lc_problem: dict | None, lc_url: str, lc_progress: tuple[int, int],
            streak: int, notices: list[str], lookahead_days: int) -> str:
    out = [f"☀️ <b>Good morning{', ' + escape(name) if name else ''}!</b>  "
           f"{today:%A, %B} {today.day}"]
    if notices:
        out.append("\n".join(notices))

    out.append("📅 <b>Today</b>\n" + ("\n".join(schedule_lines(events))
                                     or "Nothing on the calendar. Open day!"))

    if due:
        out.append(f"📚 <b>Due in the next {lookahead_days} days</b>\n" + "\n".join(due_lines(due, today, now)))

    if plan.get("articles"):
        lines = []
        for a in plan["articles"]:
            art = a["article"]
            src = f" — {escape(art.source)}" if art.source else ""
            line = f'• <b>[{escape(art.goal)}]</b> <a href="{escape(art.link, quote=True)}">{escape(art.title)}</a>{src}'
            if a.get("why"):
                line += f"\n   <i>{escape(a['why'])}</i>"
            lines.append(line)
        out.append("📰 <b>Morning reads</b>\n" + "\n".join(lines))

    if plan.get("assignments"):
        out.append("📝 <b>Today's learning assignments</b>\n" + "\n".join(
            f"• <b>{escape(a.get('goal', ''))}:</b> {escape(a['task'])}" for a in plan["assignments"]))

    if lc_problem:
        done, total = lc_progress
        fire = f" · 🔥 {streak}-day streak" if streak else ""
        out.append(
            "💻 <b>LeetCode of the day</b>\n"
            f'<a href="{lc_url}">{escape(lc_problem["title"])}</a> '
            f'({lc_problem["difficulty"]} · {escape(lc_problem["topic"])})\n'
            f"Progress: {done}/{total}{fire}\n"
            "Reply /done when you solve it or /skip to move on.")
    elif lc_progress[1]:
        out.append("💻 <b>LeetCode</b>\n🎉 You finished the whole list! Pick a new one in config.yaml.")

    if plan.get("projects"):
        out.append("🛠 <b>Projects</b>\n" + "\n".join(
            f"• <b>{escape(p['name'])}:</b> {escape(p['next_step'])}" for p in plan["projects"]))

    if plan.get("tip"):
        out.append(f"💡 <i>{escape(plan['tip'])}</i>")
    return "\n\n".join(out)


def evening(today: date, now: datetime, due_soon: list[Event], lc_problem: dict | None,
            lc_url: str, solved_today: bool, streak: int, notices: list[str]) -> str | None:
    out = ["🌙 <b>Evening check-in</b>"]
    if notices:
        out.append("\n".join(notices))
    if lc_problem and not solved_today:
        keep = f" to keep your 🔥 {streak}-day streak" if streak else ""
        out.append(f'💻 Still time for <a href="{lc_url}">{escape(lc_problem["title"])}</a>{keep}. '
                   "Reply /done when it's solved.")
    elif solved_today:
        out.append(f"💻 LeetCode done today. Nice work! 🔥 {streak}")
    if due_soon:
        out.append("📚 <b>Due before tomorrow night</b>\n" + "\n".join(due_lines(due_soon, today, now)))
    if len(out) == 1:
        return None   # nothing worth interrupting your evening for
    return "\n\n".join(out)
