# receipts

Every number your agent writes comes with a receipt, or gets flagged.

> **Status: 0.1.0, the first release.** A checking script, an agent skill, a connection to SEC EDGAR, and three evaluations whose rules were written down before they were run. The results, with their limits, are [below](#what-the-evaluation-shows).

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

Then ask Claude to check the numbers in a document against its source files, for example "use receipts to check the numbers in report.md against sales.csv". How reliably the skill is picked up when you do not name it has not been measured yet. The skill adds about 160 tokens to every session and about 3,000 when it runs (estimates from `claude plugin details`).

Both forms were tested as `claude plugin install ...` on the command line, from a clean configuration, with Claude Code 2.1.326 on Windows. The skill folder is plain Markdown plus one Python script, so other agents that read skills can use `skills/receipts` as it is. That has not been tested.

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

Sources that work today: CSV, TSV, JSON, Markdown tables, plain text, Excel values, and what US-listed companies report to the SEC.

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
- A fact whose period is one day, such as a dividend paid on one date, is stored by the SEC under that single date. Ask for it as one date, not as a range.

The rules are in sections 2 and 8 of [plan-format.md](skills/receipts/references/plan-format.md).

[examples/edgar](examples/edgar) is a short note on Apple's fiscal 2026 with one wrong figure. It runs without a network connection, from a cut-down copy of what the SEC returned on 2026-11-20:

```sh
python skills/receipts/scripts/receipts.py check examples/edgar/claims.json --edgar-cache examples/edgar/snapshot
```

```
7 numbers / 6 verified / 1 wrong / 0 unverifiable / 0 uncovered   (skipped: 1 date, 1 identifier, 3 year)
```

The note says research and development was 10.6% of revenue. Recomputed from the two filed figures it is 9.6%.

## What is here now

- [skills/receipts/SKILL.md](skills/receipts/SKILL.md): the skill. It tells an agent how to write the plan, run the script and report the result without overstating it.
- [skills/receipts/scripts/receipts.py](skills/receipts/scripts/receipts.py): `scan`, `check` and `report`. Standard library only.
- [skills/receipts/references/plan-format.md](skills/receipts/references/plan-format.md): the plan format, the rules for reading and comparing numbers, and the outputs. The script and the benchmark both follow it.
- [examples/quickstart](examples/quickstart): a short report, its source and a plan. [examples/edgar](examples/edgar): a note checked against SEC filings.
- [tests](tests): unit tests. Run `python -m unittest discover -s tests`.
- [bench](bench): the planted-error benchmark generator and scorer; [bench/edgar](bench/edgar), the builder and scorer for the EDGAR connection; [bench/e2](bench/e2), the harness, scorer, spending record and conversations of the evaluation with a model.
- [docs/design.md](docs/design.md) and [docs/eval-plan.md](docs/eval-plan.md): the design, and the evaluation plan. E1 was fixed before any code existed, E3 before the connection was written, and E2's details before its harness.

## What the evaluation shows

The rules for each evaluation were fixed in [docs/eval-plan.md](docs/eval-plan.md) before it was run. The code was frozen in one commit, and each held-out set was then run once.

| | Question | Held-out result |
|---|---|---|
| **E1** | Given a correct plan, does the script judge correctly? 64 synthetic documents, 960 statements, 288 with a planted error. | All 8 pass bars met. No planted error was accepted and no correct statement was flagged. |
| **E2** | What happens when a model writes the plan? Claude Haiku 5.5, 30 synthetic documents, 128 planted errors, 322 correct statements. | With the skill and the script: 128 of 128 planted errors flagged, 2 of 322 correct statements flagged. The same model reviewing on its own: 125 of 128 and 26 of 322 (118 of 128 under the matching rule fixed beforehand, which undercounts it). |
| **E3** | Does the EDGAR connection return what the filing prints? 20 companies, 40 filings, 1,169 facts. | All 6 pass bars met. No wrong value. 1,167 of 1,169 verified when the filing is named. |

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
- The two that were not verified are dividends per share paid on a single day. The filing tags each as a period of one day, and the SEC's API stores such a fact under a single date, so the exact-period rule finds nothing. The script abstained. Until a later version handles this, ask for a one-day period as one date.
- 800 of 800 locators that should match nothing were `unverifiable`, and 15 of 15 runs with no contact string, a corrupt cached file or an unreachable server stopped.

Both sides of this comparison start from the same submission by the company. E3 tests the connection, not whether a company tagged its filing correctly. The sixth bar was added after the first development run, when a deliberately broken copy of the tool got past the first five. The [plan's change log](docs/eval-plan.md) has the details.

### What was not measured

- Whether an agent picks up the skill when it is not named.
- Agents other than Claude Code, and the `/plugin` command typed inside the app (the command-line form was tested).
- Real documents. Checks of real documents may appear here as examples. They are not performance estimates.

### Where the runs are

Every run is logged with its commit: [bench/runs.jsonl](bench/runs.jsonl) for E1, [bench/e2/runs.jsonl](bench/e2/runs.jsonl) for E2, [bench/edgar/runs.jsonl](bench/edgar/runs.jsonl) for E3. The development runs are there too. The code was frozen at commit `a84647c`. The held-out runs were made on later commits that differ from it only in results and logs.

## Roadmap

Done in 0.1.0: the core script and its benchmark, the skill and plugin, the SEC EDGAR connection, and the three evaluations.

Next: one-day periods on EDGAR, Korean filings (DART), an audit of non-numeric claims, PDF sources.

## License

MIT
