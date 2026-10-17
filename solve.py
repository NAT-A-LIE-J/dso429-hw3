"""Answer every released round of the DSO 429 hourly quiz.

Fetches questions.csv and writes answers/round-NNNN.json for every round that
doesn't have a file yet. Claude answers every question; the formula solvers
below cross-check the question types they recognize, and any question where
the two disagree goes back to Claude for a second, more careful look.

If Claude can't be reached (no ANTHROPIC_API_KEY, API error), the round is
still submitted on time from the formulas and listed in pending.json, so later
runs retry it with Claude while answers can still be changed (3 hours).

  python3 solve.py                  # write files for new rounds
  python3 solve.py --check          # score the formulas against the practice keys
  python3 solve.py --check-llm 1,2  # score Claude on practice rounds 1 and 2
  python3 solve.py --force          # rewrite every round's file
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE = "https://raw.githubusercontent.com/FaisalXL/dso429-hourly-quiz/main/"
ROOT = Path(__file__).parent
ANSWERS = ROOT / "answers"
PENDING = ROOT / "pending.json"
LETTERS = "ABCD"
NUM = r"\$?(-?[\d,]+(?:\.\d+)?)"

MODEL = os.environ.get("QUIZ_MODEL") or "claude-opus-5-5"
# Answers stay editable for 3 hours after release; stop retrying a little
# before that so the last retry's push lands inside the window.
RETRY_WINDOW = timedelta(hours=2, minutes=45)

SYSTEM = (
    "You are answering a multiple-choice quiz. Each question has exactly one "
    "correct option. Questions may be business arithmetic or general knowledge. "
    "For calculations, compute the exact value before choosing, and watch for "
    "distractors that are off by one step (simple vs. compound interest, "
    "markup vs. margin, off-by-one dates, sign of a percentage change)."
)


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




def formula_answer(row):
    """(letter, solver name) from the formula solvers, or (None, None)."""
    q = row["question_text"]
    options = [row[f"option_{x}"] for x in "abcd"]
    for solver in SOLVERS:
        value = solver(q)
        if value is not None:
            letter = pick(value, options)
            if letter:
                return letter, solver.__name__
    return None, None


# --- Claude ---------------------------------------------------------------

def make_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("::warning::ANTHROPIC_API_KEY is not set; answering from formulas only")
        return None
    import anthropic
    return anthropic.Anthropic(max_retries=4)


def ask_claude(client, rows, effort):
    """{q_index: letter} for rows, from one structured-output request."""
    import anthropic
    blocks = []
    for r in rows:
        opts = "\n".join(f"{L}) {r[f'option_{x}']}" for L, x in zip(LETTERS, "abcd"))
        blocks.append(f"q{int(r['q_index'])}. {r['question_text']}\n{opts}")
    keys = [f"q{int(r['q_index'])}" for r in rows]
    schema = {
        "type": "object",
        "properties": {k: {"type": "string", "enum": list(LETTERS)} for k in keys},
        "required": keys,
        "additionalProperties": False,
    }
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=64000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": "Answer every question.\n\n" + "\n\n".join(blocks)}],
    ) as stream:
        response = stream.get_final_message()
    if response.stop_reason != "end_turn":
        raise anthropic.AnthropicError(f"stop_reason={response.stop_reason}")
    text = next(b.text for b in response.content if b.type == "text")
    data = json.loads(text)
    u = response.usage
    print(f"  claude ({effort}): {len(rows)} question(s), {u.input_tokens} in / {u.output_tokens} out tokens")
    return {int(k[1:]): v for k, v in data.items()}


def answer_round(client, rows):
    """({q_index: letter}, used_claude) for one round's rows."""
    formula = {int(r["q_index"]): formula_answer(r)[0] for r in rows}
    claude = {}
    if client:
        import anthropic
        try:
            claude = ask_claude(client, rows, effort="medium")
        except (anthropic.AnthropicError, ValueError, StopIteration) as e:
            print(f"::warning::Claude failed on round {rows[0]['round_id']}: {e}")

    letters = {i: claude.get(i) or formula[i] or "A" for i in formula}
    disputed = [r for r in rows if claude.get(int(r["q_index"])) and formula[int(r["q_index"])]
                and claude[int(r["q_index"])] != formula[int(r["q_index"])]]
    if disputed:
        import anthropic
        try:
            second = ask_claude(client, disputed, effort="high")
        except (anthropic.AnthropicError, ValueError, StopIteration) as e:
            print(f"::warning::Claude second look failed: {e}")
            second = {}
        for r in disputed:
            i = int(r["q_index"])
            final = second.get(i, claude[i])
            letters[i] = final
            print(f"::warning::round {r['round_id']} q{i}: Claude {claude[i]}, formula {formula[i]}, "
                  f"second look {second.get(i, '-')}, submitted {final}: {r['question_text']}")

    for r in rows:
        i = int(r["q_index"])
        if not claude.get(i) and not formula[i]:
            print(f"::warning::round {r['round_id']} q{i} guessed A (no Claude, no formula): {r['question_text']}")
    return letters, len(claude) == len(rows)


# --- Rounds ---------------------------------------------------------------

def load_pending():
    return set(json.loads(PENDING.read_text())) if PENDING.exists() else set()


def released_at(rows):
    return datetime.fromisoformat(rows[0]["released_at"].replace("Z", "+00:00"))


def rounds_to_answer(by_round, pending, force):
    now = datetime.now(timezone.utc)
    todo = []
    for rid, rows in sorted(by_round.items()):
        missing = not (ANSWERS / f"round-{rid:04d}.json").exists()
        retry = rid in pending and now - released_at(rows) < RETRY_WINDOW
        if missing or retry or force:
            todo.append(rid)
    return todo


def write_round(rid, letters):
    ordered = {f"q{i}": letters.get(i, "A") for i in range(1, 26)}
    path = ANSWERS / f"round-{rid:04d}.json"
    path.write_text(json.dumps({"round_id": rid, "answers": ordered}, indent=2) + "\n")


def check_formulas(rows):
    keys = practice_keys()
    wrong = total = 0
    for row in rows:
        k = (int(row["round_id"]), int(row["q_index"]))
        if k not in keys:
            continue
        total += 1
        letter, solver = formula_answer(row)
        if letter != keys[k]:
            wrong += 1
            print(f"round {k[0]} q{k[1]} [{solver}] got {letter}, key {keys[k]}: {row['question_text']}")
    print(f"formulas: {total - wrong}/{total} correct")
    return wrong == 0


def check_claude(by_round, round_ids):
    client = make_client()
    if not client:
        sys.exit(1)
    keys = practice_keys()
    wrong = total = 0
    for rid in round_ids:
        letters, complete = answer_round(client, by_round[rid])
        for i, letter in letters.items():
            total += 1
            if letter != keys[(rid, i)]:
                wrong += 1
                print(f"round {rid} q{i} got {letter}, key {keys[(rid, i)]}")
        if not complete:
            print(f"round {rid}: Claude did not answer every question")
    print(f"claude: {total - wrong}/{total} correct")
    return wrong == 0


def practice_keys():
    return {(int(k["round_id"]), int(k["q_index"])): k["answer"].strip() for k in fetch_csv("practice_keys.csv")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="score the formulas against practice keys")
    ap.add_argument("--check-llm", metavar="ROUNDS", help="score Claude on these practice rounds, e.g. 1,2")
    ap.add_argument("--force", action="store_true", help="rewrite files for every round")
    args = ap.parse_args()

    rows = fetch_csv("questions.csv")
    by_round = {}
    for row in rows:
        by_round.setdefault(int(row["round_id"]), []).append(row)

    if args.check:
        sys.exit(0 if check_formulas(rows) else 1)
    if args.check_llm:
        sys.exit(0 if check_claude(by_round, [int(x) for x in args.check_llm.split(",")]) else 1)

    pending = load_pending()
    todo = rounds_to_answer(by_round, pending, args.force)
    client = make_client() if todo else None
    ANSWERS.mkdir(exist_ok=True)
    for rid in todo:
        print(f"round {rid}:")
        letters, complete = answer_round(client, by_round[rid])
        write_round(rid, letters)
        if complete:
            pending.discard(rid)
        else:
            pending.add(rid)

    # Drop rounds whose edit window has closed; their file is final.
    pending = {rid for rid in pending if rid in by_round
               and datetime.now(timezone.utc) - released_at(by_round[rid]) < RETRY_WINDOW}
    if pending or PENDING.exists():
        PENDING.write_text(json.dumps(sorted(pending)) + "\n")
    print(f"answered {len(todo)} round(s): {todo}; awaiting Claude retry: {sorted(pending)}")


if __name__ == "__main__":
    main()
