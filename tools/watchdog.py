"""Alert by GitHub issue when a released round still has no answer on main.

Runs from the answer loop after every pass and from its own scheduled
workflow (watchdog.yml), so it still fires if the answer runs are stuck or
dead. For each round released more than 20 minutes ago whose file isn't on
origin/main, it opens an issue that @mentions the repo owner (GitHub emails
mentions). If no answer run is in progress, it also starts one. Issues close
themselves once the round is answered.

  python3 tools/watchdog.py                       # normal check
  python3 tools/watchdog.py --pretend-missing 117  # test: treat round 117 as missing
  python3 tools/watchdog.py --dry-run --pretend-idle --pretend-missing 117
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import solve  # noqa: E402

GRACE = timedelta(minutes=20)
LABEL = "watchdog"
COLUMNS = {"round_id", "q_index", "released_at", "question_text", "option_a", "option_b", "option_c", "option_d"}
REPO = os.environ.get("GITHUB_REPOSITORY", "NAT-A-LIE-J/dso429-hw3")
OWNER = REPO.split("/")[0]


def gh(*args, check=True):
    return subprocess.run(["gh", *args, "--repo", REPO], capture_output=True, text=True, check=check).stdout


def answered_on_main():
    subprocess.run(["git", "fetch", "--quiet", "origin", "main"], check=True)
    out = subprocess.run(["git", "ls-tree", "-r", "--name-only", "origin/main", "answers/"],
                         capture_output=True, text=True, check=True).stdout
    return set(out.split())


def open_alerts():
    out = gh("issue", "list", "--label", LABEL, "--state", "open", "--json", "number,title")
    return {i["title"]: i["number"] for i in json.loads(out or "[]")}


def answer_run_active():
    out = gh("run", "list", "--workflow", "answer.yml", "--status", "in_progress", "--limit", "1", "--json", "databaseId")
    return bool(json.loads(out or "[]"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pretend-missing", type=int, help="treat this round's file as missing (testing)")
    ap.add_argument("--pretend-idle", action="store_true", help="act as if no answer run is in progress (testing)")
    ap.add_argument("--dry-run", action="store_true", help="print actions instead of taking them")
    args = ap.parse_args()

    def act(*cmd):
        print("  ->", " ".join(cmd))
        if not args.dry_run:
            gh(*cmd)

    if not args.dry_run:
        gh("label", "create", LABEL, "--color", "d93f0b", "--description", "Automatic alert from the quiz watchdog", "--force")
    alerts = open_alerts()
    now = datetime.now(timezone.utc)
    problems = {}  # issue title -> body

    try:
        rows = solve.fetch_csv("questions.csv")
        missing_cols = COLUMNS - set(rows[0].keys()) if rows else COLUMNS
        if missing_cols:
            raise ValueError(f"columns missing: {sorted(missing_cols)}")
    except Exception as e:  # any failure to read the questions is worth an alert
        rows = None
        problems["Can't read questions.csv"] = f"The watchdog couldn't read the quiz questions file: `{e}`"

    if rows:
        by_round = {}
        for r in rows:
            by_round.setdefault(int(r["round_id"]), []).append(r)
        files = answered_on_main()
        for rid, rrows in sorted(by_round.items()):
            if rid < 49:
                continue
            path = f"answers/round-{rid:04d}.json"
            missing = path not in files or rid == args.pretend_missing
            age = now - solve.released_at(rrows)
            if missing and age > GRACE:
                deadline = solve.released_at(rrows) + timedelta(hours=3)
                problems[f"Round {rid} has no answer file"] = (
                    f"Round {rid} was released at {solve.released_at(rrows):%Y-%m-%d %H:%M} UTC, "
                    f"{int(age.total_seconds() // 60)} minutes ago, and `{path}` isn't on `main` yet. "
                    f"Full credit needs it pushed before {deadline:%H:%M} UTC.")

    restarted = False
    if any(t.startswith("Round ") for t in problems):
        if args.pretend_idle or not answer_run_active():
            act("workflow", "run", "answer.yml", "--ref", "main")
            restarted = True

    for title, body in problems.items():
        print("PROBLEM:", title)
        if title in alerts:
            continue
        body += ("\n\nThe watchdog started a new answer run, since none was in progress."
                 if restarted else "\n\nAn answer run is in progress, so this may be a push or question problem.")
        body += f"\n\nCheck the Actions tab: https://github.com/{REPO}/actions\n\n@{OWNER}"
        act("issue", "create", "--title", title, "--body", body, "--label", LABEL)

    for title, number in alerts.items():
        if title not in problems:
            print("RESOLVED:", title)
            act("issue", "close", str(number), "--comment", f"Resolved; the watchdog saw it fixed at {now:%Y-%m-%d %H:%M} UTC.")

    if not problems:
        print(f"watchdog: all released rounds answered ({now:%H:%M} UTC)")


if __name__ == "__main__":
    main()
