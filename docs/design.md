# receipts — design (v0.1 draft)

Status: the core script exists and follows `skills/receipts/references/plan-format.md`, which is now the detailed contract. The skill packaging and the SEC EDGAR connection do not exist yet. Changes to this document after the first commit are listed at the bottom.

## What it is

An agent skill plus one small script. Every number in a document an agent wrote gets a receipt: where the number came from and what it is when recomputed from that source. Numbers without a receipt are listed, not hidden.

## Principle

The model proposes, code decides. The model only writes a plan that says "this number comes from that cell, by this formula". Code reads the sources, recomputes, and gives the verdict. The model never grades its own numbers.

## Pipeline

| Step | Who | What |
|---|---|---|
| 1. `scan` | code | Find every number in the document, with line numbers. This list is the denominator. Dates, version strings, list markers and similar tokens are skipped by rule, and each skip rule reports its count. |
| 2. plan (`claims.json`) | model | For each number: the sentence quoted verbatim, the kind of claim, the source locator(s), and the formula. The plan holds locators, not values. |
| 3. `check` | code | Confirm the quote is in the document verbatim. Read the operands from the sources. Recompute. Compare with the written number. |
| 4. coverage | code | Match the scan list against the plan. Numbers the plan left out are reported as `uncovered`. |
| 5. report | code | A receipts table (Markdown) and `receipts.json`. The exit code is non-zero if any number is `wrong`. |

### Verdicts

- `verified`: the written number equals the source value or the recomputed value, under the tolerance rules below.
- `wrong`: it does not. The report shows the recomputed value.
- `unverifiable`: the source could not be read, the locator matched zero cells or more than one, the formula is not supported, or the plan declared the number as having no source (forecast, target, assumption). The tool abstains. It does not guess.
- `uncovered`: the number is in the document but not in the plan.

### Plan format (sketch)

```json
{
  "document": "report.md",
  "claims": [
    {
      "id": "c1",
      "line": 18,
      "quote": "Revenue grew 12.4% year over year",
      "number": "12.4%",
      "kind": "derived",
      "formula": "pct_change",
      "operands": {
        "new": {"file": "sales.csv", "row": {"year": "2025"}, "column": "revenue"},
        "old": {"file": "sales.csv", "row": {"year": "2024"}, "column": "revenue"}
      },
      "direction": "up"
    },
    {
      "id": "c2",
      "line": 25,
      "quote": "we expect margins to reach 30% next year",
      "number": "30%",
      "kind": "no_source",
      "reason": "forecast"
    }
  ]
}
```

### Rules

- **Tolerance.** The recomputed value must round to the written number at the written precision. Default mode also accepts truncation and marks it. Strict mode accepts rounding only.
- **Units and scale.** `%`, bp, K/M/B, and the Korean scale words 천/만/억/조 are converted. A currency that differs from the source's is marked.
- **Direction words.** "rose/fell", "up/down", "늘었다/줄었다" must agree with the sign of the recomputed change.
- **Formulas in v0.1.** value, difference, percent change, ratio, share, sum, mean, min, max, count.
- **One cell.** A locator must match exactly one cell. Row and column labels are part of the match.
- **Fail closed.** If a source file cannot be opened or parsed, `check` stops with an error. It never passes a number it could not read.
- **Wording.** The skill tells the agent to open its answer with the counts (numbers found / verified / wrong / unverifiable / uncovered) and to say "no errors found in what was checked" instead of "no errors".

## Sources in v0.1

| Source | v0.1 | Note |
|---|---|---|
| CSV, TSV, JSON, Markdown tables, plain text | yes | Python standard library only |
| Excel (.xlsx) | yes, values only | Reads stored cell values. Does not re-evaluate spreadsheet formulas. |
| SEC EDGAR | yes | See below |
| PDF | later | Needs an extra library |
| DART (Korean filings) | later | Reuses [finground-lite-kr](https://github.com/sinkytiger/finground-lite-kr) |

The script uses only the Python standard library, so it runs where installing packages is blocked.

## SEC EDGAR connection

Checked on the SEC's API page (2026-11-19):

- `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`: all XBRL facts of one company
- `https://data.sec.gov/api/xbrl/companyconcept/CIK##########/us-gaap/<Concept>.json`: one concept
- `https://data.sec.gov/submissions/CIK##########.json`: filing list
- No API key or login is needed.

Open items, to be settled before this part is built:

- The SEC's fair-access rules (request rate, how a client identifies itself). If a contact string is required, the user supplies it through an environment variable. No default identity is shipped.
- Three-month versus year-to-date values in quarterly filings.
- The same period reported more than once (restatements, later filings).
- Revenue and similar items filed under different concept names by different companies.
- Mapping a ticker to a CIK.

## Out of scope for v0.1

- Claims that are not numbers (rules, quotations, "all" / "none" statements).
- Whether the source itself is right.
- Finding sources on the web. The tool checks against files the user has and against filings.
- A hook that blocks the agent from finishing. v0.1 relies on the skill text and the exit code.

## Planned layout

```
.claude-plugin/plugin.json, marketplace.json
skills/receipts/SKILL.md
skills/receipts/references/     plan format, tolerance, locators
skills/receipts/scripts/receipts.py    scan / check / report / edgar
bench/                          planted-error generator and scorer
examples/
tests/
docs/design.md, docs/eval-plan.md
```

## Known risks

| Risk | Response |
|---|---|
| The model leaves inconvenient numbers out of the plan | Code counts the denominator, so they show up as `uncovered` |
| A number matches a different cell by coincidence | Exactly-one-cell rule, and labels are matched too |
| Rounding and unit readings are ambiguous | Rules are fixed in writing. When still ambiguous, the verdict is `unverifiable` |
| The agent skips the skill's steps | Exit code and result file in v0.1. A hook later |

## Change log

- 2026-11-19: first version, written before any code.
- 2026-11-19, later: the core script was written. Four things were added to the design above, all described in `plan-format.md`:
  - A plan may mark a bare 4-digit year as a `label`. Such tokens are listed on the receipt and leave the count of numbers.
  - A many-cells selector may stand where a formula expects one cell. Its cells are summed.
  - An internal error stops the run with exit code 2 instead of producing a verdict.
  - Year detection leans toward counting: a 4-digit number with no clear year context stays a number.
