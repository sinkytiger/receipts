# Changelog

## 0.2.1 (2026-11-23)

- **Korean month and quarter ranges.** The scan now skips `7~9월`, `1-9월`, `10∼12월` and `1~3분기` as dates. Before, it skipped only the part with `월` or `분기`, and the first number of the range (`7` in `7~9월`) counted as a number of the document, so it came back `uncovered` unless the plan declared it. Found while writing E5, the evaluation in which a model writes the plan against DART. Ranges of amounts (`7~9%`, `3-5개`) are still two numbers.
- E5 was run on 0.2.0 and is reported in the README. Its harness, records and results are in `bench/e5`.

## 0.2.0 (2026-11-22)

- **Korean filings (DART).** A source can be an amount in the financial statements of a periodic report filed with Korea's DART (사업보고서, 반기보고서, 분기보고서), read from OpenDART. A locator names the company (stock code or DART company code), the business year, the report (`q1`, `half`, `q3`, `annual`), consolidated or separate (`fs`, with no default), the statement (`BS`, `IS`, `CIS`, `CF`), the row (by account id or by name) and DART's own column name. The rules are in sections 2 and 9 of `plan-format.md`.
  - The key comes from `RECEIPTS_DART_KEY` only. It is sent only to DART (or to the address in `RECEIPTS_DART_URL`, a variable that exists for tests), and it is not written to the cache, to a receipt or to any message. No message shows the address of a request.
  - In a half-year or quarterly report the three months and the year to date are two columns of the same row. The receipt and the look-up print what each column holds.
  - DART's API serves only the latest version of a report. A plan may name the receipt number (`rcept_no`). If DART serves another, the verdict is `unverifiable` (`dart_receipt_differs`).
  - An answer is used only if its rows name the company, year and report that were asked for. A field the script does not know is refused, so a misspelled `rcept_no` cannot drop out unnoticed.
  - Not covered: the statement of changes in equity, notes, segment figures, preliminary earnings, years before 2015, and `frmtrm_q_amount` in an interim cash flow statement (DART fills that column with two different periods).
  - New commands: `receipts.py dart company`, `receipts.py dart accounts`. The second prints no amounts.
  - `examples/dart`: a Korean note on Samsung Electronics' third quarter of 2026, checked without a network connection and without a key.
- **All sources.** When `check` stops, a `receipts.json` or `receipts.md` left by an earlier run in the output folder is replaced by a note that the run stopped. A `multiplier` has to be a finite number (`NaN` used to give `wrong`). A redirect from https to http is not followed (EDGAR and DART). A fetch date in `fetched.json` has to be a real date.
  - A locator with a field the script does not know is refused, for EDGAR as for DART: a misspelled `accession` used to be skipped. A `file` locator into the script's own cache folder (`.receipts`) is refused.

How 0.2.0 was checked. E4, the evaluation of the DART connection, was registered before the connection was written. The code was frozen at commit `2ae4742`. On the held-out set (31 companies, 177 sets of statements, 44,829 amounts) it meets all 8 bars: no amount judged `wrong` in 132,384 verdicts, 76,873 of 76,873 neighbouring-column cases judged `wrong`, 6,785 of 6,785 and 599 of 599 abstentions, 95 of 95 amended-report cases, 25 of 25 stops, and the key nowhere in the output. The scoring was started three times. The first two were cut short (the computer was put to sleep; the desktop app the scoring was started from was updated) and are logged beside the third. A rule written down before the run says that scoring cut for a reason outside the tool is run again. An independent reviewer tried to break the connection twice before the freeze. On the development sets 0.2.0 meets all 8 bars of E1 and all 6 bars of E3. Their held-out sets were used once, on 0.1.0, and are not run again. The unit tests pass (117).

## 0.1.1 (2026-11-20)

- **EDGAR: a period of one day is the single date.** `2026-09-22..2026-09-22` and `2026-09-22` now name the same fact. In 0.1.0 the range form found nothing, because the SEC's API stores a one-day period under a single date. The two facts that 0.1.0 could not verify in the held-out E3 run were of this kind. Two things follow from the rule: a balance asked as a one-day range now matches too, and a single date also matches a fact stored with equal start and end dates.
- **E2 scorer.** It also reports how many statements the lone reviewer flagged when any flag on the statement's line counts. The count under the rule fixed before the run is unchanged and still reported.
- **E3 builder.** One-day periods are left out of the abstention cases that ask for a duration as one date. The SEC's API stores such a fact under that date, so the tool finds it, as 0.1.0 already did: it was never a valid abstention case. No such case had been built, in the development set or the held-out set.
- **E2 records.** Records exported by the harness now name the accepted plan's file as it is stored in the records folder.
- **README.** The token estimate, and what changed since 0.1.0.

How 0.1.1 was checked. The held-out sets were used once, on 0.1.0, and are not run again. On the development sets 0.1.1 meets all 8 bars of E1 and all 6 bars of E3 (the run logs name commit `f5f63d1` for the E1 run and `bc8e36d` for the E3 run). The two one-day facts from the held-out E3 run were tried again on their own, outside the scored runs, and both now verify. The unit tests pass (52).

The held-out results in the README were measured on 0.1.0, with the code frozen at commit `a84647c`.

## 0.1.0 (2026-11-20)

First release: the checking script, the skill, the SEC EDGAR connection, and the held-out results of E1, E2 and E3.
