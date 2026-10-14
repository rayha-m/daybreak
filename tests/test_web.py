"""Tests for the website version (daybreak/web.py). No internet needed."""
import json
import threading
import unittest
import urllib.request
from datetime import datetime
from http.server import HTTPServer
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from daybreak import web

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 12, 7, 5, tzinfo=ZoneInfo("America/Chicago"))


def fake_fetch(url):
    if "news.google.com" in url:
        return (FIX / "gnews.xml").read_text()
    if url == "https://cal.example/google.ics":
        return (FIX / "google.ics").read_text()
    if url == "https://cal.example/canvas.ics":
        return (FIX / "canvas.ics").read_text()
    raise ConnectionError("offline")


def gh(path, token, params=None):
    if path.startswith("/users/"):
        return [{"full_name": "someone/proj"}]
    if path.endswith("/commits"):
        return [{"commit": {"message": "Add parser", "committer": {"date": "2026-10-11T23:00:00Z"}}}]
    return []


FORM = {"name": "Sam", "timezone": "America/Chicago",
        "goals": [{"name": "Economics", "description": "macro"}, {"name": "", "description": "skip"}],
        "calendar_url": "https://cal.example/google.ics", "canvas_url": "https://cal.example/canvas.ics",
        "github_user": "someone", "leetcode_done": "3"}


class TestWeb(unittest.TestCase):
    def test_full_brief(self):
        out = web.build(dict(FORM), fetch=fake_fetch, gh_getter=gh, now=NOW)
        h = out["html"]
        for s in ["Good morning, Sam", "CHEM 107 Lecture", "Quiz 4: Stoichiometry",
                  "Rates hold steady as inflation cools</a> — Reuters", "Group Anagrams",
                  "Progress: 3/69", "proj"]:
            self.assertIn(s, h)
        self.assertFalse(out["ai"])

    def test_needs_a_goal(self):
        with self.assertRaises(web.BadInput):
            web.build({"goals": [{"name": "  "}]}, fetch=fake_fetch, now=NOW)

    def test_bad_calendar_link_is_a_note_not_a_crash(self):
        form = dict(FORM, calendar_url="https://cal.example/missing.ics", github_user="")
        out = web.build(form, fetch=fake_fetch, now=NOW)
        self.assertTrue(any("Calendar" in n for n in out["notes"]))
        self.assertIn("Open day", out["html"])

    def test_blocks_private_addresses(self):
        for url in ["http://127.0.0.1/x.ics", "http://169.254.169.254/latest", "file:///etc/passwd",
                    "http://localhost:8000/"]:
            with self.assertRaises(web.BadInput, msg=url):
                web._check_public_url(url)

    def test_api_handler(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("api_brief", Path(__file__).parent.parent / "api" / "brief.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        srv = HTTPServer(("127.0.0.1", 0), mod.handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_port}/api/brief"
        try:
            with mock.patch.object(mod, "build", lambda f: web.build(f, fetch=fake_fetch, gh_getter=gh, now=NOW)):
                req = urllib.request.Request(url, data=json.dumps(FORM).encode(), method="POST")
                body = json.loads(urllib.request.urlopen(req).read())
                self.assertIn("Good morning, Sam", body["html"])
                bad = urllib.request.Request(url, data=b'{"goals": []}', method="POST")
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(bad)
                self.assertEqual(ctx.exception.code, 400)
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
