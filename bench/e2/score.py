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
SPEND = os.path.join(HERE, "spend.jsonl")
DEV_SEED, HELD_OUT_SEED = 20261119, 20261120


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


def unit_group(gold, gold_plan_text) -> str:
    """Whether the document's sources are scaled, and whether they say so. From how the generator writes sources:
    JSON and Markdown always carry a unit note, Excel only when its header is on row 2, CSV never."""
    if not gold.get("scaled"):
        return "not scaled"
    stated = gold["source_type"] in ("json", "md") or (gold["source_type"] == "xlsx" and '"header_row": 2' in gold_plan_text)
    return "scaled, unit stated" if stated else "scaled, unit not stated"


def bump(table, group, key, n=1):
    row = table.setdefault(group, {"planted": 0, "correct": 0, "a_planted_flagged": 0, "a_correct_flagged": 0,
                                   "b_planted_flagged": 0, "b_correct_flagged": 0})
    row[key] += n


def score(out_root, data):
    run = read_json(os.path.join(out_root, "run.json"))
    set_name = run["set"]
    spend = {}
    if os.path.isfile(SPEND):
        with open(SPEND, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    if r.get("set") == set_name and r["doc"] in run["documents"]:
                        s = spend.setdefault((r["doc"], r["arm"]), {"requests": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0})
                        s["requests"] += 1
                        s["input_tokens"] += r["input_tokens"]
                        s["output_tokens"] += r["output_tokens"]
                        s["usd"] += r["usd"]
    arm_a = {"planted": {}, "correct": {}}
    arm_b = {"planted": {"flagged": 0, "not_flagged": 0}, "correct": {"flagged": 0, "not_flagged": 0}}
    # Not the registered rule: any flag on the statement's line counts (see heldout-recount-after-audit.md).
    arm_b_line = {"planted": 0, "correct": 0}
    totals = {"documents": 0, "statements": 0, "planted": 0, "correct": 0}
    a_extra = {"numbers": 0, "uncovered": 0, "plans_sent": 0, "corrected_plans": 0, "no_accepted_plan": 0, "refused": 0,
               "cut_off_replies": 0, "requests": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0}
    b_extra = {"unparsed": 0, "refused": 0, "cut_off_replies": 0, "flags": 0, "unmatched_flags": 0, "could_not_check": 0,
               "requests": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0}
    per_doc, not_run, by_lang, by_source, by_unit, by_trunc = [], [], {}, {}, {}, {}
    for doc in run["documents"]:
        path_a = os.path.join(out_root, doc, "A", "result.json")
        path_b = os.path.join(out_root, doc, "B", "result.json")
        if not (os.path.isfile(path_a) and os.path.isfile(path_b)):
            not_run.append(doc)                      # a run that was stopped: the document is left out and named
            continue
        gold = read_json(os.path.join(data, "docs", doc, "gold.json"))
        with open(os.path.join(data, "docs", doc, "claims.gold.json"), encoding="utf-8") as f:
            units = unit_group(gold, f.read())
        res_a, res_b = read_json(path_a), read_json(path_b)
        totals["documents"] += 1
        # arm A: the verdict of every statement's number, from the last plan the script accepted
        verdicts = {}
        if res_a["accepted_receipts"]:
            rec = read_json(os.path.join(out_root, doc, "A", *re.split(r"[\\/]", res_a["accepted_receipts"])))
            for c in rec["claims"]:
                if c.get("counted", True):
                    verdicts[(c["line"], c["start"], c["end"])] = c["verdict"]
            for u in rec["uncovered"]:
                verdicts[(u["line"], u["start"], u["end"])] = "uncovered"
            a_extra["numbers"] += rec["counts"]["numbers"]
            a_extra["uncovered"] += rec["counts"]["uncovered"]
        else:
            a_extra["no_accepted_plan"] += 1         # every number of the document counts as not checked
            numbers = res_a.get("scan_numbers") or len(gold["statements"])
            a_extra["numbers"] += numbers
            a_extra["uncovered"] += numbers
        a_extra["plans_sent"] += res_a["plans_sent"]
        a_extra["corrected_plans"] += max(res_a["plans_sent"] - 1, 0)
        a_extra["refused"] += int(res_a["refused"])
        a_extra["cut_off_replies"] += res_a.get("cut_off_replies", 0)
        answer = res_b.get("answer") or {}
        flags = [f for f in answer.get("wrong", []) if isinstance(f, dict)] if isinstance(answer.get("wrong"), list) else []
        b_extra["unparsed"] += int(not res_b["parsed"])
        b_extra["refused"] += int(res_b["refused"])
        b_extra["cut_off_replies"] += res_b.get("cut_off_replies", 0)
        b_extra["flags"] += len(flags)
        cnc = answer.get("could_not_check")
        b_extra["could_not_check"] += len(cnc) if isinstance(cnc, list) else 0
        for extra, arm in ((a_extra, "A"), (b_extra, "B")):
            for k, v in spend.get((doc, arm), {}).items():
                extra[k] += v
        used = set()
        row = {"doc": doc, "lang": gold["lang"], "source_type": gold["source_type"], "units": units, "planted": 0,
               "a_planted_flagged": 0, "a_correct_flagged": 0, "b_planted_flagged": 0, "b_correct_flagged": 0}
        for s in gold["statements"]:
            kind = "planted" if s["planted"] else "correct"
            groups = [(by_lang, gold["lang"]), (by_source, gold["source_type"]), (by_unit, units)]
            if kind == "correct":
                groups.append((by_trunc, "written cut off (truncated)" if s.get("truncated_ok") else "written rounded"))
            totals["statements"] += 1
            totals[kind] += 1
            row["planted"] += int(kind == "planted")
            for table, group in groups:
                bump(table, group, kind)
            v = verdicts.get((s["line"], s["start"], s["end"]), "not_checked" if not res_a["accepted_receipts"]
                             else "no_verdict")
            arm_a[kind][v] = arm_a[kind].get(v, 0) + 1
            hit, on_line = None, False
            for i, f in enumerate(flags):
                try:
                    same_line = int(f.get("line")) == s["line"]
                except (TypeError, ValueError):
                    same_line = False
                on_line = on_line or same_line
                if hit is None and same_line and names_number(f.get("number"), s["text"]):
                    hit = i
            arm_b_line[kind] += int(on_line)
            if hit is not None:
                used.add(hit)
            arm_b[kind]["flagged" if hit is not None else "not_flagged"] += 1
            for arm, flagged in (("a", v == "wrong"), ("b", hit is not None)):
                if flagged:
                    row[f"{arm}_{kind}_flagged"] += 1
                    for table, group in groups:
                        bump(table, group, f"{arm}_{kind}_flagged")
        b_extra["unmatched_flags"] += len(flags) - len(used)
        row.update(a_usd=round(spend.get((doc, "A"), {}).get("usd", 0.0), 6),
                   b_usd=round(spend.get((doc, "B"), {}).get("usd", 0.0), 6),
                   a_requests=spend.get((doc, "A"), {}).get("requests", 0), a_plans=res_a["plans_sent"])
        per_doc.append(row)
    n = max(totals["documents"], 1)
    tidy = lambda d: {k: (round(v, 6) if isinstance(v, float) else v) for k, v in d.items()}
    measures = {
        "arm_a": {"planted_flagged": share(arm_a["planted"].get("wrong", 0), totals["planted"]),
                  "correct_flagged": share(arm_a["correct"].get("wrong", 0), totals["correct"]),
                  "planted_verdicts": dict(sorted(arm_a["planted"].items())),
                  "correct_verdicts": dict(sorted(arm_a["correct"].items())),
                  "uncovered_share": share(a_extra["uncovered"], a_extra["numbers"]),
                  "usd_per_document": round(a_extra["usd"] / n, 6),
                  "requests_per_document": round(a_extra["requests"] / n, 2), **tidy(a_extra)},
        "arm_b": {"planted_flagged": share(arm_b["planted"]["flagged"], totals["planted"]),
                  "correct_flagged": share(arm_b["correct"]["flagged"], totals["correct"]),
                  "planted_flagged_any_flag_on_line": share(arm_b_line["planted"], totals["planted"]),
                  "correct_flagged_any_flag_on_line": share(arm_b_line["correct"], totals["correct"]),
                  "usd_per_document": round(b_extra["usd"] / n, 6),
                  "requests_per_document": round(b_extra["requests"] / n, 2), **tidy(b_extra)},
    }
    return {"run": run, "totals": totals, "not_run": not_run, "measures": measures,
            "breakdown": {"language": by_lang, "source_type": by_source, "units": by_unit,
                          "correct_statements_by_writing": by_trunc}, "documents": per_doc}


def render(res) -> str:
    t, m = res["totals"], res["measures"]

    def line(label, s):
        if s["n"] == 0:
            return f"| {label} | 0 of 0 | n/a |"
        lo, hi = s["wilson95"]
        return f"| {label} | {s['k']} of {s['n']} ({s['share']:.1%}) | {lo:.1%} to {hi:.1%} |"

    out = [f"# E2 result: {res['run']['set']}", "",
           f"{t['documents']} documents, {t['statements']} statements ({t['planted']} with a planted error, "
           f"{t['correct']} correct). Model {res['run']['model']}, effort {res['run']['effort']}.", ""]
    if res["not_run"]:
        out += [f"Not run (the run was stopped): {', '.join(res['not_run'])}. They are left out of every count.", ""]
    out += ["| Measure | Count | Wilson 95% interval |", "|---|---|---|",
            line("Arm A (skill): planted errors flagged", m["arm_a"]["planted_flagged"]),
            line("Arm B (review only): planted errors flagged", m["arm_b"]["planted_flagged"]),
            line("Arm A (skill): correct statements flagged", m["arm_a"]["correct_flagged"]),
            line("Arm B (review only): correct statements flagged", m["arm_b"]["correct_flagged"]),
            line("Arm B, any flag on the statement's line (not the registered rule): planted errors flagged",
                 m["arm_b"]["planted_flagged_any_flag_on_line"]),
            line("Arm B, any flag on the statement's line (not the registered rule): correct statements flagged",
                 m["arm_b"]["correct_flagged_any_flag_on_line"]),
            line("Arm A: numbers left uncovered", m["arm_a"]["uncovered_share"]), "",
            f"Arm A verdicts on planted errors: {m['arm_a']['planted_verdicts']}", "",
            f"Arm A verdicts on correct statements: {m['arm_a']['correct_verdicts']}", "",
            f"Arm A: {m['arm_a']['requests']} requests, {m['arm_a']['corrected_plans']} corrected plans, "
            f"{m['arm_a']['no_accepted_plan']} documents with no accepted plan, {m['arm_a']['cut_off_replies']} replies cut "
            f"off, US${m['arm_a']['usd_per_document']:.4f} per document.", "",
            f"Arm B: {m['arm_b']['requests']} requests, {m['arm_b']['unmatched_flags']} flags that match no statement, "
            f"{m['arm_b']['unparsed']} replies that could not be read, US${m['arm_b']['usd_per_document']:.4f} per document.",
            ""]
    for title, key in (("By language", "language"), ("By source type", "source_type"),
                       ("By whether the sources state their unit", "units"),
                       ("Correct statements, by how the number is written", "correct_statements_by_writing")):
        out += [f"## {title}", "", "| Group | Planted flagged: A | B | Correct flagged: A | B |", "|---|---|---|---|---|"]
        for group, r in sorted(res["breakdown"][key].items()):
            out.append(f"| {group} | {r['a_planted_flagged']} of {r['planted']} | {r['b_planted_flagged']} of {r['planted']} "
                       f"| {r['a_correct_flagged']} of {r['correct']} | {r['b_correct_flagged']} of {r['correct']} |")
        out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Score an E2 run.")
    ap.add_argument("--set", default="rehearsal", help="the run's folder name under bench/e2/out (heldout, rehearsal, ...)")
    ap.add_argument("--held-out", action="store_true", help="required to score the held-out set")
    ap.add_argument("--out", help="the run folder (default: bench/e2/out/<set>)")
    ap.add_argument("--data", help="the generated benchmark folder (default: bench/out/<seed>)")
    ap.add_argument("--results-dir", default=os.path.join(HERE, "results"))
    ap.add_argument("--runs", default=os.path.join(HERE, "runs.jsonl"))
    ap.add_argument("--no-log", action="store_true", help="print only: write no results file and no log line")
    a = ap.parse_args(argv)
    held = a.set.startswith("heldout")
    if held and not a.held_out:
        print("refusing the held-out set without --held-out", file=sys.stderr)
        return 2
    out_root = a.out or os.path.join(HERE, "out", a.set)
    data = a.data or os.path.join(ROOT, "bench", "out", str(HELD_OUT_SEED if held else DEV_SEED))
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
                            "totals": res["totals"], "not_run": res["not_run"], "measures": res["measures"]},
                           ensure_ascii=False) + "\n")
    print(f"results: {base}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
