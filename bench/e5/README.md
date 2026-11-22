# bench/e5: a model writes the plan against DART

The rules are in [docs/eval-plan.md](../../docs/eval-plan.md), section E5 and "E5 details". E4 gave the script correct
locators. E5 lets a model find them: it writes the plan for a short Korean note about one report of one company, and
the script checks it (arm A). The same model reviewing alone, with the statements in front of it, is the baseline
(arm B).

| File | What it does |
|---|---|
| `generate.py` | Writes the documents from the XBRL gold of E4's sets (`bench/dart/out/<dev|heldout>/`). Reads neither the script nor DART. |
| `run.py` | Checks the gold plans, estimates the cost without a request, and runs the two arms. Every request goes to `spend.jsonl`. |
| `score.py` | Counts what each arm did with the planted errors and the correct statements. Makes no request. |

```sh
PYTHONUTF8=1 python bench/e5/generate.py                          # development documents -> bench/e5/out/dev/
PYTHONUTF8=1 python bench/e5/run.py --check-gold                  # the gold plans through the script, no request
PYTHONUTF8=1 python bench/e5/run.py --rehearsal --dry-run         # prompt sizes and a cost estimate, no request
PYTHONUTF8=1 python bench/e5/run.py --rehearsal --fake            # the gold plan stands in for the model, no request
PYTHONUTF8=1 python bench/e5/run.py --rehearsal --approved-estimate 0.05 --key-file <file with the API key>
PYTHONUTF8=1 python bench/e5/score.py bench/e5/out/dev/run-<UTC>
```

The held-out run (`generate.py --held-out`, then `run.py --held-out`) is made once, on the commit that fixes the
prompts, the script and the skill. It needs E4's built sets and tool cache on disk: `bench/dart/out/` is not in the
repository, because it holds DART's answers. The documents that were run, their gold files and the conversations are
copied to `bench/e5/records/`.

The script under test runs with no DART key, from a copy of E4's tool cache. Paid runs need the `anthropic` package
and an API key, and stop at the cap in `run.py`.
