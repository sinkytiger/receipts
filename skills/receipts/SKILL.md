---
name: receipts
description: Check every number in a report, summary, analysis or answer against its source files before calling it checked. Use when the user asks to verify, fact-check, review or double-check numbers ("are these numbers right?", "check this against the data", "숫자 확인해줘", "검증해줘", "원자료와 대조해줘"), and before you deliver a document whose numbers come from CSV, Excel, JSON, Markdown tables or text sources. Gives each number a receipt - verified, wrong (with the recomputed value), unverifiable or uncovered - and stops you from saying "all checked" when some numbers were not.
---

# receipts

You say where each number comes from. The script reads the sources, recomputes, and decides. You never decide a verdict yourself, and you never type a source value into the plan.

The script is `scripts/receipts.py` in this skill's folder. It needs only Python (use `python3` if `python` is not found). Call it with its full path.

## Steps

1. **Get the document into a text file.** The script reads `.md` or `.txt`. If the numbers are in a chat answer, a Word file or a PDF, save the text to a file first. Line numbers refer to that file.
2. **Scan.** `python <skill>/scripts/receipts.py scan report.md` prints every number with its line. This list is the denominator. It also shows what was skipped (dates, years, versions, list markers) and why.
3. **Find the sources and their units.** Use the files the user gave you or named. Do not guess a source and do not look one up on the web. A number with no source file is `no_source`. Note what each source says about its own units: a column name such as `usd_thousands`, a note field, a header line, or what the user told you.
4. **Write `claims.json`** in the document's folder: one claim for every number in the scan list. The format is below.
5. **Check.** `python <skill>/scripts/receipts.py check claims.json` prints the receipts and writes `receipts.json` and `receipts.md`. Exit code 0 means no number is wrong, 1 means at least one is wrong, 2 means the run stopped (a file could not be read, or the plan is not valid).
6. **Repair the plan, never the verdict.** Fix your own mistakes and rerun: a claim listed under "claims that point at nothing" (the quote is not verbatim, or the line is off), a locator that matched no cell or several, a `multiplier` you left out although the source states its unit, a number still `uncovered`. When a number comes back `wrong`, check once that the locator points at what the sentence talks about and that the units follow the source. If they do, the number is wrong. Never choose a locator, formula, `multiplier` or `direction` because it makes the written number match.
7. **Report** as described at the end.

If the run stops with exit code 2, tell the user which file could not be read. Do not report any number as checked.

## A claim

```json
{
  "document": "report.md",
  "claims": [
    {"id": "c1", "line": 7, "quote": "East revenue grew 12.4% year over year", "number": "12.4%",
     "kind": "derived", "formula": "pct_change",
     "operands": {"new": {"file": "sales.csv", "row": {"year": "2025", "region": "East"}, "column": "revenue"},
                  "old": {"file": "sales.csv", "row": {"year": "2024", "region": "East"}, "column": "revenue"}}},
    {"id": "c2", "line": 9, "quote": "Total revenue in 2025 was $1.7M", "number": "$1.7M",
     "kind": "derived", "formula": "sum",
     "operands": {"cells": {"file": "sales.csv", "where": {"year": "2025"}, "column": "revenue"}}},
    {"id": "c3", "line": 14, "quote": "East took 455 orders", "number": "455",
     "kind": "lookup", "operands": {"of": {"file": "sales.csv", "row": {"year": "2025", "region": "East"}, "column": "orders"}}},
    {"id": "c4", "line": 10, "quote": "We expect total revenue to reach $2.0M next year", "number": "$2.0M",
     "kind": "no_source", "reason": "forecast"},
    {"id": "c5", "line": 3, "quote": "for the 2025 planning round", "number": "2025", "kind": "label"}
  ]
}
```

- `line`: the 1-based line of the document. `quote`: copied exactly from that one line. It can be part of the line. `number`: the number as written inside the quote, with or without its unit (`12.4%`, `$1.7M`, `455`). If it appears twice in the quote, add `"occurrence": 2`.
- Paths are relative to the folder of `claims.json`. Absolute paths work too.
- `kind`:
  - `lookup`: the number is one cell. Operand `of`.
  - `derived`: the number is computed. Give `formula` and its operands.
  - `no_source`: there is no source to check against: a forecast, a target, an assumption, a figure from outside the given files, or a calculation the formulas below cannot express. Say which in `reason`. It comes back `unverifiable`, which is the honest answer.
  - `label`: a year that the scan counted as a number (`the 2025 plan`). Only a bare 4-digit year is accepted.

### Formulas

| `formula` | `operands` | Result |
|---|---|---|
| `difference` | `a`, `b` | a − b |
| `pct_change` | `new`, `old` | (new − old) / \|old\| |
| `ratio` | `num`, `den` | num / den |
| `share` | `part`, `whole` | part / whole |
| `sum`, `mean`, `min`, `max` | `items`: a list of cells, or `cells`: a many-cells selector | as named |
| `count` | `rows` | number of matching rows |

### Locators

One cell. It must match exactly one cell.

```json
{"file": "sales.csv", "row": {"year": "2025", "region": "East"}, "column": "revenue"}
{"file": "data.json", "pointer": "/companies/0/revenue"}
{"file": "data.json", "pointer": "/rows", "row": {"name": "Acme"}, "column": "revenue"}
{"file": "tables.md", "table": 2, "row": {"Quarter": "Q3"}, "column": "Revenue"}
{"file": "book.xlsx", "sheet": "Sales", "row": {"Year": "2025"}, "column": "Revenue"}
{"file": "book.xlsx", "sheet": "Sales", "cell": "C7"}
{"file": "notes.txt", "contains": "Total revenue", "nth": 1}
```

Many cells, with `where` instead of `row`. `{}` means every row. Used by `cells`, and allowed wherever one cell is expected, in which case the cells are summed ("total revenue grew 12%" is a `pct_change` of two sums).

```json
{"file": "sales.csv", "where": {"year": "2025"}, "column": "revenue"}
```

Rows, for `count`. `test` is optional and takes `==`, `!=`, `>`, `>=`, `<`, `<=`. A count is a `derived` claim with `"formula": "count"` and `"operands": {"rows": ...}`.

```json
{"file": "orders.csv", "where": {"region": "East"}, "test": {"column": "amount", "op": ">=", "value": "1000"}}
```

Every table locator names its table the same way, whether it uses `row`, `where` or `rows`: a JSON file needs the `pointer` to the list of records (`"pointer": "/rows"`), a Markdown file the `table` number, an Excel file the `sheet`.

### Things that go wrong

- **Units of the source.** If the source is in thousands or millions, add `"multiplier": 1000` (or `1000000`) to every locator that reads such a column, inside formulas too. It works on `where` selectors as well. If the document writes a percent and the source cell holds a fraction (`0.244` for 24.4%), add `"multiplier": 100`. A percent change or share of two values in the same unit needs none.
- **Where a multiplier comes from.** From the source: its column names, notes, or what the user said. Never from the document's number. If the source does not say what unit it is in, you may assume one, but then say so in the report: the check only shows the document is consistent with that assumption. The receipt lists every multiplier you declared.
- **Direction.** For `difference` and `pct_change`, order the operands so the result has the true sign: `new` is the later period, `a − b` is later minus earlier. "fell 6.6%" is checked against a negative result. If the sentence has no clear word for up or down and the number is written without a sign, add `"direction": "up"` or `"down"` to say what the sentence means.
- **Percentage points.** A difference between two percent values is written in points (`1.3%p`, `1.3 percentage points`, `130 bp`). Use `difference`.
- **One number, one claim.** A sentence with three numbers needs three claims. They may share the quote.
- **A number the formulas cannot express** (a CAGR, a median, a rank): use `no_source` with the reason `unsupported formula`. Do not approximate it with another formula.

The full rules (how written numbers are read, rounding, scan rules, output files) are in `references/plan-format.md`. Read it when a case is not covered here.

## Report

Start with the counts line the script printed, unchanged:

```
41 numbers / 33 verified / 3 wrong / 2 unverifiable / 3 uncovered
```

Then:

- every `wrong` number: the sentence, what was written, the recomputed value, and the source cells;
- every `unverifiable` number and why;
- every `uncovered` number. If any remain, say that those were not checked;
- numbers verified with the note `truncated`: the written value is the source value cut off, not rounded (11.30 for 11.3054). Name them so the reader can decide;
- every multiplier you declared and where it came from. Mark the ones the source does not state as assumptions.

What the check does not cover, in one or two lines: numbers written as words ("five years", "두 자릿수"), the skipped tokens the scan listed (dates, years, code), claims that are not numbers, and whether the sources themselves are right.

Wording:

- Say "no errors found in the 33 numbers that were checked". Do not say "all numbers are correct" or "everything checks out" unless `wrong`, `unverifiable` and `uncovered` are all zero. Even then, say what the check covers: numbers against the given source files, not whether the sources are right and not the claims that are not numbers.
- Do not call a number verified unless the script did.
- Do not correct the document unless the user asked for corrections. If they did, change the wrong numbers to the recomputed values and run the check again.
