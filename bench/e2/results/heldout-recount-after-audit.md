# E2 held-out: a recount of arm B after an audit

This file is a note added after the held-out run. It changes no rule and no logged result.

## Why

The rule fixed before the run credits arm B with a statement when its reply lists a wrong number on the statement's line and the flagged text contains the statement's number. An independent audit of the release found that this undercounts arm B. Seven times arm B flagged a planted direction error on the right line and named the wrong word instead of the number. The rule counted those seven as misses, and the scorer reported them as "flags that match no statement".

## The recount

Every benchmark statement sits on its own line, and all 151 of arm B's flags are on statement lines. Counting any flag on the statement's line:

| | Rule fixed beforehand | Any flag on the statement's line |
|---|---|---|
| Planted errors flagged by arm B | 118 of 128 (86.2% to 95.7%) | 125 of 128 (93.3% to 99.2%) |
| Correct statements flagged by arm B | 26 of 322 (5.6% to 11.6%) | 26 of 322 (5.6% to 11.6%) |

The three planted errors arm B did not flag under either rule are rounding errors. Arm A's numbers do not depend on this rule: they are the script's verdicts.

## The seven statements

All are planted direction errors. The files are under `bench/e2/records/heldout/<document>/B/`.

| Document | Line | Number in the statement | What arm B flagged |
|---|---|---|---|
| d026 | 12 | 378 | decreased |
| d007 | 9 | 410억 | 증가했다 |
| d007 | 18 | 158 | 떨어졌다 |
| d016 | 14 | 491 | 늘었다 |
| d016 | 18 | 432 | 줄었다 |
| d024 | 21 | 1.3%p | 올랐다 |
| d029 | 13 | 18.9%p | 늘었다 |

## A note on the records

In `bench/e2/records/`, a `result.json` of arm A names the accepted plan's result as `planN/receipts.json`, its path in the run folder. In the records the same file is `planN.check.json`.
