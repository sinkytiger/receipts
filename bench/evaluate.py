#!/usr/bin/env python3
"""E1 scorer for receipts (see docs/eval-plan.md, section E1).

Usage: python bench/evaluate.py [--seed 20261119] [--out bench/out] [--tool skills/receipts/scripts/receipts.py]

Runs the tool as a black box (`scan` and `check`) on the data written by bench/generate.py, computes the
eight E1 measures, compares them with the registered bars, writes bench/results/<seed>-<commit>-<UTC>.json
and .md, and appends one line to bench/runs.jsonl. Exit 0 if every bar passes, 1 otherwise, 2 on setup errors.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

DEV_SEED, HELD_OUT_SEED = 20261119, 20261120
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERDICTS = ("verified", "wrong", "unverifiable", "uncovered")


def run(cmd, timeout):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return None, "", "timeout"


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def overlap(a, b):
    return a["line"] == b["line"] and a["start"] < b["end"] and b["start"] < a["end"]


def valid_spans(items):
    return isinstance(items, list) and all(isinstance(x, dict) and all(isinstance(x.get(k), int)
                                           for k in ("line", "start", "end")) for x in items)


def tool_scan(py, tool, report, timeout, failures, where):
    code, out, err = run([py, tool, "scan", report], timeout)
    try:
        data = json.loads(out)
        if code != 0 or not valid_spans(data.get("numbers")):
            raise ValueError(f"exit {code}")
        if not valid_spans(data.get("skipped_items", [])):
            data["skipped_items"] = []
        return data
    except (ValueError, AttributeError) as e:
        failures.append({"where": where, "step": "scan", "error": str(e), "stderr": err[-500:]})
        return None


def tool_check(py, tool, plan, timeout, failures, where, expect_ok=True):
    with tempfile.TemporaryDirectory(prefix="receipts-e1-") as tmp:
        code, out, err = run([py, tool, "check", plan, "--out", tmp], timeout)
        path = os.path.join(tmp, "receipts.json")
        data = None
        if os.path.exists(path):
            try:
                data = load_json(path)
                if not isinstance(data.get("claims"), list):
                    raise ValueError("no claims list")
            except (ValueError, AttributeError) as e:
                data = None
                err = f"invalid receipts.json: {e}\n" + err
    if expect_ok and (data is None or code not in (0, 1)):
        failures.append({"where": where, "step": "check", "exit": code, "stderr": err[-500:]})
        data = None
    return code, data


def rate(num, den):
    return None if den == 0 else round(num / den, 6)


def bump(table, key, field, n=1):
    row = table.setdefault(key, {})
    row[field] = row.get(field, 0) + n


def git(*args):
    try:
        p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=30)
        return p.stdout.decode("utf-8", "replace").strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=DEV_SEED)
    ap.add_argument("--out", default=os.path.join("bench", "out"))
    ap.add_argument("--tool", default=os.path.join("skills", "receipts", "scripts", "receipts.py"))
    ap.add_argument("--held-out", action="store_true", help="required to evaluate the held-out seed")
    ap.add_argument("--results-dir", default=os.path.join("bench", "results"))
    ap.add_argument("--runs", default=os.path.join("bench", "runs.jsonl"))
    ap.add_argument("--timeout", type=int, default=120, help="seconds per tool call")
    a = ap.parse_args(argv)
    if a.seed == HELD_OUT_SEED and not a.held_out:
        sys.exit(f"refusing seed {HELD_OUT_SEED}: it is the held-out seed. Pass --held-out only for the release run.")
    if not os.path.isfile(a.tool):
        print(f"error: the receipts tool was not found at {a.tool}. Build it first or pass --tool PATH.",
              file=sys.stderr)
        return 2
    root = os.path.join(a.out, str(a.seed))
    if not os.path.isfile(os.path.join(root, "manifest.json")):
        print(f"error: no generated data in {root}. Run: python bench/generate.py --seed {a.seed}", file=sys.stderr)
        return 2
    manifest = load_json(os.path.join(root, "manifest.json"))
    py, tool, failures = sys.executable, os.path.abspath(a.tool), []

    stmts, extra, dist_class, scan_stats = [], {}, {}, {"placed": 0, "found": 0}
    cov = {"removed": 0, "removed_found": 0, "listed_uncovered": 0}
    exits_gold, position_mismatch = {}, 0
    for i, d in enumerate(manifest["docs"]):
        folder = os.path.join(root, d["path"])
        gold = load_json(os.path.join(folder, "gold.json"))
        print(f"[{i + 1}/{len(manifest['docs'])}] {d['id']}", flush=True)
        scan = tool_scan(py, tool, os.path.join(folder, "report.md"), a.timeout, failures, d["id"])
        code, rec = tool_check(py, tool, os.path.join(folder, "claims.gold.json"), a.timeout, failures, d["id"])
        exits_gold[str(code)] = exits_gold.get(str(code), 0) + 1
        _, rec_cov = tool_check(py, tool, os.path.join(folder, "claims.coverage.json"), a.timeout, failures,
                                d["id"] + ":coverage")
        numbers = scan["numbers"] if scan else []
        skipped = scan["skipped_items"] if scan else []
        by_id = {c.get("id"): c for c in (rec or {}).get("claims", []) if isinstance(c, dict)}
        uncovered = [u for u in (rec_cov or {}).get("uncovered", []) if valid_spans([u])]
        for g in gold["statements"]:
            found = any(overlap(g, n) for n in numbers)
            c = by_id.get(g["id"])
            verdict = "error" if rec is None else (c.get("verdict") if c else "missing")
            if c and isinstance(c.get("start"), int) and not overlap(g, dict(c, line=c.get("line"))):
                position_mismatch += 1
            stmts.append(dict(g, doc=d["id"], found=found, verdict=verdict, reason=(c or {}).get("reason")))
            scan_stats["placed"] += 1
            scan_stats["found"] += int(found)
            if g["removed_in_coverage"]:
                cov["removed"] += 1
                if found:
                    cov["removed_found"] += 1
                    cov["listed_uncovered"] += int(any(overlap(g, u) for u in uncovered))
        for n in numbers:  # tokens scan reported that the generator did not place as statements
            if any(overlap(n, g) for g in gold["statements"]):
                continue
            hit = [x["rule"] for x in gold["distractors"] if overlap(n, x)]
            key = f"distractor:{hit[0]}" if hit else "not_placed"
            extra[key] = extra.get(key, 0) + 1
        if scan:
            for x in gold["distractors"]:
                if any(overlap(x, g) for g in gold["statements"]):
                    continue
                rules = [s.get("rule") for s in skipped if overlap(x, s)]
                cls = ("reported_as_number" if any(overlap(x, n) for n in numbers) else
                       "skipped_expected_rule" if x["rule"] in rules else
                       "skipped_other_rule" if rules else "not_seen")
                bump(dist_class, x["rule"], cls)

    abst = {"locator_total": 0, "locator_unverifiable": 0, "source_total": 0, "source_nonzero": 0,
            "source_exit2": 0, "by_case": {}}
    for i, e in enumerate(manifest["abstain"]):
        folder = os.path.join(root, e["path"])
        print(f"[abstain {i + 1}/{len(manifest['abstain'])}] {e['case_id']}", flush=True)
        if e["expect"] == "unverifiable":
            code, rec = tool_check(py, tool, os.path.join(folder, "claims.json"), a.timeout, failures, e["case_id"])
            c = {x.get("id"): x for x in (rec or {}).get("claims", []) if isinstance(x, dict)}.get(e["claim_id"])
            v = (c or {}).get("verdict", "error" if rec is None else "missing")
            abst["locator_total"] += 1
            abst["locator_unverifiable"] += int(v == "unverifiable")
            bump(abst["by_case"], e["kind"], v)
            bump(abst["by_case"], e["kind"], "reason:" + str((c or {}).get("reason")))
        else:
            code, _ = tool_check(py, tool, os.path.join(folder, "claims.json"), a.timeout, failures,
                                 e["case_id"], expect_ok=False)
            abst["source_total"] += 1
            abst["source_nonzero"] += int(code is not None and code != 0)
            abst["source_exit2"] += int(code == 2)
            bump(abst["by_case"], f'{e["kind"]}:{e["variant"]}', f"exit_{code}")

    planted = [s for s in stmts if s["planted"]]
    correct = [s for s in stmts if not s["planted"]]

    def cnt(items, v):
        return sum(1 for s in items if s["verdict"] == v)
    p_wrong, p_unv, p_ver = cnt(planted, "wrong"), cnt(planted, "unverifiable"), cnt(planted, "verified")
    c_ver, c_unv, c_wrong = cnt(correct, "verified"), cnt(correct, "unverifiable"), cnt(correct, "wrong")
    measures = [
        dict(n=1, name="planted errors judged verified (false accepts)", value=p_ver, bar="0", ok=p_ver == 0),
        dict(n=2, name="correct statements judged wrong (false alarms)", value=c_wrong, bar="0", ok=c_wrong == 0),
        dict(n=3, name="planted errors judged wrong", value=rate(p_wrong, len(planted)), num=p_wrong,
             den=len(planted), other_unverifiable=p_unv, other_not_unverifiable=len(planted) - p_wrong - p_unv,
             bar=">= 98%, rest unverifiable",
             ok=len(planted) > 0 and p_wrong * 100 >= 98 * len(planted) and p_wrong + p_unv == len(planted)),
        dict(n=4, name="correct statements judged verified", value=rate(c_ver, len(correct)), num=c_ver,
             den=len(correct), other_unverifiable=c_unv, other_not_unverifiable=len(correct) - c_ver - c_unv,
             bar=">= 98%, rest unverifiable",
             ok=len(correct) > 0 and c_ver * 100 >= 98 * len(correct) and c_ver + c_unv == len(correct)),
        dict(n=5, name="zero-cell and multi-cell locators judged unverifiable",
             value=rate(abst["locator_unverifiable"], abst["locator_total"]), num=abst["locator_unverifiable"],
             den=abst["locator_total"], bar="100%",
             ok=abst["locator_total"] > 0 and abst["locator_unverifiable"] == abst["locator_total"]),
        dict(n=6, name="missing or corrupt source: check exits non-zero",
             value=rate(abst["source_nonzero"], abst["source_total"]), num=abst["source_nonzero"],
             den=abst["source_total"], exit_2=abst["source_exit2"], bar="100%",
             ok=abst["source_total"] > 0 and abst["source_nonzero"] == abst["source_total"]),
        dict(n=7, name="numbers placed by the generator that scan finds",
             value=rate(scan_stats["found"], scan_stats["placed"]), num=scan_stats["found"],
             den=scan_stats["placed"], bar=">= 99%",
             ok=scan_stats["placed"] > 0 and scan_stats["found"] * 100 >= 99 * scan_stats["placed"]),
        dict(n=8, name="removed claims that scan found and the report lists as uncovered",
             value=rate(cov["listed_uncovered"], cov["removed_found"]), num=cov["listed_uncovered"],
             den=cov["removed_found"], removed_total=cov["removed"], bar="100%",
             ok=cov["removed_found"] > 0 and cov["listed_uncovered"] == cov["removed_found"]),
    ]
    breakdown = {}
    for key in ("lang", "source_type", "formula", "planted", "format"):
        t = {}
        for s in stmts:
            k = str(s[key] or "correct")
            bump(t, k, "n")
            bump(t, k, "verdict:" + str(s["verdict"]))
            bump(t, k, "scan_found", int(s["found"]))
        breakdown[key] = dict(sorted(t.items()))

    commit = git("rev-parse", "--short", "HEAD") or "nogit"
    porcelain = git("status", "--porcelain")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    all_ok = all(m["ok"] for m in measures)
    counts = {"documents": len(manifest["docs"]), "statements": len(stmts), "planted": len(planted),
              "correct": len(correct), "abstain": manifest["counts"]["abstain"]}
    result = {"timestamp": stamp, "seed": a.seed, "commit": commit, "dirty": bool(porcelain),
              "tool": os.path.relpath(tool, ROOT).replace(os.sep, "/") if tool.startswith(ROOT) else tool,
              "generator_version": manifest.get("generator_version"), "counts": counts, "measures": measures,
              "pass": all_ok, "breakdown": breakdown, "abstain": abst, "check_exit_codes_gold": exits_gold,
              "claim_position_mismatch": position_mismatch, "scan_unplaced_tokens": dict(sorted(extra.items())),
              "distractor_classification": dict(sorted(dist_class.items())), "failures": failures,
              "missed_statements": [{k: s[k] for k in ("doc", "id", "line", "text", "planted", "verdict", "found",
                                                        "reason")}
                                    for s in stmts if not s["found"] or s["verdict"] not in
                                    (("wrong",) if s["planted"] else ("verified",))][:300]}
    os.makedirs(a.results_dir, exist_ok=True)
    base = os.path.join(a.results_dir, f"{a.seed}-{commit}-{stamp}")
    with open(base + ".json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(base + ".md", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_md(result))
    line = {"timestamp": stamp, "seed": a.seed, "commit": commit, "dirty": bool(porcelain), "counts": counts,
            "measures": {str(m["n"]): m["value"] for m in measures},
            "pass": {str(m["n"]): m["ok"] for m in measures}, "all_pass": all_ok, "failures": len(failures)}
    os.makedirs(os.path.dirname(os.path.abspath(a.runs)), exist_ok=True)
    with open(a.runs, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    for m in measures:
        print(f"  {m['n']}. {'PASS' if m['ok'] else 'FAIL'}  {m['value']}  (bar {m['bar']})  {m['name']}")
    print(f"tool failures: {len(failures)}; results: {base}.json")
    return 0 if all_ok else 1


def render_md(r):
    out = [f"# E1 results: seed {r['seed']}, commit {r['commit']}{' (dirty)' if r['dirty'] else ''}", "",
           f"Run {r['timestamp']}. Documents {r['counts']['documents']}, statements {r['counts']['statements']} "
           f"({r['counts']['planted']} planted, {r['counts']['correct']} correct). Abstention cases: "
           + ", ".join(f"{k} {v}" for k, v in r["counts"]["abstain"].items()) + ".", "",
           f"Overall: {'PASS' if r['pass'] else 'FAIL'}. Tool failures (crash, bad JSON, exit 2 on a good plan): "
           f"{len(r['failures'])}.", "", "| # | Measure | Result | Bar | Pass |", "|---|---|---|---|---|"]
    for m in r["measures"]:
        val = m["value"]
        shown = (f"{val:.2%} ({m['num']}/{m['den']})" if isinstance(val, float) and "num" in m else str(val))
        out.append(f"| {m['n']} | {m['name']} | {shown} | {m['bar']} | {'yes' if m['ok'] else 'no'} |")
    for key, title in (("lang", "language"), ("source_type", "source type"), ("formula", "formula"),
                       ("planted", "error type"), ("format", "number format")):
        out += ["", f"## By {title}", "", "| key | n | verified | wrong | unverifiable | other | scan found |",
                "|---|---|---|---|---|---|---|"]
        for k, row in r["breakdown"][key].items():
            v = {x: row.get("verdict:" + x, 0) for x in ("verified", "wrong", "unverifiable")}
            other = row["n"] - sum(v.values())
            out.append(f"| {k} | {row['n']} | {v['verified']} | {v['wrong']} | {v['unverifiable']} | {other} | "
                       f"{row.get('scan_found', 0)} |")
    out += ["", "## Tokens scan reported that the generator did not place", ""]
    out += [f"- {k}: {v}" for k, v in r["scan_unplaced_tokens"].items()] or ["- none"]
    out += ["", "## Placed distractors, by expected skip rule", ""]
    out += [f"- {k}: " + ", ".join(f"{c} {n}" for c, n in sorted(v.items()))
            for k, v in r["distractor_classification"].items()] or ["- none"]
    out += ["", "## Abstention cases", ""]
    out += [f"- {k}: " + ", ".join(f"{c} {n}" for c, n in sorted(v.items())) for k, v in r["abstain"]["by_case"].items()]
    out += ["", f"`check` exit codes on gold plans: {r['check_exit_codes_gold']}. "
                f"Claims whose reported position does not overlap the gold token: {r['claim_position_mismatch']}.", ""]
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())
