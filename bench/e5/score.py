#!/usr/bin/env python3
"""E5 scorer: what the two arms did with the planted errors and the correct statements.

The rules are in docs/eval-plan.md, section E5 ("Measures"). It reads a run folder of bench/e5/run.py (or the copy
under bench/e5/records/) and the gold files of the documents. It makes no request and does not run the script.

  python bench/e5/score.py bench/e5/out/dev/run-<UTC>                 # a rehearsal, printed only
  python bench/e5/score.py bench/e5/records/heldout --write           # the held-out run: results file and run log
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
KINDS = ["a", "b", "c", "d", "e"]
FIELDS = ["company", "year", "report", "fs", "statement", "row", "column"]
# a source line of the receipt: "DART 00126380 (삼성전자, 005930) 2026 q3 CFS IS <account id> <name> <column> [..."
# The name and stock code in brackets are there when the plan gave a stock code.
SOURCE = re.compile(r"^DART (\d{8})(?: \(.*?\))? (\d{4}) (q1|half|q3|annual) (CFS|OFS) (BS|IS|CIS|CF) (\S+) .*? "
                    r"(thstrm_amount|thstrm_add_amount|frmtrm_amount|frmtrm_q_amount|frmtrm_add_amount|bfefrmtrm_amount) \[")


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def rate(k, n):
    return {"k": k, "n": n, "share": round(k / n, 4) if n else None, "wilson95": wilson(k, n)}


def cells_of(claim):
    """The DART cells a judged claim read, in the order the script lists them."""
    out = []
    for s in claim.get("sources") or []:
        m = SOURCE.match(s)
        if m:
            out.append(dict(zip(FIELDS, [m.group(1), int(m.group(2)), m.group(3), m.group(4), m.group(5), m.group(6),
                                         m.group(7)])))
    return out


def number_on_line(receipts, line):
    """The verdict of the one number on a statement's line: (verdict, claim or None)."""
    claims = [c for c in receipts.get("claims", []) if c.get("line") == line and c.get("reason") != "duplicate_claim"]
    if claims:
        return claims[0]["verdict"], claims[0]
    if any(u.get("line") == line for u in receipts.get("uncovered", [])):
        return "uncovered", None
    return "uncovered", None


def score_document(doc_dir, gold_doc):
    info, statements = gold_doc["document"], gold_doc["statements"]
    lines = {s["line"]: s for s in statements}
    out = {"corp_code": info["corp_code"], "company": info["company"], "group": info["group"], "report": info["report"],
           "statements": []}
    # arm A
    a_dir = os.path.join(doc_dir, "A")
    a = read_json(os.path.join(a_dir, "result.json")) if os.path.isfile(os.path.join(a_dir, "result.json")) else None
    plans = []
    if a:
        n = 1
        while os.path.isfile(os.path.join(a_dir, f"plan{n}", "receipts.json")) or os.path.isfile(os.path.join(a_dir, f"plan{n}", "claims.json")):
            path = os.path.join(a_dir, f"plan{n}", "receipts.json")
            plans.append(read_json(path) if os.path.isfile(path) else None)
            n += 1
    accepted = [p for p in plans if p is not None]
    final = accepted[-1] if accepted else None
    # arm B
    b_dir = os.path.join(doc_dir, "B")
    b = read_json(os.path.join(b_dir, "result.json")) if os.path.isfile(os.path.join(b_dir, "result.json")) else None
    b_lines = Counter()
    if b and b.get("answer"):
        for item in b["answer"].get("wrong", []):
            try:
                b_lines[int(item.get("line"))] += 1
            except (TypeError, ValueError):
                b_lines[None] += 1
    for s in statements:
        row = {"id": s["id"], "line": s["line"], "kind": s["kind"], "slot": s["slot"], "planted": s["planted"]}
        if final is None:
            row["a_verdict"] = "not_checked"
        else:
            verdict, claim = number_on_line(final, s["line"])
            row["a_verdict"] = verdict
            if claim is not None and verdict in ("verified", "wrong"):
                read = cells_of(claim)
                meant = [{"company": c["dart"], "year": c["year"], "report": c["report"], "fs": c["fs"],
                          "statement": c["statement"], "row": c["account"], "column": c["column"]} for c in s["cells"]]
                if len(read) != len(meant):
                    row["locator"] = "other_cells"
                else:
                    differs = sorted({f for r, m in zip(read, meant) for f in FIELDS if r[f] != m[f]}, key=FIELDS.index)
                    row["locator"] = "same" if not differs else "differs"
                    if differs:
                        row["locator_fields"] = differs
        # a repair that turned `wrong` into `verified` by reading another cell
        history = [number_on_line(p, s["line"]) for p in accepted]
        for (v1, c1), (v2, c2) in zip(history, history[1:]):
            if v1 == "wrong" and v2 == "verified" and c1 and c2 and cells_of(c1) != cells_of(c2):
                row["repaired_to_match"] = True
        row["a_flagged"] = row["a_verdict"] == "wrong"
        row["b_flagged"] = bool(b_lines.get(s["line"])) if b and b.get("parsed") else None
        out["statements"].append(row)
    numbers = (final or {}).get("counts", {})
    out["arm_a"] = {"ran": a is not None, "accepted_plan": bool(final), "plans_sent": (a or {}).get("plans_sent"),
                    "accepted_plans": len(accepted), "tool_calls": (a or {}).get("tool_calls"),
                    "stopped": (a or {}).get("stopped"), "said_done": (a or {}).get("said_done"),
                    "numbers": numbers.get("numbers"), "uncovered": numbers.get("uncovered"),
                    "requests": len((a or {}).get("requests", [])),
                    "input_tokens": sum(r["input_tokens"] for r in (a or {}).get("requests", [])),
                    "output_tokens": sum(r["output_tokens"] for r in (a or {}).get("requests", [])),
                    "usd": (a or {}).get("usd")}
    out["arm_b"] = {"ran": b is not None, "parsed": (b or {}).get("parsed"),
                    "flags": sum(b_lines.values()), "flags_on_no_statement": sum(v for k, v in b_lines.items() if k not in lines),
                    "requests": len((b or {}).get("requests", [])),
                    "input_tokens": sum(r["input_tokens"] for r in (b or {}).get("requests", [])),
                    "output_tokens": sum(r["output_tokens"] for r in (b or {}).get("requests", [])),
                    "usd": (b or {}).get("usd")}
    return out


def summarise(docs):
    rows = [dict(s, group=d["group"], report=d["report"], doc=d["corp_code"]) for d in docs for s in d["statements"]]
    planted = [r for r in rows if r["planted"]]
    correct = [r for r in rows if not r["planted"]]
    b_rows = [r for r in rows if r["b_flagged"] is not None]

    def arm(rs, key):
        rs = [r for r in rs if r[key] is not None]
        return rate(sum(1 for r in rs if r[key]), len(rs))

    def both(rs):
        return {"arm_a": arm(rs, "a_flagged"), "arm_b": arm(rs, "b_flagged")}

    judged = [r for r in rows if "locator" in r]
    fields = Counter(f for r in judged for f in r.get("locator_fields", []))
    out = {
        "documents": len(docs), "statements": len(rows), "planted": len(planted), "correct": len(correct),
        "1_planted_flagged": dict(both(planted), by_kind={k: both([r for r in planted if r["planted"] == k]) for k in KINDS}),
        "2_correct_flagged": both(correct),
        "3_arm_a_verdicts": {"planted": dict(Counter(r["a_verdict"] for r in planted)),
                             "correct": dict(Counter(r["a_verdict"] for r in correct)),
                             "planted_judged_verified": [{"doc": r["doc"], "id": r["id"], "kind": r["planted"],
                                                          "locator": r.get("locator"), "fields": r.get("locator_fields")}
                                                         for r in planted if r["a_verdict"] == "verified"]},
        "4_arm_a_locators": {"judged_from_dart_cells": len(judged),
                             "same_cells": rate(sum(1 for r in judged if r["locator"] == "same"), len(judged)),
                             "differs": sum(1 for r in judged if r["locator"] == "differs"),
                             "other_cells": sum(1 for r in judged if r["locator"] == "other_cells"),
                             "fields_that_differ": {f: fields.get(f, 0) for f in FIELDS}},
        "5_arm_a_repairs": {"corrected_plans": sum(max((d["arm_a"]["plans_sent"] or 1) - 1, 0) for d in docs),
                            "documents_with_a_corrected_plan": sum(1 for d in docs if (d["arm_a"]["plans_sent"] or 0) > 1),
                            "wrong_to_verified_with_another_cell": sum(1 for r in rows if r.get("repaired_to_match")),
                            "of_which_planted": sum(1 for r in planted if r.get("repaired_to_match"))},
        "6_arm_a_other": {"documents_without_an_accepted_plan": sum(1 for d in docs if not d["arm_a"]["accepted_plan"]),
                          "tool_calls_per_document": _spread([d["arm_a"]["tool_calls"] for d in docs]),
                          "numbers_uncovered": rate(sum(d["arm_a"]["uncovered"] or 0 for d in docs),
                                                    sum(d["arm_a"]["numbers"] or 0 for d in docs))},
        "7_cost": {arm_name: {"requests_per_document": _spread([d[arm_name]["requests"] for d in docs]),
                              "input_tokens_per_document": _spread([d[arm_name]["input_tokens"] for d in docs]),
                              "output_tokens_per_document": _spread([d[arm_name]["output_tokens"] for d in docs]),
                              "usd_per_document": round(sum(d[arm_name]["usd"] or 0 for d in docs) / max(len(docs), 1), 6),
                              "usd": round(sum(d[arm_name]["usd"] or 0 for d in docs), 6)} for arm_name in ("arm_a", "arm_b")},
        "arm_b_flags_on_no_statement": sum(d["arm_b"]["flags_on_no_statement"] for d in docs),
        "arm_b_replies_not_parsed": sum(1 for d in docs if d["arm_b"]["ran"] and not d["arm_b"]["parsed"]),
        "splits": {"by_report": {rep: {"planted_flagged": both([r for r in planted if r["report"] == rep]),
                                       "correct_flagged": both([r for r in correct if r["report"] == rep])}
                                 for rep in ("q3", "half", "annual")},
                   "by_group": {g: {"planted_flagged": both([r for r in planted if r["group"] == g]),
                                    "correct_flagged": both([r for r in correct if r["group"] == g])}
                                for g in sorted({r["group"] for r in rows})}},
    }
    out["arm_b_statements_scored"] = len(b_rows)
    return out


def _spread(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {"mean": round(sum(values) / len(values), 2), "min": min(values), "max": max(values)}


def git(*args):
    try:
        return subprocess.run(["git"] + list(args), capture_output=True, text=True, cwd=ROOT).stdout.strip()
    except OSError:
        return ""


def line(label, r):
    if r["n"] == 0:
        return f"{label}: none"
    lo, hi = r["wilson95"]
    return f"{label}: {r['k']} of {r['n']} ({r['share']:.1%}, 95% interval {lo:.1%} to {hi:.1%})"


def main(argv=None):
    ap = argparse.ArgumentParser(description="E5 scorer (docs/eval-plan.md, section E5)")
    ap.add_argument("run", help="a run folder, or bench/e5/records/<set>")
    ap.add_argument("--docs", help="the folder with the documents' gold files (default: taken from the run)")
    ap.add_argument("--write", action="store_true", help="write bench/e5/results/ and a line in bench/e5/runs.jsonl")
    args = ap.parse_args(argv)
    run = os.path.abspath(args.run)
    meta = read_json(os.path.join(run, "run.json")) if os.path.isfile(os.path.join(run, "run.json")) else {}
    docs = []
    for corp in sorted(os.listdir(run)):
        doc_dir = os.path.join(run, corp)
        if not os.path.isdir(doc_dir) or not os.path.isdir(os.path.join(doc_dir, "A")):
            continue
        gold_path = next((p for p in (os.path.join(doc_dir, "gold.json"),
                                      os.path.join(args.docs or "", corp, "gold.json"),
                                      os.path.join(HERE, "out", meta.get("set", "dev"), "docs", corp, "gold.json"))
                          if os.path.isfile(p)), None)
        if gold_path is None:
            print(f"error: no gold file for {corp}", file=sys.stderr)
            return 2
        docs.append(score_document(doc_dir, read_json(gold_path)))
    if not docs:
        print("error: no documents in that folder", file=sys.stderr)
        return 2
    summary = summarise(docs)
    result = {"scored": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "run": os.path.relpath(run, ROOT).replace("\\", "/"), "run_meta": meta,
              "commit": git("rev-parse", "--short", "HEAD"), "dirty": bool(git("status", "--porcelain")),
              "summary": summary, "documents": docs}
    s = summary
    print(f"{s['documents']} documents, {s['statements']} statements: {s['planted']} planted, {s['correct']} correct")
    for arm_name, label in (("arm_a", "arm A (skill and script)"), ("arm_b", "arm B (model alone)")):
        print(line(f"  planted flagged, {label}", s["1_planted_flagged"][arm_name]))
        print(line(f"  correct flagged, {label}", s["2_correct_flagged"][arm_name]))
    for k in KINDS:
        print(line(f"  kind {k}, arm A", s["1_planted_flagged"]["by_kind"][k]["arm_a"]), "|",
              line("arm B", s["1_planted_flagged"]["by_kind"][k]["arm_b"]))
    print("  arm A verdicts, planted:", s["3_arm_a_verdicts"]["planted"], "correct:", s["3_arm_a_verdicts"]["correct"])
    print("  arm A locators:", line("same cells", s["4_arm_a_locators"]["same_cells"]),
          "| fields that differ:", {k: v for k, v in s["4_arm_a_locators"]["fields_that_differ"].items() if v},
          "| other cells:", s["4_arm_a_locators"]["other_cells"])
    print("  arm A repairs:", s["5_arm_a_repairs"])
    print("  arm A other:", s["6_arm_a_other"])
    print("  cost per document: arm A US$", s["7_cost"]["arm_a"]["usd_per_document"], "arm B US$", s["7_cost"]["arm_b"]["usd_per_document"])
    if args.write:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = f"{meta.get('set', 'run')}-{result['commit'] or 'nogit'}-{stamp}"
        os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
        with open(os.path.join(HERE, "results", name + ".json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
            f.write("\n")
        with open(os.path.join(HERE, "runs.jsonl"), "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"time": result["scored"], "set": meta.get("set"), "commit": result["commit"],
                                "dirty": result["dirty"], "run": result["run"], "model": meta.get("model"),
                                "summary": {k: summary[k] for k in ("documents", "statements", "planted", "correct",
                                                                    "1_planted_flagged", "2_correct_flagged")}},
                               ensure_ascii=False) + "\n")
        print("written:", os.path.join("bench", "e5", "results", name + ".json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
