#!/usr/bin/env python3
"""E2 harness: a model writes the plan and the script checks it (arm A), or the model reviews alone (arm B).

The rules are in docs/eval-plan.md, section E2 and "E2 details". This script needs the `anthropic` package and an
API key (ANTHROPIC_API_KEY, or --key-file). Every request is recorded in bench/e2/spend.jsonl, and the script
refuses to go past the cap.

  python bench/e2/run.py --rehearsal --dry-run                      # no request: prompt sizes and a cost estimate
  python bench/e2/run.py --rehearsal --fake                         # no request: the gold plan stands in for the model
  python bench/e2/run.py --rehearsal --approved-estimate 0.02       # 4 development documents
  python bench/e2/run.py --held-out --approved-estimate 0.02        # the 30 held-out documents (release run)
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TOOL = os.path.join(ROOT, "skills", "receipts", "scripts", "receipts.py")
SKILL = os.path.join(ROOT, "skills", "receipts", "SKILL.md")
SPEND = os.path.join(HERE, "spend.jsonl")
DEV_SEED, HELD_OUT_SEED = 20261119, 20261120

MODEL = "claude-haiku-5-5"
EFFORT = "medium"
MAX_TOKENS = 16000
PLANS_PER_DOCUMENT = 3            # the first plan and up to two corrected ones
CAP_USD = 5.00
RESERVE_USD = 0.03                # room kept for the request about to be made
# US$ per million tokens for prompts of at most 100,000 tokens, and for longer ones
RATES = {"short": (0.10, 0.50), "long": (0.50, 2.50)}

HARNESS_NOTE = """

## How this session works

You cannot run commands or read files here. The harness does steps 1, 2 and 5 for you. The first message holds the
document with line numbers, the output of `scan`, and every source file. Reply with the contents of `claims.json`
and nothing else. The harness runs `check` and sends you what it printed. You may then send a corrected
`claims.json`, at most two times (step 6), or reply with the single word DONE when no correction is needed.
Step 7 is not needed here.
"""

BASELINE_SYSTEM = """You are a careful reviewer. You are given a report and the source files its numbers come from.

Check every number in the report against the sources. Look each value up or recompute it: differences, percent
changes, ratios, shares, sums, averages, minimums, maximums and counts. Allow for normal rounding at the precision the
report uses. Check that words such as "rose" and "fell" agree with the direction of the change. Mind the units the
sources state. Do not guess: when a number cannot be checked from the sources, list it under could_not_check.

Reply with one JSON object and nothing else:
{"wrong": [{"line": <line number>, "number": "<the number exactly as written>", "should_be": "<your value>",
            "why": "<a few words>"}],
 "could_not_check": [{"line": <line number>, "number": "<as written>"}]}
"""


class Stop(Exception):
    """The run cannot go on (exit code 2)."""


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_tool(path):
    spec = importlib.util.spec_from_file_location("receipts_tool", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read(path) -> str:
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- documents

def choose_documents(manifest, per_language):
    """The first documents of each language, in manifest order."""
    chosen = []
    for lang in ("en", "ko"):
        chosen += [d for d in manifest["docs"] if d["lang"] == lang][:per_language]
    return chosen


def render_source(tool, path) -> str:
    if not path.lower().endswith(".xlsx"):
        return read(path)
    kind, sheets = tool.load_source(path)
    out = []
    for name, grid in sheets.items():
        out.append(f"[sheet {name}]")
        rows = max((r for r, _ in grid), default=0)
        cols = max((c for _, c in grid), default=0)
        for r in range(1, rows + 1):
            out.append("\t".join(tool._cell_str(grid.get((r, c))) for c in range(1, cols + 1)))
        out.append("")
    return "\n".join(out)


def build_prompts(tool, tool_path, doc_dir):
    gold = json.loads(read(os.path.join(doc_dir, "gold.json")))
    lines = read(os.path.join(doc_dir, "report.md")).splitlines()
    numbered = "\n".join(f"{i}| {ln}" for i, ln in enumerate(lines, start=1))
    scan = subprocess.run([sys.executable, tool_path, "scan", os.path.join(doc_dir, "report.md")], capture_output=True,
                          env=dict(os.environ, PYTHONUTF8="1"))
    if scan.returncode != 0:
        raise Stop(f"scan failed on {doc_dir}: {scan.stderr.decode('utf-8', 'replace')[:300]}")
    scan_doc = json.loads(scan.stdout.decode("utf-8"))
    scan_doc["document"] = "report.md"
    sources = []
    for name in gold["source_files"]:
        note = " (an Excel file, shown sheet by sheet as tab-separated rows; row 1 of a sheet is its header)" \
            if name.lower().endswith(".xlsx") else ""
        sources.append(f"--- {name}{note} ---\n{render_source(tool, os.path.join(doc_dir, name))}")
    shared = (f"Document `report.md`. The number and bar at the start of each line are its line number and are not "
              f"part of the file.\n\n{numbered}\n\n")
    files = "Source files, in the same folder as the document:\n\n" + "\n\n".join(sources) + "\n"
    arm_a = (shared + "Output of `receipts.py scan report.md`:\n\n" + json.dumps(scan_doc, ensure_ascii=False, indent=1)
             + "\n\n" + files + "\nWrite `claims.json` for this document. Reply with the JSON only.")
    arm_b = shared + files + "\nReview the report. Reply with the JSON object only."
    return gold, arm_a, arm_b


def extract_json(text):
    """The first JSON object in a reply, or None."""
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        start = text.find("{", start + 1)
    return None


# ---------------------------------------------------------------- spending

def spent_so_far() -> float:
    if not os.path.isfile(SPEND):
        return 0.0
    total = 0.0
    with open(SPEND, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                total += float(json.loads(line)["usd"])
    return total


def cost_of(usage) -> float:
    fresh = usage.get("input_tokens", 0) or 0
    written = usage.get("cache_creation_input_tokens", 0) or 0
    cached = usage.get("cache_read_input_tokens", 0) or 0
    rate_in, rate_out = RATES["long" if fresh + written + cached > 100_000 else "short"]
    return ((fresh + 1.25 * written + 0.1 * cached) * rate_in + (usage.get("output_tokens", 0) or 0) * rate_out) / 1e6


class Model:
    """One place that talks to the API, records every request, and enforces the cap."""

    def __init__(self, key_file, set_name, cap):
        import anthropic
        self.anthropic = anthropic
        key = read(key_file).strip() if key_file else None
        self.client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        self.set_name, self.cap, self.run_usd = set_name, cap, 0.0

    def ask(self, system, messages, doc, arm, request_no):
        spent = spent_so_far()
        if spent + RESERVE_USD > self.cap:
            raise Stop(f"the spending cap would be passed: US${spent:.4f} spent of US${self.cap:.2f}. No request made.")
        a = self.anthropic
        try:
            resp = self.client.messages.create(model=MODEL, max_tokens=MAX_TOKENS, system=system, messages=messages,
                                               output_config={"effort": EFFORT})
        except a.RateLimitError as e:
            raise Stop(f"rate limited after the SDK's retries: {e.message}") from None
        except a.APIStatusError as e:
            raise Stop(f"the API answered {e.status_code}: {e.message}") from None
        except a.APIConnectionError as e:
            raise Stop(f"no connection to the API: {e}") from None
        usage = resp.usage.to_dict()
        usd = cost_of(usage)
        self.run_usd += usd
        row = {"time": utc_now(), "set": self.set_name, "doc": doc, "arm": arm, "request": request_no, "model": resp.model,
               "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
               "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
               "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) or 0,
               "usd": round(usd, 8), "stop_reason": resp.stop_reason, "request_id": resp._request_id}
        with open(SPEND, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row) + "\n")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return resp, text, row


class FakeModel:
    """Stands in for the API without any request: the gold plan for arm A, an empty review for arm B."""

    def __init__(self):
        self.run_usd = 0.0
        self.gold_plan = None

    def ask(self, system, messages, doc, arm, request_no):
        text = json.dumps(self.gold_plan) if arm == "A" and request_no == 1 else \
            "DONE" if arm == "A" else json.dumps({"wrong": [], "could_not_check": []})
        row = {"time": utc_now(), "doc": doc, "arm": arm, "request": request_no, "model": "fake", "input_tokens": 0,
               "output_tokens": 0, "usd": 0.0, "stop_reason": "end_turn"}

        class Resp:
            content = [{"type": "text", "text": text}]
            stop_reason = "end_turn"

            @staticmethod
            def to_dict():
                return {"content": Resp.content, "stop_reason": "end_turn", "model": "fake"}
        return Resp, text, row


# ---------------------------------------------------------------- the two arms

def run_arm_a(model, system, first_message, doc, doc_dir, out_dir, tool_path, gold):
    work = os.path.join(out_dir, "work")
    os.makedirs(work, exist_ok=True)
    for name in ["report.md"] + gold["source_files"]:
        shutil.copyfile(os.path.join(doc_dir, name), os.path.join(work, name))
    messages = [{"role": "user", "content": first_message}]
    requests, transcript, accepted, plans, done_word = [], [], None, 0, False
    for request_no in range(1, PLANS_PER_DOCUMENT + 1):
        resp, text, row = model.ask(system, messages, doc, "A", request_no)
        requests.append(row)
        transcript.append(resp.to_dict())
        if resp.stop_reason == "refusal":
            break
        if text.strip().strip("`. ").upper() == "DONE":
            done_word = True
            break
        plan = extract_json(text)
        if plan is None:
            note = "Your reply held no JSON object" + (" (the reply was cut off at the output limit)"
                                                        if resp.stop_reason == "max_tokens" else "")
            feedback = note + ". Reply with the full contents of claims.json and nothing else."
        else:
            plans += 1
            plan_dir = os.path.join(out_dir, f"plan{plans}")
            write_json(os.path.join(work, "claims.json"), plan)
            write_json(os.path.join(plan_dir, "claims.json"), plan)
            run = subprocess.run([sys.executable, tool_path, "check", os.path.join(work, "claims.json"), "--out",
                                  plan_dir, "--quiet"], capture_output=True, env=dict(os.environ, PYTHONUTF8="1"))
            if run.returncode in (0, 1) and os.path.isfile(os.path.join(plan_dir, "receipts.json")):
                accepted = os.path.join(f"plan{plans}", "receipts.json")
                feedback = (f"`check` ran (exit code {run.returncode}). It printed:\n\n"
                            + read(os.path.join(plan_dir, "receipts.md")))
            else:
                feedback = ("`check` stopped (exit code " + str(run.returncode) + ") and checked nothing:\n\n"
                            + run.stderr.decode("utf-8", "replace")[:2000])
        if request_no == PLANS_PER_DOCUMENT:
            break
        left = PLANS_PER_DOCUMENT - request_no
        feedback += (f"\n\nIf your plan needs a correction, reply with the full corrected claims.json "
                     f"({left} correction{'s' if left > 1 else ''} left). If it needs none, reply DONE.")
        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user", "content": feedback})
    result = {"doc": doc, "arm": "A", "requests": requests, "plans_sent": plans, "said_done": done_word,
              "accepted_receipts": accepted, "refused": any(r["stop_reason"] == "refusal" for r in requests),
              "usd": round(sum(r["usd"] for r in requests), 8)}
    write_json(os.path.join(out_dir, "transcript.json"), transcript)
    write_json(os.path.join(out_dir, "result.json"), result)
    return result


def run_arm_b(model, first_message, doc, out_dir):
    resp, text, row = model.ask(BASELINE_SYSTEM, [{"role": "user", "content": first_message}], doc, "B", 1)
    answer = extract_json(text)
    result = {"doc": doc, "arm": "B", "requests": [row], "answer": answer, "parsed": answer is not None,
              "refused": resp.stop_reason == "refusal", "usd": round(row["usd"], 8)}
    write_json(os.path.join(out_dir, "transcript.json"), [resp.to_dict()])
    write_json(os.path.join(out_dir, "result.json"), result)
    return result


# ---------------------------------------------------------------- estimate

def tokens_guess(text: str) -> int:
    """A deliberately high guess, used only before the first request. Real counts come from the API's usage."""
    plain = sum(1 for ch in text if ord(ch) < 128)
    return int(plain / 3.0 + (len(text) - plain) * 1.2) + 1


def estimate(prompts, system_a):
    """prompts: list of (doc id, arm A message, arm B message, gold plan text)."""
    rate_in, rate_out = RATES["short"]
    feedback, thinking = 1500, 3000
    rows, expected, upper = [], 0.0, 0.0
    for doc, a, b, plan in prompts:
        in_a = tokens_guess(system_a) + tokens_guess(a)
        in_b = tokens_guess(BASELINE_SYSTEM) + tokens_guess(b)
        out_plan = int(tokens_guess(plan) * 1.2) + thinking
        exp_in = in_a + (in_a + out_plan + feedback) + in_b              # two arm A requests, one arm B request
        exp_out = out_plan + out_plan // 2 + 4000
        up_in = in_a * 3 + MAX_TOKENS * 3 + feedback * 3 + in_b          # three arm A requests at the output limit
        up_out = MAX_TOKENS * 4
        e = (exp_in * rate_in + exp_out * rate_out) / 1e6
        u = (up_in * rate_in + up_out * rate_out) / 1e6
        rows.append({"doc": doc, "arm_a_first_prompt_tokens": in_a, "arm_b_prompt_tokens": in_b,
                     "expected_usd": round(e, 5), "upper_usd": round(u, 5)})
        expected += e
        upper += u
    return {"documents": len(rows), "expected_usd": round(expected, 4), "upper_usd": round(upper, 4),
            "expected_usd_per_document": round(expected / max(len(rows), 1), 5),
            "upper_usd_per_document": round(upper / max(len(rows), 1), 5), "rows": rows,
            "note": "token counts are guessed high from character counts; no request was made"}


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="E2 harness for receipts (see docs/eval-plan.md).")
    ap.add_argument("--rehearsal", action="store_true", help="4 development-seed documents (2 English, 2 Korean)")
    ap.add_argument("--held-out", action="store_true", help="the 30 held-out documents: the release run")
    ap.add_argument("--dry-run", action="store_true", help="make no request: print prompt sizes and a cost estimate")
    ap.add_argument("--fake", action="store_true", help="make no request: the gold plan stands in for the model")
    ap.add_argument("--approved-estimate", type=float, help="the approved cost per document in US$ (needed to run)")
    ap.add_argument("--key-file", help="a file that holds the API key (default: ANTHROPIC_API_KEY)")
    ap.add_argument("--tool", default=TOOL)
    ap.add_argument("--data", help="the generated benchmark folder (default: bench/out/<seed>)")
    ap.add_argument("--out", help="output folder (default: bench/e2/out/<set>)")
    ap.add_argument("--cap", type=float, default=CAP_USD, help="spending cap for all of E2 in US$ (never above 5)")
    a = ap.parse_args(argv)
    if a.rehearsal == a.held_out:
        print("choose exactly one of --rehearsal and --held-out", file=sys.stderr)
        return 2
    set_name, seed, per_lang = ("heldout", HELD_OUT_SEED, 15) if a.held_out else ("rehearsal", DEV_SEED, 2)
    cap = min(a.cap, CAP_USD)
    data = a.data or os.path.join(ROOT, "bench", "out", str(seed))
    out_root = a.out or os.path.join(HERE, "out", set_name + ("-fake" if a.fake else ""))
    try:
        if not os.path.isfile(os.path.join(data, "manifest.json")):
            raise Stop(f"no generated benchmark at {data}: run bench/generate.py for seed {seed} first")
        manifest = json.loads(read(os.path.join(data, "manifest.json")))
        docs = choose_documents(manifest, per_lang)
        tool = load_tool(a.tool)
        skill = read(SKILL)
        if skill.startswith("---"):
            skill = skill.split("---", 2)[2].lstrip("\n")
        system_a = skill + HARNESS_NOTE
        prepared = []
        for d in docs:
            doc_dir = os.path.join(data, "docs", d["id"])
            gold, msg_a, msg_b = build_prompts(tool, a.tool, doc_dir)
            prepared.append((d, doc_dir, gold, msg_a, msg_b))
        est = estimate([(d["id"], ma, mb, read(os.path.join(dd, "claims.gold.json"))) for d, dd, _, ma, mb in prepared],
                       system_a)
        if a.dry_run:
            est.update(set=set_name, seed=seed, spent_so_far_usd=round(spent_so_far(), 4), cap_usd=cap)
            print(json.dumps(est, ensure_ascii=False, indent=1))
            return 0
        if a.fake:
            model = FakeModel()
        else:
            if a.approved_estimate is None:
                raise Stop("--approved-estimate is needed: the cost per document that was shown and approved")
            model = Model(a.key_file, set_name, cap)
        meta = {"set": set_name, "seed": seed, "started": utc_now(), "model": MODEL, "effort": EFFORT,
                "max_tokens": MAX_TOKENS, "plans_per_document": PLANS_PER_DOCUMENT, "cap_usd": cap,
                "approved_estimate_usd_per_document": a.approved_estimate, "documents": [d["id"] for d in docs],
                "spent_before_usd": round(spent_so_far(), 6), "fake": a.fake}
        write_json(os.path.join(out_root, "run.json"), meta)
        write_json(os.path.join(out_root, "prompts.json"), {"arm_a_system": system_a, "arm_b_system": BASELINE_SYSTEM})
        done_cost, finished = 0.0, 0
        for d, doc_dir, gold, msg_a, msg_b in prepared:
            doc, cost = d["id"], 0.0
            for arm, runner in (("A", None), ("B", None)):
                out_dir = os.path.join(out_root, doc, arm)
                done = os.path.join(out_dir, "result.json")
                if os.path.isfile(done):
                    cost += json.loads(read(done))["usd"]
                    continue
                if a.fake:
                    model.gold_plan = json.loads(read(os.path.join(doc_dir, "claims.gold.json")))
                res = run_arm_a(model, system_a, msg_a, doc, doc_dir, out_dir, a.tool, gold) if arm == "A" \
                    else run_arm_b(model, msg_b, doc, out_dir)
                cost += res["usd"]
            finished += 1
            done_cost += cost
            print(f"[{finished}/{len(prepared)}] {doc} ({d['lang']}, {d['source_type']}): US${cost:.4f}", flush=True)
            if finished == 5 and a.approved_estimate and done_cost > 1.5 * 5 * a.approved_estimate:
                raise Stop(f"the first 5 documents cost US${done_cost:.4f}, more than 1.5 times the approved "
                           f"US${5 * a.approved_estimate:.4f}. Stopped as the plan requires.")
        meta.update(finished=utc_now(), spent_this_run_usd=round(done_cost, 6),
                    spent_total_usd=round(spent_so_far(), 6))
        write_json(os.path.join(out_root, "run.json"), meta)
        print(f"done: {finished} documents, US${done_cost:.4f} in this set; US${spent_so_far():.4f} spent in all of E2")
        return 0
    except Stop as e:
        print(f"e2: stopped: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
