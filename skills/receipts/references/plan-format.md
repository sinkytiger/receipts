# Plan format, rules and outputs (v0.1)

This is the contract between the model that writes a plan, the script that checks it, and the benchmark. If the script and this file disagree, this file is right and the script has a bug.

## 1. The plan: `claims.json`

```json
{
  "document": "report.md",
  "claims": [ { ... }, { ... } ]
}
```

- `document` and every source `file` are paths relative to the folder of the plan file.
- The plan holds locations, never values. Any number typed into a plan is ignored, except inside `row` / `where` filters and `test`.

### Claim fields

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Unique string within the plan |
| `line` | yes | 1-based line number of the document line that holds the quote |
| `quote` | yes | A verbatim substring of that single line |
| `number` | yes | The number as written, a substring of `quote`. It only locates the number, so it may be the bare digits or include the currency symbol, scale and unit. The value is read from the document, not from this string. |
| `occurrence` | no | 1-based. Which place in `quote` where `number` stands as a whole number. A match inside a longer number (`3` in `2023`) is not a place. Default 1. |
| `kind` | yes | `lookup`, `derived`, `no_source` or `label` |
| `formula` | for `derived` | One of the formulas in section 3. `lookup` means `value`. |
| `operands` | for `lookup`, `derived` | See section 3 |
| `direction` | no | `up` or `down`. Only used with `difference` and `pct_change`. |
| `reason` | for `no_source` | Free text, for example `forecast`, `target`, `assumption` |

`label` says "this token is a year, not a quantity" (`2025 revenue`, `the 2025 plan`). It is accepted only for a bare 4-digit number from 1900 to 2100. The token then moves from the numbers to the skipped tokens under the rule `label`, and the receipt lists it. A `label` claim on any other token is refused (`label_not_allowed`) and the number stays `uncovered`.

## 2. Locators

### Single cell

Exactly one cell must match. Zero matches or more than one give `unverifiable`.

```json
{"file": "sales.csv", "row": {"year": "2025", "region": "East"}, "column": "revenue"}
{"file": "data.json", "pointer": "/companies/0/revenue"}
{"file": "data.json", "pointer": "/companies", "row": {"name": "Acme"}, "column": "revenue"}
{"file": "tables.md", "table": 2, "row": {"Quarter": "Q3"}, "column": "Revenue"}
{"file": "book.xlsx", "sheet": "Sales", "row": {"Year": "2025"}, "column": "Revenue"}
{"file": "book.xlsx", "sheet": "Sales", "cell": "C7"}
{"file": "notes.txt", "contains": "Total revenue", "nth": 1}
```

- **CSV / TSV** (`.csv`, `.tsv`): the first line is the header. `row` lists column/value pairs that must all match.
- **JSON** (`.json`): `pointer` is an RFC 6901 JSON Pointer. With `row` and `column`, the pointer must lead to an array of objects.
- **Markdown** (`.md`): `table` is the 1-based index of the pipe table in the file. The first table row is the header.
- **Excel** (`.xlsx`): `sheet` is the sheet name. The header is row 1 unless `header_row` (1-based) says otherwise. Stored values are read. Formulas are not re-evaluated.
- **Text** (`.txt`, or any other extension): `contains` must match exactly one line. `nth` (1-based, default 1) picks the n-th number on that line, counted the way `scan` counts numbers.
- **Matching**: cell text and filter values are compared as strings after trimming spaces. A numeric cell whose value is a whole number is compared without a decimal part (`2025.0` matches `"2025"`). A `row` or `where` key that is not a column of the table gives `locator_no_match`.
- **Cell values**: a cell may hold a JSON or Excel number, or text with thousands separators, a currency symbol, a trailing `%`, or parentheses for a negative value (`(1,500)`).
- **`multiplier`** (optional, a number or a numeric string): the cell value is multiplied by it. Use it when the source is in thousands or millions. It works on many-cells selectors too, on each cell. It appears on the receipt.
- Files are read as UTF-8 (a BOM is accepted).

### Many cells

Used by `sum`, `mean`, `min`, `max` and by the `whole` of `share`.

```json
{"file": "sales.csv", "where": {"year": "2025"}, "column": "revenue"}
```

`where` works like `row` but may match many rows. `{}` selects every row. It needs `pointer` for JSON, `table` for Markdown and `sheet` for Excel. Zero matching rows give `unverifiable`. Empty cells are skipped. A cell that is not a number gives `unverifiable`.

A many-cells selector may also stand wherever a formula expects one cell. Its cells are then summed, and the receipt says so. This covers "total revenue grew 12%" (`pct_change` of two sums) and "average order value" (`ratio` of two sums).

### Rows, for `count`

```json
{"file": "orders.csv", "where": {"region": "East"}, "test": {"column": "amount", "op": ">=", "value": "1000"}}
```

`test` is optional. `op` is one of `==`, `!=`, `>`, `>=`, `<`, `<=`, compared as numbers.

## 3. Formulas

| `formula` | `operands` | Result |
|---|---|---|
| `value` | `{"of": cell}` | the cell |
| `difference` | `{"a": cell, "b": cell}` | a − b |
| `pct_change` | `{"new": cell, "old": cell}` | (new − old) / \|old\| |
| `ratio` | `{"num": cell, "den": cell}` | num / den |
| `share` | `{"part": cell, "whole": cell or many}` | part / sum(whole) |
| `sum`, `mean`, `min`, `max` | `{"items": [cell, ...]}` or `{"cells": many}` | as named |
| `count` | `{"rows": rows}` | number of matching rows |

Division by zero gives `unverifiable`.

## 4. How a written number is read

The script reads the number from the document at the place the claim points to.

- **Sign**: `-`, `−` (U+2212) or `+` directly before the number.
- **Digits**: `1234`, `1,234`, `1,234.5`. A comma is a thousands separator, a point is the decimal mark.
- **Percent**: `%`, `percent`, `퍼센트`.
- **Percentage points**: `%p`, `pp`, `percentage points`, `%포인트`.
- **Basis points**: `bp`, `bps`.
- **Multiples**: `x` or `배` after the number (`1.8x`).
- **Scale**: `K`, `M`, `B`, `T` attached to the number; `thousand`, `million`, `billion`, `trillion`; Korean `십`, `백`, `천` and `만`, `억`, `조`, including compounds such as `3조 2,000억` and `2천억`.
- **Ignored**: currency symbols and words (`$`, `US$`, `₩`, `€`, `£`, `¥`, `원`, `달러`, `USD`, `KRW`), counter words (`명`, `건`, `개`, `units`, ...).

### Unit of the comparison

| Written as | Formula | Compared with |
|---|---|---|
| percent | `pct_change`, `ratio`, `share` | result × 100 |
| plain or multiple | `pct_change`, `ratio`, `share` | result |
| percent | any other formula | result (the source cells are already in percent) |
| percentage points | `difference` | result |
| basis points | `difference` | result × 100 |
| percentage points or basis points | any other formula | `unverifiable` |

### Tolerance

Let `q` be the value of one unit in the last written digit after scale (`12.4%` → 0.1, `$1.2M` → 100,000, `3조 2,000억` → 1억), `w` the written value and `v` the recomputed value.

- `verified` if `w` equals `v` rounded to `q` half-up (a tie goes away from zero), or half-even.
- In the default mode also `verified` if `w` equals `v` truncated toward zero at `q`. The receipt notes `truncated`.
- `--mode strict` accepts rounding only.
- **Hedged integers**: if the number is an integer ending in zeros and a hedge word (`about`, `approximately`, `roughly`, `around`, `nearly`, `almost`, `~`, `약`, `대략`, `거의`) stands directly before it or before its currency symbol, `q` is 10 to the number of trailing zeros (`about 1,200,000` → 100,000).

### Direction

Only for `difference` and `pct_change`.

- If the number is written with an explicit sign (`-3.2%`, `+1.5%p`), the comparison is signed and direction words are ignored.
- Otherwise the script looks for direction words in the quote. Up: rose, risen, increased, increase, grew, grown, growth, up, higher, gained, climbed, jumped, surged, expanded, 증가, 늘었, 늘어, 늘며, 늘고, 상승, 올랐, 올라, 오른, 성장, 확대, 높아, 급등. Down: fell, fallen, decreased, decrease, declined, decline, dropped, drop, down, lower, shrank, contracted, slipped, plunged, reduced, 감소, 줄었, 줄어, 줄며, 줄고, 하락, 내렸, 내려, 내린, 떨어, 축소, 낮아, 급락.
- English words are matched as whole words, Korean ones as stems (`늘었` matches `늘었다`).
- If the quote has words of exactly one direction, that is the direction. If the plan's `direction` disagrees with it, the verdict is `unverifiable`.
- If the quote has no direction word, or both kinds, the plan's `direction` is used. If there is none, the comparison is signed.
- With a direction, the written number is a size: `|w|` is compared with `|v|`, and `v` must be positive for `up` and negative for `down`. Otherwise `wrong`.

## 5. Verdicts

Every number that `scan` finds ends in exactly one of four buckets.

| Verdict | When |
|---|---|
| `verified` | section 4 holds |
| `wrong` | it does not. The receipt shows the recomputed value. |
| `unverifiable` | `no_source`; a locator matched zero or several cells; a cell is not a number; an unsupported unit/formula pair; division by zero; direction conflict; a second claim on a number that already has one |
| `uncovered` | no claim points at the number |

A claim whose quote is not on its line, or whose `number` is not in the quote, binds to nothing. It is listed under `unbound_claims` and the number it meant stays `uncovered`.

A claim may point at a token that `scan` skipped (a year, for example). The token is then counted as a number.

## 6. `scan`

`receipts.py scan DOCUMENT` prints JSON:

```json
{
  "document": "report.md",
  "numbers": [{"line": 18, "start": 14, "end": 19, "text": "12.4%"}],
  "skipped": {"date": 12, "version": 2},
  "skipped_items": [{"line": 3, "start": 0, "end": 10, "text": "2026-11-19", "rule": "date"}]
}
```

`line` is 1-based. `start` and `end` are 0-based character offsets in the line, `end` exclusive. A token runs from the sign or currency symbol through the percent sign, scale or multiple word.

Skip rules, each with its own count. When two rules could apply, the earlier one in this list wins:

- `code`: fenced and inline code
- `url`: links and e-mail addresses
- `date`: full dates, month and day, quarters and fiscal labels such as `Q3`, `Q3 2025`, `1분기`, `FY2025`
- `year`: a bare 4-digit number from 1900 to 2100 in a year context. The contexts are: after `in`, `since`, `during`, `until`, `through`, `year`, `fiscal`, `vs`, `end of`, `as of`; after `by` or `from`, unless a direction word comes directly before it (`fell by 2000` and `rose from 1950` are amounts, while `up 12% from 2024` names a year); before `년`; in a year range; or joined to another year by `to`, `and`, `or`, `-`, `–`. A number followed by a unit or counter word (`2000 units`, `2000명`) is never a year.
- `time`, `version`
- `heading_number` (`## 2. Results`), `list_marker` (`1.` at the start of a line)
- `reference` (`[3]`)
- `identifier`: digits glued to ASCII letters or joined to a word by a hyphen, such as `CO2`, `sales_2025`, `COVID-19`. Hangul glued to a number (`3명`, `0.86였다`, `2026-11-19에`) changes nothing: the number is still a number and the date is still a date.
- `ordinal`: `1st`, `2nd`, `제3`, `3번째`

The rules lean toward counting. A 4-digit number with no year context stays a number and shows up as `uncovered` if the plan leaves it out.

## 7. `check`

`receipts.py check PLAN [--out DIR] [--mode default|strict] [--strict]` writes `receipts.json` and `receipts.md` into `DIR` (default: the plan's folder) and prints the Markdown.

```json
{
  "document": "report.md",
  "mode": "default",
  "counts": {"numbers": 41, "verified": 33, "wrong": 3, "unverifiable": 2, "uncovered": 3},
  "skipped": {"date": 12, "version": 2},
  "claims": [
    {"id": "c1", "line": 18, "start": 14, "end": 19, "text": "12.4%", "quote": "Revenue grew 12.4% year over year",
     "verdict": "wrong", "reason": "mismatch", "recomputed": "11.9%", "recomputed_raw": "11.93415637860082304526748971",
     "sources": ["sales.csv [year=2025] revenue = 1088", "sales.csv [year=2024] revenue = 972"], "note": ""}
  ],
  "uncovered": [{"line": 30, "start": 5, "end": 10, "text": "1,204"}],
  "unbound_claims": [{"id": "c9", "reason": "quote_not_on_line"}],
  "labels": [{"id": "c10", "line": 3, "start": 20, "end": 24, "text": "2025"}]
}
```

Reasons for unbound claims: `invalid_claim`, `quote_not_on_line`, `number_not_in_quote`, `no_number_at_position`, `label_not_allowed`.

`counts.verified + wrong + unverifiable + uncovered = counts.numbers`.

Reasons: `match`, `truncated`, `mismatch`, `direction_mismatch`, `declared_no_source`, `locator_no_match`, `locator_multiple`, `cell_not_numeric`, `unsupported`, `division_by_zero`, `direction_conflict`, `duplicate_claim`, `internal_error`.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | no `wrong`. With `--strict`: also no `unverifiable`, `uncovered` or unbound claim |
| 1 | at least one `wrong` (or a `--strict` violation) |
| 2 | stopped: the document or a source file is missing, unreadable or corrupt, the plan is not valid, or the script hit an internal error. Nothing is passed. |

A missing sheet, table, column or key inside a readable file is a locator problem (`locator_no_match`), not a stop.
