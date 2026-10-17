# DSO 429 Homework 3: The Hourly Quiz

Answers to the hourly business-arithmetic quiz, one file per round in `answers/`.

`solve.py` pulls [`questions.csv`](https://github.com/FaisalXL/dso429-hourly-quiz/blob/main/questions.csv), solves each question with a fixed formula for its type, and writes `answers/round-NNNN.json` for every round that doesn't have one yet. It scores 1200/1200 on the practice keys (`python3 solve.py --check`).

A GitHub Actions workflow (`.github/workflows/answer.yml`) runs it every 10 minutes and pushes any new round files to `main`, so graded rounds get answered within the full-credit window without a laptop staying on.

Question types handled: discounts, total revenue, currency conversion, weighted averages, gross margin, compound interest, break-even units, ticket probabilities, percentage change, ratio splits, number series, and settlement dates. Any question the solver doesn't recognize is still submitted on time and shows up as a warning in the workflow run.
