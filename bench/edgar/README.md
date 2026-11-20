# bench/edgar: E3 benchmark (SEC EDGAR connection)

Implements E3 of [`docs/eval-plan.md`](../../docs/eval-plan.md). Standard library only, Python 3.9+.
Written from the plan and the contract ([`plan-format.md`](../../skills/receipts/references/plan-format.md)) without
reading the tool. The builder reads filings (inline XBRL under `www.sec.gov/Archives`); it never reads `companyfacts`,
which is the tool's data path.

## Run

```sh
export RECEIPTS_SEC_USER_AGENT="<your name and e-mail, as the SEC asks>"   # never written to any file
PYTHONUTF8=1 python bench/edgar/build.py          # development: AAPL MSFT JPM WMT XOM -> bench/edgar/out/dev/
PYTHONUTF8=1 python bench/edgar/evaluate.py       # runs skills/receipts/scripts/receipts.py on it
```

Held-out (once, on the release commit): `build.py --held-out`, then `evaluate.py --set heldout --held-out`.
A held-out ticker is refused without `--held-out`, before any request.

`build.py` options: `--tickers AAPL,MSFT` (development tickers only), `--cache DIR` (default `bench/edgar/cache/`),
`--out DIR` (default `bench/edgar/out/`), `--offline` (build only from cached files; stop if one is missing),
`--fetch-only` (see below), `--self-test`. `evaluate.py` options: `--set dev|heldout`, `--held-out`, `--tool PATH`, `--tool-cache DIR`,
`--out DIR`, `--results-dir DIR`, `--runs FILE`, `--timeout SEC` (per tool call, default 600).

Fair access: every request sends `User-Agent` from `RECEIPTS_SEC_USER_AGENT` (no default; empty means exit 2 with no
request) and `Accept-Encoding: gzip`, at most 5 requests per second, 30 s timeout, one retry after 2 s on a timeout,
5xx or 429, otherwise exit 2. Every response is cached under `bench/edgar/cache/` (gitignored) and never fetched again.
A build makes 1 + 3 requests per company. The scorer passes its environment to the tool, which needs the same variable
for its first fetch of each company.

**Fetch once, build offline.** `build.py --fetch-only` (with `--held-out` or `--tickers` as for a build) puts into
the cache exactly the files a build reads: the ticker list, each company's filing list and the primary documents of
its most recent inline-XBRL 10-K and 10-Q, with the same pacing, retry and User-Agent rules. It parses nothing,
writes nothing under `--out`, prints one line per file (`cached already` / `fetched`) and the number of requests
made. A company without such a filing is reported and skipped. A failed request stops with exit 2; files fetched so
far stay in the cache. It cannot be combined with `--offline` or `--self-test`. Afterwards `build.py --offline`
needs no network and no contact string.

Exit codes. `build.py`: 0 every filing built (or fetched, with `--fetch-only`), 1 at least one filing could not be
built (parsing or case-building raised an error: it is listed under `missing` in the manifest with the exception
type and message, and the other filings are built), 2 stopped (a fetch problem stops at once). `evaluate.py`: 0 all six bars pass, 1 any fails, 2 the cases or the tool
are missing, or the held-out set was asked for without `--held-out`.

## What is written where

`bench/edgar/out/<dev|heldout>/` (gitignored):

- `manifest.json`: fetch date, per company the CIK and name, per filing form, accession, filing and report date,
  document URL, the date the document was cached, counts and skipped tags; filings not found.
- `<TICKER>-<form>/document.md`: one line per sampled fact, e.g. ``- `us-gaap:Assets` at 2026-06-27: 364,980 million``.
- `<TICKER>-<form>/pinned.json`, `open.json`, `abstain-concept.json`, `abstain-period.json`, `abstain-instant.json`,
  `abstain-unit.json`: the plans. `gold.json`: per sampled fact its claim id, line, concept, unit, period, displayed
  text, written number, scale, sign, value, all displays, whether they differ, pair id; the pairs; the abstention
  locators; eligible facts and skipped tags by reason.
- `tool-cache/`: the tool's EDGAR cache for normal runs. `<TICKER>-<form>/receipts-<plan>.json`: the tool's last answer.

Committed: `bench/edgar/results/<set>-<commit>-<UTC>.json` and `.md`, and one line per scorer run in
`bench/edgar/runs.jsonl` (UTC time, set, commit, dirty flag, counts, measures, pass per bar, accession numbers).

## Choices the plan leaves to the builder and scorer

- **Filing.** In `filings.recent`, the entry with form exactly `10-K` (or `10-Q`) and `isInlineXBRL` = 1 with the
  latest `filingDate` (first in the list on a tie). Older pages of the filing list are not read.
- **Parsing.** Tags, attributes and context/unit elements are matched on local names, case-insensitively, any prefix.
  The displayed text is the text content with inner tags removed, entities decoded, outer whitespace stripped; a
  nested tag gives each enclosing fact the same text. `us-gaap` is the prefix bound to a `fasb.org/us-gaap` namespace
  (or the literal prefix when no binding is found). A context has a dimension when it holds `segment` or `scenario`.
  Units resolve through the unit definition: `iso4217:USD` is `USD`, `xbrli:shares` is `shares`, their division is
  `USD/shares`; everything else (pure, other currencies, custom) is skipped as `unit`.
- **Plain number.** Formats `ixt:num-dot-decimal`, `ixt:numdotdecimal` or none, with text `1234`, `1,234` (groups of
  three) and an optional decimal part. A zero format is `fixed-zero` or any `zerodash` variant, and only a dash-like
  text counts (otherwise `zero_format_not_dash`). Other formats are skipped as `format` and listed by name.
- **Skip order** (first reason wins, one per tag): `nil`, `no_concept`, `not_us_gaap`, `context_missing`,
  `dimension`, `no_period`, `unit`, `not_plain_number` / `zero_format_not_dash` / `format`, `bad_scale`.
- **Gold display.** Quantum = 10^(scale - digits after the decimal point); the smallest quantum wins, the first in
  document order on a tie; a dash zero has quantum 10^scale. A fact's displays differ when their values after scale
  and sign are not all equal.
- **Scale.** An eligible fact whose gold display has a scale other than 0, 3, 6, 9 cannot be written with a scale
  word. It stays in the eligible count but is left out of the sample, and is counted as `excluded_scale`.
- **Sample.** Eligible, writable facts in order of first appearance. `random.Random(accession)` draws
  `sample(facts, min(25, n))`, then, with the same generator, `sample(pairs, min(5, n))` from the same-end pairs in
  order of their first member. A pair is the two earliest-appearing durations of one concept, unit and end date
  (instants have no start date and form no pairs). Sample order: the 25 in draw order, then the members of the drawn
  pairs not already in it, pair by pair. Fact ids `f01`... follow that order and equal the line numbers.
- **Document line.** Concept in backticks, dates as `YYYY-MM-DD`, then the number: the displayed digits with commas,
  a scale word (`thousand`, `million`, `billion`; none for 0), a leading `-` when the tag has `sign="-"`, and `0` for
  a zero (no sign, no scale word).
- **Plans.** `kind: lookup`, `operands.of` = EDGAR locator with `unit` always given; `quote` is the line without its
  leading `- `; `number` is the written number including its sign and scale word. Company: ticker in 10-K plans, CIK
  digits without leading zeros in 10-Q plans.
- **Abstention.** From the sample in sample order: the first 5 facts with `Zz` added to the concept; the first 5 with
  the end date (or the instant) moved three days later; the first 5 durations asked as their end date; the first 5
  facts in USD or shares with the two swapped. The locators keep the filing's accession (the pinned form), so the case
  cannot be answered by another filing. Each kind is its own plan file, so no plan claims a number twice.
- **Scoring.** A plan whose `check` crashes, times out, exits 2 or leaves no valid `receipts.json` gives every claim in
  it the verdict `error`; a claim absent from `claims` is `missing` (or `unbound` when listed there). Measure 3 counts
  `unverifiable` with reason `edgar_versions_differ` as correct only when the tool's cached
  `companyfacts/CIK##########.json` holds at least two different values for that concept, unit (case-insensitive) and
  exact period (an instant is a row without `start`). Measure 1 counts claims (pinned and open together).
- **Measure 6** (added by the plan's amendment of 2026-11-20). A sampled fact is in it when the tool's cached company
  facts hold two or more different values for its concept, unit and exact period (the same test as the confirmation
  in measure 3). It passes only when its open claim is `unverifiable` with reason `edgar_versions_differ` and its
  pinned claim is `verified`. A run with no such fact shows `n/a (0 cases)`, records `"cases": 0`, and counts the
  measure as passing. The results list every fact of the measure with both verdicts.
- **Stop cases.** The first 5 filings of the manifest, pinned plan, each in a fresh temporary cache: (a) no contact
  string; (b) a broken `companyfacts` file for the company, plus a copy of the normal tool cache's ticker list when the
  plan names a ticker; (c) the placeholder contact string `receipts-bench stop-case (no request is expected to
  succeed)` and both EDGAR address variables pointing at a local port that was bound and closed.
- Stored tool stderr has the contact string replaced, should the tool ever print it.

## Self-test (offline)

```sh
PYTHONUTF8=1 python bench/edgar/build.py --self-test           # parser, sample, cases, fetch rules (fake opener)
PYTHONUTF8=1 python bench/edgar/testdata/run_selftest.py       # offline build + scorer with stub tools
```

`testdata/` holds a synthetic inline-XBRL document, a synthetic ticker list, filing-list template and company facts,
a stub tool that answers from the company facts, and seven broken stubs (always `verified`; a wrong value; never exit 2;
open claims always `edgar_versions_differ`; pinned claims never found; the latest report taken silently when filings
differ; the accession pin ignored when filings differ). `run_selftest.py` asserts that the good stub
passes all six bars and each broken stub fails exactly the bars it should. It writes only under
`bench/edgar/out/selftest/` and logs to a `runs.jsonl` there.
