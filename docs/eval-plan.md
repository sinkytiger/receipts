# receipts — evaluation plan (registered before any code)

This file is committed before the tool, the benchmark generator, or any result exists. It fixes what will be measured and which results count as a pass. Later changes are listed at the bottom with a date and a reason.

## Terms

- **Statement**: one sentence in a benchmark document that contains one checked number.
- **Planted error**: a statement whose number or direction word was made wrong on purpose. The generator asserts that the planted version differs from the truth under the tolerance rules in `docs/design.md`.
- **Gold plan**: the correct `claims.json` for a document (right locators and formulas), written by the generator.

## E1: the code alone, with gold plans (free)

No model is involved. This measures whether the script reads, recomputes and judges correctly.

### Data

- Built by `bench/generate.py` from synthetic tables. Development seed: `20261119`. Held-out seed: `20261120`.
- Per seed: at least 60 documents and at least 900 statements, half English and half Korean. The generator prints the exact counts and the results file records them.
- Sources: CSV, JSON, Markdown table and XLSX. Each type is the source of at least 10 documents.
- Formulas: value, difference, percent change, ratio, share, sum, mean, min, max, count. Each appears in the statements.
- 30% of statements carry one planted error. The type is drawn uniformly from:
  1. digit typo
  2. wrong period (the neighbouring period's value)
  3. wrong scale (for example thousand for million)
  4. direction word flipped (the number stays, "rose" becomes "fell")
  5. stale value (the previous row's value)
  6. rounding outside tolerance
  7. wrong denominator in a percent change or share
  8. wrong count
- Abstention cases, separate from the above: locators that match zero cells, locators that match two or more cells, and source files that are missing or corrupt. At least 20 of each.
- Coverage cases: for every document, a second copy of the gold plan with a random 10% of its claims removed.

### Measures and pass bars

| # | Measure | Bar |
|---|---|---|
| 1 | Planted errors judged `verified` (false accepts) | 0 |
| 2 | Correct statements judged `wrong` (false alarms) | 0 |
| 3 | Planted errors judged `wrong` | at least 98%. The rest must be `unverifiable`. |
| 4 | Correct statements judged `verified` | at least 98%. The rest must be `unverifiable`. |
| 5 | Zero-cell and multi-cell locators judged `unverifiable` | 100% |
| 6 | Missing or corrupt source: `check` exits non-zero | 100% |
| 7 | Numbers placed by the generator that `scan` finds | at least 99% |
| 8 | Removed claims that `scan` found and the report lists as `uncovered` | 100% |

Reported without a bar: tokens `scan` flags that the generator did not place, by skip rule; results by language, source type, formula and error type.

### Procedure

- Development runs use the development seed as often as needed. Every run is logged with the commit hash.
- The held-out seed is run once, on the commit that is to be released. The README's headline numbers come from that run.
- If a held-out bar fails, the README says so. A fix may then be evaluated on a new seed, and the README lists every held-out run that was made.

## E2: end to end, with a model writing the plan (paid)

- Model: `claude-haiku-5-5`. Spending cap: US$5 for all of E2. A cost estimate is shown and approved before the first paid call. If the first 5 documents cost more than 1.5 times the estimate, the run stops.
- Data: 30 documents from the held-out seed, 15 English and 15 Korean, chosen by the generator before the run.
- Arm A, skill on: the model writes `claims.json`, the script checks.
- Arm B, baseline: the same model is given the document and the sources and asked to review it and list the wrong numbers. No script.
- Measures: planted errors caught, correct statements flagged as wrong, cost per document, and for arm A the share of numbers left `uncovered`. Counts are reported with Wilson 95% intervals.
- No pass bars. The numbers are reported as they come out, whichever arm does better.

## E3: SEC EDGAR connection

To be registered in this file before that part is run. Outline: values returned by the connection for a fixed list of companies, concepts and periods are compared with the values printed in the filings.

## Real cases

Checks of real documents may appear in the README as examples. They are not performance estimates and will be labelled that way.

## Change log

- 2026-11-19: registered. No code, generator or result existed at this commit.
