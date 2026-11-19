#!/usr/bin/env python3
"""E3 scorer for receipts (see docs/eval-plan.md, section E3).

Usage: python bench/edgar/evaluate.py [--set dev|heldout] [--held-out] [--tool skills/receipts/scripts/receipts.py]

Runs the tool as a black box (`check ... --edgar-cache ... --quiet`) on the cases written by bench/edgar/build.py,
computes the five E3 measures, compares them with the registered bars, writes
bench/edgar/results/<set>-<commit>-<UTC>.json and .md, and appends one line to bench/edgar/runs.jsonl.
Exit 0 if every bar passes, 1 otherwise, 2 when the cases or the tool are missing.
The tool reads RECEIPTS_SEC_USER_AGENT from the environment that is passed through; it is never printed or written.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
UA_ENV = "RECEIPTS_SEC_USER_AGENT"
URL_ENVS = ("RECEIPTS_EDGAR_DATA_URL", "RECEIPTS_EDGAR_WWW_URL")
PLACEHOLDER_UA = "receipts-bench stop-case (no request is expected to succeed)"
ABSTAIN_FILES = ("abstain-concept.json", "abstain-period.json", "abstain-instant.json", "abstain-unit.json")
PLAN_FILES = ("pinned.json", "open.json") + ABSTAIN_FILES
STOP_VARIANTS = ("no_contact_string", "corrupt_companyfacts", "closed_port")
STOP_PLANS = 5


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def scrub(text):
    secret = os.environ.get(UA_ENV, "").strip()
    return text.replace(secret, "<contact string>") if secret and isinstance(text, str) else text


def run_check(py, tool, plan, cache, env, timeout, keep=None):
    """Run `check`; returns (exit code or None, receipts.json data or None, stderr tail)."""
    with tempfile.TemporaryDirectory(prefix="receipts-e3-") as tmp:
        cmd = [py, tool, "check", plan, "--out", tmp, "--edgar-cache", cache, "--quiet"]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
            code, err = p.returncode, p.stderr.decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            code, err = None, "timeout"
        except OSError as e:
            code, err = None, f"could not start the tool: {e}"
        data, path = None, os.path.join(tmp, "receipts.json")
        if os.path.exists(path):
            try:
                data = load_json(path)
                if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
                    raise ValueError("no claims list")
            except (ValueError, OSError) as e:
                data, err = None, f"invalid receipts.json: {e}\n" + err
            if keep:
                shutil.copyfile(path, keep)
    return code, data, scrub(err[-800:])


def verdict_of(data, cid):
    if data is None:
        return "error", None
    for c in data.get("claims", []):
        if isinstance(c, dict) and c.get("id") == cid:
            return str(c.get("verdict")), c.get("reason")
    for u in data.get("unbound_claims", []) or []:
        if isinstance(u, dict) and u.get("id") == cid:
            return "unbound", u.get("reason")
    return "missing", None


def is_cik(edgar):
    s = str(edgar).strip()
    if s[:3].upper() == "CIK":
        s = s[3:]
    return s.isdigit()


def companyfacts_values(cf, concept, unit, period):
    """Distinct values the cached company facts hold for this concept, unit and period."""
    try:
        tax, tag = concept.split(":", 1)
        units = cf["facts"][tax][tag]["units"]
    except (KeyError, TypeError, ValueError):
        return set()
    rows = []
    for name, items in units.items():
        if str(name).lower() == unit.lower() and isinstance(items, list):
            rows += [r for r in items if isinstance(r, dict)]
    if ".." in period:
        s, e = period.split("..", 1)
        rows = [r for r in rows if r.get("start") == s and r.get("end") == e]
    else:
        rows = [r for r in rows if "start" not in r and r.get("end") == period]
    vals = set()
    for r in rows:
        try:
            vals.add(Decimal(str(r.get("val"))))
        except InvalidOperation:
            pass
    return vals


def closed_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def rate(num, den):
    return None if den == 0 else round(num / den, 6)


def bump(table, key, field, n=1):
    row = table.setdefault(str(key), {})
    row[field] = row.get(field, 0) + n


def git(*args):
    try:
        p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=30)
        return p.stdout.decode("utf-8", "replace").strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", choices=("dev", "heldout"), default="dev")
    ap.add_argument("--held-out", action="store_true", help="required to evaluate the held-out set")
    ap.add_argument("--out", default=os.path.join(HERE, "out"), help="the builder's output root")
    ap.add_argument("--tool", default=os.path.join(ROOT, "skills", "receipts", "scripts", "receipts.py"))
    ap.add_argument("--tool-cache", help="EDGAR cache for normal runs (default <out>/<set>/tool-cache)")
    ap.add_argument("--results-dir", default=os.path.join(HERE, "results"))
    ap.add_argument("--runs", default=os.path.join(HERE, "runs.jsonl"))
    ap.add_argument("--timeout", type=int, default=600, help="seconds per tool call")
    a = ap.parse_args(argv)
    if a.set == "heldout" and not a.held_out:
        print("refusing the held-out set: pass --held-out only for the release run.", file=sys.stderr)
        return 2
    if not os.path.isfile(a.tool):
        print(f"error: the receipts tool was not found at {a.tool}. Pass --tool PATH.", file=sys.stderr)
        return 2
    root = os.path.join(a.out, a.set)
    mpath = os.path.join(root, "manifest.json")
    if not os.path.isfile(mpath):
        print(f"error: no cases in {root}. Run: python bench/edgar/build.py"
              + (" --held-out" if a.set == "heldout" else ""), file=sys.stderr)
        return 2
    manifest = load_json(mpath)
    filings = manifest.get("filings") or []
    missing = [os.path.join(f["folder"], n) for f in filings for n in PLAN_FILES + ("gold.json", "document.md")
               if not os.path.isfile(os.path.join(root, f["folder"], n))]
    if not filings or missing:
        print(f"error: cases missing in {root}: {missing[:5] or 'no filings'}", file=sys.stderr)
        return 2
    py, tool = sys.executable, os.path.abspath(a.tool)
    cache = os.path.abspath(a.tool_cache or os.path.join(root, "tool-cache"))
    os.makedirs(cache, exist_ok=True)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    failures, records, abst, exits = [], [], [], {}
    cf_memo = {}

    def companyfacts(cik):
        if cik not in cf_memo:
            path = os.path.join(cache, "companyfacts", f"CIK{int(cik):010d}.json")
            try:
                cf_memo[cik] = load_json(path)
            except (OSError, ValueError):
                cf_memo[cik] = None
        return cf_memo[cik]

    golds = {}
    for i, f in enumerate(filings):
        folder = os.path.join(root, f["folder"])
        gold = golds[f["folder"]] = load_json(os.path.join(folder, "gold.json"))
        print(f"[{i + 1}/{len(filings)}] {f['folder']} {f['accession']}", flush=True)
        results = {}
        for name in PLAN_FILES:
            keep = os.path.join(folder, "receipts-" + name)
            if os.path.exists(keep):
                os.remove(keep)
            code, data, err = run_check(py, tool, os.path.join(folder, name), cache, env, a.timeout, keep)
            exits.setdefault(name, {})
            exits[name][str(code)] = exits[name].get(str(code), 0) + 1
            if code not in (0, 1) or data is None:
                failures.append({"where": f"{f['folder']}/{name}", "exit": code, "stderr": err})
                data = None
            results[name] = data
        for g in gold["facts"]:
            for arm in ("pinned", "open"):
                v, reason = verdict_of(results[arm + ".json"], g["id"])
                confirmed = None
                if arm == "open" and v == "unverifiable" and reason == "edgar_versions_differ":
                    cf = companyfacts(f["cik"])
                    confirmed = cf is not None and len(companyfacts_values(cf, g["concept"], g["unit"],
                                                                           g["period"])) >= 2
                records.append({"filing": f["folder"], "accession": f["accession"], "company": f["ticker"],
                                "form": f["form"], "arm": arm, "id": g["id"], "concept": g["concept"],
                                "unit": g["unit"], "period": g["period"], "written": g["written"],
                                "value": g["value"], "scale": g["scale"], "sign": g["sign"] or "none",
                                "displays_differ": g["displays_differ"], "pair": g["pair"], "verdict": v,
                                "reason": reason, "confirmed_differ": confirmed})
        for name in ABSTAIN_FILES:
            for c in gold["abstain"][name]:
                v, reason = verdict_of(results[name], c["id"])
                abst.append({"filing": f["folder"], "kind": name[len("abstain-"):-len(".json")], "id": c["id"],
                             "verdict": v, "reason": reason})

    # stop cases: the first 5 pinned plans of the run, three variants each, each in a fresh cache
    port = closed_port()
    stops = []
    for f in filings[:STOP_PLANS]:
        plan = os.path.join(root, f["folder"], "pinned.json")
        claims = load_json(plan).get("claims") or []
        edgar = claims[0]["operands"]["of"]["edgar"] if claims else f.get("company_in_plans", "")
        for variant in STOP_VARIANTS:
            with tempfile.TemporaryDirectory(prefix="receipts-e3-stop-") as tmpcache:
                env2 = dict(env)
                note = ""
                if variant == "no_contact_string":
                    env2.pop(UA_ENV, None)
                elif variant == "corrupt_companyfacts":
                    os.makedirs(os.path.join(tmpcache, "companyfacts"))
                    with open(os.path.join(tmpcache, "companyfacts", f"CIK{int(f['cik']):010d}.json"), "w",
                              encoding="utf-8") as fh:
                        fh.write('{"cik": ' + str(int(f["cik"])) + ', "entityName": "corrupt, "facts": {')
                    if not is_cik(edgar):
                        src = os.path.join(cache, "company_tickers.json")
                        if os.path.isfile(src):
                            shutil.copyfile(src, os.path.join(tmpcache, "company_tickers.json"))
                        else:
                            note = "ticker list not in the normal tool cache, not copied"
                else:
                    env2[UA_ENV] = PLACEHOLDER_UA
                    for k in URL_ENVS:
                        env2[k] = f"http://127.0.0.1:{port}"
                code, _, err = run_check(py, tool, plan, tmpcache, env2, a.timeout)
            stops.append({"filing": f["folder"], "variant": variant, "exit": code, "ok": code == 2, "note": note,
                          "stderr": "" if code == 2 else err})
            print(f"[stop] {f['folder']} {variant}: exit {code}", flush=True)

    pinned = [r for r in records if r["arm"] == "pinned"]
    opened = [r for r in records if r["arm"] == "open"]

    def cnt(items, v):
        return sum(1 for r in items if r["verdict"] == v)
    wrong = cnt(pinned, "wrong") + cnt(opened, "wrong")
    p_ver, p_unv = cnt(pinned, "verified"), cnt(pinned, "unverifiable")
    o_ver, o_unv = cnt(opened, "verified"), cnt(opened, "unverifiable")
    o_differ = sum(1 for r in opened if r["verdict"] == "unverifiable" and r["reason"] == "edgar_versions_differ")
    o_conf = sum(1 for r in opened if r["confirmed_differ"])
    o_ok = o_ver + o_conf
    a_unv = cnt(abst, "unverifiable")
    s_ok = sum(1 for s in stops if s["ok"])
    measures = [
        dict(n=1, name="sampled facts judged wrong, pinned or open", value=wrong, den=len(pinned) + len(opened),
             wrong_pinned=cnt(pinned, "wrong"), wrong_open=cnt(opened, "wrong"), bar="0",
             ok=wrong == 0 and len(pinned) + len(opened) > 0),
        dict(n=2, name="pinned: judged verified", value=rate(p_ver, len(pinned)), num=p_ver, den=len(pinned),
             other_unverifiable=p_unv, other_not_unverifiable=len(pinned) - p_ver - p_unv,
             bar=">= 98%, rest unverifiable",
             ok=len(pinned) > 0 and p_ver * 100 >= 98 * len(pinned) and p_ver + p_unv == len(pinned)),
        dict(n=3, name="open: judged verified, or unverifiable because filings differ (confirmed)",
             value=rate(o_ok, len(opened)), num=o_ok, den=len(opened), verified=o_ver, differ_confirmed=o_conf,
             differ_not_confirmed=o_differ - o_conf, other_unverifiable=o_unv - o_conf,
             other_not_unverifiable=len(opened) - o_ver - o_unv, bar=">= 98%, rest unverifiable",
             ok=len(opened) > 0 and o_ok * 100 >= 98 * len(opened) and o_ver + o_unv == len(opened)),
        dict(n=4, name="abstention cases judged unverifiable", value=rate(a_unv, len(abst)), num=a_unv,
             den=len(abst), bar="100%", ok=len(abst) > 0 and a_unv == len(abst)),
        dict(n=5, name="stop cases that exit with code 2", value=rate(s_ok, len(stops)), num=s_ok, den=len(stops),
             bar="100%", ok=len(stops) > 0 and s_ok == len(stops)),
    ]

    breakdown = {}
    for key in ("company", "form", "unit", "scale", "sign"):
        t = {}
        for r in records:
            k = f"{r[key]} / {r['arm']}"
            bump(t, k, "n")
            bump(t, k, "verdict:" + r["verdict"])
        breakdown[key] = dict(sorted(t.items()))
    abst_by = {}
    for r in abst:
        bump(abst_by, r["kind"], "n")
        bump(abst_by, r["kind"], "verdict:" + r["verdict"])
        bump(abst_by, r["kind"], "reason:" + str(r["reason"]))
    pairs = {"total": 0, "both_pinned_verified": 0, "both_open_verified": 0}
    by_key = {(r["filing"], r["arm"], r["id"]): r for r in records}
    for folder, gold in golds.items():
        for p in gold.get("pairs", []):
            pairs["total"] += 1
            for arm in ("pinned", "open"):
                if all(by_key.get((folder, arm, m), {}).get("verdict") == "verified" for m in p["members"]):
                    pairs[f"both_{arm}_verified"] += 1
    differ_facts = [{k: r[k] for k in ("filing", "arm", "id", "concept", "period", "written", "verdict", "reason")}
                    for r in records if r["displays_differ"]]
    eligible = {"numeric_tags": 0, "eligible_facts": 0, "eligible_displays_differ": 0, "excluded_scale": 0,
                "writable_facts": 0, "same_end_pairs": 0}
    skipped, skipped_formats = {}, {}
    for gold in golds.values():
        for k in eligible:
            eligible[k] += gold["counts"].get(k, 0)
        for k, v in gold.get("skipped", {}).items():
            skipped[k] = skipped.get(k, 0) + v
        for k, v in gold.get("skipped_formats", {}).items():
            skipped_formats[k] = skipped_formats.get(k, 0) + v

    commit = git("rev-parse", "--short", "HEAD") or "nogit"
    porcelain = git("status", "--porcelain")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    all_ok = all(m["ok"] for m in measures)
    accessions = [f"{f['ticker']} {f['form']} {f['accession']}" for f in filings]
    counts = {"filings": len(filings), "facts": len(pinned), "pinned": len(pinned), "open": len(opened),
              "abstain": len(abst), "stop_cases": len(stops), "pairs": pairs["total"]}
    extra = {"open_abstaining_because_filings_differ": {"n": o_differ, "share": rate(o_differ, len(opened)),
                                                        "confirmed": o_conf},
             "same_end_pairs": pairs, "displays_differ": differ_facts, "eligible": eligible,
             "skipped_tags": dict(sorted(skipped.items())), "skipped_formats": dict(sorted(skipped_formats.items()))}
    tool_rel = os.path.relpath(tool, ROOT).replace(os.sep, "/") if tool.startswith(ROOT) else tool
    result = {"timestamp": stamp, "set": a.set, "commit": commit, "dirty": bool(porcelain), "tool": tool_rel,
              "fetch_date": manifest.get("fetch_date"), "builder_version": manifest.get("builder_version"),
              "filings": [{k: f.get(k) for k in ("ticker", "cik", "form", "accession", "filing_date", "report_date",
                                                  "document_url", "document_fetched")} for f in filings],
              "missing_filings": manifest.get("missing", []), "counts": counts, "measures": measures,
              "pass": all_ok, "reported": extra, "breakdown": breakdown, "abstain_by_kind": abst_by,
              "stop_cases": stops, "check_exit_codes": exits, "failures": failures,
              "not_passing": [r for r in records if r["verdict"] != "verified" and not r["confirmed_differ"]][:300]
              + [dict(r, arm="abstain") for r in abst if r["verdict"] != "unverifiable"][:100]}
    os.makedirs(a.results_dir, exist_ok=True)
    base = os.path.join(a.results_dir, f"{a.set}-{commit}-{stamp}")
    with open(base + ".json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(base + ".md", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_md(result))
    line = {"timestamp": stamp, "set": a.set, "commit": commit, "dirty": bool(porcelain), "counts": counts,
            "measures": {str(m["n"]): m["value"] for m in measures},
            "pass": {str(m["n"]): m["ok"] for m in measures}, "all_pass": all_ok, "failures": len(failures),
            "fetch_date": manifest.get("fetch_date"), "accessions": accessions}
    os.makedirs(os.path.dirname(os.path.abspath(a.runs)), exist_ok=True)
    with open(a.runs, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    for m in measures:
        print(f"  {m['n']}. {'PASS' if m['ok'] else 'FAIL'}  {m['value']}  (bar {m['bar']})  {m['name']}")
    print(f"tool failures: {len(failures)}; results: {base}.json")
    return 0 if all_ok else 1


def render_md(r):
    c = r["counts"]
    out = [f"# E3 results: {r['set']} set, commit {r['commit']}{' (dirty)' if r['dirty'] else ''}", "",
           f"Run {r['timestamp']}. Filings fetched {r['fetch_date']}: {c['filings']}. Sampled facts {c['facts']} "
           f"(each asked pinned and open), same-end pairs {c['pairs']}, abstention cases {c['abstain']}, "
           f"stop cases {c['stop_cases']}.", "",
           f"Overall: {'PASS' if r['pass'] else 'FAIL'}. Plans the tool did not answer (crash, timeout, exit 2, "
           f"no receipts.json): {len(r['failures'])}.", "", "| # | Measure | Result | Bar | Pass |",
           "|---|---|---|---|---|"]
    for m in r["measures"]:
        val = m["value"]
        shown = (f"{val:.2%} ({m['num']}/{m['den']})" if isinstance(val, float) and "num" in m
                 else f"{val} of {m['den']}" if m["n"] == 1 else str(val))
        out.append(f"| {m['n']} | {m['name']} | {shown} | {m['bar']} | {'yes' if m['ok'] else 'no'} |")
    rep = r["reported"]
    od = rep["open_abstaining_because_filings_differ"]
    sp = rep["same_end_pairs"]
    share = "n/a" if od["share"] is None else f"{od['share']:.2%}"
    out += ["", "## Reported without a bar", "",
            f"- Open claims unverifiable because filings report different values: {od['n']} ({share}), "
            f"confirmed in the cached company facts: {od['confirmed']}.",
            f"- Same-end pairs with both members verified: pinned {sp['both_pinned_verified']} of {sp['total']}, "
            f"open {sp['both_open_verified']} of {sp['total']}.",
            f"- Eligible facts {rep['eligible']['eligible_facts']} in {rep['eligible']['numeric_tags']} numeric tags; "
            f"facts whose displays differ {rep['eligible']['eligible_displays_differ']}; with a scale that cannot be "
            f"written (not 0, 3, 6, 9) {rep['eligible']['excluded_scale']}; same-end pairs "
            f"{rep['eligible']['same_end_pairs']}.",
            "- Skipped tags: " + (", ".join(f"{k} {v}" for k, v in rep["skipped_tags"].items()) or "none") + ".",
            "- Skipped formats: " + (", ".join(f"{k} {v}" for k, v in rep["skipped_formats"].items()) or "none")
            + "."]
    out += ["", "### Sampled facts whose displays differ", ""]
    out += [f"- {d['filing']} {d['id']} ({d['arm']}) `{d['concept']}` {d['period']} written {d['written']}: "
            f"{d['verdict']} {d['reason'] or ''}" for d in rep["displays_differ"]] or ["- none"]
    for key in ("company", "form", "unit", "scale", "sign"):
        out += ["", f"## By {key}", "", "| key / arm | n | verified | wrong | unverifiable | other |",
                "|---|---|---|---|---|---|"]
        for k, row in r["breakdown"][key].items():
            v = {x: row.get("verdict:" + x, 0) for x in ("verified", "wrong", "unverifiable")}
            out.append(f"| {k} | {row['n']} | {v['verified']} | {v['wrong']} | {v['unverifiable']} | "
                       f"{row['n'] - sum(v.values())} |")
    out += ["", "## Abstention cases by kind", ""]
    out += [f"- {k}: " + ", ".join(f"{x} {n}" for x, n in sorted(v.items())) for k, v in r["abstain_by_kind"].items()]
    out += ["", "## Stop cases", "", "| filing | case | exit | pass |", "|---|---|---|---|"]
    out += [f"| {s['filing']} | {s['variant']} | {s['exit']} | {'yes' if s['ok'] else 'no'} |"
            for s in r["stop_cases"]]
    out += ["", "## Filings", "", "| company | form | accession | filed | document fetched |", "|---|---|---|---|---|"]
    out += [f"| {f['ticker']} | {f['form']} | {f['accession']} | {f['filing_date']} | {f['document_fetched']} |"
            for f in r["filings"]]
    if r["missing_filings"]:
        out += ["", "Not found: " + "; ".join(f"{m['ticker']} {m['form']} ({m['reason']})"
                                               for m in r["missing_filings"])]
    out += ["", f"`check` exit codes by plan file: {r['check_exit_codes']}.", ""]
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())
