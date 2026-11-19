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

A crash, a missing claim or any other verdict counts as a failure of the measure it belongs to.

Reported without a bar: the share of open claims that abstain because filings disagree; same-end pairs with both members `verified`; results by company, form, unit, scale and sign; facts whose displays differ; eligible facts and skipped tags by reason.

### Procedure

- Development companies may be run as often as needed. Every scorer run is logged with the commit hash in `bench/edgar/runs.jsonl`.
- The held-out companies are run once, on the commit that is to be released, together with E1's held-out seed. If a held-out bar fails, the README says so.
- EDGAR is live. A run on a later day may sample newer filings, so results name their filings.
- Fair access. The SEC asks automated clients to identify themselves and allows 10 requests per second. The person who runs E3 supplies the contact string through an environment variable. None is shipped in the repository or written into results. The builder and the tool make at most 5 requests per second, cache what they fetch and do not fetch it twice. A run needs about four requests per company.

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
