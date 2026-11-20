# Changelog

## 0.1.1 (2026-11-20)

- **EDGAR: a period of one day is the single date.** `2026-09-22..2026-09-22` and `2026-09-22` now name the same fact. In 0.1.0 the range form found nothing, because the SEC's API stores a one-day period under a single date. The two facts that 0.1.0 could not verify in the held-out E3 run were of this kind. Two things follow from the rule: a balance asked as a one-day range now matches too, and a single date also matches a fact stored with equal start and end dates.
- **E2 scorer.** It also reports how many statements the lone reviewer flagged when any flag on the statement's line counts. The count under the rule fixed before the run is unchanged and still reported.
- **E3 builder.** One-day periods are left out of the abstention cases that ask for a duration as one date. The SEC's API stores such a fact under that date, so the tool finds it, as 0.1.0 already did: it was never a valid abstention case. No such case had been built, in the development set or the held-out set.
- **E2 records.** Records exported by the harness now name the accepted plan's file as it is stored in the records folder.
- **README.** The token estimate, and what changed since 0.1.0.

How 0.1.1 was checked. The held-out sets were used once, on 0.1.0, and are not run again. On the development sets 0.1.1 meets all 8 bars of E1 and all 6 bars of E3 (the run logs name commit `f5f63d1` for the E1 run and `bc8e36d` for the E3 run). The two one-day facts from the held-out E3 run were tried again on their own, outside the scored runs, and both now verify. The unit tests pass (52).

The held-out results in the README were measured on 0.1.0, with the code frozen at commit `a84647c`.

## 0.1.0 (2026-11-20)

First release: the checking script, the skill, the SEC EDGAR connection, and the held-out results of E1, E2 and E3.
