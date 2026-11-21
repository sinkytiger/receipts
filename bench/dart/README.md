# bench/dart: E4 benchmark (DART connection)

Implements E4 of [`docs/eval-plan.md`](../../docs/eval-plan.md). Standard library only, Python 3.9+.
Written from the plan and the contract ([`plan-format.md`](../../skills/receipts/references/plan-format.md),
sections 1, 2, 4, 7 and 9) without reading the tool. The gold comes from the XBRL instance DART serves for a receipt
number (`fnlttXbrl.xml`). From the statements answer (`fnlttSinglAcntAll.json`, the tool's data path) the builder
takes the list of rows (statement, account id, account name) and the receipt number, and no amount.

## Run

Four steps. Only the first and the third need the key, and only they make requests.

```sh
export RECEIPTS_DART_KEY="<your own OpenDART key>"     # never printed, never written to any file

PYTHONUTF8=1 python bench/dart/build.py --fetch-only    # 1. fetch: 10 development companies -> bench/dart/cache/
PYTHONUTF8=1 python bench/dart/build.py --offline       # 2. build: bench/dart/out/dev/ from the cache, no request
PYTHONUTF8=1 python bench/dart/evaluate.py --fill       # 3. fill: the tool runs once with the key, into its own cache
unset RECEIPTS_DART_KEY
PYTHONUTF8=1 python bench/dart/evaluate.py              # 4. score: from caches, no key, no request
```

Held-out (once, on the release commit). A held-out company, the draw and the held-out set are refused without
`--held-out`, before any request.

```sh
export RECEIPTS_DART_KEY="<your own OpenDART key>"
PYTHONUTF8=1 python bench/dart/build.py --fetch-only --held-out               # 11 named companies and the draw of 20
PYTHONUTF8=1 python bench/dart/build.py --offline --held-out                  # bench/dart/out/heldout/
PYTHONUTF8=1 python bench/dart/evaluate.py --fill --set heldout --held-out    # the tool, once, with the key
unset RECEIPTS_DART_KEY
PYTHONUTF8=1 python bench/dart/evaluate.py --set heldout --held-out           # score
```

The fill step makes the tool fetch one statements answer per set, one answer "no data" per set (year 2031) and the
company code file once: about 117 requests for the development set.

Options. `build.py`: `--companies 00126380,00120030` (development codes only), `--cache DIR` (default
`bench/dart/cache/`), `--out DIR` (default `bench/dart/out/`), `--self-test`. `evaluate.py`: `--set dev|heldout`,
`--held-out`, `--tool PATH` (default `skills/receipts/scripts/receipts.py`), `--tool-cache DIR` (default
`<out>/<set>/tool-cache`), `--out DIR`, `--cache DIR` (the builder's cache), `--results-dir DIR`, `--runs FILE`,
`--timeout SEC` (per tool call, default 900), `--jobs N` (plans checked at the same time while scoring, default 1),
`--self-test`.

Exit codes. `build.py`: 0 done, 1 at least one set could not be built (listed under `failed_sets` in the manifest,
the others are built), 2 stopped. `evaluate.py --fill`: 0 every plan answered, 1 a plan was not answered, 2 cannot
start. `evaluate.py`: 0 all seven bars met, 1 any bar not met, 2 cannot start (no cases, no tool, no fill record, or
a held-out set without `--held-out`).

### The key and the requests

- The key is read from `RECEIPTS_DART_KEY` only. It is sent as the `crtfc_key` parameter and nowhere else. No
  message shows it or the address of a request: a failed request is reported by its endpoint name
  (`fnlttSinglAcntAll.json: HTTP 503`). Unexpected errors are printed without a traceback for the same reason.
- At most 4 requests a second (0.25 s apart; the plan allows 5). 30 s timeout, 180 s for zip files. A request with
  no answer, or HTTP 429 or 5xx, is tried once more after 2 s. A redirect is not followed.
- DART's statuses: `000` and `013` (no data) are answers and are cached; for the XBRL file `014` (no such file) is
  one too. Every other status (`010`, `011`, `012`, `020`, `100`, `101`, `800`, `900`, `901`) stops the fetch and is
  not cached.
- Every answer is cached and never fetched again. A fetch that stopped can simply be started again.
- The development fetch made 100 requests on 2026-11-21: 1 company code file, 60 statements, 29 XBRL files,
  10 filing lists. One more request, with a receipt number that cannot exist, was made while writing the fetcher,
  to see how DART answers "no such file".

## What is written where

`bench/dart/cache/` (git-ignored), the builder's cache:

```
corpCode.zip                                  the company code file (development runs)
corpCode-heldout.zip                          the same, fetched by the held-out run on its day
fnlttSinglAcntAll/<corp>_<year>_<reprt>_<fs>.json   DART's answer as sent, status 000 or 013
fnlttXbrl/<rcept_no>_<reprt>.zip              the XBRL file of a receipt number
fnlttXbrl/<rcept_no>_<reprt>.status.json      DART's answer when it has no such file
list/<corp>_A_20250101_p<page>.json           the company's periodic reports, earlier versions included
fetched.json                                  for each file the UTC date it was fetched (and the list's last day)
```

`bench/dart/out/<dev|heldout>/` (git-ignored):

- `manifest.json`: builder version, fetch dates, per company its stock code and filing list, per set the receipt
  number, the periods read from the instance, the plans, the counts, whether it is amended and the earlier receipt
  number; sets DART has no data for; totals; per company counts; for the held-out set the draw (kept, passed over
  with the reason).
- `sets/<corp>-<report>-<fs>/document.md`: one line per fact, e.g.
  ``- `IS · dart_OperatingIncomeLoss · 영업이익 · thstrm_amount` 값 107조 4,405억원``.
- `sets/.../by-id.json`, `by-name.json`, `pinned.json`: the fact plans, all on `document.md`.
- `sets/.../swap.md`, `swap.json`: the swap cases (the label names the fact and says whose amount is shown).
- `sets/.../abstain-account.json`, `-column`, `-year`, `-fs`, `-sce`, `-receipt`, `-statement`: one plan per kind.
  `abstain-cfq.json`: in interim sets with cash flow facts only (see "The change of 2026-11-21").
- `sets/.../amended.json`: only in amended sets.
- `sets/.../gold.json`: per fact its id, line, statement, account, name, column, period, value, unit, way and
  written form; the swap, abstention and amended cases; counts; facts without a gold and rows left out by reason.
- `sets/.../receipts-<plan>.json`: the tool's last answer to each plan (written by the scorer).
- `tool-cache/`: the tool's own DART cache, filled by the fill step. `tool-cache-fill.json`: the record of the fill
  step (runs, exit codes, the key search). `tool-cache-fill-out/`: the tool's output folders of the fill step.
- `bench/dart/out/selftest/run-*/`: what the self-test wrote. A new folder each time; delete them when you like.

Committed: `bench/dart/results/<set>-<commit>-<UTC>.json` and `.md`, and one line per scoring run in
`bench/dart/runs.jsonl` (UTC time, set, commit, dirty flag, tool path, counts, measures, pass per bar, how the
`wrong` verdicts split, fetch dates, receipt numbers).

Git-ignore lines needed (not added by this folder's author, who may write under `bench/dart/` only):

```
bench/dart/cache/
bench/dart/out/
```

## How the gold is read

- **Instance.** The one `.xbrl` file in the zip. Read with `xml.etree` as a stream; a file with a DOCTYPE or an
  entity declaration is not read. Prefixes are resolved as each file declares them (the taxonomy addresses differ
  between filings); a prefix declared with two addresses is not used.
- **Element.** The account id split at its first underscore: `ifrs-full_Revenue` is `ifrs-full:Revenue`.
- **Context.** Exactly one `xbrldi:explicitMember` and nothing else in its segment or scenario, on the dimension
  `ifrs-full:ConsolidatedAndSeparateFinancialStatementsAxis`, with member `ifrs-full:ConsolidatedMember` (CFS) or
  `ifrs-full:SeparateMember` (OFS). A context with a typed member, a second member or no member is not used.
- **Periods, from the instance itself.** The instance states its periods as facts: `dart-gcd:DocumentPeriodStartDate`
  and `DocumentPeriodEndDate` on the members of `dart-gcd:PeriodAxis` (the report's own period; the last business
  year; the year before last; in interim reports the quarterly or half-year report of the last business year).
  A period is used only if it joins the next one (the previous year ends the day before the report's period
  starts). The three months of an interim report are not stated that way: they are the one period of 80 to 100
  days that ends on the period end and starts after the business year, among the contexts of the set's own member.
  The same holds a year earlier. Context ids (`CFY2026dTQQ` and so on) are not read.
- **Columns.** The contract's table (section 2). Balance sheet columns are single dates, the others are spans. An
  interim cash flow statement has two columns of facts, `thstrm_amount` and `frmtrm_amount`. The contract calls
  `frmtrm_q_amount` "not usable" there, so it is no fact (see "The change of 2026-11-21").
- **A fact has a gold** when the instance holds at least one fact for the element, the member and exactly that
  period, each one a plain number, and all with the same value and unit. Otherwise it is counted under one reason:
  `no_xbrl_file`, `xbrl_not_readable`, `prefix_not_declared`, `period_not_known`, `no_fact` (no fact of the element
  for that member at all), `only_instant_facts` (a span is asked and the element has facts at single dates only),
  `only_duration_facts`, `no_fact_in_period`, `not_a_number`, `values_differ`.

## Choices the plan leaves to the builder and scorer

Builder:

1. **Order.** Companies in the plan's order (named held-out companies, then the drawn ones in draw order), reports
   annual, half, q3, sets CFS then OFS. "The first 5 sets of the run" are the first five in the manifest.
2. **A set** whose answer names more than one receipt number is reported as missing (none occurred). A set with data
   but no fact with a gold is listed under `sets_without_gold` and has no cases.
3. **Eligible rows.** `sj_div` one of BS, IS, CIS, CF; `account_id` starting with `ifrs-full_` or `dart_`; the id
   counted among all rows of that statement in the set. Rows left out are counted as
   `statement_of_changes_in_equity`, `other_statement`, `no_standard_account_id`, `other_id_prefix`,
   `account_id_repeats`.
4. **Balances in a flow statement.** DART tags the opening and closing cash of the cash flow statement as amounts
   at a single date (the end of the period). The plan asks for "the period the contract gives for that column",
   which is a span there, so these rows have no gold (`only_instant_facts`). They are counted and left out.
5. **Facts** are ordered by row as DART lists them, then by column in the order of the contract's table. Fact
   `f0001` is line 1.
6. **Ways of writing**, in turn by line: whole won, `백만원`, whole 억, 조 and 억. `백만원` only when the value is a
   non-zero multiple of 1,000,000; 억 from 1억 up; 조 and 억 from 1조 up; otherwise whole won. Rounding to whole 억 is
   half-up, away from zero. When the 억 part is zero the fourth way reads `3조원`. Zero is `0원`. A value with a
   decimal part keeps it in the first way.
7. **Unit word.** `원` when the instance's unit is KRW or KRW per share. Another currency is written after a space
   (`1,234 USD`), a share count ends in `주`, a pure number has no word. The development set has KRW only.
8. **Line and claim.** `- ` + the label in inline code + ` 값 ` + the number. Everything but the number is inside
   inline code, which `scan` skips, so the line holds one number. `quote` is `값 <number with unit>`, `number` is the
   sign, the digits and the scale words, without the unit word. `kind` is `lookup`. `year` is written as a number.
9. **Company.** The stock code in annual sets when the company code file has exactly one company with that stock
   code, otherwise the company code. The company code in the other sets.
10. **By name.** A row is in the by-name plan when its name, trimmed, is not empty and occurs once among all rows of
    that statement in the set. The plan gives the name as DART writes it.
11. **Swap.** For each fact in document order: kind (a), kind (b), kind (c), where the neighbour has a gold. The
    neighbour's value is written in the way that is next in turn in the swap document. The case is not built when
    that written number matches the fact's own gold under the contract's default tolerance (rounded half-up or
    half-even at the last written digit, or cut off toward zero). Those are counted by kind. Kind (c) needs the
    same statement, account id and column with a gold in the company's other set of the same report.
12. **Abstention.** Each kind is its own plan, so no plan claims a number twice. The last digit of the receipt
    number is raised by one (9 becomes 0). The other statement is the first of BS, IS, CIS, CF that is not the
    fact's own and has no row with that account id in the set (a statement the set does not have counts). The
    eighth kind, `cfq` (added on 2026-11-21, see below), takes the first five cash flow facts of an interim set in
    document order and asks each under the column `frmtrm_q_amount`. Annual sets and interim sets without a cash
    flow fact have no `cfq` case. It counts under measure 4 like the other kinds.
13. **Amended.** The filing list is asked from 2025-01-01 to the day of the fetch (Korean time), every page, with
    earlier versions (`last_reprt_at=N`). Names are compared after removing leading marks in square brackets and
    extra spaces. An earlier filing has an earlier filing date, or the same date and a smaller receipt number. A
    set whose receipt number is not in the list is not treated as amended and the manifest says so.
14. **Draw.** Companies of the company code file with a stock code, without the 10 development and the 11 named
    held-out companies, each code once, in ascending order of the SHA-256 hex digest of `receipts-E4:` + company
    code. A company is kept when the annual report 2025 has statements (CFS is asked first, OFS only if CFS has
    none) and the XBRL answer for that receipt number is a zip with exactly one `.xbrl` file. The held-out run
    fetches its own copy of the company code file (`corpCode-heldout.zip`), so that it is fetched on that day and
    still never twice. The walk is repeated from the cache by the offline build and gives the same companies.
    Without `--held-out` the order is never computed.

Scorer:

15. **Fill.** The tool runs once per set on `by-id.json` (as the plan says) and on `abstain-year.json`, because the
    answer "no data" for year 2031 has to be in the tool's cache before a run without a key. No other plan is
    run with the key. In particular `abstain-fs.json` is not: a tool that passes `fs` to DART unchecked then needs
    a request while scoring and stops. The fill step refuses to run when `RECEIPTS_DART_URL` is set (the key would
    go to another address), except on made-up data.
16. **Key search.** The key as given, in lower case and in upper case, is looked for in what the tool printed
    (stdout and stderr of every run) and in the name and the contents of every file under the tool's cache and
    output folders, zip members included. The record keeps counts and places, never the key. It also says whether
    the fill started with an empty cache and whether the tool file is the one scored later.
17. **Scoring environment.** `RECEIPTS_DART_KEY` and `DART_API_KEY` removed, `RECEIPTS_DART_CACHE` removed,
    `RECEIPTS_DART_URL` set to a local port that was bound and closed. The tool is called as the contract writes
    it: `check PLAN --out DIR --dart-cache DIR`.
18. **A plan the tool does not answer** (crash, timeout, exit 2, no valid `receipts.json`) gives every claim in it
    the verdict `error`. A claim absent from `claims` is `missing`, or `unbound` when listed there.
19. **Measures.** 1 counts claims of the three fact plans together. 2 is met when each of the three plans has at
    least one claim, at least 98% `verified`, and nothing but `unverifiable` besides. 3 is met when every swap case
    whose fact was `verified` in the by-id plan is `wrong` and every other swap case is `unverifiable`. 5 takes a
    fact as met when its claim in `amended.json` is `unverifiable` and its claim in `pinned.json` is `verified`.
    7 adds the places found in the fill run and in the stop cases that set a made-up key.
20. **Stop cases.** The by-id plan of the first five sets, five cases each, each in a fresh cache and output
    folder: (a) no key; the address points at the local stand-in, so that a tool that asks anyway does not reach
    DART, and the requests it sees are reported (without a bar); (b) a cut-off statements file for the set, no
    key, the address at a closed port, plus a copy of the tool cache's `corpCode.zip` when the plan names a stock
    code; (c) a made-up key and a closed port; (d), (e) a made-up key and the stand-in, which answers every request
    with HTTP 200 and status `020` or `800`, in JSON for `.json` endpoints and in XML otherwise. The made-up key is
    `madeup` + 34 hex characters derived from the case's name. Whether a stopped answer was left in the cache is
    reported without a bar.
21. **`wrong` verdicts** on fact claims are split after scoring by comparing the value the tool reports
    (`recomputed_raw`) with the cell of the cached statements answer (the tool's cache, else the builder's): the
    tool returned the value the API holds, another value, or not known. The results also say in how many of them the
    API cell differs from the XBRL value.

## What the development companies showed (2026-11-21)

- 58 of 60 sets have data. 동원모빌리티 (business year to March) has no third-quarter report 2026 yet, CFS or OFS.
  Its annual report "2025" is the year to March 2025 and its half-year report "2026" is April to September 2026:
  whatever DART returns under the registered year is used, and the dates come from the instance.
- 17,333 possible facts in 6,409 eligible rows: 17,018 with a gold, 315 without (`only_instant_facts` 272, the
  opening and closing cash rows; `no_fact_in_period` 43, rows a company shows in one period only).
- Cases: by id 17,018, by name 16,072, pinned 17,018, swap 32,237 (a 4,538, b 14,397, c 13,302; not built because
  the neighbour as written passes for the fact: a 56, b 425, c 1,156), abstention 2,220 (290 of each of seven
  kinds, 190 of kind `cfq`), amended 40. Ways of writing: whole won 8,463, `백만원` 3,019, 억 3,923, 조 and 억 1,613.
- Amended sets: the annual report 2025 of GS건설, 자이에스앤디, 삼성생명 and KB금융, CFS and OFS, 8 sets and 40 cases.
  케이티's third-quarter report carries the mark `[첨부추가]` on its only version and is not amended.

### The change of 2026-11-21: `frmtrm_q_amount` in an interim cash flow statement

As registered, the contract's table said that in an interim cash flow statement `frmtrm_q_amount` holds "the same
period a year earlier", like `frmtrm_amount`, and the builder (version `e4-builder-1`) made a fact of it. Built that
way the development set had 18,349 facts with a gold. A comparison with the cached statements answers, made while
building and not part of the builder, showed 22 of them in which the API's cell differs from the XBRL value: all
22 are `frmtrm_q_amount` of an interim cash flow statement (18 rows of the profit of the period and 4 rows of a
bank's credit loss charge, in the half-year and third-quarter sets of 삼성전자, 현대자동차, 삼성생명 and KB금융 and the
half-year sets of 동원모빌리티). For a row whose element also has a three-month amount DART fills that cell with
the same three months a year earlier, and with the cumulative period in every other row. A tool that returns the
API's cell would have been judged `wrong` on them, so measure 1 could not be met as registered.

The contract was changed for that reason (section 2: "not usable", a locator that names the column there gives
`unsupported`; the cumulative comparison is `frmtrm_amount`). The builder (version `e4-builder-2`) follows it:

- `frmtrm_q_amount` of an interim cash flow statement is no fact any more, in the fact plans or as a swap
  neighbour. Interim cash flow facts use `thstrm_amount` and `frmtrm_amount`.
- One abstention kind was added, `cfq` (choice 12 above), expected `unverifiable`, counted under measure 4.
- Rebuilt, the development set has 17,018 facts with a gold. In the same comparison every one of the 17,018 API
  cells equals the XBRL value.

The dated entry in the plan's change log is written by the plan's author, not by this folder's.

## Self-test (offline, made-up data)

```sh
PYTHONUTF8=1 python bench/dart/build.py --self-test       # the builder alone
PYTHONUTF8=1 python bench/dart/evaluate.py --self-test    # builder, fetch from a local made-up DART, scorer
```

`testdata/fake_dart.py` is a made-up DART: fictional companies (가상전자, codes `9900xxxx` and `Zxxxxx`), receipt
numbers of the year 2099, invented amounts, a made-up key, served on 127.0.0.1 in DART's formats. It has a company
with a business year to March, a company with separate statements only, an amended report, rows without a standard
id, a repeated id, a repeated name, a balance in the cash flow statement, a row tagged twice with different values,
an amount the API leaves empty, facts on a breakdown axis, and candidates for the draw (no statements, no XBRL file,
separate statements only). `testdata/stub_tool.py` stands in for the tool; it was written from the contract, talks
only to 127.0.0.1 and has one switch per fault. `testdata/run_selftest.py` asserts that the stub meets all seven
bars and that each broken stub fails exactly these:

| stub | fault | bars it fails |
|---|---|---|
| `stub_column_ignored.py` | every claim reads `thstrm_amount` | 1, 2, 3, 4, 5 |
| `stub_fs_unchecked.py` | `fs` passed to DART as written | 4 |
| `stub_receipt_ignored.py` | `rcept_no` not looked at | 4, 5 |
| `stub_status_as_no_data.py` | every status other than `000` is "no data" | 6 |
| `stub_name_substring.py` | a name matches as a substring | 2 |
| `stub_statement_ignored.py` | the statement is not looked at | 2, 4 |
| `stub_cfq_allowed.py` | `frmtrm_q_amount` of an interim cash flow statement read like any column | 4 |
| `stub_key_leak.py` | prints the address of each request | 7 |
| `stub_key_in_cache.py` | writes the address of each request into its cache | 7 |
| `stub_always_verified.py` | every claim is `verified` | 3, 4, 5 |
| `stub_no_stop.py` | never exits with code 2 | 6 |

The made-up API fills `frmtrm_q_amount` of an interim cash flow statement the way DART does (three months in the
profit row, the cumulative period elsewhere), so `stub_cfq_allowed.py` is caught by the `cfq` cases alone.

It also scores the held-out path on made-up companies (refusals, the draw, a run with no amended report) and a
made-up API that disagrees with its XBRL file in one cell of every set (the operating income of the current period
is twice as large in the statements answer): bars 1, 2 and 3 fail and every `wrong` verdict is traced to the API,
none to the tool. A self-test run is not an E4 result and is not logged in `bench/dart/runs.jsonl`.
