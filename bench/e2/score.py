#!/usr/bin/env python3
"""Score an E2 run (see docs/eval-plan.md, section E2 and "E2 details"). No request is made.

  python bench/e2/score.py --set rehearsal
  python bench/e2/score.py --set heldout --held-out
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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SEEDS = {"rehearsal": 20261119, "rehearsal-fake": 20261119, "heldout": 20261120, "heldout-fake": 20261120}


def read_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def wilson(k: int, n: int, z: float = 1.959964):
    """Wilson 95% interval for k of n."""
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def share(k, n):
    return {"k": k, "n": n, "share": round(k / n, 4) if n else None, "wilson95": wilson(k, n)}


def number_parts(text):
    """The digit groups of a written number, commas removed: '5,970억 5,000만' -> ['5970', '5000']."""
    return [g.replace(",", "") for g in re.findall(r"\d[\d,]*(?:\.\d+)?", str(text))]


def names_number(flag_text, statement_text) -> bool:
    """True when the flagged text contains the statement's number as whole digit groups, in order."""
    want, have = number_parts(statement_text), number_parts(flag_text)
    if not want:
        return False
    return any(have[i:i + len(want)] == want for i in range(len(have) - len(want) + 1))


def git(*args) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def score(out_root, data):
    run = read_json(os.path.join(out_root, "run.json"))
    arm_a = {"planted": {}, "correct": {}}
    arm_b = {"planted": {"flagged": 0, "not_flagged": 0}, "correct": {"flagged": 0, "not_flagged": 0}}
    totals = {"documents": 0, "statements": 0, "planted": 0, "correct": 0}
    a_extra = {"numbers": 0, "uncovered": 0, "plans_sent": 0, "corrected_plans": 0, "no_accepted_plan": 0, "refused": 0,
               "requests": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0}
    b_extra = {"unparsed": 0, "refused": 0, "flags": 0, "unmatched_flags": 0, "could_not_check": 0, "requests": 0,
               "input_tokens": 0, "output_tokens": 0, "usd": 0.0}
    per_doc, by_lang = [], {}
    for doc in run["documents"]:
        gold = read_json(os.path.join(data, "docs", doc, "gold.json"))
        res_a = read_json(os.path.join(out_root, doc, "A", "result.json"))
        res_b = read_json(os.path.join(out_root, doc, "B", "result.json"))
        totals["documents"] += 1
        # arm A: the verdict of every statement's number
        verdicts = {}
        if res_a["accepted_receipts"]:
            rec = read_json(os.path.join(out_root, doc, "A", res_a["accepted_receipts"]))
            for c in rec["claims"]:
                if c.get("counted", True):
                    verdicts[(c["line"], c["start"], c["end"])] = c["verdict"]
            for u in rec["uncovered"]:
                verdicts[(u["line"], u["start"], u["end"])] = "uncovered"
            a_extra["numbers"] += rec["counts"]["numbers"]
            a_extra["uncovered"] += rec["counts"]["uncovered"]
        else:
            a_extra["no_accepted_plan"] += 1
        a_extra["plans_sent"] += res_a["plans_sent"]
        a_extra["corrected_plans"] += max(res_a["plans_sent"] - 1, 0)
        a_extra["refused"] += int(res_a["refused"])
        for r in res_a["requests"]:
            a_extra["requests"] += 1
            a_extra["input_tokens"] += r["input_tokens"]
            a_extra["output_tokens"] += r["output_tokens"]
            a_extra["usd"] += r["usd"]
        # arm B: flags by line and digits
        answer = res_b.get("answer") or {}
        flags = [f for f in answer.get("wrong", []) if isinstance(f, dict)] if isinstance(answer.get("wrong"), list) else []
        b_extra["unparsed"] += int(not res_b["parsed"])
        b_extra["refused"] += int(res_b["refused"])
        b_extra["flags"] += len(flags)
        b_extra["could_not_check"] += len(answer.get("could_not_check", []) or []) if isinstance(answer, dict) else 0
        for r in res_b["requests"]:
            b_extra["requests"] += 1
            b_extra["input_tokens"] += r["input_tokens"]
            b_extra["output_tokens"] += r["output_tokens"]
            b_extra["usd"] += r["usd"]
        used = set()
        row = {"doc": doc, "lang": gold["lang"], "source_type": gold["source_type"], "planted": 0,
               "a_planted_wrong": 0, "a_correct_wrong": 0, "b_planted_flagged": 0, "b_correct_flagged": 0}
        lang = by_lang.setdefault(gold["lang"], {"planted": 0, "correct": 0, "a_planted_wrong": 0, "a_correct_wrong": 0,
                                                 "b_planted_flagged": 0, "b_correct_flagged": 0})
        for s in gold["statements"]:
            kind = "planted" if s["planted"] else "correct"
            totals["statements"] += 1
            totals[kind] += 1
            lang[kind] += 1
            row["planted"] += int(kind == "planted")
            v = verdicts.get((s["line"], s["start"], s["end"]), "not_checked" if not res_a["accepted_receipts"]
                             else "no_verdict")
            arm_a[kind][v] = arm_a[kind].get(v, 0) + 1
            hit = None
            for i, f in enumerate(flags):
                try:
                    same_line = int(f.get("line")) == s["line"]
                except (TypeError, ValueError):
                    same_line = False
                if same_line and names_number(f.get("number"), s["text"]):
                    hit = i
                    break
            if hit is not None:
                used.add(hit)
            arm_b[kind]["flagged" if hit is not None else "not_flagged"] += 1
            if v == "wrong":
                row[f"a_{kind}_wrong"] += 1
                lang[f"a_{kind}_wrong"] += 1
            if hit is not None:
                row[f"b_{kind}_flagged"] += 1
                lang[f"b_{kind}_flagged"] += 1
        b_extra["unmatched_flags"] += len(flags) - len(used)
        row.update(a_usd=round(res_a["usd"], 6), b_usd=round(res_b["usd"], 6), a_requests=len(res_a["requests"]))
        per_doc.append(row)
    n = max(totals["documents"], 1)
    measures = {
        "arm_a": {"planted_flagged": share(arm_a["planted"].get("wrong", 0), totals["planted"]),
                  "correct_flagged": share(arm_a["correct"].get("wrong", 0), totals["correct"]),
                  "planted_verdicts": dict(sorted(arm_a["planted"].items())),
                  "correct_verdicts": dict(sorted(arm_a["correct"].items())),
                  "uncovered_share": share(a_extra["uncovered"], a_extra["numbers"]),
                  "usd_per_document": round(a_extra["usd"] / n, 6), "requests_per_document": round(a_extra["requests"] / n, 2),
                  **{k: (round(v, 6) if isinstance(v, float) else v) for k, v in a_extra.items()}},
        "arm_b": {"planted_flagged": share(arm_b["planted"]["flagged"], totals["planted"]),
                  "correct_flagged": share(arm_b["correct"]["flagged"], totals["correct"]),
                  "usd_per_document": round(b_extra["usd"] / n, 6), "requests_per_document": round(b_extra["requests"] / n, 2),
                  **{k: (round(v, 6) if isinstance(v, float) else v) for k, v in b_extra.items()}},
    }
    return {"run": run, "totals": totals, "measures": measures, "by_language": by_lang, "documents": per_doc}


def render(res) -> str:
    t, m = res["totals"], res["measures"]

    def line(label, s):
        if s["n"] == 0:
            return f"| {label} | 0 of 0 | n/a |"
        lo, hi = s["wilson95"]
        return f"| {label} | {s['k']} of {s['n']} ({s['share']:.1%}) | {lo:.1%} to {hi:.1%} |"

    out = [f"# E2 result: {res['run']['set']}", "",
           f"{t['documents']} documents, {t['statements']} statements ({t['planted']} with a planted error, "
           f"{t['correct']} correct). Model {res['run']['model']}, effort {res['run']['effort']}.", "",
           "| Measure | Count | Wilson 95% interval |", "|---|---|---|",
           line("Arm A (skill): planted errors flagged", m["arm_a"]["planted_flagged"]),
           line("Arm B (review only): planted errors flagged", m["arm_b"]["planted_flagged"]),
           line("Arm A (skill): correct statements flagged", m["arm_a"]["correct_flagged"]),
           line("Arm B (review only): correct statements flagged", m["arm_b"]["correct_flagged"]),
           line("Arm A: numbers left uncovered", m["arm_a"]["uncovered_share"]), "",
           f"Arm A verdicts on planted errors: {m['arm_a']['planted_verdicts']}", "",
           f"Arm A verdicts on correct statements: {m['arm_a']['correct_verdicts']}", "",
           f"Arm A: {m['arm_a']['requests']} requests, {m['arm_a']['corrected_plans']} corrected plans, "
           f"{m['arm_a']['no_accepted_plan']} documents with no accepted plan, US${m['arm_a']['usd_per_document']:.4f} "
           "per document.", "",
           f"Arm B: {m['arm_b']['requests']} requests, {m['arm_b']['unmatched_flags']} flags that match no statement, "
           f"{m['arm_b']['unparsed']} replies that could not be read, US${m['arm_b']['usd_per_document']:.4f} per document.",
           ""]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Score an E2 run.")
    ap.add_argument("--set", default="rehearsal", choices=sorted(SEEDS))
    ap.add_argument("--held-out", action="store_true", help="required to score the held-out set")
    ap.add_argument("--out", help="the run folder (default: bench/e2/out/<set>)")
    ap.add_argument("--data", help="the generated benchmark folder (default: bench/out/<seed>)")
    ap.add_argument("--results-dir", default=os.path.join(HERE, "results"))
    ap.add_argument("--runs", default=os.path.join(HERE, "runs.jsonl"))
    ap.add_argument("--no-log", action="store_true", help="print only: write no results file and no log line")
    a = ap.parse_args(argv)
    if a.set.startswith("heldout") and not a.held_out:
        print("refusing the held-out set without --held-out", file=sys.stderr)
        return 2
    out_root = a.out or os.path.join(HERE, "out", a.set)
    data = a.data or os.path.join(ROOT, "bench", "out", str(SEEDS[a.set]))
    if not os.path.isfile(os.path.join(out_root, "run.json")):
        print(f"no run at {out_root}", file=sys.stderr)
        return 2
    res = score(out_root, data)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    res.update(timestamp=stamp, commit=git("rev-parse", "--short", "HEAD"), dirty=bool(git("status", "--porcelain")))
    text = render(res)
    print(text)
    if a.no_log or res["run"].get("fake"):
        return 0
    os.makedirs(a.results_dir, exist_ok=True)
    base = os.path.join(a.results_dir, f"{a.set}-{res['commit']}-{stamp}")
    with open(base + ".json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    with open(base + ".md", "w", encoding="utf-8", newline="\n") as f:
        f.write(text + "\n")
    with open(a.runs, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"timestamp": stamp, "set": a.set, "commit": res["commit"], "dirty": res["dirty"],
                            "totals": res["totals"], "measures": res["measures"]}, ensure_ascii=False) + "\n")
    print(f"results: {base}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
