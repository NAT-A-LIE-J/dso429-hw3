"""List the distinct question templates in data/questions.csv with one example each."""
import collections
import csv
import re

rows = list(csv.DictReader(open("data/questions.csv")))
counts = collections.Counter()
examples = {}
for r in rows:
    key = " ".join(re.sub(r"\d[\d,.]*", "#", r["question_text"]).split()[:6])
    counts[key] += 1
    examples.setdefault(key, r)

print(len(rows), "rows", len(counts), "templates")
for key, n in counts.most_common():
    r = examples[key]
    opts = " / ".join(r[f"option_{x}"] for x in "abcd")
    print(n, "|", r["question_text"], "|", opts)
