#!/usr/bin/env python3
"""A small stand-in for the receipts tool, used only to test bench/edgar/evaluate.py offline.

It understands `check PLAN --out DIR --edgar-cache DIR [--quiet]` for plans made of EDGAR `lookup` claims, answers from
a cached companyfacts file, and "fetches" a missing file by copying it from the folder in STUB_EDGAR_SRC (no network),
after the same checks the contract asks for: a contact string must be set, and with RECEIPTS_EDGAR_DATA_URL or
RECEIPTS_EDGAR_WWW_URL set it tries that address (a closed local port in the scorer's stop case) and stops.
The broken variants (stub_*.py next to this file) change one behaviour each, to show that each bar can fail.
"""
import argparse
import json
import os
import re
import shutil
import sys
import urllib.request
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

SCALE = {"": 0, "thousand": 3, "million": 6, "billion": 9}
NUM_RE = re.compile(r"^([-+]?)((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?:\s+(thousand|million|billion))?$")


class Stopped(Exception):
    pass


def main(argv=None, mode="good"):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("plan")
    ap.add_argument("--out")
    ap.add_argument("--edgar-cache")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    with open(a.plan, encoding="utf-8") as fh:
        plan = json.load(fh)
    claims = plan["claims"]
    out = a.out or os.path.dirname(a.plan)

    def write(results):
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "receipts.json"), "w", encoding="utf-8") as fh:
            json.dump({"document": plan["document"], "claims": results}, fh)

    try:
        results = answer(plan, claims, a, mode)
    except Stopped as e:
        if mode == "never_exit2":
            write([{"id": c["id"], "verdict": "unverifiable", "reason": "internal_error"} for c in claims])
            return 0
        print(f"stopped: {e}", file=sys.stderr)
        return 2
    write(results)
    return 1 if any(r["verdict"] == "wrong" for r in results) else 0


def answer(plan, claims, a, mode):
    cache = a.edgar_cache

    def load(rel):
        path = os.path.join(cache, *rel.split("/"))
        if not os.path.isfile(path):
            if not os.environ.get("RECEIPTS_SEC_USER_AGENT", "").strip():
                raise Stopped("no contact string")
            base = os.environ.get("RECEIPTS_EDGAR_DATA_URL") or os.environ.get("RECEIPTS_EDGAR_WWW_URL")
            if base:
                try:
                    urllib.request.urlopen(base + "/" + rel, timeout=5)
                except Exception as e:  # the stop case points at a closed port
                    raise Stopped(f"request failed: {type(e).__name__}")
                raise Stopped("unexpected answer from the test address")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.copyfile(os.path.join(os.environ["STUB_EDGAR_SRC"], *rel.split("/")), path)
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except ValueError:
            raise Stopped(f"corrupt cached file {rel}")

    results = []
    for c in claims:
        loc = c["operands"]["of"]
        edgar = str(loc["edgar"])
        digits = edgar[3:] if edgar.upper().startswith("CIK") else edgar
        if digits.isdigit():
            cik = int(digits)
        else:
            tick = {str(v["ticker"]).upper(): int(v["cik_str"]) for v in load("company_tickers.json").values()}
            cik = tick.get(edgar.upper())
        cf = load(f"companyfacts/CIK{cik:010d}.json") if cik else {"facts": {}}
        tax, tag = loc["concept"].split(":", 1)
        units = cf.get("facts", {}).get(tax, {}).get(tag, {}).get("units", {})
        rows = [r for k, v in units.items() if k.lower() == loc.get("unit", "").lower() for r in v]
        period = loc["period"]
        if ".." in period:
            s, e = period.split("..")
            rows = [r for r in rows if r.get("start") == s and r.get("end") == e]
        else:
            rows = [r for r in rows if "start" not in r and r.get("end") == period]
        all_vals = {Decimal(str(r["val"])) for r in rows}
        latest = max(rows, key=lambda r: (r.get("filed", ""), r["accn"])) if rows else None
        if loc.get("accession"):
            rows = [r for r in rows if r["accn"].replace("-", "") == loc["accession"].replace("-", "")]
        vals = {Decimal(str(r["val"])) for r in rows}
        if mode == "latest_on_differ" and not loc.get("accession") and len(vals) > 1:
            vals = {Decimal(str(latest["val"]))}  # silently takes the latest report
        if mode == "ignore_pin_on_differ" and loc.get("accession") and len(all_vals) > 1:
            vals = all_vals  # the accession pin is ignored on facts whose filings differ
        m = NUM_RE.match(c["number"])
        sign = -1 if m.group(1) == "-" else 1
        exp = SCALE[m.group(3) or ""] - (len(m.group(2).split(".")[1]) if "." in m.group(2) else 0)
        w = sign * Decimal(m.group(2).replace(",", "")).scaleb(SCALE[m.group(3) or ""])
        q = Decimal(1).scaleb(exp)
        if not vals:
            v, r = "unverifiable", "locator_no_match"
        elif len(vals) > 1:
            v, r = "unverifiable", "edgar_versions_differ"
        else:
            val = vals.pop()
            if mode == "wrong_value":
                val += 7 * q
            hit = any(val.quantize(q, rounding=x) == w for x in (ROUND_HALF_UP, ROUND_HALF_EVEN, ROUND_DOWN))
            v, r = ("verified", "match") if hit else ("wrong", "mismatch")
        if mode == "always_verified":
            v, r = "verified", "match"
        if mode == "fake_differ" and not loc.get("accession"):
            v, r = "unverifiable", "edgar_versions_differ"
        if mode == "pinned_abstain" and loc.get("accession"):
            v, r = "unverifiable", "locator_no_match"
        results.append({"id": c["id"], "line": c["line"], "verdict": v, "reason": r})
    return results


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
