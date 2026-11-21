# Example: a Korean note checked against DART filings

`report.md` is a short note in Korean on Samsung Electronics' third quarter of 2026. One figure in it is wrong on purpose. `claims.json` says which amount in the company's third-quarter report each number comes from.

Run it without a network connection and without a key, from the snapshot in this folder:

```sh
python skills/receipts/scripts/receipts.py check examples/dart/claims.json --dart-cache examples/dart/snapshot
```

The first line of the result:

```
7 numbers / 6 verified / 1 wrong / 0 unverifiable / 0 uncovered   (skipped: 7 date, 1 year)
```

The note says the third-quarter net profit was 194.0조원. That is the figure for January to September. The three months alone were 75.1조원. A quarterly report carries both, in two columns of the same row (`thstrm_amount` and `thstrm_add_amount`), and the claim names the three-month column because the sentence says "3분기".

## The snapshot

`snapshot/` is a cut-down copy of what OpenDART returned on 2026-11-21: four rows of the consolidated statements in Samsung Electronics' third-quarter report (receipt number 20261116001482), and the company's line of the company code file. The full answer has 223 rows and the code file about 120,000 companies. Values were not edited. `snapshot/fetched.json` holds the fetch date that the receipt shows.

## Reading the live data

Get a free key at [opendart.fss.or.kr](https://opendart.fss.or.kr), set `RECEIPTS_DART_KEY` to it and leave out `--dart-cache`:

```sh
python skills/receipts/scripts/receipts.py check examples/dart/claims.json
```

The script then fetches the report once into `examples/dart/.receipts/dart/` and checks against that. The key is sent to DART only. It is not written to the cache or to the receipt.
