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

### SEC EDGAR

A single cell may also be a fact a company reported to the SEC. Section 8 says how the data is fetched.

```json
{"edgar": "AAPL", "concept": "us-gaap:Assets", "unit": "USD", "period": "2026-09-26"}
{"edgar": "320193", "concept": "us-gaap:NetIncomeLoss", "period": "2025-09-28..2026-09-26",
 "accession": "0000320193-26-000026"}
```

| Field | Required | Meaning |
|---|---|---|
| `edgar` | yes | A ticker or a CIK. Digits only, or `CIK` followed by digits, is a CIK (leading zeros are optional). Anything else is a ticker, looked up in the SEC's ticker list without regard to case. |
| `concept` | yes | `taxonomy:Tag` as the filing tags it, for example `us-gaap:Assets`. Case matters. |
| `period` | yes | One date `YYYY-MM-DD` for a balance, or `start..end` (`YYYY-MM-DD..YYYY-MM-DD`) for a flow. |
| `unit` | no | As EDGAR names it: `USD`, `shares`, `USD/shares`, `pure`. Case does not matter. It may be left out when the company reports the concept in one unit only. |
| `accession` | no | Only the fact as reported in this filing (`0000320193-26-000026`, dashes optional). |
| `version` | no | `first` or `latest`. See below. |

- **Exact periods.** The dates must equal the dates in the filing. Three months and nine months that end on the same day are two periods. A flow asked as one date, or a balance asked as a range of more than one day, matches nothing. A period of one day is one date: `2026-09-22..2026-09-22` and `2026-09-22` name the same fact, because the SEC's API stores a one-day period (a dividend paid on one date, for example) under a single date.
- **No match.** An unknown ticker or CIK, a concept the company has not reported, a unit the concept does not have, a period with no fact, or an accession that does not report the fact give `locator_no_match`. A concept with several units and no `unit` gives `locator_multiple`. A `period`, `accession` or `version` that is not written as above gives `locator_no_match`.
- **The same fact in several filings.** Companies repeat earlier periods as comparison columns, so most facts are reported more than once. If every filing gives the same value, that value is used. If they differ (a restatement or a reclassification) and the locator has neither `accession` nor `version`, the verdict is `unverifiable` with the reason `edgar_versions_differ`, and the receipt lists each value with its filing. With `version`, the report with the earliest (`first`) or latest (`latest`) filing date is used, and the receipt notes that other filings differ. If the reports on that filing date, or inside the one `accession`, still differ, the verdict is `unverifiable` (`edgar_versions_differ`).
- **Values** are as the company filed them: money in whole dollars (or whole units of the currency named), share counts in whole shares, per-share amounts in dollars, and ratios (unit `pure`, a tax rate for example) as fractions, so 17% is stored as 0.17. A document that writes such a ratio as a percent needs `"multiplier": 100`. `multiplier` works as on any cell.
- **What EDGAR does not hold.** The SEC's API carries the facts a company reports for itself as a whole. Numbers on a breakdown axis (by segment, product or region) are not there and give `locator_no_match`. Check those against a file. Concepts a company defined itself (under its own prefix instead of `us-gaap` or `dei`) were also absent from every company's data looked at so far.
- An EDGAR locator is a single cell. `where` and `rows` do not work with it (`unsupported`). To add up EDGAR facts, list them under `items`.

### DART (Korean filings)

A single cell may also be an amount in the financial statements of a periodic report filed with Korea's DART (1분기·반기·3분기·사업보고서). Section 9 says how the data is fetched.

```json
{"dart": "005930", "year": 2026, "report": "q3", "fs": "CFS", "statement": "IS",
 "account": "dart_OperatingIncomeLoss", "column": "thstrm_amount"}
{"dart": "00126380", "year": 2025, "report": "annual", "fs": "OFS", "statement": "BS",
 "name": "자산총계", "column": "frmtrm_amount", "rcept_no": "20260310002820"}
```

| Field | Required | Meaning |
|---|---|---|
| `dart` | yes | The company: a stock code (6 characters, `005930`) or DART's own company code (8 digits, `00126380`). Letters in a stock code are read without regard to case. |
| `year` | yes | The business year as DART files the report (`bsns_year`), 2015 or later. A whole number or a string of four digits. |
| `report` | yes | `q1`, `half`, `q3` or `annual`: 1분기보고서, 반기보고서, 3분기보고서, 사업보고서. |
| `fs` | yes | `CFS` for the consolidated statements (연결), `OFS` for the separate ones (별도·개별). There is no default. |
| `statement` | yes | `BS` (재무상태표), `IS` (손익계산서), `CIS` (포괄손익계산서) or `CF` (현금흐름표). |
| `account` | one of the two | The row's `account_id`, for example `ifrs-full_Revenue`. Case matters. |
| `name` | one of the two | The row's `account_nm`, for example `영업이익`, compared after trimming spaces at both ends. |
| `column` | yes | Which amount of the row, under DART's own name: `thstrm_amount`, `thstrm_add_amount`, `frmtrm_amount`, `frmtrm_q_amount`, `frmtrm_add_amount` or `bfefrmtrm_amount`. |
| `rcept_no` | no | The receipt number (14 digits) of the filing. The claim is checked only if DART serves the report under this number. |

`report`, `fs` and `statement` are read without regard to case.

- **One company.** Eight digits are a company code and are used as they are. Six characters are a stock code, looked up in DART's company code file. No company with that stock code gives `locator_no_match`, several give `locator_multiple`. Any other text gives `locator_no_match`. A company name is not accepted: find the code with the look-up command in section 9.
- **One row.** The row is looked for among the rows of the named statement in the named report. With `account`, its `account_id` must equal it. With `name`, its `account_nm` must equal it. With both, both must hold. No row gives `locator_no_match`, several rows give `locator_multiple`. DART marks a row that has no standard code with the text `-표준계정코드 미사용-` in place of an id. `account` never matches such a row. Use `name` for it.
- **One amount.** The column must be present in the row and hold a number. A column that is absent or empty gives `locator_no_match`. Text that is not a number gives `cell_not_numeric`.
- **No report.** If DART has no statements for that company, year, report and `fs` (its answer `013`), the verdict is `unverifiable` (`locator_no_match`). So is a field that is not written as above: a year before 2015, a `report`, `fs`, `statement` or `column` outside the lists, a `rcept_no` that is not 14 digits.
- **What each column holds**, as DART's guide describes the columns. The receipt prints the name DART gives the period and the table's words for the column (`제 58 기 3분기, the three months`).

| Report | Statement | `thstrm_amount` | `thstrm_add_amount` | `frmtrm_amount` | `frmtrm_q_amount` | `frmtrm_add_amount` | `bfefrmtrm_amount` |
|---|---|---|---|---|---|---|---|
| `annual` | `BS` | the year end | | the previous year end | | | the year end two years back |
| `annual` | `IS`, `CIS`, `CF` | the year | | the previous year | | | the year two years back |
| `q1`, `half`, `q3` | `BS` | the period end | | the end of the previous business year | | | |
| `q1`, `half`, `q3` | `IS`, `CIS` | the three months | from the start of the business year to the period end | | the same three months a year earlier | the same cumulative period a year earlier | |
| `q1`, `half`, `q3` | `CF` | from the start of the business year to the period end | | the same period a year earlier | the same period a year earlier | | |

  In a half-year or third-quarter report, `thstrm_amount` of an income statement is the last three months alone. The figure for the six or nine months is `thstrm_add_amount`. On an interim balance sheet the comparison column is the end of the previous business year, not the same date a year earlier.
- **One version.** DART's API serves one version of a report, the latest. When a company amends a report, the amended statements take the place of the earlier ones. The receipt shows the receipt number of the filing the value comes from. With `rcept_no`, the claim is checked only if that is the number DART serves. If DART serves the report under another number, the verdict is `unverifiable` with the reason `dart_receipt_differs`, and the receipt names the number DART serves. A plan that names the receipt number therefore says so when a report has been replaced, instead of comparing the document with other figures.
- **Values** are in whole units of the row's currency, which the receipt shows: whole won for `KRW`, won for per-share amounts. A document that writes `107조 4,405억원` needs no `multiplier`. `multiplier` works as on any cell.
- **What DART locators do not cover.** The statement of changes in equity (`SCE`) gives `unsupported` in this version. Notes, segment figures and anything else outside the four statements are not in this API. Neither are preliminary earnings (잠정실적), which are not periodic reports. DART's API has statements from business year 2015, for listed companies and for the larger unlisted ones that report under IFRS.
- A DART locator is a single cell. `where` and `rows` do not work with it (`unsupported`). To add up DART amounts, list them under `items`.

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
- `identifier`: digits glued to ASCII letters or joined to a word by a hyphen, such as `CO2`, `sales_2025`, `COVID-19`; and form names written as digits, a hyphen and one or two capital letters, such as `10-K`, `10-Q`, `8-K`, `20-F`, `10-K/A`. A number before a lower-case word (`10-year`, `3-month`) is still a number. Hangul glued to a number (`3명`, `0.86였다`, `2026-11-19에`) changes nothing: the number is still a number and the date is still a date.
- `ordinal`: `1st`, `2nd`, `제3`, `3번째`

The rules lean toward counting. A 4-digit number with no year context stays a number and shows up as `uncovered` if the plan leaves it out.

## 7. `check`

`receipts.py check PLAN [--out DIR] [--mode default|strict] [--strict] [--edgar-cache DIR] [--dart-cache DIR] [--refresh]` writes `receipts.json` and `receipts.md` into `DIR` (default: the plan's folder) and prints the Markdown.

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
  "labels": [{"id": "c10", "line": 3, "start": 20, "end": 24, "text": "2025"}],
  "truncated": 1,
  "multipliers": [{"file": "sales.csv", "column": "revenue", "multiplier": "1000", "claims": 4}]
}
```

`truncated` is how many of the verified numbers match only by truncation. `multipliers` lists every multiplier the plan declared, with the number of claims that use it. The script cannot check a multiplier: it is the plan's statement about the unit of the source, and it has to come from the source, not from the number in the document. The Markdown receipt shows both.

Reasons for unbound claims: `invalid_claim`, `quote_not_on_line`, `number_not_in_quote`, `no_number_at_position`, `label_not_allowed`.

`counts.verified + wrong + unverifiable + uncovered = counts.numbers`.

Reasons: `match`, `truncated`, `mismatch`, `direction_mismatch`, `declared_no_source`, `locator_no_match`, `locator_multiple`, `cell_not_numeric`, `unsupported`, `division_by_zero`, `direction_conflict`, `duplicate_claim`, `edgar_versions_differ`, `dart_receipt_differs`, `internal_error`.

When a plan uses EDGAR, `receipts.json` also has `"edgar": {"files": [{"file": "companyfacts/CIK0000320193.json", "fetched": "2026-11-20"}]}`, and the Markdown receipt shows the same dates.

When a plan uses DART, `receipts.json` also has `"dart": {"files": [{"file": "fnlttSinglAcntAll/00126380_2026_11014_CFS.json", "fetched": "2026-11-21", "rcept_no": "20261116001482"}]}`, and the Markdown receipt shows the same. `rcept_no` is left out for a file that holds no statements.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | no `wrong`. With `--strict`: also no `unverifiable`, `uncovered` or unbound claim |
| 1 | at least one `wrong` (or a `--strict` violation) |
| 2 | stopped: the document or a source file is missing, unreadable or corrupt, EDGAR or DART could not be read (sections 8 and 9), the plan is not valid, or the script hit an internal error. Nothing is passed. |

A missing sheet, table, column or key inside a readable file is a locator problem (`locator_no_match`), not a stop.

## 8. EDGAR access

The script reads two things from the SEC, with the Python standard library:

- `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`: every fact of one company. One request per company.
- `https://www.sec.gov/files/company_tickers.json`: the ticker list. Fetched only when a locator names a ticker.

**Who is asking.** The SEC asks automated clients to say who they are in the `User-Agent` header, in the form `Name contact@example.com`. The script takes that text from the environment variable `RECEIPTS_SEC_USER_AGENT`. There is no default. If a request is needed and the variable is empty, the script stops with exit code 2 and makes no request. The text is sent only as the `User-Agent` header of the two requests above. A redirect to another host is not followed. The text is not written to the cache or to a receipt.

**Rate.** The SEC allows 10 requests per second. The script makes at most 5.

**Cache.** Responses are stored under `.receipts/edgar/` in the plan's folder, or in the folder given by `--edgar-cache` or by the environment variable `RECEIPTS_EDGAR_CACHE`:

```
<cache>/company_tickers.json
<cache>/companyfacts/CIK0000320193.json
<cache>/fetched.json
```

A cached file is used as it is, however old, and no request is made for it. `--refresh` fetches again. `fetched.json` records the date (UTC) each file was fetched, for example `{"companyfacts/CIK0000320193.json": "2026-11-20"}`. A file with no entry there counts as fetched on its modification date. The date is shown on the receipt, so a reader sees how fresh the EDGAR data was. A filing made after that date is not in the cache: its facts give `locator_no_match` until the cache is refreshed.

**Stops.** A failed request (no connection, an HTTP error, a timeout), a response that is not the expected JSON, or a cached file that is not the expected JSON stops the script with exit code 2. One HTTP error is an answer and not a stop: 404 for a company's facts means the SEC has none under that CIK, which is `locator_no_match`. A request that gets no HTTP answer (no connection, a timeout, a broken body) or gets HTTP 429, 500, 502, 503 or 504 is tried once more after two seconds. A redirect to another host counts as a failed request.

**Other addresses.** `RECEIPTS_EDGAR_DATA_URL` and `RECEIPTS_EDGAR_WWW_URL` replace `https://data.sec.gov` and `https://www.sec.gov`. They exist for tests. The requests, with the contact string, then go to those addresses instead of the SEC's. When either is set, the receipt says which address was used.

### Looking things up

A plan needs the company's own concept names and exact period dates. Three commands print them as JSON, from the same cache:

```
receipts.py edgar company AAPL
receipts.py edgar concepts AAPL --match revenue
receipts.py edgar facts AAPL us-gaap:Assets --since 2025-01-01
```

- `company`: the CIK and the name.
- `concepts`: the concepts the company has reported whose name or label contains the text (all of them without `--match`), each with its label, its units, the number of facts and the latest period end. At most 60 are printed unless `--limit` says otherwise (`0` prints all). `matched` is the full count.
- `facts`: one concept's facts, each with period, value, form, accession and filing date, and `filings_differ: true` when filings give different values for that period. `--unit`, `--since` and `--until` (compared with the period end) narrow the list.

These commands use `.receipts/edgar/` in the current folder unless `--edgar-cache` or `RECEIPTS_EDGAR_CACHE` names another, so run them in the plan's folder to share its cache. They stop with exit code 2 when the company or the concept is not found.

## 9. DART access

The script reads two things from OpenDART (`opendart.fss.or.kr`), the API of Korea's Financial Supervisory Service, with the Python standard library:

- `https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json`: the full financial statements of one report. One request per company, year, report and `fs`.
- `https://opendart.fss.or.kr/api/corpCode.xml`: the company code file, a zip. Fetched only when a locator names a stock code, or for the `company` look-up below.

**The key.** DART gives every user a free key, and its terms forbid letting anyone else use it. The script takes the key from the environment variable `RECEIPTS_DART_KEY`. There is no default and no key is shipped. If a request is needed and the variable is empty, the script stops with exit code 2 and makes no request. The key is sent only as the `crtfc_key` parameter of the two requests above. A redirect to another host is not followed. The key is not written to the cache or to a receipt, and no message of the script shows it or the address of a request.

**Rate.** The script makes at most 5 requests a second. DART allows 20,000 requests a day per key.

**Cache.** Responses are stored under `.receipts/dart/` in the plan's folder, or in the folder given by `--dart-cache` or by the environment variable `RECEIPTS_DART_CACHE`:

```
<cache>/corpCode.zip
<cache>/fnlttSinglAcntAll/00126380_2026_11014_CFS.json
<cache>/fetched.json
```

A statements file is named by company code, year, DART's report code (`11013` for `q1`, `11012` for `half`, `11014` for `q3`, `11011` for `annual`) and `fs`. A cached file is used as it is, however old, and no request is made for it. `--refresh` fetches again. `fetched.json` records the date (UTC) each file was fetched. A file with no entry there counts as fetched on its modification date. The receipt shows that date and the receipt number of each report. DART's answer that it has no such report is cached like any other answer, so a report filed after that date, or an amendment, is not seen until the cache is refreshed.

**DART's answers.** DART puts a status code in each answer. `000` is data. `013` means it has nothing for the request, which is `locator_no_match`. Any other status stops the script with exit code 2, with the code and DART's message: `010` and `011` (a key that is not registered or cannot be used), `012` (an address that is not allowed), `020` (the request limit is exceeded), `100` and `101` (a bad request), `800` (DART is under maintenance), `900`, `901`. A stopped answer is not cached.

**Stops.** A failed request (no connection, an HTTP error, a timeout), a response that is not the expected JSON or zip, or a cached file that is not, stops the script with exit code 2. A request that gets no HTTP answer or gets HTTP 429, 500, 502, 503 or 504 is tried once more after two seconds. A redirect to another host counts as a failed request.

**Other address.** `RECEIPTS_DART_URL` replaces `https://opendart.fss.or.kr`. It exists for tests. The requests, with the key, then go to that address instead of DART's. When it is set, the receipt says which address was used.

### Looking things up

A plan needs the company's code and the rows exactly as DART names them. Two commands print them as JSON, from the same cache:

```
receipts.py dart company 삼성전자
receipts.py dart accounts 005930 --year 2026 --report q3 --fs CFS --statement IS --match 영업
```

- `company`: for a stock code or a company code, that company. For any other text, the companies whose name contains it, the ones with a stock code first. Each is printed with its company code, name and stock code. At most 60 are printed unless `--limit` says otherwise (`0` prints all). `matched` is the full count.
- `accounts`: the rows of one report, each with its statement, `account`, `name` and, for every column that holds a value, the value, the name DART gives that period and what the table in section 2 says the column holds. The output also names the receipt number and the currency. `--statement` and `--match` (text in the account id or the name) narrow the list. `--year`, `--report` and `--fs` are required. At most 60 rows are printed unless `--limit` says otherwise.

These commands use `.receipts/dart/` in the current folder unless `--dart-cache` or `RECEIPTS_DART_CACHE` names another, so run them in the plan's folder to share its cache. They stop with exit code 2 when the company is not found or DART has no such report.
