"""Run with:  python -m unittest discover tests -v

Uses saved sample files instead of the internet, so it works offline.
"""

import json
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from daybreak import ai, ical, leetcode, news, run, telegram

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("America/Chicago")


def at(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


class TestICal(unittest.TestCase):
    def setUp(self):
        self.text = (FIX / "google.ics").read_text()

    def events_on(self, d):
        s, e = ical.day_bounds(d, TZ)
        return ical.parse_ics(self.text, TZ, s, e)

    def test_recurring_class_and_others_today(self):
        titles = [e.title for e in self.events_on(date(2026, 10, 12))]
        self.assertEqual(titles, ["Career Fair (Engineering)", "CHEM 107 Lecture",
                                  "ADSC project team meeting"])

    def test_times_converted_to_local(self):
        ev = {e.title: e for e in self.events_on(date(2026, 10, 12))}
        self.assertEqual(ev["ADSC project team meeting"].start.hour, 15)   # 20:30Z = 3:30 PM CDT
        self.assertEqual(ev["CHEM 107 Lecture"].location, "Heldenfels Hall 100, College Station")

    def test_class_time_survives_daylight_saving(self):
        # DST ends Nov 1 2026; class must still be at 9:10 local on Nov 2.
        ev = self.events_on(date(2026, 11, 2))
        self.assertEqual((ev[0].start.hour, ev[0].start.minute), (9, 10))

    def test_exdate_removes_cancelled_class(self):
        self.assertEqual(self.events_on(date(2026, 10, 9)), [])

    def test_moved_instance_overrides(self):
        ev = self.events_on(date(2026, 10, 14))
        self.assertEqual([(e.title, e.location) for e in ev],
                         [("CHEM 107 Exam 2 Review (moved room)", "ZACH 350")])

    def test_until_stops_repeats(self):
        self.assertEqual(self.events_on(date(2026, 12, 14)), [])

    def test_canvas_course_and_due_time(self):
        s = at(2026, 10, 12)
        items = ical.parse_ics((FIX / "canvas.ics").read_text(), TZ, s, s + timedelta(days=4), "Canvas")
        quiz = next(i for i in items if i.title.startswith("Quiz"))
        self.assertEqual(quiz.course, "CHEM-107-501")
        self.assertEqual((quiz.start.date(), quiz.start.hour, quiz.start.minute), (date(2026, 10, 12), 23, 59))
        self.assertNotIn("Too far away", [i.title for i in items])


class TestNews(unittest.TestCase):
    def test_rss_and_atom(self):
        rss = news.parse_feed((FIX / "finance.xml").read_text(), "CNBC")
        self.assertEqual(rss[0].summary, "The central bank held rates steady.")
        self.assertEqual(rss[2].title, "Bank earnings preview & what to watch")
        atom = news.parse_feed((FIX / "ds.xml").read_text())
        self.assertEqual((atom[0].link, atom[0].source), ("https://example.com/gb", "DS Blog"))

    def test_collect_filters_old_and_seen(self):
        goals = [{"name": "Finance", "feeds": ["finance.xml"]}]
        got = news.collect(goals, {"https://example.com/banks"}, at(2026, 10, 12, 7),
                           fetcher=lambda u: (FIX / u).read_text(), log=lambda m: None)
        self.assertEqual([a.link for a in got["Finance"]], ["https://example.com/fed"])

    def test_broken_feed_does_not_crash(self):
        def bad(u):
            raise ConnectionError("offline")
        got = news.collect([{"name": "X", "feeds": ["u"]}], set(), at(2026, 10, 12), fetcher=bad,
                           log=lambda m: None)
        self.assertEqual(got["X"], [])


class TestLeetCode(unittest.TestCase):
    def test_list_and_streak(self):
        probs = leetcode.load_list()
        self.assertEqual(len(probs), 69)              # 75 minus 6 premium
        st = {"done": [], "skipped": []}
        self.assertEqual(leetcode.current(probs, st)["slug"], "two-sum")
        leetcode.mark(st, "two-sum", "done", date(2026, 10, 10))
        leetcode.mark(st, "contains-duplicate", "done", date(2026, 10, 11))
        self.assertEqual(st["streak"], 2)
        self.assertEqual(leetcode.live_streak(st, date(2026, 10, 12)), 2)
        self.assertEqual(leetcode.live_streak(st, date(2026, 10, 14)), 0)
        leetcode.mark(st, "valid-anagram", "skipped", date(2026, 10, 12))
        self.assertEqual(leetcode.current(probs, st)["slug"], "group-anagrams")


class TestTelegram(unittest.TestCase):
    def test_split_keeps_sections_whole(self):
        text = "\n\n".join(["<b>section</b> " + "x" * 1500] * 5)
        parts = telegram.split_message(text)
        self.assertTrue(all(len(p) <= telegram.MAX_LEN for p in parts))
        self.assertTrue(all(p.count("<b>") == p.count("</b>") for p in parts))


class TestEndToEnd(unittest.TestCase):
    """Builds a whole brief from the sample files, with no network and no AI key."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        cfg = (ROOT / "config.yaml").read_text()
        cfg = cfg.replace("https://www.cnbc.com/id/100003114/device/rss/rss.html", "finance.xml") \
                 .replace("https://towardsdatascience.com/feed", "ds.xml") \
                 .replace("https://www.statnews.com/feed/", "med.xml")
        (self.dir / "config.yaml").write_text(cfg)
        self.state = self.dir / "state.json"

    def tearDown(self):
        shutil.rmtree(self.dir)

    def fetch_feed(self, url):
        p = FIX / url
        if not p.exists():
            raise ConnectionError("offline")
        return p.read_text()

    def gh(self, path, token, params=None):
        if path.startswith("/users/"):
            return [{"full_name": "rayha-m/daybreak"}, {"full_name": "rayha-m/forked", "fork": True}]
        if path.endswith("/commits"):
            return [{"commit": {"message": "Add Canvas parser\n\nmore", "committer": {"date": "2026-10-11T23:00:00Z"}}}]
        return [{"title": "Add evening check-in"}, {"title": "PR", "pull_request": {}}]

    def go(self, mode, now, dry_run=True):
        env = {"GOOGLE_CALENDAR_ICS": "google", "CANVAS_ICS": "canvas"}
        return run.run(mode, config_path=self.dir / "config.yaml", state_path=self.state,
                       dry_run=dry_run, now=now, env=env,
                       fetch_ics=lambda u: (FIX / f"{u}.ics").read_text(),
                       fetch_feed=self.fetch_feed, gh_getter=self.gh)

    def test_morning_brief(self):
        text = self.go("morning", at(2026, 10, 12, 7, 2))
        for expected in ["Good morning, Rayha", "Monday, October 12", "9:10 AM–10:00 AM",
                         "CHEM 107 Lecture", "Career Fair", "⚠️ <b>Today 11:59 PM</b>",
                         "Lab Report 3", "Fed signals pause", "gradient boosting", "GLP-1",
                         "Two Sum", "Progress: 0/69", "Work on: Add evening check-in"]:
            self.assertIn(expected, text)
        self.assertIn("Today 9:00 AM</b> — Reading check", text)       # due later this morning
        late = self.go("morning", at(2026, 10, 12, 10, 30))
        self.assertNotIn("Reading check", late)                            # already passed by 10:30
        for unexpected in ["Too far away", "Tomorrow only thing", "Old story"]:
            self.assertNotIn(unexpected, text)

    def test_auto_mode_timing_and_done_flow(self):
        cfg = run.load_config(self.dir / "config.yaml")

        def mode_at(h, m, state):
            return run.decide_mode(run.Ctx(cfg=cfg, tz=TZ, now=at(2026, 10, 12, h, m), state=state))

        self.assertIsNone(mode_at(6, 30, {}))                                  # too early
        self.assertEqual(mode_at(7, 10, {}), "morning")
        self.assertIsNone(mode_at(7, 40, {"last_morning": "2026-10-12"}))      # already sent
        self.assertEqual(mode_at(20, 45, {"last_morning": "2026-10-12"}), "evening")
        with unittest.mock.patch.object(run.telegram, "send") as send:
            self._send_morning_and_reply(send)

    def _send_morning_and_reply(self, send):
        if True:
            ctx_env = {"GOOGLE_CALENDAR_ICS": "google", "CANVAS_ICS": "canvas",
                       "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "42"}
            common = dict(config_path=self.dir / "config.yaml", state_path=self.state, env=ctx_env,
                          fetch_ics=lambda u: (FIX / f"{u}.ics").read_text(),
                          fetch_feed=self.fetch_feed, gh_getter=self.gh)
            with unittest.mock.patch.object(run.telegram, "read_commands", return_value=([], 5)):
                run.run("auto", now=at(2026, 10, 12, 7, 5), **common)
                self.assertEqual(send.call_count, 1)
                self.assertIsNone(run.run("auto", now=at(2026, 10, 12, 7, 35), **common))
            state = json.loads(self.state.read_text())
            self.assertEqual(state["today"]["leetcode"], "two-sum")
            self.assertEqual(len(state["seen_articles"]), 3)

            # She replies /done during the day; evening check-in congratulates her.
            with unittest.mock.patch.object(run.telegram, "read_commands", return_value=(["/done"], 6)):
                text = run.run("auto", now=at(2026, 10, 12, 20, 40), **common)
            self.assertIn("✅ Solved Two Sum", text)
            self.assertIn("LeetCode done today", text)

            # Next morning: new problem, streak shown, yesterday's articles not repeated.
            with unittest.mock.patch.object(run.telegram, "read_commands", return_value=([], 7)):
                text = run.run("auto", now=at(2026, 10, 13, 7, 1), **common)
            self.assertIn("Contains Duplicate", text)
            self.assertIn("🔥 1-day streak", text)
            self.assertNotIn("Fed signals pause", text)


class TestAIFallbackAndParsing(unittest.TestCase):
    def test_ai_plan_uses_only_real_articles(self):
        arts = {"Finance": [news.Article("Fed holds", "https://x/fed", "", None, "CNBC", "Finance")],
                "Medicine": [news.Article("GLP-1", "https://x/glp", "", None, "STAT", "Medicine")]}
        goals = [{"name": "Finance"}, {"name": "Medicine"}]
        prompt, ids = ai.build_prompt(goals, arts, [], "9:10 AM: CHEM", [], date(2026, 10, 12))
        self.assertEqual(set(ids), {"fin0", "med0"})
        self.assertIn("[fin0]", prompt)
        fake = {"articles": [{"id": "fin0", "why": "Rates drive everything."},
                             {"id": "zzz9", "why": "invented"}],
                "assignments": [{"goal": "Finance", "task": "Explain the fed funds rate."}],
                "projects": [], "tip": "Small steps."}
        with unittest.mock.patch.object(ai, "call_gemini", return_value=fake):
            plan = ai.plan(goals, arts, [], "", [], date(2026, 10, 12), "key", log=lambda m: None)
        self.assertTrue(plan["ai"])
        self.assertEqual([a["article"].link for a in plan["articles"]], ["https://x/fed"])
        self.assertEqual(plan["tip"], "Small steps.")

    def test_bad_ai_response_falls_back(self):
        arts = {"Finance": [news.Article("T", "https://x", "", None, "S", "Finance")]}
        with unittest.mock.patch.object(ai, "call_gemini", return_value={"articles": [{"id": "made-up"}]}):
            plan = ai.plan([{"name": "Finance"}], arts, [], "", [], date(2026, 10, 12), "key",
                           log=lambda m: None)
        self.assertEqual(plan["articles"][0]["article"].link, "https://x")   # never a hallucinated link
        self.assertTrue(plan["assignments"])


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
