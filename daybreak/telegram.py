"""Sends the brief through a Telegram bot and reads your replies.

Why Telegram instead of SMS (for now): bots are free, setup takes ~5 minutes,
there's no carrier registration, messages can be long and have links/bold
text, and you can reply with commands like /done.

There's no server listening for replies. Instead, each run asks Telegram
"any new messages since last time?" (getUpdates) and handles them then.
"""

from __future__ import annotations

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000   # Telegram's hard limit is 4096 characters per message


def _call(token: str, method: str, payload: dict | None = None, timeout: int = 30) -> dict:
    import requests
    r = requests.post(API.format(token=token, method=method), json=payload or {}, timeout=timeout)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data.get('description', r.text)}")
    return data["result"]


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    """Split on blank lines (between sections) so we never cut an HTML tag in half."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = block[:limit]
    if current:
        chunks.append(current)
    return chunks


def send(token: str, chat_id: str | int, html_text: str) -> None:
    for chunk in split_message(html_text):
        _call(token, "sendMessage", {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        })


def get_updates(token: str, offset: int | None) -> list[dict]:
    payload = {"timeout": 0, "allowed_updates": ["message"]}
    if offset is not None:
        payload["offset"] = offset
    return _call(token, "getUpdates", payload)


def read_commands(token: str, chat_id: str | int, offset: int | None) -> tuple[list[str], int | None]:
    """Returns (list of commands like '/done', new offset). Ignores other chats."""
    updates = get_updates(token, offset)
    commands, new_offset = [], offset
    for u in updates:
        new_offset = u["update_id"] + 1
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(chat_id):
            continue   # someone else found your bot; ignore them
        text = (msg.get("text") or "").strip()
        if text.startswith("/"):
            # "/done@MyBot extra words" -> "/done"
            commands.append(text.split()[0].split("@")[0].lower())
    return commands, new_offset


def find_chat_ids(token: str) -> list[tuple[int, str]]:
    """Setup helper: who has messaged the bot? (chat id, name)"""
    seen = {}
    for u in get_updates(token, None):
        chat = (u.get("message") or {}).get("chat") or {}
        if "id" in chat:
            seen[chat["id"]] = chat.get("first_name") or chat.get("title") or chat.get("username", "")
    return list(seen.items())
