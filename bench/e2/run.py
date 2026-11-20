#!/usr/bin/env python3
"""E2 harness: a model writes the plan and the script checks it (arm A), or the model reviews alone (arm B).

The rules are in docs/eval-plan.md, section E2 and "E2 details". This script needs the `anthropic` package and an
API key (ANTHROPIC_API_KEY, or --key-file). Every request is recorded in bench/e2/spend.jsonl, and the script
refuses to go past the cap.

  python bench/e2/run.py --rehearsal --dry-run                      # no request: prompt sizes and a cost estimate
  python bench/e2/run.py --rehearsal --fake                         # no request: the gold plan stands in for the model
  python bench/e2/run.py --rehearsal --approved-estimate 0.02       # 4 development documents
  python bench/e2/run.py --rehearsal --docs d003,d007 --approved-estimate 0.02
  python bench/e2/run.py --held-out --approved-estimate 0.02        # the 30 held-out documents (release run)
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TOOL = os.path.join(ROOT, "skills", "receipts", "scripts", "receipts.py")
SKILL = os.path.join(ROOT, "skills", "receipts", "SKILL.md")
SPEND = os.path.join(HERE, "spend.jsonl")
RECORDS = os.path.join(HERE, "records")
DEV_SEED, HELD_OUT_SEED = 20261119, 20261120

MODEL = "claude-haiku-5-5"
EFFORT = "medium"
MAX_TOKENS = 16000
PLANS_PER_DOCUMENT = 3            # the first plan and up to two corrected ones
CAP_USD = 5.00
RESERVE_USD = 0.03                # room kept for the request about to be made
CHECK_TIMEOUT = 120               # seconds for one run of the script
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
report uses. A value that is cut off at that precision instead of rounded also counts as correct (2.36 for 2.368).
Check that words such as "rose" and "fell" agree with the direction of the change. Mind the units the sources state.
Do not guess: when a number cannot be checked from the sources, list it under could_not_check.

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


def tool_env():
    """The script runs without a contact string and with an empty EDGAR cache, so no plan can reach the SEC."""
    env = dict(os.environ, PYTHONUTF8="1")
    for name in ("RECEIPTS_SEC_USER_AGENT", "RECEIPTS_EDGAR_DATA_URL", "RECEIPTS_EDGAR_WWW_URL", "RECEIPTS_EDGAR_CACHE"):
        env.pop(name, None)
    return env


# ---------------------------------------------------------------- documents

def choose_documents(manifest, per_language, ids=None):
    """The first documents of each language in manifest order, or the documents named."""
    if ids:
        by_id = {d["id"]: d for d in manifest["docs"]}
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise Stop(f"no such document: {', '.join(missing)}")
        return [by_id[i] for i in ids]
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
                          env=tool_env(), timeout=CHECK_TIMEOUT)
    if scan.returncode != 0:
        raise Stop(f"scan failed on {doc_dir}: {scan.stderr.decode('utf-8', 'replace')[:300]}")
    scan_doc = json.loads(scan.stdout.decode("utf-8"))
    scan_doc["document"] = "report.md"
    sources = []
    for name in gold["source_files"]:
        note = " (an Excel file, shown sheet by sheet as tab-separated rows)" if name.lower().endswith(".xlsx") else ""
        sources.append(f"--- {name}{note} ---\n{render_source(tool, os.path.join(doc_dir, name))}")
    shared = (f"Document `report.md`. The number and bar at the start of each line are its line number and are not "
              f"part of the file.\n\n{numbered}\n\n")
    files = "Source files, in the same folder as the document:\n\n" + "\n\n".join(sources) + "\n"
    arm_a = (shared + "Output of `receipts.py scan report.md`:\n\n" + json.dumps(scan_doc, ensure_ascii=False, indent=1)
             + "\n\n" + files + "\nWrite `claims.json` for this document. Reply with the JSON only.")
    arm_b = shared + files + "\nReview the report. Reply with the JSON object only."
    return gold, len(scan_doc["numbers"]), arm_a, arm_b


def extract_json(text, key):
    """The reply's JSON object: the outermost object, which must hold `key` as a list. Anything else is None.

    Only the first `{` is tried. A reply that was cut off therefore reads as None and never as one of its inner objects.
    """
    start = text.find("{")
    if start == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) and isinstance(obj.get(key), list) else None


def says_done(text) -> bool:
    return len(text) < 40 and re.sub(r"[^A-Za-z]", "", text).upper() == "DONE"


# ---------------------------------------------------------------- spending

def spend_rows():
    if not os.path.isfile(SPEND):
        return []
    with open(SPEND, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def spent_so_far() -> float:
    return sum(float(r["usd"]) for r in spend_rows())


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
        self.set_name, self.cap = set_name, cap

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

class Record:
    """What was said, written to disk after every request so that a stopped run loses nothing."""

    def __init__(self, out_dir, system, first_message):
        self.out_dir = out_dir
        self.turns = [{"role": "system", "text": system}, {"role": "user", "text": first_message}]
        self.raw = []
        self.save()

    def reply(self, resp, text, row):
        self.turns.append({"role": "assistant", "text": text, "stop_reason": row["stop_reason"],
                           "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"], "usd": row["usd"]})
        self.raw.append(resp.to_dict())
        self.save()

    def user(self, text):
        self.turns.append({"role": "user", "text": text})
        self.save()

    def save(self):
        write_json(os.path.join(self.out_dir, "conversation.json"), self.turns)
        write_json(os.path.join(self.out_dir, "transcript.json"), self.raw)


def run_arm_a(model, system, first_message, doc, doc_dir, out_dir, tool_path, gold, scan_numbers):
    work = os.path.join(out_dir, "work")
    os.makedirs(work, exist_ok=True)
    for name in ["report.md"] + gold["source_files"]:
        shutil.copyfile(os.path.join(doc_dir, name), os.path.join(work, name))
    record = Record(out_dir, system, first_message)
    messages = [{"role": "user", "content": first_message}]
    requests, accepted, plans, done_word, cut_off = [], None, 0, False, 0
    for request_no in range(1, PLANS_PER_DOCUMENT + 1):
        resp, text, row = model.ask(system, messages, doc, "A", request_no)
        requests.append(row)
        record.reply(resp, text, row)
        if resp.stop_reason == "refusal":
            break
        if says_done(text):
            done_word = True
            break
        cut = resp.stop_reason == "max_tokens"
        cut_off += int(cut)
        plan = None if cut else extract_json(text, "claims")
        if plan is None:
            feedback = ("Your reply was cut off at the output limit, so no plan was read" if cut else
                        "Your reply held no claims.json (one JSON object with a `claims` list)")
            feedback += ". Reply with the full contents of claims.json and nothing else."
        else:
            plans += 1
            plan_dir = os.path.join(out_dir, f"plan{plans}")
            write_json(os.path.join(work, "claims.json"), plan)
            write_json(os.path.join(plan_dir, "claims.json"), plan)
            try:
                run = subprocess.run([sys.executable, tool_path, "check", os.path.join(work, "claims.json"), "--out",
                                      plan_dir, "--quiet"], capture_output=True, env=tool_env(), timeout=CHECK_TIMEOUT)
                code, err = run.returncode, run.stderr.decode("utf-8", "replace")
            except subprocess.TimeoutExpired:
                code, err = 2, f"the script did not finish in {CHECK_TIMEOUT} seconds"
            if code in (0, 1) and os.path.isfile(os.path.join(plan_dir, "receipts.json")):
                accepted = f"plan{plans}/receipts.json"
                feedback = f"`check` ran (exit code {code}). It printed:\n\n" + read(os.path.join(plan_dir, "receipts.md"))
            else:
                feedback = f"`check` stopped (exit code {code}) and checked nothing:\n\n" + err[:2000]
        if request_no == PLANS_PER_DOCUMENT:
            break
        left = PLANS_PER_DOCUMENT - request_no
        feedback += (f"\n\nIf your plan needs a correction, reply with the full corrected claims.json "
                     f"({left} correction{'s' if left > 1 else ''} left). If it needs none, reply DONE.")
        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user", "content": feedback})
        record.user(feedback)
    result = {"doc": doc, "arm": "A", "requests": requests, "plans_sent": plans, "said_done": done_word,
              "cut_off_replies": cut_off, "accepted_receipts": accepted, "scan_numbers": scan_numbers,
              "refused": any(r["stop_reason"] == "refusal" for r in requests),
              "usd": round(sum(r["usd"] for r in requests), 8)}
    write_json(os.path.join(out_dir, "result.json"), result)
    return result


def run_arm_b(model, first_message, doc, out_dir):
    record = Record(out_dir, BASELINE_SYSTEM, first_message)
    resp, text, row = model.ask(BASELINE_SYSTEM, [{"role": "user", "content": first_message}], doc, "B", 1)
    record.reply(resp, text, row)
    cut = resp.stop_reason == "max_tokens"
    answer = None if cut else extract_json(text, "wrong")
    result = {"doc": doc, "arm": "B", "requests": [row], "answer": answer, "parsed": answer is not None,
              "cut_off_replies": int(cut), "refused": resp.stop_reason == "refusal", "usd": round(row["usd"], 8)}
    write_json(os.path.join(out_dir, "result.json"), result)
    return result


def export_records(out_root, set_name, docs):
    """Copy what a reader needs to check the run into bench/e2/records/<set>/ (tracked): no thinking data, no keys."""
    dest_root = os.path.join(RECORDS, set_name)
    for doc in docs:
        for arm in ("A", "B"):
            src = os.path.join(out_root, doc, arm)
            if not os.path.isfile(os.path.join(src, "result.json")):
                continue
            dest = os.path.join(dest_root, doc, arm)
            os.makedirs(dest, exist_ok=True)
            shutil.copyfile(os.path.join(src, "result.json"), os.path.join(dest, "result.json"))
            shutil.copyfile(os.path.join(src, "conversation.json"), os.path.join(dest, "conversation.json"))
            n = 1
            while arm == "A" and os.path.isdir(os.path.join(src, f"plan{n}")):
                shutil.copyfile(os.path.join(src, f"plan{n}", "claims.json"), os.path.join(dest, f"plan{n}.claims.json"))
                if os.path.isfile(os.path.join(src, f"plan{n}", "receipts.json")):
                    shutil.copyfile(os.path.join(src, f"plan{n}", "receipts.json"),
                                    os.path.join(dest, f"plan{n}.check.json"))
                n += 1
    for name in ("run.json", "prompts.json"):
        if os.path.isfile(os.path.join(out_root, name)):
            os.makedirs(dest_root, exist_ok=True)
            shutil.copyfile(os.path.join(out_root, name), os.path.join(dest_root, name))


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
    ap.add_argument("--rehearsal", action="store_true", help="development-seed documents (default: 2 English, 2 Korean)")
    ap.add_argument("--held-out", action="store_true", help="the 30 held-out documents: the release run")
    ap.add_argument("--docs", help="rehearsal only: document ids, comma-separated, instead of the default four")
    ap.add_argument("--name", help="rehearsal only: a name for this rehearsal's folder (default: rehearsal)")
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
    if a.held_out and (a.docs or a.name):
        print("--docs and --name are for rehearsals: the held-out documents are fixed by the plan", file=sys.stderr)
        return 2
    set_name, seed, per_lang = ("heldout", HELD_OUT_SEED, 15) if a.held_out else (a.name or "rehearsal", DEV_SEED, 2)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", set_name) or (a.rehearsal and set_name.startswith("heldout")):
        print("the rehearsal name may hold letters, digits, - and _ and may not start with heldout", file=sys.stderr)
        return 2
    cap = min(a.cap, CAP_USD)
    data = a.data or os.path.join(ROOT, "bench", "out", str(seed))
    out_root = a.out or os.path.join(HERE, "out", set_name + ("-fake" if a.fake else ""))
    try:
        if not os.path.isfile(os.path.join(data, "manifest.json")):
            raise Stop(f"no generated benchmark at {data}: run bench/generate.py for seed {seed} first")
        manifest = json.loads(read(os.path.join(data, "manifest.json")))
        docs = choose_documents(manifest, per_lang, a.docs.split(",") if a.docs else None)
        tool = load_tool(a.tool)
        system_a = read(SKILL) + HARNESS_NOTE              # the skill file as shipped, front matter included
        prepared = []
        for d in docs:
            doc_dir = os.path.join(data, "docs", d["id"])
            gold, scan_numbers, msg_a, msg_b = build_prompts(tool, a.tool, doc_dir)
            prepared.append((d, doc_dir, gold, scan_numbers, msg_a, msg_b))
        est = estimate([(d["id"], ma, mb, read(os.path.join(dd, "claims.gold.json")))
                        for d, dd, _, _, ma, mb in prepared], system_a)
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
        first_five_stop = round(1.5 * 5 * a.approved_estimate, 6) if a.approved_estimate else None
        start = {"started": utc_now(), "spent_before_usd": round(spent_so_far(), 6)}
        run_path = os.path.join(out_root, "run.json")
        meta = json.loads(read(run_path)) if os.path.isfile(run_path) else {
            "set": set_name, "seed": seed, "model": MODEL, "effort": EFFORT, "max_tokens": MAX_TOKENS,
            "plans_per_document": PLANS_PER_DOCUMENT, "cap_usd": cap, "documents": [d["id"] for d in docs],
            "approved_estimate_usd_per_document": a.approved_estimate, "first_five_documents_stop_usd": first_five_stop,
            "fake": a.fake, "starts": []}
        if meta["documents"] != [d["id"] for d in docs]:
            raise Stop(f"{out_root} holds a run on other documents: use another --name or --out")
        meta["starts"].append(start)
        write_json(run_path, meta)
        write_json(os.path.join(out_root, "prompts.json"), {"arm_a_system": system_a, "arm_b_system": BASELINE_SYSTEM})
        finished = 0
        for d, doc_dir, gold, scan_numbers, msg_a, msg_b in prepared:
            doc = d["id"]
            for arm in ("A", "B"):
                out_dir = os.path.join(out_root, doc, arm)
                if os.path.isfile(os.path.join(out_dir, "result.json")):
                    continue
                if os.path.isdir(out_dir):                         # a stopped attempt: keep it, start the arm again
                    n = 1
                    while os.path.exists(f"{out_dir}-interrupted{n}"):
                        n += 1
                    os.rename(out_dir, f"{out_dir}-interrupted{n}")
                if a.fake:
                    model.gold_plan = json.loads(read(os.path.join(doc_dir, "claims.gold.json")))
                if arm == "A":
                    run_arm_a(model, system_a, msg_a, doc, doc_dir, out_dir, a.tool, gold, scan_numbers)
                else:
                    run_arm_b(model, msg_b, doc, out_dir)
            finished += 1
            done_ids = [x[0]["id"] for x in prepared[:finished]]
            # everything charged for these documents in this set, stopped attempts included
            charged = sum(r["usd"] for r in spend_rows() if r.get("set") == set_name and r["doc"] in done_ids)
            this = sum(r["usd"] for r in spend_rows() if r.get("set") == set_name and r["doc"] == doc)
            print(f"[{finished}/{len(prepared)}] {doc} ({d['lang']}, {d['source_type']}): US${this:.4f}", flush=True)
            if finished == 5 and first_five_stop is not None and charged > first_five_stop:
                raise Stop(f"the first 5 documents cost US${charged:.4f}, more than 1.5 times the approved "
                           f"US${5 * a.approved_estimate:.4f}. Stopped as the plan requires.")
        total = sum(r["usd"] for r in spend_rows() if r.get("set") == set_name)
        meta.update(finished=utc_now(), spent_this_set_usd=round(total, 6), spent_total_usd=round(spent_so_far(), 6))
        write_json(run_path, meta)
        if not a.fake:
            export_records(out_root, set_name, [d["id"] for d in docs])
        print(f"done: {finished} documents, US${total:.4f} in this set; US${spent_so_far():.4f} spent in all of E2")
        return 0
    except Stop as e:
        print(f"e2: stopped: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
