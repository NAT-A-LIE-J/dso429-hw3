"""Audit every graded round: file present and valid, push time vs. release,
and submitted answers vs. the formula solvers.

  python3 tools/audit.py            # needs gh logged in, run from hw3/
"""
import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import solve  # noqa: E402

REPO = "NAT-A-LIE-J/dso429-hw3"
FIRST_GRADED = 49


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def gh_json(*args):
    out = subprocess.run(["gh", "api", "--paginate", *args], capture_output=True, text=True, check=True).stdout
    # --paginate concatenates JSON arrays as "][": merge them
    return json.loads("[" + out.strip()[1:-1].replace("]\n[", ",").replace("][", ",") + "]") if out.strip().startswith("[") else json.loads(out)


def push_times():
    """{commit sha: time GitHub received the push containing it}."""
    times = {}
    for ev in gh_json(f"repos/{REPO}/activity?ref=refs/heads/main&per_page=100"):
        if ev.get("activity_type") not in ("push", "force_push"):
            continue
        cmp = subprocess.run(["gh", "api", f"repos/{REPO}/compare/{ev['before']}...{ev['after']}", "-q", ".commits[].sha"],
                             capture_output=True, text=True).stdout.split()
        for sha in cmp or [ev["after"]]:
            times.setdefault(sha, ts(ev["timestamp"]))
    return times


def first_commit_with(path):
    """(sha, commit time) of the earliest commit on main that added path."""
    out = subprocess.run(["git", "log", "--diff-filter=A", "--format=%H %cI", "--", path],
                         capture_output=True, text=True, check=True).stdout.split("\n")
    lines = [l for l in out if l.strip()]
    sha, when = lines[-1].split() if lines else (None, None)
    return sha, (ts(when) if when else None)


def credit(hours):
    return 1.0 if hours <= 3 else max(0.0, 1.0 - 0.1 * (int(hours - 3) + 1))


def main():
    rows = list(csv.DictReader(open("data/questions.csv")))
    by_round = {}
    for r in rows:
        by_round.setdefault(int(r["round_id"]), []).append(r)
    graded = sorted(rid for rid in by_round if rid >= FIRST_GRADED)
    pushes = push_times()

    problems, disagreements, unrecognized = [], [], []
    late = []
    for rid in graded:
        path = Path(f"answers/round-{rid:04d}.json")
        if not path.exists():
            problems.append(f"round {rid}: MISSING")
            continue
        try:
            data = json.loads(path.read_text())
            ans = data["answers"]
            bad = [f"q{i}" for i in range(1, 26) if ans.get(f"q{i}") not in tuple("ABCD")]
            if bad:
                problems.append(f"round {rid}: invalid {bad}")
        except (ValueError, KeyError) as e:
            problems.append(f"round {rid}: bad JSON ({e})")
            continue

        released = solve.released_at(by_round[rid])
        sha, committed = first_commit_with(str(path))
        received = pushes.get(sha, committed)
        hours = (received - released).total_seconds() / 3600
        if credit(hours) < 1:
            late.append(f"round {rid}: received {hours:.2f} h after release (credit {credit(hours):.0%})")

        for r in by_round[rid]:
            i = int(r["q_index"])
            f, solver = solve.formula_answer(r)
            if f is None:
                unrecognized.append((rid, i, ans[f"q{i}"], r))
            elif f != ans[f"q{i}"]:
                disagreements.append(f"round {rid} q{i} [{solver}]: submitted {ans[f'q{i}']}, formula {f}: {r['question_text']}")

        print(f"round {rid:3d}: released {released:%m-%d %H:%M}Z, pushed +{hours * 60:5.1f} min")

    print(f"\n{len(graded)} graded rounds released ({graded[0]}-{graded[-1]})")
    print("file problems:", problems or "none")
    print("late rounds:", late or "none")
    print(f"submitted vs formula, disagreements: {len(disagreements)}")
    for d in disagreements:
        print("  ", d)
    print(f"questions the formulas don't recognize: {len(unrecognized)}")
    for rid, i, a, r in unrecognized:
        opts = " | ".join(f"{L}) {r[f'option_{x}']}" for L, x in zip("ABCD", "abcd"))
        print(f"   round {rid} q{i} submitted {a}: {r['question_text']}  [{opts}]")


if __name__ == "__main__":
    main()
