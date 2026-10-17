# DSO 429 Homework 3: The Hourly Quiz

Answers to the hourly quiz, one file per round in `answers/`.

## How it works

A GitHub Actions workflow (`.github/workflows/answer.yml`) runs `solve.py` every 10 minutes and pushes any new round files to `main`, so rounds get answered inside the full-credit window without a laptop staying on.

For each round that doesn't have a file yet, `solve.py`:

1. Pulls [`questions.csv`](https://github.com/FaisalXL/dso429-hourly-quiz/blob/main/questions.csv).
2. Sends all 25 questions to Claude (`claude-opus-5-5`) in one request, with the answer constrained to a JSON object of `q1`–`q25` → `A`/`B`/`C`/`D`.
3. Cross-checks against formula solvers for the 12 business-arithmetic question types seen in practice (1200/1200 on the practice keys). Any disagreement goes back to Claude at higher effort, and the workflow log shows a warning for each one.
4. If Claude can't be reached, still submits the round on time from the formulas, records it in `pending.json`, and retries it with Claude on later runs while answers are still editable (3 hours after release).

## Setup

The workflow reads an Anthropic API key from the repository secret `ANTHROPIC_API_KEY`:

```bash
gh secret set ANTHROPIC_API_KEY --repo NAT-A-LIE-J/dso429-hw3
```

To use a different model, set the repository variable `QUIZ_MODEL` (for example `gh variable set QUIZ_MODEL --body claude-sonnet-5-5`).

## Checking accuracy

```bash
python3 solve.py --check            # formulas vs. practice keys (free)
python3 solve.py --check-llm 1,2    # Claude vs. practice rounds 1 and 2 (uses the API)
```
