"""Open an issue when a scheduled workflow fails twice in a row.

    python src/watch_schedules.py                 # act on the repository
    python src/watch_schedules.py --dry-run       # print what it would do

A run that GitHub never gave a runner (collect.yml, 2026-10-05T20:49Z: "The
job was not acquired by Runner of type hosted even after multiple attempts")
starts none of its own steps, so nothing inside that workflow can notice it.
This looks from outside, at the completed scheduled runs of every workflow in
.github/workflows that has a `schedule`, and:

  * two or more non-success runs at the head  -> open an issue, or update the
    one already open under the same title (never a second one);
  * an open issue whose streak has been broken by a success -> comment, close;
  * a streak of two or more that came and went between two looks (scheduled
    runs fire hours late, so the watcher can sleep through a whole streak)
    -> open the issue and close it at once, so it is still reported.

Every conclusion other than `success` counts, `skipped` included: none of the
watched workflows has a job-level `if`, so a skipped run means the job never
ran. Runs still queued or in progress are not counted.

The issue body mentions the owner so the mail arrives. A marker in the body
records the newest failed run it reported, so the same streak is never
reported twice.

Anything this cannot see — no completed runs for a workflow, an API error, no
scheduled workflows found at all — exits non-zero rather than reporting all
clear.
"""

import argparse
import glob
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

THRESHOLD = 2
MENTION = "@yasumorishima"
SELF = "schedule-watch.yml"
# How far back a streak that has already recovered is still reported. The
# marker stops it being reported twice; this stops a first run, or a run after
# the issues were tidied, from digging up streaks from weeks ago.
LOOKBACK = timedelta(hours=48)
RUNS_PER_WORKFLOW = 30
TITLE = "定時実行が連続で失敗: {file}"
MARKER = "<!-- schedule-watch file={file} last-failed-run={run_id} -->"
MARKER_RE = re.compile(r"<!-- schedule-watch file=(\S+) last-failed-run=(\d+) -->")


class WatchError(RuntimeError):
    """The watcher could not see what it needs to; it must not say all clear."""


# --- what is watched ---------------------------------------------------------

def scheduled_workflows(directory: str = ".github/workflows") -> list[dict]:
    import yaml

    found = []
    for path in sorted(glob.glob(os.path.join(directory, "*.yml"))
                       + glob.glob(os.path.join(directory, "*.yaml"))):
        name = os.path.basename(path)
        if name == SELF:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                doc = yaml.safe_load(f)
            # PyYAML reads the bare key `on` as the boolean True.
            triggers = doc.get("on", doc.get(True)) or {}
        except (yaml.YAMLError, AttributeError) as e:
            raise WatchError(f"{name}: cannot read its triggers: {e}") from e
        if isinstance(triggers, dict) and triggers.get("schedule"):
            found.append({"file": name, "name": doc.get("name", name),
                          "crons": [s["cron"] for s in triggers["schedule"]]})
    if not found:
        raise WatchError(f"no scheduled workflows found under {directory}")
    return found


# --- the rule ----------------------------------------------------------------

def completed(runs: list[dict]) -> list[dict]:
    """Newest first, finished runs only."""
    done = [r for r in runs if r.get("status") == "completed"]
    return sorted(done, key=lambda r: (r["created_at"], r["id"]), reverse=True)


def leading_failures(runs: list[dict]) -> list[dict]:
    out = []
    for r in runs:
        if r.get("conclusion") == "success":
            break
        out.append(r)
    return out


def recent_recovered_streak(runs: list[dict], now: datetime) -> list[dict]:
    """The newest streak of THRESHOLD+ failures behind a success, if recent."""
    i = 0
    while i < len(runs) and runs[i].get("conclusion") != "success":
        i += 1
    while i < len(runs):
        while i < len(runs) and runs[i].get("conclusion") == "success":
            i += 1
        streak = leading_failures(runs[i:])
        if not streak:
            return []
        if parse_time(streak[0]["updated_at"]) < now - LOOKBACK:
            return []
        if len(streak) >= THRESHOLD:
            return streak
        i += len(streak)
    return []


def reported_runs(issues: list[dict], file: str) -> set[int]:
    seen = set()
    for issue in issues:
        for f, run_id in MARKER_RE.findall(issue.get("body") or ""):
            if f == file:
                seen.add(int(run_id))
    return seen


def decide(wf: dict, runs: list[dict], open_issue: dict | None,
           recent_issues: list[dict], now: datetime) -> dict:
    """What to do about one workflow. Pure: no I/O."""
    runs = completed(runs)
    if not runs:
        raise WatchError(f"{wf['file']}: no completed scheduled runs to judge")
    streak = leading_failures(runs)
    title = TITLE.format(file=wf["file"])

    if len(streak) >= THRESHOLD:
        body = render_body(wf, streak, recovered_by=None)
        if open_issue is None:
            return {"op": "open", "title": title, "body": body}
        if not any(r["id"] in reported_runs([open_issue], wf["file"]) for r in streak):
            # The open issue is about an earlier streak that a success broke
            # while nobody looked. Editing it would mail no one about this one.
            return {"op": "close_and_open", "number": open_issue["number"],
                    "comment": render_recovery(runs[len(streak)]),
                    "title": title, "body": body}
        if open_issue.get("body") != body:
            return {"op": "update", "number": open_issue["number"], "body": body}
        return {"op": "none", "why": f"{len(streak)} in a row, issue already up to date"}

    if open_issue is not None:
        success = next((r for r in runs if r.get("conclusion") == "success"), None)
        if success is None:
            return {"op": "none", "why": "issue open, no success yet"}
        return {"op": "close", "number": open_issue["number"],
                "comment": render_recovery(success)}

    missed = recent_recovered_streak(runs, now)
    # Any overlap is the same streak: the marker holds the newest failure at
    # the issue's last update, and the streak may have grown after it.
    reported = reported_runs(recent_issues, wf["file"])
    if missed and not any(r["id"] in reported for r in missed):
        success = runs[runs.index(missed[0]) - 1]
        return {"op": "open_and_close", "title": title,
                "body": render_body(wf, missed, recovered_by=success),
                "comment": render_recovery(success)}

    return {"op": "none", "why": f"latest {runs[0]['conclusion']}, "
                                 f"{len(streak)} non-success at the head"}


# --- what the issue says -----------------------------------------------------

def render_body(wf: dict, streak: list[dict], recovered_by: dict | None) -> str:
    lines = [
        f"{MENTION} `{wf['file']}`（{wf['name']}）の定時実行が "
        f"**{len(streak)} 回連続で success 以外**で終わりました。",
        "",
    ]
    if recovered_by is not None:
        lines += [f"見張りが見る前に回復済み（{run_link(recovered_by)} が success）。"
                  "連続していた事実だけを残すため、開いてすぐ閉じます。", ""]
    lines += ["| run | conclusion | 予定時刻（created） | 終了（updated） |",
              "|---|---|---|---|"]
    for r in streak:
        lines.append(f"| {run_link(r)} | `{r.get('conclusion')}` | "
                     f"{r['created_at']} | {r['updated_at']} |")
    lines += [
        "",
        f"cron: {', '.join('`%s`' % c for c in wf['crons'])}",
        "",
        "ステップが 1 つも始まっていない run は実行機の割り当て失敗（GitHub 側）、"
        "ステップのどこかで落ちた run はこの repo 側の故障。run を開いて見分ける。",
        "",
        "最新の定時 run が success になると、見張り（`schedule-watch.yml`）が"
        "コメントを付けてこの Issue を閉じます。",
        "",
        MARKER.format(file=wf["file"], run_id=streak[0]["id"]),
    ]
    return "\n".join(lines)


def render_recovery(success: dict) -> str:
    return (f"回復：{run_link(success)}（{success['created_at']} 予定）が success。"
            "連続失敗が途切れたので閉じます。")


def run_link(r: dict) -> str:
    return f"[#{r.get('run_number', r['id'])}]({r['html_url']})"


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# --- GitHub ------------------------------------------------------------------

class GitHub:
    def __init__(self, repo: str, token: str, api: str = "https://api.github.com"):
        self.repo, self.token, self.api = repo, token, api

    def _call(self, method: str, path: str, payload: dict | None = None):
        req = urllib.request.Request(
            f"{self.api}/repos/{self.repo}{path}", method=method,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={**({"Authorization": f"Bearer {self.token}"} if self.token else {}),
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            raise WatchError(f"{method} {path}: HTTP {e.code} {e.read()[:300]!r}") from e
        except urllib.error.URLError as e:
            raise WatchError(f"{method} {path}: {e.reason}") from e

    def runs(self, file: str) -> list[dict]:
        data = self._call("GET", f"/actions/workflows/{file}/runs?event=schedule"
                                 f"&status=completed&per_page={RUNS_PER_WORKFLOW}")
        if "workflow_runs" not in data:
            raise WatchError(f"{file}: unexpected runs response {str(data)[:200]}")
        return data["workflow_runs"]

    def issues(self, state: str, pages: int) -> list[dict]:
        out = []
        for page in range(1, pages + 1):
            batch = self._call("GET", f"/issues?state={state}&sort=updated"
                                      f"&direction=desc&per_page=100&page={page}")
            out += [i for i in batch if "pull_request" not in i]
            if len(batch) < 100:
                break
        return out

    def create_issue(self, title: str, body: str) -> int:
        return self._call("POST", "/issues", {"title": title, "body": body})["number"]

    def update_issue(self, number: int, body: str) -> None:
        self._call("PATCH", f"/issues/{number}", {"body": body})

    def comment(self, number: int, body: str) -> None:
        self._call("POST", f"/issues/{number}/comments", {"body": body})

    def close(self, number: int) -> None:
        self._call("PATCH", f"/issues/{number}",
                   {"state": "closed", "state_reason": "completed"})


def apply(gh, action: dict) -> None:
    op = action["op"]
    if op == "open":
        gh.create_issue(action["title"], action["body"])
    elif op == "update":
        gh.update_issue(action["number"], action["body"])
    elif op == "close":
        gh.comment(action["number"], action["comment"])
        gh.close(action["number"])
    elif op == "close_and_open":
        gh.comment(action["number"], action["comment"])
        gh.close(action["number"])
        gh.create_issue(action["title"], action["body"])
    elif op == "open_and_close":
        number = gh.create_issue(action["title"], action["body"])
        gh.comment(number, action["comment"])
        gh.close(number)


def watch(gh, workflows: list[dict], now: datetime, dry_run: bool = False) -> list[dict]:
    """Judge every workflow before touching anything, then act.

    One workflow the watcher cannot see fails the whole run, after the others
    have still been acted on: a blind spot must not be green, and it must not
    hide a real streak elsewhere either.
    """
    open_issues = gh.issues("open", pages=10)
    recent = gh.issues("all", pages=1)
    report, errors = [], []
    for wf in workflows:
        title = TITLE.format(file=wf["file"])
        mine = [i for i in open_issues if i["title"] == title]
        try:
            action = decide(wf, gh.runs(wf["file"]), mine[0] if mine else None,
                            recent, now)
        except WatchError as e:
            errors.append(str(e))
            continue
        report.append({"file": wf["file"], **{k: v for k, v in action.items()
                                               if k in ("op", "number", "why")}})
        if not dry_run:
            apply(gh, action)
    if errors:
        raise WatchError("; ".join(errors))
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workflows", default=".github/workflows")
    args = ap.parse_args()
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not repo or (not token and not args.dry_run):
        print("::error::GITHUB_REPOSITORY and GITHUB_TOKEN are required")
        return 1
    try:
        report = watch(GitHub(repo, token), scheduled_workflows(args.workflows),
                       datetime.now(timezone.utc), dry_run=args.dry_run)
    except WatchError as e:
        print(f"::error::{e}")
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
