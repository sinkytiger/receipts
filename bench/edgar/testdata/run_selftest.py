#!/usr/bin/env python3
"""Offline self-test of the E3 builder and scorer. No request leaves the machine.

  PYTHONUTF8=1 python bench/edgar/testdata/run_selftest.py

1. build.py --self-test (parser, sample, cases, fetch rules with a fake opener).
2. A synthetic cache (ticker list, filing lists, the synthetic document as every primary document) is written under
   bench/edgar/out/selftest/, and build.py --offline builds the cases from it.
3. evaluate.py runs on those cases with the stub tool and with each broken stub, logging to
   bench/edgar/out/selftest/ (never to bench/edgar/runs.jsonl), and the expected pass/fail per bar is asserted.
"""
import json
import os
import subprocess
import sys
import tempfile

TD = os.path.dirname(os.path.abspath(__file__))
EDGAR = os.path.dirname(TD)
ROOT = os.path.dirname(os.path.dirname(EDGAR))
PY = sys.executable
TEST = os.path.join(EDGAR, "out", "selftest")
TICKERS = ("AAPL", "MSFT", "JPM")
PLACEHOLDER = "receipts-selftest offline placeholder"

# stub file -> bars expected to fail
EXPECT = {"stub_tool.py": set(), "stub_always_verified.py": {4, 6}, "stub_wrong_value.py": {1, 2, 3, 6},
          "stub_never_exit2.py": {5}, "stub_fake_differ.py": {3}, "stub_pinned_abstain.py": {2, 6},
          "stub_latest_on_differ.py": {6}, "stub_ignore_pin_on_differ.py": {2, 6}}


def run(args, env=None):
    e = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    e.update(env or {})
    p = subprocess.run([PY] + args, capture_output=True, env=e, cwd=ROOT)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def check(cond, what):
    if not cond:
        sys.exit(f"SELF-TEST FAILED: {what}")
    print(f"ok  {what}")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_src(src, spec, tickers, ciks, manifest):
    """Synthetic company facts per company: each fact once per filing, each restated row under an older accession."""
    write(os.path.join(src, "company_tickers.json"), json.dumps(tickers))
    for t in TICKERS:
        cik = ciks[t]
        accs = [f["accession"] for f in manifest["filings"] if f["ticker"] == t]
        facts = {}
        for rows, accns in ((spec["facts"], accs), (spec["restated"], [f"{cik:010d}-24-000050"])):
            for concept, unit, start, end, val in rows:
                tax, tag = concept.split(":")
                lst = facts.setdefault(tax, {}).setdefault(tag, {"label": tag, "units": {}})["units"].setdefault(
                    unit, [])
                for accn in accns:
                    row = {"end": end, "val": val, "accn": accn, "fy": 2026, "fp": "Q3", "form": "10-Q",
                           "filed": "2026-07-31"}
                    if start:
                        row["start"] = start
                    lst.append(row)
        write(os.path.join(src, "companyfacts", f"CIK{cik:010d}.json"),
              json.dumps({"cik": cik, "entityName": f"Synthetic {t}", "facts": facts}))


def main():
    build, evaluate = os.path.join(EDGAR, "build.py"), os.path.join(EDGAR, "evaluate.py")
    code, out, err = run([build, "--self-test"])
    check(code == 0, "build.py --self-test: " + (out.strip().splitlines() or [err])[-1])

    os.makedirs(TEST, exist_ok=True)
    work = tempfile.mkdtemp(prefix="run-", dir=TEST)
    cache = os.path.join(work, "cache")
    with open(os.path.join(TD, "company_tickers.json"), encoding="utf-8") as fh:
        tickers = json.load(fh)
    write(os.path.join(cache, "www.sec.gov", "files", "company_tickers.json"), json.dumps(tickers))
    with open(os.path.join(TD, "submissions-template.json"), encoding="utf-8") as fh:
        template = fh.read()
    with open(os.path.join(TD, "synthetic-10q.htm"), encoding="utf-8") as fh:
        doc = fh.read()
    ciks = {v["ticker"]: v["cik_str"] for v in tickers.values()}
    for t in TICKERS:
        cik = ciks[t]
        sub = template.replace("CIKNUM", str(cik)).replace("CIK10", f"{cik:010d}").replace("TICKER", t)
        write(os.path.join(cache, "data.sec.gov", "submissions", f"CIK{cik:010d}.json"), sub)
        for acc, name in ((f"{cik:010d}-26-000020", "syn-20260627.htm"), (f"{cik:010d}-25-000079",
                                                                           "syn-20250927.htm")):
            write(os.path.join(cache, "www.sec.gov", "Archives", "edgar", "data", str(cik), acc.replace("-", ""),
                               name), doc)

    no_ua = {"RECEIPTS_SEC_USER_AGENT": ""}
    code, out, err = run([build, "--offline", "--cache", cache, "--out", work, "--tickers", ",".join(TICKERS)], no_ua)
    check(code == 0, "offline build from a pre-filled cache, no contact string needed")
    code, _, err = run([build, "--offline", "--cache", cache, "--out", work, "--tickers", "WMT"], no_ua)
    check(code == 2, "offline build stops (exit 2) when a company is not in the cached files")
    code, _, err = run([build, "--cache", os.path.join(work, "empty-cache"), "--out", work], no_ua)
    check(code == 2 and "No request was made" in err, "online build without a contact string stops before a request")
    code, _, err = run([build, "--held-out", "--tickers", "AAPL", "--cache", cache, "--out", work])
    check(code == 2, "--tickers refused with --held-out")
    dev = os.path.join(work, "dev")
    with open(os.path.join(dev, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    forms = [(f["ticker"], f["form"], f["accession"]) for f in manifest["filings"]]
    check(forms == [(t, form, f"{ciks[t]:010d}-{'25-000079' if form == '10-K' else '26-000020'}")
                    for t in TICKERS for form in ("10-K", "10-Q")],
          "picks the newest inline 10-K and 10-Q, not 10-K/A, not a non-inline 10-Q")
    check(all(f["counts"]["sampled_facts"] == 23 and f["counts"]["sampled_pairs"] == 4 for f in manifest["filings"]),
          "23 sampled facts and 4 pairs per filing")
    with open(os.path.join(dev, "AAPL-10-K", "pinned.json"), encoding="utf-8") as fh:
        k_plan = json.load(fh)
    with open(os.path.join(dev, "AAPL-10-Q", "open.json"), encoding="utf-8") as fh:
        q_plan = json.load(fh)
    check(k_plan["claims"][0]["operands"]["of"]["edgar"] == "AAPL"
          and q_plan["claims"][0]["operands"]["of"]["edgar"] == str(ciks["AAPL"]), "ticker in 10-K, CIK in 10-Q")

    # synthetic "SEC" source for the stub: company facts per company, from facts.json
    with open(os.path.join(TD, "facts.json"), encoding="utf-8") as fh:
        spec = json.load(fh)
    src = os.path.join(work, "edgar-src")
    write_src(src, spec, tickers, ciks, manifest)
    src_same = os.path.join(work, "edgar-src-no-restatement")
    write_src(src_same, dict(spec, restated=[]), tickers, ciks, manifest)

    env = {"RECEIPTS_SEC_USER_AGENT": PLACEHOLDER, "STUB_EDGAR_SRC": src, "RECEIPTS_EDGAR_DATA_URL": "",
           "RECEIPTS_EDGAR_WWW_URL": "", "RECEIPTS_EDGAR_CACHE": ""}
    results, runs = os.path.join(work, "results"), os.path.join(work, "runs.jsonl")
    for stub, fails in EXPECT.items():
        tool_cache = tempfile.mkdtemp(prefix="tool-cache-", dir=work)
        code, out, err = run([evaluate, "--out", work, "--tool", os.path.join(TD, stub), "--tool-cache", tool_cache,
                              "--results-dir", results, "--runs", runs, "--timeout", "60"], env)
        path = out.strip().splitlines()[-1].split("results: ", 1)[-1] if out.strip() else ""
        check(os.path.isfile(path), f"{stub}: results written ({err.strip()[-300:]})")
        with open(path, encoding="utf-8") as fh:
            r = json.load(fh)
        failed = {m["n"] for m in r["measures"] if not m["ok"]}
        check(failed == fails and code == (1 if fails else 0),
              f"{stub}: bars failing {sorted(failed)} (expected {sorted(fails)}), exit {code}")
        if stub == "stub_tool.py":
            od = r["reported"]["open_abstaining_because_filings_differ"]
            check(od["n"] == od["confirmed"] == 6, "restated fact: open unverifiable, confirmed in company facts (6)")
            check(r["counts"] == {"filings": 6, "facts": 138, "pinned": 138, "open": 138, "abstain": 120,
                                  "stop_cases": 15, "pairs": 24}, f"counts {r['counts']}")
            check(r["reported"]["same_end_pairs"]["both_pinned_verified"] == 24, "pairs verified")
            check(len(r["reported"]["displays_differ"]) == 12, "displays-differ fact reported per filing and arm")
            m6 = r["measures"][5]
            check(m6["cases"] == 6 and len(r["reported"]["measure_6_facts"]) == 6 and m6["ok"],
                  "measure 6: the restated fact in each filing, open differ + pinned verified")
    tool_cache = tempfile.mkdtemp(prefix="tool-cache-", dir=work)
    code, out, err = run([evaluate, "--out", work, "--tool", os.path.join(TD, "stub_tool.py"), "--tool-cache",
                          tool_cache, "--results-dir", results, "--runs", runs, "--timeout", "60"],
                         dict(env, STUB_EDGAR_SRC=src_same))
    path = out.strip().splitlines()[-1].split("results: ", 1)[-1]
    with open(path, encoding="utf-8") as fh:
        r = json.load(fh)
    with open(path[:-5] + ".md", encoding="utf-8") as fh:
        md = fh.read()
    check(code == 0 and r["measures"][5]["cases"] == 0 and r["measures"][5]["ok"] and "n/a (0 cases)" in md,
          "measure 6 with no case: n/a (0 cases), counted as passing")
    with open(runs, encoding="utf-8") as fh:
        check(len(fh.readlines()) == len(EXPECT) + 1, "one runs.jsonl line per scorer run, in the test folder")
    code, _, err = run([evaluate, "--out", work, "--set", "heldout", "--tool", os.path.join(TD, "stub_tool.py"),
                        "--runs", runs, "--results-dir", results])
    check(code == 2 and "refusing" in err, "held-out set refused without --held-out")
    code, _, _ = run([evaluate, "--out", work, "--tool", os.path.join(work, "no-such-tool.py"), "--runs", runs,
                      "--results-dir", results])
    check(code == 2, "missing tool: exit 2")
    code, _, _ = run([evaluate, "--out", os.path.join(work, "nothing"), "--tool", os.path.join(TD, "stub_tool.py"),
                      "--runs", runs, "--results-dir", results])
    check(code == 2, "missing cases: exit 2")
    leaked = []
    for base, _, files in os.walk(work):
        for n in files:
            with open(os.path.join(base, n), "rb") as fh:
                if PLACEHOLDER.encode() in fh.read():
                    leaked.append(os.path.join(base, n))
    check(not leaked, f"the contact string is in no written file {leaked[:3]}")
    check(not os.path.exists(os.path.join(work, "empty-cache")), "nothing cached by the stopped build")
    print(f"all self-tests passed; test output left in {work}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
