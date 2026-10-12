"""Daybreak: your whole morning in one Telegram message.

Usage:
  python main.py preview            # build today's morning brief and print it (sends nothing)
  python main.py preview evening    # same for the evening check-in
  python main.py send morning       # build and send the morning brief now
  python main.py send evening       # build and send the evening check-in now
  python main.py auto               # what GitHub Actions runs: sends whichever is due
  python main.py setup              # find your Telegram chat ID (after you message your bot)
"""

import os
import sys
from pathlib import Path

# Lets you keep secrets in a local .env file while testing on your laptop.
env_file = Path(__file__).with_name(".env")
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from daybreak import run, telegram  # noqa: E402


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "preview"
    if cmd == "setup":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            sys.exit("Set TELEGRAM_BOT_TOKEN first (in .env or your terminal).")
        chats = telegram.find_chat_ids(token)
        if not chats:
            sys.exit("No messages yet. Open your bot in Telegram, send it 'hi', then run this again.")
        for chat_id, name in chats:
            print(f"Chat ID for {name}: {chat_id}")
        print("\nPut that number in TELEGRAM_CHAT_ID.")
        return
    if cmd == "preview":
        mode = argv[1] if len(argv) > 1 else "morning"
        run.run(mode, dry_run=True)
    elif cmd == "send":
        run.run(argv[1] if len(argv) > 1 else "morning")
    elif cmd == "auto":
        run.run("auto")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
