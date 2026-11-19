# receipts

Every number your agent writes comes with a receipt, or gets flagged.

> **Status: pre-release.** The checking script, the agent skill and the benchmark are here and the plugin installs from GitHub. The release numbers do not exist yet, so treat it as a preview.

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

Then ask Claude to check the numbers in a document against its source files, for example "use receipts to check the numbers in report.md against sales.csv". How reliably the skill is picked up when you do not name it has not been measured yet. The skill adds about 150 tokens to every session and about 2,300 when it runs (estimates from `claude plugin details`).

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

Sources that work today: CSV, TSV, JSON, Markdown tables, plain text and Excel values. SEC EDGAR filings are planned for the first release.

## What is here now

- [skills/receipts/SKILL.md](skills/receipts/SKILL.md): the skill. It tells an agent how to write the plan, run the script and report the result without overstating it.
- [skills/receipts/scripts/receipts.py](skills/receipts/scripts/receipts.py): `scan`, `check` and `report`. Standard library only.
- [skills/receipts/references/plan-format.md](skills/receipts/references/plan-format.md): the plan format, the rules for reading and comparing numbers, and the outputs. The script and the benchmark both follow it.
- [examples/quickstart](examples/quickstart): a short report, its source and a plan.
- [tests](tests): unit tests. Run `python -m unittest discover -s tests`.
- [bench](bench): the planted-error benchmark generator and scorer.
- [docs/design.md](docs/design.md) and [docs/eval-plan.md](docs/eval-plan.md): the design, and the evaluation plan that was fixed before any code was written.

## Where the evaluation stands

The plan in [docs/eval-plan.md](docs/eval-plan.md) has three parts. Only the first has been run, and only on the development seed.

- **E1, the script alone with correct plans.** On the development seed (64 documents, 960 statements, 96 abstention cases) all 8 pass bars are met. Every run is logged in [bench/runs.jsonl](bench/runs.jsonl). These are development numbers. The numbers that count come from the held-out seed, which is run once on the release commit and has not been run.
- **E2, a model writing the plan.** Not run. E1 says the script reads and compares correctly when the plan is right. It says nothing about how well a model writes plans.
- **E3, SEC EDGAR.** Not built.

The benchmark documents are synthetic and built from sentence templates, half English and half Korean.

## Roadmap

1. ~~Core script and the planted-error benchmark.~~ Done.
2. ~~Skill and plugin packaging.~~ Done.
3. SEC EDGAR connection.
4. End-to-end evaluation with a model, the held-out run, then the first release.

Later: Korean filings (DART), an audit of non-numeric claims, PDF sources.

## License

MIT
