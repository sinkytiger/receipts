# receipts

Every number your agent writes comes with a receipt, or gets flagged.

> **Status: not usable yet.** This repository holds the design and the evaluation plan. They were committed before any code or result existed. There is nothing to install.

[한국어](README.ko.md)

## The problem

An agent writes a report and says the numbers were checked. Often they were not, or only some were. A reader cannot tell which.

## The idea

The model proposes, code decides.

1. Code finds every number in the document. That count is the denominator.
2. The model writes a plan: for each number, the sentence it is in, the source cell it comes from, and the formula. The plan holds locations, not values.
3. Code reads the sources, recomputes, and gives each number a verdict: `verified`, `wrong`, or `unverifiable`.
4. Numbers the plan left out are listed as `uncovered`.

The planned output looks like this:

```
41 numbers / 33 verified / 3 wrong / 2 unverifiable / 3 uncovered   (skipped: 12 dates, 2 versions)

| line | sentence                          | written | source                    | recomputed | verdict |
| 18   | Revenue grew 12.4% year over year | 12.4%   | sales.csv rows 2025, 2024 | 11.9%      | wrong   |
```

Planned sources for the first release: CSV, TSV, JSON, Markdown tables, plain text, Excel values, and SEC EDGAR filings. The script will use only the Python standard library.

## What is here now

- [docs/design.md](docs/design.md): how it is meant to work, and what it will not do.
- [docs/eval-plan.md](docs/eval-plan.md): what will be measured and which results count as a pass, fixed before the code is written.

## Roadmap

1. Core script (`scan`, `check`, report) and the planted-error benchmark.
2. Skill and plugin packaging.
3. SEC EDGAR connection.
4. End-to-end evaluation with a model, then the first release.

Later: Korean filings (DART), an audit of non-numeric claims, PDF sources.

## License

MIT
