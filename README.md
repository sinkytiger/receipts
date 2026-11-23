# receipts

Every number your agent writes comes with a receipt, or gets flagged.

> **Status: 0.2.0.** A checking script, an agent skill, connections to SEC EDGAR and to Korea's DART, and four evaluations whose rules were written down before they were run. The results, with their limits, are [below](#what-the-evaluation-shows). E1, E2 and E3 were measured on 0.1.0, E4 on 0.2.0. [CHANGELOG.md](CHANGELOG.md) says what changed between them.

[한국어](README.ko.md)

## The problem

An agent writes a report and says the numbers were checked. Often they were not, or only some were. A reader cannot tell which.

## The idea

The model proposes, code decides.

1. Code finds every number in the document. That count is the denominator.
2. The model writes a plan: for each number, the sentence it is in, the source cell it comes from, and the formula. The plan holds locations, not values.
3. Code reads the sources, recomputes, and gives each number a verdict: `verified`, `wrong`, or `unverifiable`.
4. Numbers the plan left out are listed as `uncovered`.

## Install the skill in Claude Code

```
/plugin install receipts --marketplace sinkytiger/receipts
```

The one-step form needs Claude Code 2.1.275 or later. On an older version use two steps:

```
/plugin marketplace add sinkytiger/receipts
/plugin install receipts@receipts
```

Then ask Claude to check the numbers in a document against its source files, for example "use receipts to check the numbers in report.md against sales.csv". How reliably the skill is picked up when you do not name it has not been measured yet. The skill adds about 180 tokens to every session and about 4,400 when it runs (estimates from `claude plugin details`, for 0.2.0).

Both forms were tested as `claude plugin install ...` on the command line, from a clean configuration, with Claude Code 2.1.326 on Windows. For 0.2.0 the one-step form was tested again, with Claude Code 2.1.330. The skill folder is plain Markdown plus one Python script, so other agents that read skills can use `skills/receipts` as it is. That has not been tested.

## Try the script without an agent

It needs Python and nothing else. It is written for Python 3.9 and later and has been run on 3.12 so far.

```sh
python skills/receipts/scripts/receipts.py check examples/quickstart/claims.json
```

The example report says East revenue grew 12.4%. The source says otherwise:

```
9 numbers / 6 verified / 1 wrong / 1 unverifiable / 1 uncovered   (skipped: 1 date, 2 heading_number, 1 label, 3 year)

### wrong (1)

| line | sentence | written | source | recomputed | note |
|---|---|---|---|---|---|
| 7 | East revenue grew 12.4% year over year | 12.4% | sales.csv [year=2025, region=East] revenue = 1088000; sales.csv [year=2024, region=East] revenue = 972000 | 11.9% (exact 11.9342) | mismatch |
```

The command also writes `receipts.json` and `receipts.md` next to the plan. The exit code is 1 when a number is wrong and 2 when a source cannot be read.

Sources that work today: CSV, TSV, JSON, Markdown tables, plain text, Excel values, what US-listed companies report to the SEC, and the financial statements Korean companies file with DART.

## Check against SEC filings

A source can be a fact a company reported in its 10-K or 10-Q. The script reads it from the SEC's EDGAR API.

```json
{"edgar": "AAPL", "concept": "us-gaap:Assets", "unit": "USD", "period": "2026-09-26"}
```

- The SEC asks automated clients to say who they are. Put your name and e-mail address in the environment variable `RECEIPTS_SEC_USER_AGENT`. Nothing is shipped as a default, and without it the script stops before making a request. The text is sent only with these requests to the SEC and is not stored anywhere.
- Each company is fetched once and cached under `.receipts/edgar/`. The receipt shows the fetch date. The script makes at most 5 requests a second (the SEC allows 10).
- Periods are exact. A quarter and the year to date end on the same day and differ in the start date, so one cannot pass for the other.
- When filings report different values for the same period (a restatement), the script abstains and lists them, unless the plan names the filing or picks the first or the latest report.
- `receipts.py edgar concepts AAPL --match revenue` and `receipts.py edgar facts AAPL us-gaap:Assets` print a company's concept names and period dates, which a plan needs.
- Segment, product and regional numbers are not in this API. Concepts a company defined itself were not in it either, for the five companies looked at.
- A fact whose period is one day, such as a dividend paid on one date, is stored by the SEC under that single date. It can be asked for as the date or as a one-day range.

The rules are in sections 2 and 8 of [plan-format.md](skills/receipts/references/plan-format.md).

[examples/edgar](examples/edgar) is a short note on Apple's fiscal 2026 with one wrong figure. It runs without a network connection, from a cut-down copy of what the SEC returned on 2026-11-20:

```sh
python skills/receipts/scripts/receipts.py check examples/edgar/claims.json --edgar-cache examples/edgar/snapshot
```

```
7 numbers / 6 verified / 1 wrong / 0 unverifiable / 0 uncovered   (skipped: 1 date, 1 identifier, 3 year)
```

The note says research and development was 10.6% of revenue. Recomputed from the two filed figures it is 9.6%.

## Check against Korean filings (DART)

A source can be an amount in the financial statements of a periodic report filed with Korea's DART: the annual, half-year and quarterly reports (사업보고서, 반기보고서, 분기보고서). The script reads it from OpenDART, the API of the Financial Supervisory Service.

```json
{"dart": "005930", "year": 2026, "report": "q3", "fs": "CFS", "statement": "IS",
 "account": "dart_OperatingIncomeLoss", "column": "thstrm_amount"}
```

- DART gives every user a free key at [opendart.fss.or.kr](https://opendart.fss.or.kr), and its terms forbid letting anyone else use it. Put yours in the environment variable `RECEIPTS_DART_KEY`. Nothing is shipped as a default, and without it the script stops before making a request. The key is sent only to DART, or to the address in `RECEIPTS_DART_URL` if you set that variable, which exists for tests. It is not written to the cache, to a receipt or to any message.
- Each report is fetched once and cached under `.receipts/dart/`. The receipt shows the fetch date and the receipt number of the filing. The script makes at most 5 requests a second.
- The locator names the things people mix up: consolidated or separate (`fs`, with no default), the statement, and the column. In a half-year or quarterly report the three months and the year to date are two columns of the same row. The plan has to say which one a sentence means, and the receipt prints what that column holds.
- DART's API holds only the latest version of a report. A plan can name the receipt number of the filing the document used. If the report has been amended since, the script abstains instead of comparing the document with other figures.
- `receipts.py dart company 삼성전자` and `receipts.py dart accounts 005930 --year 2026 --report q3 --fs CFS` print a company's codes and the rows of a report, with what each column holds. They print no amounts: a plan is written from what a row means, not from which value matches.
- Amounts are in whole units of the report's currency, which is won for almost every company. A document may write them as `107조 4,405억원`.
- The statement of changes in equity, notes, segment figures, preliminary earnings (잠정실적) and years before 2015 are not covered.

The rules are in sections 2 and 9 of [plan-format.md](skills/receipts/references/plan-format.md).

[examples/dart](examples/dart) is a short note in Korean on Samsung Electronics' third quarter of 2026 with one wrong figure. It runs without a network connection and without a key, from a cut-down copy of what DART returned on 2026-11-21:

```sh
python skills/receipts/scripts/receipts.py check examples/dart/claims.json --dart-cache examples/dart/snapshot
```

```
7 numbers / 6 verified / 1 wrong / 0 unverifiable / 0 uncovered   (skipped: 7 date, 1 year)
```

The note gives 194.0조원 as the third-quarter net profit. That is the figure for January to September. The quarter alone was 75.1조원.

## What is here now

- [skills/receipts/SKILL.md](skills/receipts/SKILL.md): the skill. It tells an agent how to write the plan, run the script and report the result without overstating it.
- [skills/receipts/scripts/receipts.py](skills/receipts/scripts/receipts.py): `scan`, `check` and `report`, and the look-ups `edgar` and `dart`. Standard library only.
- [skills/receipts/references/plan-format.md](skills/receipts/references/plan-format.md): the plan format, the rules for reading and comparing numbers, and the outputs. The script and the benchmark both follow it.
- [examples/quickstart](examples/quickstart): a short report, its source and a plan. [examples/edgar](examples/edgar): a note checked against SEC filings. [examples/dart](examples/dart): a Korean note checked against DART filings.
- [tests](tests): unit tests. Run `python -m unittest discover -s tests`.
- [bench](bench): the planted-error benchmark generator and scorer; [bench/edgar](bench/edgar) and [bench/dart](bench/dart), the builders and scorers for the EDGAR and DART connections; [bench/e2](bench/e2) and [bench/e5](bench/e5), the harnesses, scorers, spending records and conversations of the evaluations with a model.
- [docs/design.md](docs/design.md) and [docs/eval-plan.md](docs/eval-plan.md): the design, and the evaluation plan. E1 was fixed before any code existed, E3 and E4 before their connections were written, E2's details before its harness, and E5 before its generator and harness.

## What the evaluation shows

The rules for each evaluation were fixed in [docs/eval-plan.md](docs/eval-plan.md) before it was run. For each release the code was frozen in one commit before its held-out sets were used: `a84647c` for E1, E2 and E3 (version 0.1.0) and `2ae4742` for E4 (version 0.2.0). The held-out sets of E1, E2 and E3 were each run once. E4's was scored three times, because the first two scorings were cut short. The E4 section says how. E5 was run after the 0.2.0 release, with the same script and skill: its harness was frozen at `15b4dc5` and its held-out run was made once, on that commit.

| | Question | Held-out result |
|---|---|---|
| **E1** | Given a correct plan, does the script judge correctly? 64 synthetic documents, 960 statements, 288 with a planted error. | All 8 pass bars met. No planted error was accepted and no correct statement was flagged. |
| **E2** | What happens when a model writes the plan? Claude Haiku 5.5, 30 synthetic documents, 128 planted errors, 322 correct statements. | With the skill and the script: 128 of 128 planted errors flagged, 2 of 322 correct statements flagged. The same model reviewing on its own: 125 of 128 and 26 of 322 (118 of 128 under the matching rule fixed beforehand, which undercounts it). |
| **E3** | Does the EDGAR connection return what the filing prints? 20 companies, 40 filings, 1,169 facts. | All 6 pass bars met. No wrong value. 1,167 of 1,169 verified when the filing is named. |
| **E4** | Does the DART connection return what the company filed, and does it tell neighbouring columns apart? 31 companies, 177 sets of statements, 44,829 amounts. | All 8 pass bars met, on the third scoring. The first two were cut short and are reported below. No wrong value in any of them. |
| **E5** | When a model has to find the DART locators itself, are the wrong numbers flagged? Claude Haiku 5.5, 31 Korean documents about 31 companies, 124 planted errors, 248 correct statements. | With the skill and the script: 124 of 124 planted errors flagged, 0 of 248 correct statements flagged. The same model reviewing on its own, with the statements in front of it: 120 of 124 and 2 of 248 under the rule fixed beforehand (124 of 124 if one reply with a stray character is read by hand). No pass bars. |

### E1, the script alone

No model is involved. The generator writes the documents, the sources and the correct plans, and plants an error in 30% of the statements. On the held-out seed:

- 288 of 288 planted errors were judged `wrong`, and 672 of 672 correct statements `verified`;
- 48 of 48 locators that match no cell or several were `unverifiable`, and 48 of 48 missing or corrupt sources stopped the run;
- `scan` found 960 of 960 statement numbers, and 128 of 128 numbers removed from a plan were listed as `uncovered`.

This shows that the script reads and compares correctly when the plan is right. The documents are built from sentence templates, half English and half Korean, so real reports will be messier.

### E2, a model writes the plan

Two arms on the same 30 documents, with the same model and request settings. In arm A the model gets the skill text and the script's list of every number in the document, writes the plan, and the script decides. It may correct its plan twice. In arm B the model reviews the document against the sources in one request and lists the wrong numbers. It gets no output of the script. E2 has no pass bars.

| | Arm A: skill and script | Arm B: the model alone |
|---|---|---|
| Planted errors flagged | 128 of 128 (95% interval 97.1% to 100%) | 125 of 128 (93.3% to 99.2%) when any flag on the statement's line counts. 118 of 128 (86.2% to 95.7%) under the rule fixed beforehand. |
| Correct statements flagged | 2 of 322 (0.2% to 2.2%) | 26 of 322 (5.6% to 11.6%) |
| Numbers left unchecked | 0 of 450 `uncovered`, 2 `unverifiable` | 164 items listed as not checkable, none of them a benchmark statement |
| Cost per document | US$0.0075, 2.2 requests on average | US$0.0013, 1 request |

What to keep in mind:

- **The matching rule fixed beforehand undercounts arm B.** It credits a flag only when the flagged text contains the statement's number. Seven times arm B flagged a wrong direction word on the right line ("decreased", "늘었다") without quoting the number, and the rule counted these as misses. Counting any flag on the statement's line, arm B flagged 125 of 128. The three it missed are rounding errors. Its count of correct statements flagged is 26 under both rules. This recount was made after the run, when an audit found the gap, so both figures are shown. The seven cases are listed in [bench/e2/results/heldout-recount-after-audit.md](bench/e2/results/heldout-recount-after-audit.md).
- **Most of arm B's false alarms are about one convention.** 22 of its 26 are numbers written cut off instead of rounded (2.36 for 2.368). The benchmark counts those as correct, the script accepts them by default, and arm B was told so. On numbers written rounded the arms are close: 2 of 289 for arm A, 4 of 289 for arm B.
- **A wrong plan can still make a correct number look wrong.** Both of arm A's false alarms come from one Korean document where the model set multipliers that convert into the unit of the sentence, not the unit of the source. The receipt lists every multiplier a plan declares, so a reader can see this.
- **Arm A abstained twice.** In one document whose CSV source does not state its unit, the model's first plan verified two numbers with a multiplier of one million. In its corrected plan it withdrew both, because the source does not state the unit, and marked them as having no source.
- **One model, one run.** Other models, other settings and real documents may give other numbers. The arms differ in cost: arm A used 67 requests, arm B 30.
- **What changed between the rehearsals and this run.** For arm B: how its flags are matched to statements, and a sentence telling it that cut-off values count as correct. For arm A: after the second rehearsal the skill text, which is arm A's prompt, gained two lines, on Excel sheets whose header is not on row 1 and on shares of a many-cells total. For both: a wrong note about Excel headers was removed from the sources they see, and the harness was made robust against cut-off replies and stopped runs. All of it is in the plan's change log, with the reasons. The rehearsals used development documents only.

Every conversation, plan and script result of this run is in [bench/e2/records/heldout](bench/e2/records/heldout), and every request with its cost in [bench/e2/spend.jsonl](bench/e2/spend.jsonl). All of E2, rehearsals included, cost US$0.36.

### E3, SEC EDGAR

Values the connection returns are compared with the numbers printed in the filings. For each of 20 companies, the most recent 10-K and 10-Q were read, and up to 35 tagged numbers per filing were sampled.

- No fact was judged `wrong`, in 2,338 verdicts.
- With the filing named, 1,167 of 1,169 were verified. With no filing named, 1,145 were verified and 22 abstained because filings report different values for that fact.
- The two that were not verified are dividends per share paid on a single day. The filing tags each as a period of one day, and the SEC's API stores such a fact under a single date, so the exact-period rule finds nothing. The script abstained. Version 0.1.1 treats a one-day range as the single date. Tried again on their own after the 0.1.0 release, both facts verify. That check is not part of the result above.
- 800 of 800 locators that should match nothing were `unverifiable`, and 15 of 15 runs with no contact string, a corrupt cached file or an unreachable server stopped.

Both sides of this comparison start from the same submission by the company. E3 tests the connection, not whether a company tagged its filing correctly. The sixth bar was added after the first development run, when a deliberately broken copy of the tool got past the first five. The [plan's change log](docs/eval-plan.md) has the details.

### E4, Korean filings (DART)

Values the connection returns are compared with the XBRL files the companies filed. For 31 companies, the annual report 2025 and the half-year and third-quarter reports 2026 were read, consolidated and separate: 177 sets of statements and 44,829 amounts. Eleven of the companies are large ones named beforehand. Twenty were drawn by a rule fixed beforehand from the 3,999 other companies that have a stock code in DART's company list, so no one chose them. Nine of the 186 possible sets do not exist: three of the drawn companies file no consolidated statements.

- No amount was judged `wrong`, in 132,384 verdicts. All 44,829 were verified when asked by account id, all 42,726 when asked by name, and all 44,829 with the receipt number named.
- In 76,873 of 76,873 cases where the document held the number of a neighbouring column, the verdict was `wrong`: the year to date in place of the three months, last year in place of this year, separate in place of consolidated.
- In 6,785 of 6,785 cases where the script should abstain, it did: an account that does not exist, a column the report does not have, a year without a report, a statement type DART does not know, the statement of changes in equity, a changed receipt number. So did 599 of 599 locators that name a row by a name several rows share.
- Ten of the companies had amended a report. In 95 of 95 cases a claim that names the earlier receipt number was `unverifiable`, and the same amount with the current number was verified.
- 25 of 25 runs with no key, a corrupt cached file, an unreachable server or an error status from DART stopped. The key appeared nowhere in what the script printed or wrote.

What to keep in mind:

- **The scoring was started three times, and all three are logged.** The first was cut when the computer was put to sleep in the middle, for ten and a half hours. Windows records the power button or the lid as the reason. The four plans running at that moment passed the time limit and counted as failures (628 swap cases and 15 abstention cases, all of one set of statements), so that run reports two bars as not met. The second was cut a few minutes in, when the desktop app the scoring had been started from was updated: 1,583 plans and all 25 stop cases could not start, so that run reports six bars as not met. A rule written down before the held-out run says that scoring cut for a reason outside the tool is run again. The third ran to its end, with the same caches and code, and is the result above. For the plans that did run, the first two scorings give the same verdict counts as the third, and no amount was judged `wrong` in any of the three.
- **Who chose the companies.** The eleven named companies were chosen by the author, who had read DART statements of all eleven in an earlier project. The twenty drawn ones were chosen by no one.
- **Both sides start from the same filing.** E4 tests the connection. It does not test whether a company tagged its statements correctly.
- **What is not in it.** The statement of changes in equity, which the script refuses. Rows without a standard account id (1,344 rows). The opening and closing cash rows of cash flow statements, which DART tags at a single date (826 amounts). Another 1,176 amounts had no fact for that period in the XBRL file and were left out.
- **No model is involved.** E4 gives the script correct locators. How well a model picks the right column when it writes the plan against DART was not measured in E4. E5 measures it.
- **What changed on the way, all before any held-out data was fetched.** On the development companies the author of the benchmark found that DART fills one comparison column of interim cash flow statements with two different periods. The contract had it wrong, and the script now refuses that column. Deliberately broken copies of the tool were scored to check the scorer. One got past the seven bars, and an eighth was added. A reviewer that had written none of the code tried to break the connection twice. What held up was fixed, apart from one limit that the contract now states: the rows of an answer do not say whether they are consolidated or separate. The [plan's change log](docs/eval-plan.md) has each step.

### E5, a model writes the plan against DART

E4 gave the script correct locators. E5 lets a model find them. Each document is a short Korean note about one report of one company, with 12 statements. Four of them carry a planted error: the three months and the cumulative figure exchanged, this period and the comparison period exchanged, consolidated and separate exchanged, an amount changed by 3% to 30%, or a wrong percent change or ratio (in 4 of the 18 of this kind, the right size with the wrong direction word). The truth comes from the companies' XBRL files, as in E4. The companies are E4's 31 held-out ones, and the script and the skill are those of version 0.2.0, unchanged.

The same model (Claude Haiku 5.5) ran both arms. With the skill it gets the document, the scan output and three tools: two stand in for the look-up commands and list companies, rows and columns but no amounts, and one returns the plan format. It writes the plan, the script judges, and it may correct the plan twice. After each check it reads the receipt, which shows the amounts of the cells the plan read. On its own it gets the document and the statements of the right report as text (the balance sheet, the income statements and the cash flow statement, every row with the amount columns the plan format lists), and reviews in one request.

| | Skill and script | Model alone |
|---|---|---|
| Planted errors flagged | 124 of 124 (95% interval 97.0% to 100%) | 120 of 124 (92.0% to 98.7%); 124 of 124 read by hand, see below |
| Correct statements flagged | 0 of 248 (0% to 1.5%) | 2 of 248 (0.2% to 2.9%) |
| Requests per document | 4 to 8, 5.7 on average | 1 |
| Cost per document | US$0.021 | US$0.0019 |

- **The model found the cells.** Of the 371 numbers the script judged, 367 were read from exactly the cell the sentence means. The other 4 read the same year-end balance from the annual report instead of the comparison column of the interim report: the same date and the same amount. One correct number was left `uncovered`: the model did not bind the negative amount `-88억원` in any of its three plans.
- **No plan was fitted to a number.** The model sent 10 corrected plans in 8 documents. In 5 of them the first plan put a cell outside `operands`, and the script refused 10 of its 12 numbers as not checkable. Between plans, 52 verdicts changed, all of them from `unverifiable` or `uncovered`. No number already judged `wrong` or `verified` changed.
- **The skill caught about as many.** Under the rule fixed before the run the skill flagged 124 of 124 planted errors and the model alone 120 of 124, with overlapping 95% intervals; read by hand, both caught all 124. The model alone was handed the right report and saw the amounts; with the skill it had to find the company, the report and the rows, and it cost about 11 times as much per document. What the skill adds here is the receipt for each number.
- **One reply could not be read.** One reply of the model alone was not valid JSON (a stray period). The rule fixed before the run says such a reply flags nothing, which gives 120 of 124. Read by hand, it names the four planted lines of its document and no other, which would give 124 of 124.
- **Where the model alone went wrong.** Its 4 misses are all in the reply that could not be read, an annual document of one of the 11 named companies. Its 2 false flags, both its own miscalculations, are in named companies too. On the 20 drawn companies both arms flagged 80 of 80 planted errors and 0 of 160 correct statements.
- **Some planted errors show without a source.** In 39 of the 124, the shown amount also stands on another line, the document states the inputs of the figure, or the amount is an input of a correct percent change or ratio whose other input the document states. With these left out the counts are 85 of 85 for the skill and 82 of 85 for the model alone (the three misses are in the reply that could not be read).

What to keep in mind: the notes are made from sentence templates that name the statements and the period in every sentence, so real notes are harder. The form `7~9월` was avoided because the scan counts its first number as a number of the document, which is a defect to fix. It is one model, one setting, one run. There were no pass bars. By kind, the model alone flagged a 21 of 21, b 30 of 31, c 26 of 27, d 26 of 27 and e 17 of 18; the skill flagged all of each. Before the run an agent that wrote none of the code reviewed the generator, the harness and the scorer, and its findings were fixed before any held-out document existed. All of E5 cost US$0.90 in 258 requests, two rehearsals included. The conversations, the tool calls and every plan are in [bench/e5/records](bench/e5/records).

### What was not measured

- Whether an agent picks up the skill when it is not named.
- Agents other than Claude Code, and the `/plugin` command typed inside the app (the command-line form was tested).
- A model writing plans against EDGAR. E2 used files only, and E5 covers DART.
- Real documents. Checks of real documents may appear here as examples. They are not performance estimates.

### Where the runs are

Every scoring of the tool is logged with its commit: [bench/runs.jsonl](bench/runs.jsonl) for E1, [bench/e2/runs.jsonl](bench/e2/runs.jsonl) for E2, [bench/edgar/runs.jsonl](bench/edgar/runs.jsonl) for E3, [bench/dart/runs.jsonl](bench/dart/runs.jsonl) for E4, [bench/e5/runs.jsonl](bench/e5/runs.jsonl) for E5. The development runs of E1 to E4 are there too; E5's two rehearsals were scored but not logged. For E1, E2 and E3 the code was frozen at commit `a84647c`. The held-out runs were made on later commits that differ from it only in results and logs. Versions 0.1.1 and 0.2.0 changed code after that. The held-out sets of E1, E2 and E3 were not run again: both versions were rerun on the development sets only, where they meet every bar of E1 and E3. For E4 the code was frozen at commit `2ae4742`, and its held-out scorings are logged at `f3a841c`, as `nogit` (git could not be run while the app was being updated) and at `79dafe1`. E5's held-out run was made on `15b4dc5` and its scoring is logged at `f74ce9e`. Scorings of deliberately broken copies of the tool are not in these files. The plan's change log reports them.

## Roadmap

Done in 0.1.0: the core script and its benchmark, the skill and plugin, the SEC EDGAR connection, and the three evaluations. Done in 0.1.1: one-day periods on EDGAR. Done in 0.2.0: Korean filings (DART) and their evaluation.

Done after 0.2.0, with the same script and skill: E5, a model writing plans against DART.

Next: an audit of non-numeric claims, PDF sources, a scan rule for Korean month ranges such as `7~9월`, and a model writing plans against EDGAR.

## License

MIT
