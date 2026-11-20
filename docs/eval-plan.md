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
