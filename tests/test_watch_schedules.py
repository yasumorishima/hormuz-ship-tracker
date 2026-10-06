"""The watcher that mails when scheduled runs fail in a row.

Each test is written so that the watcher leaking the case fails it: a single
failure must not open anything, two must, an open issue must be updated rather
than doubled, a success must close it, and a blind watcher must go red rather
than report all clear.
"""

import os
import sys
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import watch_schedules as ws  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
WF = {"file": "collect.yml", "name": "Collect AIS window",
      "crons": ["7,22,37,52 * * * *"]}
TITLE = ws.TITLE.format(file="collect.yml")


def runs(*conclusions, status="completed", start=NOW):
    """Newest first, 15 minutes apart, as the API returns them."""
    out = []
    for i, c in enumerate(conclusions):
        t = start - timedelta(minutes=15 * i)
        # The id follows the time, so the same run keeps its id across calls.
        run_id = int(t.timestamp()) // 60 - 29_800_000
        out.append({"id": run_id, "run_number": run_id, "status": status,
                    "conclusion": c, "html_url": f"https://x/runs/{run_id}",
                    "created_at": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "updated_at": (t + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return out


class FakeGitHub:
    def __init__(self, runs_by_file, issues=()):
        self.runs_by_file = runs_by_file
        self.issues_ = [dict(i) for i in issues]
        self.created, self.updated, self.comments, self.closed = [], [], [], []

    def runs(self, file):
        r = self.runs_by_file[file]
        if isinstance(r, Exception):
            raise r
        return r

    def issues(self, state, pages):
        return [i for i in self.issues_ if state == "all" or i["state"] == state]

    def create_issue(self, title, body):
        number = 100 + len(self.issues_)
        self.issues_.append({"number": number, "title": title, "body": body, "state": "open"})
        self.created.append(number)
        return number

    def update_issue(self, number, body):
        self._get(number)["body"] = body
        self.updated.append(number)

    def comment(self, number, body):
        self.comments.append((number, body))

    def close(self, number):
        self._get(number)["state"] = "closed"
        self.closed.append(number)

    def _get(self, number):
        return next(i for i in self.issues_ if i["number"] == number)


def watch(gh, now=NOW):
    return ws.watch(gh, [WF], now)


class OpeningTest(unittest.TestCase):
    def test_a_single_failure_opens_nothing(self):
        gh = FakeGitHub({"collect.yml": runs("failure", "success", "success")})
        watch(gh)
        self.assertEqual(gh.created, [])

    def test_a_single_cancel_after_a_recovered_single_opens_nothing(self):
        gh = FakeGitHub({"collect.yml": runs("cancelled", "success", "failure", "success")})
        watch(gh)
        self.assertEqual(gh.created, [])

    def test_two_in_a_row_open_one_issue_that_mails_the_owner(self):
        history = runs("cancelled", "failure", "success")
        gh = FakeGitHub({"collect.yml": history})
        watch(gh)
        self.assertEqual(len(gh.created), 1)
        issue = gh.issues_[0]
        self.assertEqual(issue["title"], TITLE)
        self.assertEqual(issue["state"], "open")
        self.assertIn("@yasumorishima", issue["body"])
        self.assertIn("**2 回連続", issue["body"])
        for r in history[:2]:
            self.assertIn(r["html_url"] + ")", issue["body"])
        self.assertIn("`cancelled`", issue["body"])
        self.assertIn("`failure`", issue["body"])
        self.assertNotIn(history[2]["html_url"] + ")", issue["body"])  # the success before

    def test_every_non_success_counts(self):
        for c in ("timed_out", "startup_failure", "skipped", "action_required", "neutral"):
            with self.subTest(conclusion=c):
                gh = FakeGitHub({"collect.yml": runs(c, c, "success")})
                watch(gh)
                self.assertEqual(len(gh.created), 1)

    def test_runs_not_finished_are_not_counted(self):
        live = runs("failure", status="in_progress") + runs(None, status="queued")
        done = runs("failure", "success", start=NOW - timedelta(hours=1))
        gh = FakeGitHub({"collect.yml": live + done})
        watch(gh)
        self.assertEqual(gh.created, [])

    def test_order_comes_from_the_runs_not_the_response(self):
        gh = FakeGitHub({"collect.yml": list(reversed(runs("failure", "failure", "success")))})
        watch(gh)
        self.assertEqual(len(gh.created), 1)


class UpdatingTest(unittest.TestCase):
    def test_an_open_issue_is_updated_not_doubled(self):
        gh = FakeGitHub({"collect.yml": runs("failure", "failure", "success")})
        watch(gh)
        gh.runs_by_file["collect.yml"] = runs("failure", "failure", "failure", "success",
                                              start=NOW + timedelta(minutes=15))
        watch(gh, NOW + timedelta(minutes=15))
        self.assertEqual(len(gh.created), 1)
        self.assertEqual(gh.updated, [gh.created[0]])
        self.assertIn("**3 回連続", gh.issues_[0]["body"])
        self.assertEqual(gh.issues_[0]["state"], "open")

    def test_nothing_new_touches_nothing(self):
        gh = FakeGitHub({"collect.yml": runs("failure", "failure", "success")})
        watch(gh)
        watch(gh)
        self.assertEqual((len(gh.created), gh.updated, gh.comments), (1, [], []))


class ClosingTest(unittest.TestCase):
    def test_a_success_closes_it_with_a_comment(self):
        gh = FakeGitHub({"collect.yml": runs("failure", "failure", "success")})
        watch(gh)
        number = gh.created[0]
        later = NOW + timedelta(minutes=15)
        gh.runs_by_file["collect.yml"] = runs("success", "failure", "failure", start=later)
        watch(gh, later)
        self.assertEqual(gh.closed, [number])
        self.assertEqual(gh.comments[0][0], number)
        self.assertIn(gh.runs_by_file["collect.yml"][0]["html_url"] + ")", gh.comments[0][1])
        self.assertEqual(gh.created, [number])  # and not reopened as "missed"

    def test_a_closed_streak_is_not_reported_again(self):
        gh = FakeGitHub({"collect.yml": runs("failure", "failure", "success")})
        watch(gh)
        later = NOW + timedelta(minutes=15)
        gh.runs_by_file["collect.yml"] = runs("success", "failure", "failure", start=later)
        for _ in range(3):
            watch(gh, later)
        self.assertEqual(len(gh.created), 1)
        self.assertEqual(len(gh.closed), 1)


class GrownStreakTest(unittest.TestCase):
    def test_a_streak_that_grew_unseen_then_recovered_is_closed_once(self):
        gh = FakeGitHub({"collect.yml": runs("failure", "failure", "success")})
        watch(gh)
        later = NOW + timedelta(minutes=30)
        gh.runs_by_file["collect.yml"] = runs("success", "failure", "failure",
                                              "failure", "success", start=later)
        watch(gh, later)
        watch(gh, later)
        self.assertEqual(len(gh.created), 1)
        self.assertEqual(len(gh.closed), 1)


class MissedStreakTest(unittest.TestCase):
    """Scheduled runs fire hours late; a streak can come and go unwatched."""

    def test_a_streak_the_watcher_slept_through_is_still_reported(self):
        history = runs("success", "failure", "timed_out", "success")
        gh = FakeGitHub({"collect.yml": history})
        watch(gh)
        self.assertEqual(len(gh.created), 1)
        self.assertEqual(gh.closed, gh.created)
        self.assertIn("@yasumorishima", gh.issues_[0]["body"])
        self.assertIn(history[0]["html_url"] + ")", gh.comments[0][1])  # the success
        watch(gh)
        self.assertEqual(len(gh.created), 1)

    def test_an_old_streak_is_left_alone(self):
        old = NOW - ws.LOOKBACK - timedelta(hours=1)
        gh = FakeGitHub({"collect.yml": runs("success", start=NOW)
                         + runs("failure", "failure", "success", start=old)})
        watch(gh)
        self.assertEqual(gh.created, [])


class BlindnessTest(unittest.TestCase):
    def test_no_runs_is_an_error_not_all_clear(self):
        gh = FakeGitHub({"collect.yml": []})
        with self.assertRaises(ws.WatchError):
            watch(gh)

    def test_only_unfinished_runs_is_an_error(self):
        gh = FakeGitHub({"collect.yml": runs(None, status="in_progress")})
        with self.assertRaises(ws.WatchError):
            watch(gh)

    def test_an_api_error_fails_the_watcher_after_the_others_are_judged(self):
        other = dict(WF, file="publish.yml")
        gh = FakeGitHub({"collect.yml": ws.WatchError("HTTP 502"),
                         "publish.yml": runs("failure", "failure")})
        with self.assertRaises(ws.WatchError):
            ws.watch(gh, [WF, other], NOW)
        self.assertEqual(len(gh.created), 1)

    def test_main_exits_non_zero_on_an_api_error(self):
        class Broken(ws.GitHub):
            def _call(self, *a, **k):
                raise ws.WatchError("HTTP 500")
        argv = ["watch_schedules.py", "--workflows",
                os.path.join(ROOT, ".github", "workflows")]
        with mock.patch.object(ws, "GitHub", Broken), \
                mock.patch.object(sys, "argv", argv), \
                mock.patch.dict(os.environ, GITHUB_REPOSITORY="o/r", GITHUB_TOKEN="t"), \
                mock.patch("builtins.print"):
            self.assertEqual(ws.main(), 1)

    def test_http_failures_raise(self):
        gh = ws.GitHub("o/r", "t", api="http://127.0.0.1:9")  # nothing listens
        with self.assertRaises(ws.WatchError):
            gh.runs("collect.yml")


def fire_minutes(cron: str) -> list[int]:
    """Minutes of the day a cron fires, for the shapes used here."""
    minute, hour, dom, month, dow = cron.split()
    assert (dom, month, dow) == ("*", "*", "*"), cron

    def expand(field, top):
        if field == "*":
            return list(range(top))
        if field.startswith("*/"):
            return list(range(0, top, int(field[2:])))
        return [int(x) for x in field.split(",")]
    return sorted(h * 60 + m for h in expand(hour, 24) for m in expand(minute, 60))


def widest_gap(crons: list[str]) -> int:
    fires = sorted({m for c in crons for m in fire_minutes(c)})
    return max(b - a for a, b in zip(fires, fires[1:] + [fires[0] + 1440]))


class TheRealRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.watched = ws.scheduled_workflows(os.path.join(ROOT, ".github", "workflows"))

    def test_every_scheduled_workflow_is_watched_and_not_the_watcher(self):
        self.assertEqual({w["file"] for w in self.watched},
                         {"collect.yml", "compact.yml", "publish.yml", "sar-collect.yml"})

    def test_the_watcher_looks_at_least_as_often_as_anything_it_watches(self):
        import yaml
        with open(os.path.join(ROOT, ".github", "workflows", ws.SELF)) as f:
            doc = yaml.safe_load(f)
        mine = [s["cron"] for s in doc[True]["schedule"]]
        tightest = min(widest_gap(w["crons"]) for w in self.watched)
        self.assertLessEqual(widest_gap(mine), tightest)

    def test_the_watcher_has_only_the_permissions_it_needs(self):
        import yaml
        with open(os.path.join(ROOT, ".github", "workflows", ws.SELF)) as f:
            doc = yaml.safe_load(f)
        self.assertEqual(doc["permissions"],
                         {"contents": "read", "actions": "read", "issues": "write"})
        with open(os.path.join(ROOT, ".github", "workflows", ws.SELF)) as f:
            self.assertNotIn("secrets.", f.read())

    def test_publish_keeps_the_watcher_enabled_too(self):
        with open(os.path.join(ROOT, ".github", "workflows", "publish.yml")) as f:
            self.assertIn(ws.SELF, f.read())

    def test_threshold_is_two(self):
        self.assertEqual(ws.THRESHOLD, 2)


if __name__ == "__main__":
    unittest.main()
