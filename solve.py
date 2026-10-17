"""Answer every released round of the DSO 429 hourly quiz.

Fetches questions.csv, solves each question with the matching formula, and
writes answers/round-NNNN.json for every round that doesn't have a file yet.

  python3 solve.py            # write files for new rounds
  python3 solve.py --check    # score the solver against the practice keys
  python3 solve.py --force    # rewrite every round's file
"""
import argparse
import csv
import io
import json
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = "https://raw.githubusercontent.com/FaisalXL/dso429-hourly-quiz/main/"
ANSWERS = Path(__file__).parent / "answers"
LETTERS = "ABCD"
NUM = r"\$?(-?[\d,]+(?:\.\d+)?)"


def fetch_csv(name):
    req = urllib.request.Request(BASE + name, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return list(csv.DictReader(io.StringIO(resp.read().decode("utf-8"))))


def num(s):
    return float(s.replace(",", "").replace("$", ""))


def find(pattern, text):
    m = re.search(pattern, text, re.IGNORECASE)
    return [num(g) if re.fullmatch(r"-?[\d,]+(?:\.\d+)?", g) else g for g in m.groups()] if m else None


# Each solver returns the correct value (a number, or a date for settlement
# questions), or None when the question isn't its type. Percentages are
# returned in percent units (12.5 means 12.5%).

def discount(q):
    v = find(rf"listed at {NUM} is discounted by {NUM}%", q)
    return v and v[0] * (1 - v[1] / 100)


def revenue(q):
    v = find(rf"sells {NUM} units at {NUM} each", q)
    return v and v[0] * v[1]


def currency(q):
    v = find(rf"invoice for {NUM} is converted to \w+ at {NUM} \w+ per", q)
    return v and v[0] * v[1]


def weighted_average(q):
    v = find(rf"had {NUM} orders averaging {NUM}.*?had {NUM} orders averaging {NUM}", q)
    return v and (v[0] * v[1] + v[2] * v[3]) / (v[0] + v[2])


def gross_margin(q):
    v = find(rf"generates {NUM} in revenue against {NUM} in cost", q)
    return v and (v[0] - v[1]) / v[0] * 100


def compound(q):
    v = find(rf"investment of {NUM} grows at {NUM}% compounded annually.*?after {NUM} years?", q)
    return v and v[0] * (1 + v[1] / 100) ** v[2]


def break_even(q):
    v = find(rf"Fixed costs are {NUM}\. Each unit sells for {NUM} with a variable cost of {NUM}", q)
    return v and round(v[0] / (v[1] - v[2]))


def probability(q):
    counts = dict((kind.lower(), num(n)) for n, kind in re.findall(r"(\d[\d,]*) (\w+) tickets", q))
    target = find(r"probability it is an? (\w+) ticket", q)
    if not counts or not target or target[0].lower() not in counts:
        return None
    return counts[target[0].lower()] / sum(counts.values()) * 100


def percent_change(q):
    v = find(rf"moved from {NUM} to {NUM}", q)
    return v and (v[1] - v[0]) / v[0] * 100


def ratio_split(q):
    v = find(rf"{NUM} \w+ budget is split between two \w+ in the ratio (\d+):(\d+)\. What does the (first|second)", q)
    if not v:
        return None
    total, a, b, which = v
    return total * (a if which.lower() == "first" else b) / (a + b)


def series(q):
    m = re.search(r"series runs ([\d,.\s-]+?)\. If", q)
    if not m:
        return None
    xs = [num(x) for x in re.split(r",\s*", m.group(1).strip()) if x]
    diffs = {round(b - a, 6) for a, b in zip(xs, xs[1:])}
    if len(diffs) == 1:
        return xs[-1] + diffs.pop()
    ratios = {round(b / a, 6) for a, b in zip(xs, xs[1:]) if a}
    if len(ratios) == 1:
        return xs[-1] * ratios.pop()
    second = {round((c - b) - (b - a), 6) for a, b, c in zip(xs, xs[1:], xs[2:])}
    if len(second) == 1:
        return xs[-1] + (xs[-1] - xs[-2]) + second.pop()
    return None


def settlement(q):
    m = re.search(r"signed on (\d{1,2} \w+ \d{4}) has an? (\d+)-day", q)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%d %B %Y").date() + timedelta(days=int(m.group(2)))


SOLVERS = [discount, revenue, currency, weighted_average, gross_margin, compound,
           break_even, probability, percent_change, ratio_split, series, settlement]


def parse_option(opt):
    opt = opt.strip()
    try:
        return datetime.strptime(opt, "%d %B %Y").date()
    except ValueError:
        pass
    m = re.search(r"-?[\d,]*\.?\d+", opt.replace("$", ""))
    return num(m.group()) if m else None


def pick(value, options):
    """Letter of the option closest to value, or None if nothing parses."""
    parsed = [parse_option(o) for o in options]
    if isinstance(value, date):
        hits = [i for i, p in enumerate(parsed) if p == value]
        return LETTERS[hits[0]] if hits else None
    scored = [(abs(p - value), i) for i, p in enumerate(parsed) if isinstance(p, float)]
    return LETTERS[min(scored)[1]] if scored else None


def answer(row):
    q = row["question_text"]
    options = [row[f"option_{x}"] for x in "abcd"]
    for solver in SOLVERS:
        value = solver(q)
        if value is not None:
            letter = pick(value, options)
            if letter:
                return letter, solver.__name__
    return None, None


def solve_rounds(rows):
    rounds = {}
    unsolved = []
    for row in rows:
        letter, _ = answer(row)
        if letter is None:
            unsolved.append(row)
            letter = "A"  # still submit the round on time; flagged below
        rounds.setdefault(int(row["round_id"]), {})[f"q{int(row['q_index'])}"] = letter
    return rounds, unsolved


def check(rows):
    keys = {(int(k["round_id"]), int(k["q_index"])): k["answer"].strip() for k in fetch_csv("practice_keys.csv")}
    wrong = 0
    total = 0
    for row in rows:
        k = (int(row["round_id"]), int(row["q_index"]))
        if k not in keys:
            continue
        total += 1
        letter, solver = answer(row)
        if letter != keys[k]:
            wrong += 1
            print(f"round {k[0]} q{k[1]} [{solver}] got {letter}, key {keys[k]}: {row['question_text']}")
    print(f"{total - wrong}/{total} correct")
    return wrong == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="score against practice keys")
    ap.add_argument("--force", action="store_true", help="rewrite files for every round")
    args = ap.parse_args()

    rows = fetch_csv("questions.csv")
    if args.check:
        sys.exit(0 if check(rows) else 1)

    rounds, unsolved = solve_rounds(rows)
    ANSWERS.mkdir(exist_ok=True)
    written = []
    for rid, answers in sorted(rounds.items()):
        path = ANSWERS / f"round-{rid:04d}.json"
        if path.exists() and not args.force:
            continue
        ordered = {f"q{i}": answers.get(f"q{i}", "A") for i in range(1, 26)}
        path.write_text(json.dumps({"round_id": rid, "answers": ordered}, indent=2) + "\n")
        written.append(rid)

    print(f"wrote {len(written)} round file(s): {written}")
    for row in unsolved:
        print(f"::warning::unsolved round {row['round_id']} q{row['q_index']}: {row['question_text']}")


if __name__ == "__main__":
    main()
