# receipts

Every number your agent writes comes with a receipt, or gets flagged.

> **Status: pre-release.** The checking script, the agent skill, the SEC EDGAR connection and the benchmark are here and the plugin installs from GitHub. The release numbers do not exist yet, so treat it as a preview.

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

Then ask Claude to check the numbers in a document against its source files, for example "use receipts to check the numbers in report.md against sales.csv". How reliably the skill is picked up when you do not name it has not been measured yet. The skill adds about 160 tokens to every session and about 2,900 when it runs (estimates from `claude plugin details`).

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

- The SEC asks automated clients to say who they are. Put your name and e-mail address in the environment variable `RECEIPTS_SEC_USER_AGENT`. Nothing is shipped as a default, and without it the script stops before making a request. The text goes to the SEC only.
- Each company is fetched once and cached under `.receipts/edgar/`. The receipt shows the fetch date. The script makes at most 5 requests a second (the SEC allows 10).
- Periods are exact. A quarter and the year to date end on the same day and differ in the start date, so one cannot pass for the other.
- When filings report different values for the same period (a restatement), the script abstains and lists them, unless the plan names the filing or picks the first or the latest report.
- `receipts.py edgar concepts AAPL --match revenue` and `receipts.py edgar facts AAPL us-gaap:Assets` print a company's concept names and period dates, which a plan needs.
- Segment, product and regional numbers are not in this API.

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
- [bench](bench): the planted-error benchmark generator and scorer, and [bench/edgar](bench/edgar), the builder and scorer for the EDGAR connection.
- [docs/design.md](docs/design.md) and [docs/eval-plan.md](docs/eval-plan.md): the design, and the evaluation plan that was fixed before any code was written.

## Where the evaluation stands

The plan in [docs/eval-plan.md](docs/eval-plan.md) has three parts. E1 and E3 have been run on their development sets only. E2 has not been run.

- **E1, the script alone with correct plans.** On the development seed (64 documents, 960 statements, 96 abstention cases) all 8 pass bars are met. Every run is logged in [bench/runs.jsonl](bench/runs.jsonl). These are development numbers. The numbers that count come from the held-out seed, which is run once on the release commit and has not been run.
- **E2, a model writing the plan.** Not run. E1 says the script reads and compares correctly when the plan is right. It says nothing about how well a model writes plans.
- **E3, SEC EDGAR.** Registered before the connection was written. Values the connection returns are compared with the numbers printed in the filings. On the 5 development companies (9 filings, 266 sampled facts) all 6 pass bars are met:
  - no fact was judged wrong;
  - with the filing named, 266 of 266 were verified;
  - with no filing named, 262 were verified and 4 abstained because filings report different values for that fact;
  - 180 of 180 locators that should match nothing were `unverifiable`, and 15 of 15 runs that could not read EDGAR stopped.

  Runs are logged in [bench/edgar/runs.jsonl](bench/edgar/runs.jsonl). These are development numbers. The 20 held-out companies are run once on the release commit and have not been fetched. Two things to know: the sixth bar was added after the first run, when a deliberately broken copy of the tool got past the first five (the [plan's change log](docs/eval-plan.md) has the details); and one development company, XOM, files under a new registrant with no 10-K yet, so there are 9 filings and not 10.

The benchmark documents are synthetic and built from sentence templates, half English and half Korean.

## Roadmap

1. ~~Core script and the planted-error benchmark.~~ Done.
2. ~~Skill and plugin packaging.~~ Done.
3. ~~SEC EDGAR connection.~~ Done. Its held-out evaluation runs at the release.
4. End-to-end evaluation with a model, the held-out run, then the first release.

Later: Korean filings (DART), an audit of non-numeric claims, PDF sources.

## License

MIT
