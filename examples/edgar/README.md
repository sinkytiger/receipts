# Example: a note checked against SEC filings

`report.md` is a short note on Apple's fiscal 2026. One figure in it is wrong on purpose. `claims.json` says which fact in Apple's filings each number comes from.

Run it without a network connection, from the snapshot in this folder:

```sh
python skills/receipts/scripts/receipts.py check examples/edgar/claims.json --edgar-cache examples/edgar/snapshot
```

The first line of the result:

```
7 numbers / 6 verified / 1 wrong / 0 unverifiable / 0 uncovered   (skipped: 1 date, 1 identifier, 3 year)
```

The note says research and development was 10.6% of revenue. Recomputed from the two filed figures it is 9.6%.

## The snapshot

`snapshot/` is a cut-down copy of what the SEC's API returned on 2026-11-20: Apple's line in the ticker list, and five concepts from Apple's company facts with their annual-report rows. Values were not edited. `snapshot/fetched.json` holds the fetch date that the receipt shows.

## Reading the live data

Set `RECEIPTS_SEC_USER_AGENT` to your name and e-mail address (the SEC asks automated clients to say who they are) and leave out `--edgar-cache`:

```sh
python skills/receipts/scripts/receipts.py check examples/edgar/claims.json
```

The script then fetches Apple's facts once into `examples/edgar/.receipts/edgar/` and checks against those.
