---
name: receipts
description: Check every number in a report, summary, analysis or answer against its source files before calling it checked. Use when the user asks to verify, fact-check, review or double-check numbers ("are these numbers right?", "check this against the data", "숫자 확인해줘", "검증해줘", "원자료와 대조해줘"), and before you deliver a document whose numbers come from CSV, Excel, JSON, Markdown tables or text sources, or from what a US-listed company reported to the SEC (10-K, 10-Q on EDGAR) or a Korean company filed with DART (사업보고서, 반기·분기보고서). Gives each number a receipt - verified, wrong (with the recomputed value), unverifiable or uncovered - and stops you from saying "all checked" when some numbers were not.
---

# receipts

You say where each number comes from. The script reads the sources, recomputes, and decides. You never decide a verdict yourself, and you never type a source value into the plan.

The script is `scripts/receipts.py` in this skill's folder. It needs only Python (use `python3` if `python` is not found). Call it with its full path.

## Steps

1. **Get the document into a text file.** The script reads `.md` or `.txt`. If the numbers are in a chat answer, a Word file or a PDF, save the text to a file first. Line numbers refer to that file.
2. **Scan.** `python <skill>/scripts/receipts.py scan report.md` prints every number with its line. This list is the denominator. It also shows what was skipped (dates, years, versions, list markers) and why.
3. **Find the sources and their units.** Use the files the user gave you or named. Do not guess a source and do not look one up on the web. There are two outside sources: SEC EDGAR, for figures a US-listed company reported in its filings, and Korea's DART, for the financial statements in a Korean company's periodic reports (see "SEC filings" and "Korean filings" below). A number with no source is `no_source`. Note what each source says about its own units: a column name such as `usd_thousands`, a note field, a header line, or what the user told you.
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
| `share` | `part`, `whole` (one cell, or a many-cells selector, which is summed) | part / whole |
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

If the header of an Excel sheet is not on row 1, because a title or a unit note sits above it, add `"header_row": 2` (the row the header is on) to every locator on that sheet. `row`, `where` and `rows` then work as usual, so there is no need to fall back to `cell` addresses.

### SEC filings (EDGAR)

A cell can be a fact a US-listed company reported to the SEC. It works wherever one cell is expected, inside formulas too.

```json
{"edgar": "AAPL", "concept": "us-gaap:Assets", "unit": "USD", "period": "2026-09-26"}
{"edgar": "AAPL", "concept": "us-gaap:NetIncomeLoss", "unit": "USD", "period": "2025-09-28..2026-09-26"}
```

- **Who is asking.** The SEC wants automated clients to identify themselves. The script reads the text from the environment variable `RECEIPTS_SEC_USER_AGENT` (a name and an e-mail address) and stops with exit code 2 when it is empty and something has to be fetched. A company that is already in the cache needs no request. Ask the user to set it. Never make one up and never put your own or anyone else's address there.
- **Find the names and dates first.** Run these in the folder of `claims.json`, so they share its cache. Each company is fetched once.
  - `receipts.py edgar concepts AAPL --match revenue` lists the company's concepts with their labels. Companies use different concepts for the same idea (revenue is not always `us-gaap:Revenues`).
  - `receipts.py edgar facts AAPL us-gaap:Assets --since 2025-01-01` lists the periods, values and filings of one concept.
- **Pick the concept by its meaning**, from its label and what the sentence talks about. Never pick a concept, period or filing because its value matches the document.
- **Periods are exact.** A balance is one date (`2026-09-26`). A flow is `start..end`. A quarter and the year to date end on the same day and differ only in the start, so read the sentence: "in the third quarter" is three months, "in the first nine months" is not. A period of one day, such as a dividend paid on one date, is written as that date.
- **Restated figures.** If filings give different values for the same period, the verdict is `unverifiable` (`edgar_versions_differ`) and the receipt lists them. Then follow the document: if it cites a filing, add `"accession": "0000320193-26-000026"`; if it says "as originally reported" use `"version": "first"`; if it means the current figure use `"version": "latest"`. If the document does not say, leave it and report it.
- Money is in whole dollars and share counts in whole shares, so those need no `multiplier`. A ratio (unit `pure`, a tax rate for example) is stored as a fraction, 0.17 for 17%: add `"multiplier": 100` when the document writes it as a percent.
- Segment, product and regional numbers, and concepts a company defined itself, are not in this data: check them against a file, or mark them `no_source`.
- A filing made after the fetch date on the receipt is not in the cache. Add `--refresh` to `check` to fetch again.

### Korean filings (DART)

A cell can be an amount in the financial statements of a periodic report a Korean company filed with DART (사업보고서, 반기보고서, 분기보고서). It works wherever one cell is expected, inside formulas too.

```json
{"dart": "005930", "year": 2026, "report": "q3", "fs": "CFS", "statement": "IS",
 "account": "dart_OperatingIncomeLoss", "column": "thstrm_amount"}
```

- **The key.** DART gives every user a free key (opendart.fss.or.kr). The script reads it from the environment variable `RECEIPTS_DART_KEY` and stops with exit code 2 when it is empty and something has to be fetched. A report that is already in the cache needs no key. Ask the user to set the variable. Never ask them to paste the key into the chat, and never print it or put it in a file or a command.
- **Find the company and the rows first.** Run these in the folder of `claims.json`, so they share its cache.
  - `receipts.py dart company 삼성전자` gives the stock code and the company code. `dart` takes either, as a string. A name is not accepted.
  - `receipts.py dart accounts 005930 --year 2026 --report q3 --fs CFS --statement IS --match 영업` lists the rows of one report: `account`, `name`, and for each column its value, the name DART gives the period and what the column holds.
- **`report`** is `q1`, `half`, `q3` or `annual`. **`year`** is the business year as DART files it. For a company whose year does not end in December, look at the period names in the listing to see which year holds the period.
- **`fs`** is `CFS` (연결) or `OFS` (별도). There is no default. "연결 기준" is `CFS`, "별도 기준" is `OFS`. If the document does not say, use `CFS` when the company files consolidated statements, and say in the report that you assumed it.
- **`statement`** is `BS`, `IS`, `CIS` or `CF`. The same account can stand in more than one statement.
- **Pick the column by what the sentence says, never by its value.** In a half-year or third-quarter report, `thstrm_amount` of an income statement is the last three months alone. "3분기 누적", "1~9월", "상반기" are `thstrm_add_amount`. "전년 동기" is `frmtrm_q_amount` for three months and `frmtrm_add_amount` for the cumulative period. On an interim balance sheet, `frmtrm_amount` is the end of the previous business year, not the same date a year earlier. In an annual report, `frmtrm_amount` is the previous year and `bfefrmtrm_amount` the year before it. No report holds a fourth quarter alone: it is the annual `thstrm_amount` minus the third-quarter `thstrm_add_amount` (`difference`).
- **Pick the row by its meaning.** Prefer `account`, the id. A row listed with `"account": null` has no standard code: name it with `name`, copied exactly. If two rows match, add `name` to `account`.
- **Amended reports.** DART's API holds only the latest version of a report, and the receipt shows its receipt number. If the document cites a filing (a receipt number, or a DART link with `rcpNo=`), add `"rcept_no"`. The verdict is then `unverifiable` (`dart_receipt_differs`) when that filing has been replaced, instead of a comparison with other figures.
- Amounts are in whole won, so `107조 4,405억원` needs no `multiplier`. Per-share amounts are in won.
- The statement of changes in equity, notes, segment figures, preliminary earnings (잠정실적) and years before 2015 are not in this data: check them against a file, or mark them `no_source`.
- A report filed or amended after the fetch date on the receipt is not in the cache. Add `--refresh` to `check` to fetch again.

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
- every multiplier you declared and where it came from. Mark the ones the source does not state as assumptions;
- if EDGAR was used: the fetch date the receipt shows, and every `accession` or `version` you chose and why;
- if DART was used: the fetch date and the receipt numbers the receipt shows, and every `fs` you assumed because the document did not say.

What the check does not cover, in one or two lines: numbers written as words ("five years", "두 자릿수"), the skipped tokens the scan listed (dates, years, code), claims that are not numbers, and whether the sources themselves are right.

Wording:

- Say "no errors found in the 33 numbers that were checked". Do not say "all numbers are correct" or "everything checks out" unless `wrong`, `unverifiable` and `uncovered` are all zero. Even then, say what the check covers: numbers against the given source files, not whether the sources are right and not the claims that are not numbers.
- Do not call a number verified unless the script did.
- Do not correct the document unless the user asked for corrections. If they did, change the wrong numbers to the recomputed values and run the check again.
