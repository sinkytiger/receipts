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

### E2 details, fixed on 2026-11-20 before any E2 call

The lines above are the registration of 2026-11-19. They left the following open.

- **Documents.** The first 15 English and the first 15 Korean documents of the held-out seed, in the order of the generator's manifest.
- **Request settings, both arms.** `claude-haiku-5-5` through the Anthropic SDK, effort `medium` (the model's default), thinking left at the model's default, `max_tokens` 16,000, no sampling parameters, no prompt caching.
- **What the model sees, both arms.** The document with line numbers, and every source file as text. An Excel file is shown sheet by sheet as tab-separated rows.
- **Arm A.** The system prompt is the file `skills/receipts/SKILL.md` as shipped, front matter included, plus a short note that the harness runs the script. The first message also holds the output of `scan`. The model replies with `claims.json`. The harness runs `check` and returns the printed receipt. The model may send a corrected plan up to two times, as step 6 of the skill allows, or reply `DONE`. The last plan that `check` accepted counts. If none was accepted, every number of that document counts as not checked.
- **Arm B.** One request. The system prompt asks for a careful review: look up or recompute every number, allow for normal rounding and for a value cut off at the written precision (the script accepts both by default), check direction words, and list what cannot be checked. The reply is a JSON list of wrong numbers with their line numbers. No script and no scan output.
- **Scoring.** The generator's gold file says which statements carry a planted error. Arm A: a statement is flagged when its number's verdict is `wrong`. Arm B: a statement is flagged when the reply lists a wrong number on the statement's line whose text contains the statement's number (changed after the rehearsal, see the change log). Reported for each arm: planted errors flagged, correct statements flagged, each with a Wilson 95% interval. Also reported: for arm A the verdicts of planted and correct statements (`wrong`, `verified`, `unverifiable`, `uncovered`), the share of numbers left `uncovered`, the number of corrected plans and of documents with no accepted plan; for arm B the flags that match no statement; for both the requests, tokens and cost per document. Counts are also split by language, by source type, by whether the sources state their unit, and, for correct statements, by whether the number is written rounded or cut off.
- **Asymmetry, stated up front.** Arm A may use up to three requests per document and arm B one. The cost per document shows the difference.
- **Rehearsal.** Before the held-out run the harness is tried on four development-seed documents, two English and two Korean, inside the same US$5 cap. Its purpose is to find harness errors. Prompts may be corrected after it, and the prompts used for the held-out run are committed before that run. No rehearsal number is reported as an E2 result.
- **Records.** For every document and arm the conversation (prompts, replies, feedback), every plan with the script's result, and the review reply are copied to `bench/e2/records/` and committed. Thinking data is not kept.
- **Spending.** Every request is written to `bench/e2/spend.jsonl` with its token counts and cost. The harness adds up that file before each request and refuses to go past the cap. The estimate that was approved, and the rule for the first five documents, are stored with the run.
- **Order of the held-out runs.** The tool, the skill and the version numbers are frozen in one commit. On that commit: E1's held-out seed once, E3's held-out companies once, then E2. Commits after it and before the release tag change only results, logs and documents. If a held-out bar fails, the README says so.

## E3: SEC EDGAR connection (free)

Registered on 2026-11-20, before the connection, its builder or its scorer was written. What had been looked at by then: the SEC's two pages on its APIs and on fair access, and, in a browser, Apple's responses (one concept, the company facts, the filing list), one Apple 10-Q document, and the SEC's ticker list.

No model is involved. E3 asks three things. Given a correct locator (company, concept, unit, period), does the connection return the value the company printed in its filing? Does it abstain when it should? Does it stop when it cannot read EDGAR?

### Two data paths

- **The tool** reads `data.sec.gov/api/xbrl/companyfacts`.
- **The gold** is read from the filing's primary document (inline XBRL) under `www.sec.gov/Archives`. For each tagged number, the gold is the text displayed in the document, times the scale the tag declares, negated when the tag carries a minus sign.
- The builder that reads the documents and the scorer are written by a separate agent from this plan and the contract, without reading the tool. The builder does not read the company facts.

Both paths start from the same submission by the company. E3 tests the connection: company lookup, concept, unit, period and duration, periods reported more than once, and scale. It does not test whether a company tagged its filing correctly.

### Companies

- Development, used as often as needed while building: `AAPL`, `MSFT`, `JPM`, `WMT`, `XOM`.
- Held-out, run once: `JNJ`, `KO`, `NVDA`, `COST`, `PG`, `HD`, `BAC`, `UNH`, `PFE`, `CVX`, `CSCO`, `ORCL`, `NKE`, `DIS`, `MCD`, `CAT`, `T`, `AMZN`, `GS`, `BA`.

The lists were fixed for a spread of industries and fiscal year ends. For the 24 companies other than Apple, only the ticker list was read before registration, to confirm each ticker exists. Until the held-out run, no filing or API response of a held-out company is fetched. The builder refuses a held-out ticker without `--held-out`.

### Filings and facts

- Per company: the most recent `10-K` and the most recent `10-Q` in the SEC's filing list on the day of the run that are inline XBRL. Amendments are not used. The results file records the accession numbers and the fetch date.
- Eligible facts in a filing's primary document: numeric tags (`ix:nonFraction`) whose concept is in the `us-gaap` taxonomy, whose context has no dimension, whose unit is USD, shares or USD per share, that are not nil, and whose displayed text is a plain number (digits, commas, a decimal point) or a dash that the tag declares as zero. A fact is identified by concept, unit and period. A period is one date, or a start date and an end date.
- A fact displayed more than once is taken once. Its gold is the display with the finest last digit after scale. Facts whose displays differ are counted and reported separately.
- Sample per filing, drawn with a random generator seeded with the accession number: 25 eligible facts, plus up to 5 same-end pairs. A same-end pair is two facts with the same concept, unit and end date and different start dates, for example three months and nine months. Both members are taken. If a filing has fewer eligible facts or pairs, all are taken.
- Expected size: up to 35 facts per filing, so up to 350 for the development companies and up to 1,400 for the held-out ones. The builder prints the exact counts.

### Cases

For each filing the builder writes one test document, with one line per sampled fact that shows the number as displayed (with `thousand`, `million` or `billion` for scale 3, 6 or 9, and a leading minus when the tag says negative), and these plans:

- **Pinned**: each claim is a `lookup` on an EDGAR locator with company, concept, unit, period and the accession number of the filing. The company is given as a ticker in the 10-K plans and as a CIK in the 10-Q plans.
- **Open**: the same without the accession number.
- **Abstention**, from the first sampled facts of each filing, each expected `unverifiable`: 5 with a concept name that does not exist; 5 with the period end moved three days later; 5 duration facts asked as one date (the end date); 5 with USD and shares swapped.

Stop cases, each run on the first 5 pinned plans of the run and each expected to exit with code 2: an empty cache and no contact string set; a cache that holds a corrupt company facts file for the company; an empty cache and the EDGAR address replaced by a closed local port.

### Measures and pass bars

| # | Measure | Bar |
|---|---|---|
| 1 | Sampled facts judged `wrong`, pinned or open. The document holds the printed number, so `wrong` means the connection returned another value. | 0 |
| 2 | Pinned: judged `verified` | at least 98%. The rest must be `unverifiable`. |
| 3 | Open: judged `verified`, or `unverifiable` because filings report different values for that fact. The scorer confirms each such case in the cached company facts. | at least 98%. The rest must be `unverifiable`. |
| 4 | Abstention cases judged `unverifiable` | 100% |
| 5 | Stop cases that exit with code 2 | 100% |
| 6 | Added 2026-11-20, see the change log. Facts for which the company facts hold different values in different filings: the open claim is `unverifiable` because filings differ, and the pinned claim is `verified`. | 100% |

A crash, a missing claim or any other verdict counts as a failure of the measure it belongs to.

Reported without a bar: the share of open claims that abstain because filings disagree; same-end pairs with both members `verified`; results by company, form, unit, scale and sign; facts whose displays differ; eligible facts and skipped tags by reason.

### Procedure

- Development companies may be run as often as needed. Every scorer run is logged with the commit hash in `bench/edgar/runs.jsonl`.
- The held-out companies are run once, on the commit that is to be released, together with E1's held-out seed. If a held-out bar fails, the README says so.
- EDGAR is live. A run on a later day may sample newer filings, so results name their filings.
- Fair access. The SEC asks automated clients to identify themselves and allows 10 requests per second. The person who runs E3 supplies the contact string through an environment variable. None is shipped in the repository or written into results. The builder and the tool make at most 5 requests per second, cache what they fetch and do not fetch it twice. A run needs about four requests per company.

## E4: DART connection (free)

Registered on 2026-11-21, before the connection, its builder or its scorer was written. What had been looked at by then:

- OpenDART's guide pages for three of its APIs (the full financial statements of one company, the XBRL file of a report, the company code file) and its terms of use.
- Live responses for four companies. 삼성전자: the statements of the annual report 2025, the half-year report 2026 and the third-quarter report 2026, the XBRL files of the annual and the third-quarter report, the company record, the filing list, and requests with an unknown company code, with a statement type that does not exist, and with years that have no report. GS건설: the statements of the annual report 2023 and the filing list. 자이에스앤디: the statements of the annual report 2025 and the filing list. 동원모빌리티: the annual statements under the years 2025 and 2026.
- The company code file, and one page of the list of annual reports filed from April to June 2026, which shows company names and receipt numbers only.
- An earlier project by the same author (finground-lite-kr) read DART statements of 16 large companies in October 2026. All 16 are named below: five in the development list and the eleven named held-out companies. Nothing of this connection existed then.

No model is involved. E4 asks five things. Given a correct locator (company, year, report, consolidated or separate, statement, account, column), does the connection return the value in the XBRL file the company filed? When the document holds the number of a neighbouring column, does the check say `wrong`? Does it abstain when it should? Does it stop when it cannot read DART? Does the key stay out of everything the tool prints or writes?

### Two data paths

- **The tool** reads `opendart.fss.or.kr/api/fnlttSinglAcntAll.json`.
- **The gold** is read from the XBRL instance that DART serves for the same receipt number (`fnlttXbrl.xml`). For a row and a column, the gold is the fact whose element is the row's account id, whose context has exactly one dimension member (the consolidated one or the separate one), and whose period is the period the contract gives for that column (section 2, "What each column holds"). The dates of the periods are taken from the instance itself.
- From the API response the builder takes only the list of rows (statement, account id, account name) and the receipt number. It reads no amount from it. The scorer may read the cached API response after scoring, only to say whether a `wrong` verdict came from the tool or from a difference between the API and the XBRL file.
- The builder and the scorer are written by a separate agent from this plan and the contract, without reading the tool.

Both paths start from the same filing. E4 tests the connection: company lookup, report, consolidated or separate, statement, account by id and by name, column, sign, the receipt number, and the reading of amounts written the Korean way. It does not test whether a company tagged its statements correctly. It does not cover the statement of changes in equity, which the tool refuses in this version, or rows without a standard account id, whose gold cannot be found in the XBRL file by id.

### Companies

- Development, used as often as needed while building (10): 삼성전자 `00126380`, GS건설 `00120030`, 자이에스앤디 `00367844`, 동원모빌리티 `00118008`, 현대자동차 `00164742`, SK하이닉스 `00164779`, NAVER `00266961`, 케이티 `00190321`, 삼성생명 `00126256`, KB금융 `00688996`.
- Held-out, named (11): 기아 `00106641`, 현대모비스 `00164788`, POSCO홀딩스 `00155319`, LG화학 `00356361`, 삼성SDI `00126362`, 삼성바이오로직스 `00877059`, SK텔레콤 `00159023`, 삼성물산 `00149655`, LG에너지솔루션 `01515323`, 카카오 `00258801`, LG전자 `00401731`.
- Held-out, drawn (20): on the day of the held-out run the company code file is fetched. The companies in it that have a stock code and are not named above are put in the order of the SHA-256 digest (hexadecimal, ascending) of the text `receipts-E4:` followed by the company code. Walking that order, a company is kept if DART returns statements for its annual report 2025 (consolidated or separate) and the XBRL file of that receipt number. The walk stops at 20 kept companies. Companies passed over are listed with the reason.

The development list has the four companies whose responses were read before registration, two financial companies, a company whose business year ends in March, and two companies with amended reports. The eleven named held-out companies are large ones. The drawn ones can be of any size and no one chooses them. Until the held-out run, no statement, XBRL file or filing list of a held-out company is fetched, and the order of the draw is not computed. The builder refuses a held-out company and the draw without `--held-out`.

### Reports and facts

- Per company: the annual report of year 2025, the half-year report of year 2026 and the third-quarter report of year 2026, each consolidated and separate. That is up to six sets of statements. A set DART has no data for is reported as missing and skipped. The results record the receipt numbers and the fetch date.
- Eligible rows: the rows of the balance sheet, the income statement, the statement of comprehensive income and the cash flow statement whose account id starts with `ifrs-full_` or `dart_` and occurs once in that statement of that set.
- Columns: the ones the contract's table gives for the report type and the statement.
- A fact is a set, a statement, an account id and a column. It has a gold when the instance holds at least one fact for that element, member and period, and all of them carry the same value. A fact without a gold is counted by reason and left out.
- Every fact with a gold is used. There is no sampling. The builder prints the counts.

### Cases

For each set the builder writes test documents with one line per fact. A line shows a value in one of four ways, taken in turn: whole won with commas (`107,440,482,000,000원`); in `백만원` when that is exact; rounded to whole 억 (`1,074,405억원`); as 조 and 억, rounded to whole 억 (`107조 4,405억원`). The third way is used only from 1억 up and the fourth only from 1조 up; smaller values are written the first way. A negative value has a leading minus sign. The plans:

- **By id**: each claim is a `lookup` on a DART locator with the company, `year`, `report`, `fs`, `statement`, `account` and `column`. The company is given as a stock code in the annual sets and as a company code in the others. The document shows the gold.
- **By name**: the same with `name` in place of `account`, for rows whose name occurs once in that statement of the set.
- **Pinned**: by id, plus the receipt number of the set.
- **Swap**, each expected `wrong`: the locator names the fact and the document shows the gold of a neighbour. Neighbours are (a) three months and cumulative in the interim income statements: `thstrm_amount` with `thstrm_add_amount`, and `frmtrm_q_amount` with `frmtrm_add_amount`; (b) this period and the comparison period: `thstrm_amount` with `frmtrm_amount` in annual sets, interim balance sheets and interim cash flow statements, `thstrm_amount` with `frmtrm_q_amount` and `thstrm_add_amount` with `frmtrm_add_amount` in interim income statements; (c) consolidated and separate: the same statement, account id and column in the company's other set of the same report. A swap case is built for each fact and each kind of neighbour it has, when the neighbour's value as written does not match the fact's gold under the default tolerance.
- **Abstention**, from the first five facts of each set, each expected `unverifiable`: the account id with `Zz` appended; a column the report type does not have (`frmtrm_q_amount` in annual sets, `bfefrmtrm_amount` in the others); `year` 2031; `fs` given as `XXX`; `statement` given as `SCE`; the set's receipt number with its last digit changed; the statement replaced by another of the four that does not hold that account id in the set.
- **Amended reports**: a set is amended when the company's filing list shows an earlier filing with the same report name once a leading mark in square brackets, such as `[기재정정]`, is removed. For each amended set, its first five facts are pinned to the earliest such receipt number. Each is expected `unverifiable`. The same facts pinned to the receipt number DART serves are in the pinned plan.

Stop cases, each run on the by-id plans of the first 5 sets of the run and each expected to exit with code 2: an empty cache and no key set; a cache that holds a corrupt statements file for the set; an empty cache, a made-up key and the DART address replaced by a closed local port; an empty cache, a made-up key and the address replaced by a local stand-in that answers every request with DART's status `020` (request limit exceeded); the same with status `800` (maintenance).

The key. Scoring runs from caches, without a key. The tool's cache is filled by running the tool once on the by-id plans with the real key set. That run, and every stop case that sets a made-up key, is searched for the key it was given: in what the tool printed, and in every file under its cache and output folders.

### Measures and pass bars

| # | Measure | Bar |
|---|---|---|
| 1 | Fact claims judged `wrong`, by id, by name or pinned. The document holds the value from the XBRL file, so `wrong` means the connection returned another value. | 0 |
| 2 | By id, by name and pinned, each counted on its own: judged `verified` | at least 98% each. The rest must be `unverifiable`. |
| 3 | Swap cases judged `wrong`, among the cases whose fact was `verified` by id | 100%. The other swap cases must be `unverifiable`. |
| 4 | Abstention cases judged `unverifiable` | 100% |
| 5 | Amended reports: the claims pinned to the earlier receipt number are `unverifiable`, and the same facts pinned to the served number are `verified` | 100%. A run without an amended report has no cases. It then counts as met, and the results say that it had none. |
| 6 | Stop cases that exit with code 2 | 100% |
| 7 | Places where a key appears in what the tool printed or wrote | 0 |
| 8 | Added 2026-11-21, see the change log. Ambiguous rows: a locator that names a row by a name, or by an account id, that several rows of the statement share is judged `unverifiable`. | 100%. A run without such a row has no cases. It then counts as met, and the results say that it had none. |

A crash, a missing claim or any other verdict counts as a failure of the measure it belongs to.

Reported without a bar: `wrong` verdicts split by whether the tool returned the value the API holds; facts without a gold, and rows left out, by reason; results by company, report, consolidated or separate, statement, column and way of writing.

### Procedure

- Development companies may be run as often as needed. Every scorer run is logged with the commit hash in `bench/dart/runs.jsonl`.
- A check of the scorer, before the held-out run: at least six deliberately broken copies of the tool are scored on the development set (the column ignored; `fs` passed to DART unchecked; the receipt number ignored; every status other than `000` treated as no data; a name matched as a substring; the statement ignored). Each must fail at least one bar. If one passes all bars, a measure is added with a dated entry in the change log, before any held-out data is fetched.
- The held-out companies are run once, on the commit that is to be released. If a held-out bar fails, the README says so.
- DART is live and reports can be amended, so results name their receipt numbers.
- The key and the rate. DART gives each user a free key and its terms forbid letting others use it. The person who runs E4 supplies their own key through an environment variable. None is shipped in the repository or written into results. The builder and the tool make at most 5 requests per second, cache what they fetch and do not fetch it twice. A run needs about ten requests per company (six sets of statements, up to three XBRL files, one filing list), the company code file once, and the requests spent on drawn companies that are passed over. DART's limit is 20,000 requests a day per key.
- Fetching is a step of its own. Building the cases and scoring then run from the cached files without any request, so they can be repeated.
- Before real data is scored, the tool and the scorer may be run together on made-up data to check that the pieces fit. Such a run is not an E4 result.

## E5: a model writes the plan against DART (paid)

Registered on 2026-11-23, before the generator, the harness, the scorer and the prompts of E5 exist. E4 gave the script correct locators. E5 asks what happens when a model has to find them.

- **Question.** A document states figures of a Korean company. A model writes the plan with the skill, and the script checks it. Are the wrong numbers flagged and the correct ones left alone? The baseline is the same model reviewing alone, with the statements in front of it.
- **Model.** `claude-haiku-5-5`, with the request settings of E2: through the Anthropic SDK, effort `medium`, thinking left at the model's default, `max_tokens` 16,000, no sampling parameters, no prompt caching.
- **Spending.** Cap: US$3 for all of E5, the rehearsal included. A cost estimate is shown and approved before the first paid call. Every request is written to `bench/e5/spend.jsonl` with its token counts and cost. The harness adds that file up before each request and refuses to go past the cap. If the first 5 held-out documents cost more than 1.5 times the estimate, the run stops.
- **No pass bars.** The numbers are reported as they come out, whichever arm does better.

### Data

- **Where the truth comes from.** The documents are written from the gold files of E4's sets (`bench/dart/out/<dev|heldout>/sets/*/gold.json`). Each amount there was read from the company's XBRL file, with its period as the dates in that file. An E5 statement describes its period from those dates, so what a sentence means does not rest on the column table of the contract.
- **Companies.** Development: the 10 development companies of E4. Held-out: the 31 held-out companies of E4 (11 named, 20 drawn). The E4 held-out set is not fetched or scored again: E5 reads its gold files and a copy of the tool's cache as they are. The script has not changed since it was frozen for E4 (`2ae4742`), and nothing of E5 is developed on the held-out companies.
- **Documents.** One document per company, in Korean, about one report: the third quarter of 2026, the first half of 2026, or the year 2025. The report follows the company's position in the manifest (third quarter, half year, annual, and so on in turn). If the company has no set for it, the next in turn is taken. The document is about the consolidated statements when the company has them for that report, and about the separate ones otherwise. A company without the sets a document needs is listed and left out.
- **Statements.** A document has 12 statements, one per line, each with one number to check. An interim document has three amounts of the income statement for the three months, two for the cumulative period, one for a year earlier, two of the balance sheet (the period end and the end of the previous year), one of the cash flow statement, one from the company's other statements (separate, if the document is consolidated), and two derived figures (a percent change against a year earlier, and a margin or a debt ratio). An annual document has the same make-up, with the year, the previous year and the year before it in place of the interim periods. Rows come from a fixed list of standard account ids, taken in order as far as the set has them with a gold. The sentence uses the row's own name in the filing. A slot the set cannot fill is filled from another kind, and the manifest says so.
- **Planted errors.** Four of the 12 statements carry a wrong number. The kinds are (a) three months and cumulative exchanged, in interim income statements, (b) this period and the comparison period exchanged, (c) consolidated and separate exchanged, (d) the amount changed by 3% to 30%, and (e) a wrong derived figure: its sign turned, or computed from an input exchanged as in a, b or c. Kinds a, b and c show a number that is in the filing, under another label. An interim document gets one each of a, b and c and one of d or e. An annual document gets b, c, d and e. A kind the company cannot have (no separate statements, for example) is replaced by d. A planted number is kept only if the script's default tolerance cannot take it for the true one at the written precision.
- **Written forms.** Amounts as in E4's documents (won, 백만원, 억원, 조 and 억원), percentages with one decimal. Correct numbers are rounded at the written precision.
- **Gold plans.** For every statement the generator writes the locator the sentence means. With these plans the script must judge every planted statement `wrong` and every other statement `verified`, with nothing `uncovered`. The harness refuses a set of documents for which it does not. This check makes no model call.
- **Seed.** Every choice comes from a seeded generator, so a set of documents can be built again.

### Arms

- **Arm A, skill on.** System prompt: `skills/receipts/SKILL.md` as shipped, front matter included, plus a short note on how the session works. First message: the document with line numbers and the output of `scan`. The model can call three tools, which return what the script prints, unchanged: `dart company`, `dart accounts` (company, year, report, fs, and optionally statement and match), and one that returns `references/plan-format.md`. At most 12 tool calls per document. The script runs on a copy of the tool's cache with no key set: it can read the company code file and each company's annual report of 2025, half-year report of 2026 and third-quarter report of 2026, and nothing else. The note says so. The model replies with `claims.json`. The harness runs `check` and returns the printed receipt. The model may send a corrected plan up to two times, as step 6 of the skill allows, or reply `DONE`. The last plan that `check` accepted counts. If none was accepted, every number of that document counts as not checked.
- **Arm B, the model alone.** One request, no script and no tools. The model gets the document with line numbers and the statements of the report the document is about, consolidated and separate where both exist: every row of the balance sheet, the income statements and the cash flow statement with every amount, as tab-separated text. The column headings say what each column holds, in the words of the contract's column table. The system prompt asks for a careful review, as in E2: look up or recompute every number, allow for normal rounding and for a value cut off at the written precision, check direction words, and list what cannot be checked. The reply is a JSON list of wrong numbers with their line numbers.
- **Asymmetry, stated up front.** Arm A has to find the company, the report and the rows itself and may use many requests. Arm B is handed the right report and has one request. Arm B sees the amounts. In arm A only the script does.

### Measures

Counts are reported with Wilson 95% intervals.

1. Planted errors flagged, for each arm, in total and by kind. Arm A: the verdict of the statement's number is `wrong`. Arm B: the reply lists a wrong number on the statement's line.
2. Correct statements flagged, for each arm.
3. Arm A, the verdicts of planted and of correct statements: `wrong`, `verified`, `unverifiable`, `uncovered`. A planted statement judged `verified` is reported on its own: the plan read a cell that matches the number instead of the cell the sentence means.
4. Arm A, the locators: for every number judged from DART cells, whether the plan read the cell the generator meant, and if not, which field differs (company, year, report, fs, statement, row, column).
5. Arm A, repairs: corrected plans per document, and the numbers that went from `wrong` to `verified` between two plans because the locator changed.
6. Arm A: documents with no accepted plan, tool calls per document, and the share of numbers left `uncovered`.
7. Both arms: requests, tokens and cost per document. Arm B: flags that match no statement.

Counts are also split by report (third quarter, half year, annual) and by whether the company is one of the 11 named ones.

### Procedure

1. The generator, the harness and the scorer are written by the tool's author. Before the held-out run, an agent that wrote none of them reviews them and the development documents.
2. Free checks first: the gold plans on the development documents, and a dry run of the harness that makes no request (prompt sizes and a cost estimate).
3. Rehearsal: four development documents, both arms, inside the cap. Its purpose is to find harness errors. Prompts may be corrected after it. If the rehearsal shows that the skill text or the script should change, the change is made on development documents only, logged in the change log, and E1, E3 and E4 are run again on their development sets. No rehearsal number is reported as an E5 result.
4. The prompts, the script and the skill are committed. On that commit the held-out documents are generated and the held-out run is made, once. If the run is cut for a reason outside the script and the model (the machine, or the connection to the API), the results say so, and the documents that did not finish are run again. Nothing that finished is run again.
5. Records: for every document and arm the conversation, the tool calls with what they returned, every plan with the script's result, and the review reply are committed under `bench/e5/records/`. Thinking data is not kept.
6. The README reports the result whichever way it comes out, with the cost.

## Real cases

Checks of real documents may appear in the README as examples. They are not performance estimates and will be labelled that way.

## Change log

- 2026-11-19: registered. No code, generator or result existed at this commit.
- 2026-11-19, later the same day: the generator and scorer were written. No measure or bar changed. Details the plan left open, as the generator settles them:
  - The generator and scorer were written by a separate agent from `docs/eval-plan.md` and the contract (`skills/receipts/references/plan-format.md`), without reading the tool.
  - Size per seed: 64 documents of 15 statements each (960 statements); 24 abstention cases of each of four kinds (zero-match locator, multi-match locator, missing source, corrupt source); 2 claims removed per document in the coverage plans (10% of 15, rounded half-up).
  - Error types. Wrong scale: a wrong scale word, or with no scale word a shift of the decimal point by one place. Stale value: the row directly above in the source. Wrong denominator: (new − old) / |new| for a percent change, part / (whole − part) for a share. Wrong count: off by 1 or 2. Rounding outside tolerance: the last written digit is off by one or two units.
  - About 10% of correct statements are written truncated instead of rounded (only where the two differ). The default mode must accept them.
  - Measures 3 and 4: a crash, a missing claim or any verdict other than the expected one or `unverifiable` counts as a failure. Measure 6 counts any non-zero exit, and the results file also reports how many exits were exactly code 2. Measure 7 counts the statement numbers of the 64 documents, not the distractors.
  - The first development run (commit `e5a1488`, working tree not clean) was started by the generator's author by accident, with the default tool path. It is kept in `bench/runs.jsonl` like every other run.
- 2026-11-20: E3 registered in place of its outline. No connection code, builder, scorer or E3 result existed at this commit. E1 and E2 are unchanged.
- 2026-11-20, later the same day: the E3 builder and scorer were written by a separate agent from this plan and the contract, without reading the tool. No measure or bar changed. Details the plan left open, as the builder and scorer settle them (the full list is in `bench/edgar/README.md`):
  - Filing: in the recent part of the SEC's filing list, the entry with the exact form and inline XBRL that has the latest filing date.
  - A fact whose finest display has a scale other than 0, 3, 6 or 9 cannot be written with a scale word. It stays in the eligible count and is left out of the sample.
  - Same-end pairs are formed from durations: the two earliest-appearing start dates of one concept, unit and end date. The sample is the 25 drawn facts, then the members of the drawn pairs that are not already in it.
  - A zero is written `0`. The test lines carry no unit words.
  - Abstention locators keep the filing's accession number. Each kind of abstention case has its own plan file, so that no plan claims a number twice.
  - Scoring: if `check` exits with code 2, crashes or times out on a plan, every claim in that plan counts as a failure. Measure 1 counts claims, pinned and open together. The stop cases use the pinned plans of the first 5 filings of the run.
  - Fetching is a step of its own (`build.py --fetch-only`), run by the person who owns the contact string. Building the cases and scoring then run from the cached files without any request, so they can be repeated.
  - Before any real filing was fetched, the tool and the scorer were run together on a made-up filing and made-up company facts, to check that the pieces fit. That run is not an E3 result and is not in `bench/edgar/runs.jsonl`.
- 2026-11-20, after the first development run (commit `c37a731`, all five bars met): measure 6 added. Reason: four deliberately broken copies of the tool were scored on the development set as a check of the scorer. Three failed the bars they should. The fourth, which silently takes the latest report when filings give different values, passed all five, because measure 3 accepts both `verified` and a confirmed abstention, and the sampled filings are the latest ones. Measure 6 requires the abstention on such facts in the open plan and the value of the pinned filing in the pinned plan. Measures 1 to 5 and their bars are unchanged. The held-out companies had not been fetched or run. A run with no such fact has no cases for measure 6. It then counts as met, and the results say that it had no cases. With measure 6 in place the check was repeated with five broken copies (period start ignored; latest report taken silently; nearest period used when none matches; a corrupt cache treated as no data; the named filing ignored). Each failed at least one bar, and the tool itself met all six (commit `1f3776d`). The broken copies of the tool are not part of the repository and their runs are not in `bench/edgar/runs.jsonl`. The scorer's own self-test, which uses made-up data, has two stub tools with the same two faults (`bench/edgar/testdata/`).
- 2026-11-20, same entry, a fact about the development set: `XOM` now files under a new registrant (CIK 2115436) that has a 10-Q and no 10-K yet. The builder reports the missing 10-K and uses the 10-Q only, so the development set has 9 filings.
- 2026-11-20: E2's open details were fixed in the section "E2 details" above, before the harness was written and before any request to a model. The model, the two arms, the measures, the US$5 cap and the absence of pass bars are as registered on 2026-11-19.
- 2026-11-20, after the E2 rehearsal (four development documents, 13 requests, US$0.035) and before the held-out run: one scoring rule for arm B was widened. The rule "same digits" missed a flag when the model quoted more than the number (`48,374 units in 2024` for `48,374`), which counted a false alarm of arm B as a flag that matches no statement. The rule is now: the flagged text contains the statement's number as whole digit groups. This can only add to arm B's counts, of planted errors flagged and of correct statements flagged alike. Arm A's scoring and both prompts are unchanged.
- 2026-11-20, after an independent review of the E2 harness and before the held-out run. The reviewer recounted the rehearsal score from the raw files and got the scorer's numbers. It also found defects, each confirmed and fixed:
  - Arm B was told to allow for rounding only, while the generator counts a value cut off at the written precision as correct and the script accepts it by default. Three of arm B's four rehearsal false alarms were such values. Arm B's prompt now says that a cut-off value counts as correct, and correct statements are also reported split by how the number is written.
  - A reply cut off at the output limit could be misread as one of its inner objects. Only the outermost object with the expected list is read now, and a cut-off reply counts as unread.
  - The note on Excel sources said row 1 is the header, which is wrong for workbooks whose header is on row 2. The note is gone.
  - Some generated sources are scaled without saying so (CSV files, and Excel files with the header on row 1). Both arms then have to infer the unit. Results are also reported split by whether the sources state their unit.
  - A run stopped in the middle of a document lost its replies and could be charged twice without the first-five rule seeing it. Every reply is now written as it arrives, a stopped attempt is kept, the first-five sum comes from the spending record, and the scorer names documents that were not run.
  - A document with no accepted plan was left out of the uncovered share. Its numbers now count as uncovered, as the plan says.
  - The conversation of each arm, the plans and the script's results are now committed (see Records above).
  - The script runs without a contact string and with an empty EDGAR cache, so a plan cannot make it contact the SEC. The script has a time limit. A reply of `DONE` in bold is read as DONE. The skill file is sent with its front matter.
- 2026-11-20, same day: a second rehearsal of four development documents with Markdown and Excel sources (`d003`, `d007`, `d004`, `d024`), because the first four had only CSV and JSON sources. It runs inside the same cap and is not an E2 result.
- 2026-11-20, same day: the second rehearsal showed a gap in the skill text. On an Excel sheet with its header on row 2 the model did not know `header_row`, fell back to cell addresses and marked four numbers as having no source. `SKILL.md` now explains `header_row` and says that the whole of a share may be a many-cells selector. A third rehearsal of two such documents (`d024`, `d012`) checked the change. The three rehearsals together made 32 requests for US$0.093, on development documents only. After this entry the skill, the script and both prompts are frozen for the held-out runs.
- 2026-11-20: the held-out runs were made, each once. The code was frozen at commit `a84647c`. The runs are recorded at later commits that differ from it only in results and logs: `git diff a84647c <commit>` shows no change under `skills`, the benchmark scripts, the tests or the plugin manifest. E1 on seed 20261120 at `281b6ce`: all 8 bars met. E3 on the 20 held-out companies at `33e7b1b`: all 6 bars met. E2 on 30 held-out documents at `72bba95`, scored at `f66c86a`: reported without bars, as registered.
- 2026-11-20, a fact from the E3 held-out run: the two sampled facts that were not verified have a period of one day. The SEC's API stores such a fact with one date and no start date, so the locator's exact period matched nothing and the tool abstained. The code was not changed. The README names the limit.
- 2026-11-20, after the held-out E2 run: an independent audit of the release recounted the E2, E3 and E1 held-out numbers from the raw files and got the reported values. It also found that the arm B matching rule undercounts arm B: seven planted direction errors were flagged on the right line by naming the direction word, without the number, and the rule counted them as misses. No rule was changed after the run. The README and `bench/e2/results/heldout-recount-after-audit.md` show both counts: 118 of 128 under the registered rule, and 125 of 128 when any flag on the statement's line counts. The count of correct statements flagged by arm B is 26 either way.
- 2026-11-20, version 0.1.1, after the release of 0.1.0. Four things changed. No held-out set was run again, and the held-out results stay those of 0.1.0.
  - The tool: a period of one day (`d..d`) names the same fact as the date `d` (contract, section 2). The two facts of the held-out E3 run that were not verified are of this kind. Tried again on their own with 0.1.1, outside the scored runs, both verify with and without the filing named.
  - The E3 builder (changed by a separate agent, without reading the tool): durations of one day are left out of the abstention cases that ask for a duration as one date. The SEC's API stores a one-day fact under that date, so the tool finds it, as 0.1.0 already did: such a case was never a valid abstention. None had been built, in the development set or the held-out set. The other cases are built as before. The development set was rebuilt and has the same 266 facts.
  - The E2 scorer: besides the registered counts it reports arm B's counts when any flag on the statement's line counts. On the held-out run it gives 125 of 128 and 26 of 322, the numbers of the recount above.
  - The E2 harness: the records it exports name the accepted plan's file as it is stored in the records folder. Nothing was run with it.
  - On the development sets 0.1.1 meets all 6 bars of E3 (commit `bc8e36d`) and all 8 bars of E1 (commit `f5f63d1`).
- 2026-11-21: E4 (DART connection) registered, together with the DART part of the contract (`skills/receipts/references/plan-format.md`, sections 2 and 9). No connection code, builder, scorer or E4 result existed at this commit. E1, E2 and E3 are unchanged.
- 2026-11-21, later the same day: the E4 builder and scorer were written by a separate agent from this plan and the contract, without reading the tool. No measure or bar changed. Details the plan left open, as the builder and scorer settle them (the full list is in `bench/dart/README.md`):
  - Periods are taken from the period facts the instance states about itself (`dart-gcd:DocumentPeriodStartDate` and `DocumentPeriodEndDate`). The three months of an interim report are the one period of 80 to 100 days that ends on the period end. Context ids are not read.
  - Balances in a flow statement. The opening and closing cash rows of a cash flow statement are tagged at a single date, while the column's period there is a span. Read strictly they have no gold. They are counted as `only_instant_facts` and left out.
  - A line of a test document holds one number: everything else is in inline code, which `scan` skips. `백만원` is used only when it is exact. Rounding to whole 억 is half-up.
  - The company is given as a stock code in annual sets when the company code file has exactly one company with that stock code, otherwise as the company code.
  - By name: a row is in the by-name plan when its name occurs once among all rows of that statement in the set.
  - A swap case is not built when the neighbour's value as written matches the fact's own gold under the default tolerance. Those are counted by kind.
  - Each kind of abstention case has its own plan file, so that no plan claims a number twice. The changed receipt number is the set's number with its last digit raised by one.
  - Amended reports: the filing list is asked from 2025-01-01, with earlier versions. An earlier filing has an earlier date, or the same date and a smaller receipt number.
  - The draw asks for consolidated statements first and for separate ones only if there are none. A company is kept only if the XBRL answer is a zip with exactly one instance file.
  - Filling. The plan says the tool's cache is filled by running the tool once on the by-id plans. The year-2031 abstention plans are run in that step too, because DART's answer "no data" for them has to be in the cache before a run without a key. No other plan is run with the key. In particular the `fs` `XXX` plans are not, so a tool that passes `fs` to DART unchecked needs a request while scoring and stops.
  - Scoring runs with no key and with the DART address set to a closed local port. A plan the tool does not answer (a crash, a timeout, exit code 2, no valid result file) counts every claim in it as a failure. Measure 2 needs each of the three plans to reach 98% on its own.
  - Stop cases. In the case "an empty cache and no key set" the address points at a local stand-in, so that a faulty tool cannot reach DART. In the case "a corrupt statements file" the company code file is copied into the case's cache when the plan names a stock code, so that the stop comes from the corrupt file. The made-up keys are 40 characters derived from the case's name.
  - The reason of an expected `unverifiable` is not fixed by the plan. Any reason counts, and the reasons are reported.
  - The builder and scorer have a self-test on made-up companies with a stub tool and eleven faulty stubs. It is not an E4 result and is not in `bench/dart/runs.jsonl`.
- 2026-11-21, facts about the development set: 58 of the 60 sets have data. 동원모빌리티, whose business year ends in March, has no third-quarter report under year 2026 yet. Four development companies have an amended annual report 2025, not two as the registration says: GS건설, 자이에스앤디, 삼성생명 and KB금융.
- 2026-11-21, before the tool was run on the development set: the contract's table was wrong in one cell, and the contract and the cases were changed. The table said that in an interim cash flow statement `frmtrm_q_amount` holds "the same period a year earlier". The builder's author found, by a comparison with the cached answers that is not part of the builder, that DART fills that column with the cumulative period in most rows and with the three months in a few (the profit of the period, a bank's credit loss charge). In the 58 development sets, the interim cash flow rows that have both `frmtrm_q_amount` and `frmtrm_amount` hold the same value in 1,523 rows and different values in 22. What changed:
  - The contract (section 2): a locator that names `frmtrm_q_amount` in an interim cash flow statement gives `unsupported`. The cumulative comparison is `frmtrm_amount`. The tool follows it (commit `c5a6bfe`).
  - The facts: interim cash flow facts use `thstrm_amount` and `frmtrm_amount` only. The development set went from 18,349 facts with a gold to 17,018.
  - The cases: an eighth kind of abstention case, counted under measure 4. In each interim set, the first five cash flow facts are asked under `frmtrm_q_amount`. Each is expected `unverifiable`.
  - Measures 1 to 7 and their bars are unchanged. No held-out data had been fetched, and the tool had not been run on the development set.
- 2026-11-21, same day, wording of the contract after registration, with no change to any rule that decides a verdict: `year` is a whole number or a string of four digits; the receipt prints the period name and the table's words for the column; the `accounts` look-up also prints what a column holds.
- 2026-11-21, the first development run of the tool: all seven bars met at commit `a38abf7` (17,018 facts with a gold; the results are in `bench/dart/results/`). The tool was changed after that run, as the next entry says, so this is not the run of the version to be released.
- 2026-11-21, after an independent review of the connection and before the held-out run. A reviewer that had written none of the code read the contract, the script, the tests and the documents, and tried to break the script against a local stand-in, without reading `bench/dart`. Each finding was checked again and, where it held, fixed. What changed in the tool and the contract:
  - An answer is used only if its rows name the company, the year and the report that were asked for, and carry a receipt number. Before, a cached file under the wrong name, or an answer for another report, was used as if it were the right one. In the 58 development answers every one of the 11,269 rows names its own request.
  - A field the script does not know gives `locator_no_match`. Before, a misspelled `rcept_no` was skipped without a word and the claim was checked against whatever version DART serves.
  - A column the contract's table leaves empty for that report and statement is refused, whatever the row holds. In the development answers 32 rows of interim balance sheets carry a `bfefrmtrm_amount`.
  - An answer whose rows carry more than one receipt number gives `unverifiable` unless the locator names one. None occurred in the development answers.
  - The message DART sends with a status is never shown, only the code and the script's own words for it. An answer that repeats the key is not cached. Digits in a company code, a year and a receipt number are ASCII digits only.
  - The receipt names another address than DART's from what was recorded when the file was fetched, not from the variable of the current run. During scoring that variable points at a closed port, so the receipts of the first development run named an address nothing had been fetched from.
  - The `accounts` look-up prints no amounts, and only the columns a locator may name. The `company` look-up also searches the English names.
  - When `check` stops, an earlier `receipts.json` or `receipts.md` in the output folder is replaced by a note, so that an earlier result cannot pass for the stopped run. A multiplier has to be a finite number.
  - The cases and the measures of E4 did not change. The builder and the scorer were not changed for these fixes.
- 2026-11-21, a rule for the held-out run, fixed before it. Twice during development the network failed for about a minute (the name of DART's host could not be resolved), and the tool stopped on the plans it was filling at that moment, as it should. Filling the cache is not the scored run. If the fetch of the builder or the fill of the tool's cache is cut short because DART could not be read, that step is started again, and it fetches only what is missing. The scoring is run once, after the fill record shows that every plan was answered. If scoring itself fails for a reason outside the tool (the machine, not DART, since scoring makes no request), the results say so and the scoring is run again.
- 2026-11-21, the development run of version 0.2.0: all seven bars met (the run is logged at commit `305bda1`; the code is that of `e0c248e`). E1 and E3 were run again on their development sets with this version and meet all their bars (`e0c248e`, `0914799`).
- 2026-11-21, the check of the scorer, and measure 8. Nine deliberately broken copies of version 0.2.0 were scored on the development set, each with a tool cache of its own, filled by that copy. The six faults the plan lists each failed at least one bar: the column ignored (bars 1, 2, 3, 4, 5); `fs` passed to DART unchecked (4); the receipt number ignored (4, 5); every status other than `000` treated as no data (6); a name matched as a substring (2); the statement ignored (2, 4, 5). Of three more faults that the plan does not list, two failed a bar: `frmtrm_q_amount` read in an interim cash flow statement (4), and a corrupt cached file treated as no data (6). The third met all seven bars: a copy that takes the first row when several rows match, instead of abstaining. No case named an ambiguous row, because rows whose account id repeats are left out of the facts and rows whose name repeats are left out of the by-name plan. Under the rule of the procedure, measure 8 was added before any held-out data was fetched:
  - Cases: in each set, rows whose name occurs more than once among the rows of their statement are asked by name alone, and rows whose standard account id occurs more than once are asked by id alone. At most the first ten rows of each kind per set. Each is expected `unverifiable`.
  - Measure 8: such cases judged `unverifiable`, bar 100%. A run without such a row has no cases and counts as met.
  - Measures 1 to 7 and their bars are unchanged. The broken copies of the tool are not part of the repository and their runs are not in `bench/dart/runs.jsonl`. The scorer's self-test, which uses made-up data, has stub tools with the same faults (`bench/dart/testdata/`).
- 2026-11-21, measure 8 on the development set: version 0.2.0 as it stood at commit `e0c248e` meets all eight bars (logged at `33d04e5`; 280 ambiguous-row cases by name, none by id, since no development set lists a standard account id twice in one statement). The broken copy that takes the first of several rows now fails it, with none of its 280 cases `unverifiable`, and meets the other seven.
- 2026-11-21, after a second pass of the reviewer and before the held-out run. The reviewer ran its first experiments again on commit `e0c248e`, ran 61 changed copies of the script against the tests, and read the contract against the code sentence by sentence. It found the first round fixed, apart from the points below, and ten changes the tests did not notice. What changed in the tool, the contract and the tests:
  - The note that a cached file came from another address than DART's is written before the file is stored. Before, a note that could not be written left the file in the cache without it.
  - When a stopped check cannot replace an earlier result file it says so, and a result that cannot be written is a stop.
  - A body that ends before its announced length, or before the end of its gzip stream, is a failed request and is tried once more.
  - A company code file in which a name or a stock code cannot be read stops the run, like one in which a company code cannot be read. The file inside the zip has a size limit.
  - A `file` locator into the script's own cache folder (`.receipts`) is refused. As a plain file the cache gave any row and column, the statement of changes in equity included, without the rules of a DART locator.
  - An EDGAR locator with a field the script does not know is refused, like a DART one. A misspelled `accession` used to be skipped. The E3 cases use the known fields only.
  - A multiplier has a size limit. A cached file that holds a stop status is reported as a cache problem.
  - Tests were added for nine of the ten changes that went unnoticed. The tenth changed nothing a user can see.
  - Known and left as it is: the rows of an answer do not say whether they are consolidated or separate, so a cached file moved between the two names is not noticed. The contract says so.
  - The cases, the measures and the bars of E4 did not change, and neither did the builder or the scorer. The broken copies of the check of the scorer were made from commit `e0c248e`. The code that is released differs from it by the changes of this entry.
- 2026-11-21, the code is frozen for the held-out run at commit `2ae4742` (version 0.2.0). On it the unit tests pass (117), and the development sets meet every bar: E1 (logged at `2ae4742`), E3 (`c5e2e76`) and E4 with its eight measures (`00582a2`, with a tool cache filled afresh by this version). Later commits up to the held-out run differ from `2ae4742` only in results, logs and this file. No held-out statement, XBRL file or filing list had been fetched, and the order of the draw had not been computed.
- 2026-11-22, the held-out run, first scoring. The held-out data was fetched on 2026-11-21 in one pass (329 requests): the 11 named companies and 20 drawn ones, out of 3,999 candidates, with 9 passed over because DART has no statements for their annual report 2025. The set has 177 sets of statements (three drawn companies file no consolidated statements) and 44,829 facts with a gold. The tool's cache was filled in one pass (354 runs, every plan answered, the key in 0 places). The scoring, logged at commit `f3a841c`, was cut by the machine: Windows recorded that the computer went to sleep at 23:38:49 on 2026-11-21 (Kernel-Power event 42) and woke at 10:10:13 the next morning. The four plans that were running at that moment, all of one set (`00428251-half-CFS`: the swap plan with 628 cases and three abstention plans with 5 cases each), passed the scorer's time limit of 900 seconds per plan and count as not answered. The files show it too: the answer written last before the gap is from 23:38:49 and the next one from 10:10:13. That scoring reports measures 1, 2, 5, 6, 7 and 8 as met, and measures 3 and 4 as not met, by exactly those 628 and 15 cases. It stays in `bench/dart/runs.jsonl` and `bench/dart/results/` as it is.
  - Under the rule fixed on 2026-11-21, before the held-out run ("If scoring itself fails for a reason outside the tool (the machine, not DART, since scoring makes no request), the results say so and the scoring is run again"), the scoring is run again, once, with the same command, the same caches and the same code, and with the computer kept awake. Nothing is fetched again and nothing is rebuilt.
- 2026-11-22, the second scoring was cut as well, about six minutes in. It was started from an agent session of the Claude desktop app, and that session ended while the scoring ran. From then on no new process could start: 1,583 of the 2,179 plans and all 25 stop cases ended with the Windows exit code 3221225794 (0xC0000142, a process that could not be initialised) and with no output, and the scorer could not run `git`, so its result is filed under `nogit`. The tool did not run for those plans. No fact claim was judged `wrong` in the plans that did run. The result is kept as it is in `bench/dart/runs.jsonl` and `bench/dart/results/`.
  - Under the same rule the scoring is run a third time, with the same command, caches and code. It is started outside the session, so that it does not end with it, and the process itself asks Windows not to go to sleep while it runs.
- 2026-11-22, the third scoring ran to its end, from 15:17:42 to 15:33:06 local time. It is logged at commit `79dafe1` with a clean tree, and the tool file is the one of the frozen commit `2ae4742`. Every one of the 2,179 plans was answered and all eight bars are met: 0 of 132,384 fact claims judged `wrong`; verified 44,829 of 44,829 by id, 42,726 of 42,726 by name and 44,829 of 44,829 pinned; 76,873 of 76,873 swap cases `wrong`; 6,785 of 6,785 abstention cases `unverifiable`; 95 of 95 amended cases; 25 of 25 stop cases; the key in 0 places; 599 of 599 ambiguous rows `unverifiable` (all by name: no held-out set lists a standard account id twice in one statement). This is the held-out result of E4. The two scorings that were cut are in `bench/dart/runs.jsonl` and `bench/dart/results/` beside it, and the README reports all three. They differ from the third only in the plans that were cut: four plans of one set in the first, and the 1,583 plans that never started in the second.
- 2026-11-22, corrections to the entries above, after an independent check of the documents against the raw files. The checker recounted all 216,736 claims of the third scoring from the tool's answers and got the reported numbers, and read the 44,829 gold values again from the XBRL files with a reader of its own.
  - The first scoring. Windows records the reason of the sleep as "Button or Lid" and the wake source as the power button. The computer was put to sleep. It did not fail. The wake time in the event is 10:10:12.6.
  - The second scoring. Besides the 1,583 plans, all 25 stop cases could not start, so that run reports six bars as not met (2, 3, 4, 5, 6 and 8). The whole run lasted 5 minutes 38 seconds (10:16:53 to 10:22:31), so it was cut earlier than "about six minutes in". Windows records the Claude desktop app being updated at 10:22:31. The `nogit` line has no real dirty flag, because git could not be run.
  - "They differ from the third only in the plans that were cut" holds in this sense: for the plans that did run (2,175 in the first scoring, 596 in the second), the verdict counts equal those of the third scoring on the same plans.
  - "Twice during development the network failed": when that entry was written it had happened once, at 14:47 on 2026-11-21. The second time was at 16:20, during the check of the scorer.
  - The check of the scorer. A first round, on the code as it was before the first review's fixes, was started at 14:38 on 2026-11-21 and stopped part-way because the code was about to change: two copies had been scored and three more had been started. It is not counted. The registered sentence "Every scorer run is logged with the commit hash in `bench/dart/runs.jsonl`" is kept for runs of the tool. Runs of broken copies are logged outside the repository.
- 2026-11-22, a correction to the entry above, found by an independent check of the posts about version 0.2.0 against the raw files. The first round of the check of the scorer: its logs, kept outside the repository, show three copies scored and two more started, not two and three. `column_ignored`, `fs_unchecked` and `receipt_ignored` each have a complete score log with the seven bars of that time, written at 14:42, 14:51 and 14:56 on 2026-11-21. The fill of `status_as_no_data` ended after its first set of statements and its scorer stopped at once because there was no fill record, and `name_substring` has only the start of a fill. Neither was scored. The round is still not counted, and no result changes.
- 2026-11-23, E5 registered (section "E5: a model writes the plan against DART"), before its generator, harness, scorer and prompts were written. It answers the limit stated for E4, that no model was involved. Its held-out documents are written from the gold files of E4's held-out companies: that set is read as it is, and is not fetched or scored again.
