#!/usr/bin/env python3
"""E5 harness: a model writes the plan against DART and the script checks it (arm A), or the model reviews alone
with the statements in front of it (arm B).

The rules are in docs/eval-plan.md, section E5 and "E5 details". This script needs the `anthropic` package and an
API key (ANTHROPIC_API_KEY, or --key-file) for paid runs only. Every request is recorded in bench/e5/spend.jsonl, and
the script refuses to go past the cap. The script under test runs from a copy of E4's tool cache with no DART key.

  python bench/e5/run.py --check-gold                               # the gold plans through the script, no request
  python bench/e5/run.py --rehearsal --dry-run                      # no request: prompt sizes and a cost estimate
  python bench/e5/run.py --rehearsal --fake                         # no request: the gold plan stands in for the model
  python bench/e5/run.py --rehearsal --approved-estimate 0.05       # 4 development documents
  python bench/e5/run.py --held-out --approved-estimate 0.05        # the held-out documents (release run)
  python bench/e5/run.py --held-out --resume <run folder> ...       # finish a run that was cut
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TOOL = os.path.join(ROOT, "skills", "receipts", "scripts", "receipts.py")
SKILL = os.path.join(ROOT, "skills", "receipts", "SKILL.md")
PLAN_FORMAT = os.path.join(ROOT, "skills", "receipts", "references", "plan-format.md")
SPEND = os.path.join(HERE, "spend.jsonl")
RECORDS = os.path.join(HERE, "records")
OUT = os.path.join(HERE, "out")
E4_TOOL_CACHE = {"dev": os.path.join(ROOT, "bench", "dart", "out", "dev", "tool-cache-v4"),
                 "heldout": os.path.join(ROOT, "bench", "dart", "out", "heldout", "tool-cache")}

MODEL = "claude-haiku-5-5"
EFFORT = "medium"
MAX_TOKENS = 16000
PLANS_PER_DOCUMENT = 3            # the first plan and up to two corrected ones
TOOL_CALLS = 12                   # look-ups per document
REQUESTS_PER_DOCUMENT = 20        # arm A: look-ups, plans and replies that held neither
LISTING_LIMIT = 60000             # characters of one tool answer; a longer one is not returned
REHEARSAL_DOCUMENTS = 4
CAP_USD = 3.00
RESERVE_USD = 0.12                # room kept for the request about to be made (150,000 tokens in, 16,000 out)
CHECK_TIMEOUT = 300               # seconds for one run of the script
# US$ per million tokens for prompts of at most 100,000 tokens, and for longer ones
RATES = {"short": (0.10, 0.50), "long": (0.50, 2.50)}
REPORT_CODE = {"annual": "11011", "half": "11012", "q1": "11013", "q3": "11014"}
BASIS = {"CFS": "consolidated", "OFS": "separate"}

HARNESS_NOTE = """

## How this session works

You cannot run commands or read files here. The harness does steps 1, 2 and 5 for you. The first message holds the
document with line numbers and the output of `scan`. The source to check against is DART.

Three tools stand in for the commands and the reference file of the skill. Each returns what the script prints,
unchanged:

- `dart_company` runs `receipts.py dart company <query>`.
- `dart_accounts` runs `receipts.py dart accounts <company> --year .. --report .. --fs .. [--statement ..] [--match ..] [--limit ..]`.
- `read_plan_format` returns `references/plan-format.md`.

You have at most 12 tool calls. A listing longer than 60,000 characters is not returned: narrow it with `statement`
or `match`. The script runs from a cache, with no key. It can read the company code file and, for each company, the
reports it has filed among these three: the annual report of 2025, the half-year report of 2026 and the third-quarter
report of 2026. Nothing else can be fetched here: a look-up of any other report stops with a message about the key,
which only means that the report is not in the cache.

When you have what you need, reply with the contents of `claims.json` and nothing else. The harness runs `check` and
sends you what it printed. You may then send a corrected `claims.json`, at most two times (step 6), or reply with the
single word DONE when no correction is needed. Step 7 is not needed here.
"""

TOOLS = [
    {"name": "dart_company",
     "description": "Runs `receipts.py dart company <query>` and returns what it prints: the companies whose stock "
                    "code, DART company code or name matches.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["query"],
                      "properties": {"query": {"type": "string",
                                               "description": "a stock code, a DART company code, or part of a name"}}}},
    {"name": "dart_accounts",
     "description": "Runs `receipts.py dart accounts <company> --year --report --fs [--statement] [--match] [--limit]` "
                    "and returns what it prints: the rows of one report with, for each column a locator may name, "
                    "the name DART gives the period and what the column holds. No amounts.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["company", "year", "report", "fs"],
                      "properties": {"company": {"type": "string", "description": "a stock code or a DART company code"},
                                     "year": {"type": "integer", "description": "the business year"},
                                     "report": {"type": "string", "description": "q1, half, q3 or annual"},
                                     "fs": {"type": "string", "description": "CFS (consolidated) or OFS (separate)"},
                                     "statement": {"type": "string", "description": "BS, IS, CIS or CF"},
                                     "match": {"type": "string", "description": "text in the account id or the name"},
                                     "limit": {"type": "integer", "description": "how many rows to print (0 = all)"}}}},
    {"name": "read_plan_format",
     "description": "Returns the skill's reference file `references/plan-format.md`.",
     "input_schema": {"type": "object", "additionalProperties": False, "properties": {}}},
]

BASELINE_SYSTEM = """You are a careful reviewer. You are given a report about a Korean company and the financial statements its numbers come from, as the company filed them with DART.

Check every number in the report against the statements. Look each value up or recompute it: differences, percent
changes and ratios. Allow for normal rounding at the precision the report uses. A value that is cut off at that
precision instead of rounded also counts as correct (2.36 for 2.368). Check that words such as "늘었다" and "줄었다"
agree with the direction of the change. Mind which statements a sentence talks about (consolidated or separate) and
which period. The heading of each column says what the column holds. Amounts are in won.
Do not guess: when a number cannot be checked from the statements, list it under could_not_check.

Reply with one JSON object and nothing else:
{"wrong": [{"line": <line number>, "number": "<the number exactly as written>", "should_be": "<your value>",
            "why": "<a few words>"}],
 "could_not_check": [{"line": <line number>, "number": "<as written>"}]}
"""

# what each column holds, in the words of the column table of the contract (references/plan-format.md, section 2)
HOLDS = {
    ("annual", "BS"): [("thstrm_amount", "the year end"), ("frmtrm_amount", "the previous year end"),
                       ("bfefrmtrm_amount", "the year end two years back")],
    ("annual", "flow"): [("thstrm_amount", "the year"), ("frmtrm_amount", "the previous year"),
                         ("bfefrmtrm_amount", "the year two years back")],
    ("interim", "BS"): [("thstrm_amount", "the period end"), ("frmtrm_amount", "the end of the previous business year")],
    ("interim", "income"): [("thstrm_amount", "the three months"),
                            ("thstrm_add_amount", "from the start of the business year to the period end"),
                            ("frmtrm_q_amount", "the same three months a year earlier"),
                            ("frmtrm_add_amount", "the same cumulative period a year earlier")],
    ("interim", "CF"): [("thstrm_amount", "from the start of the business year to the period end"),
                        ("frmtrm_amount", "the same period a year earlier"), ("frmtrm_q_amount", "not usable")],
}
# the fields in which DART names a column's period, in the order they are tried: in interim income and cash flow
# statements the comparison period is named in frmtrm_q_nm and the rows have no frmtrm_nm
PERIOD_NAME = {"thstrm_amount": ("thstrm_nm",), "thstrm_add_amount": ("thstrm_nm",),
               "frmtrm_amount": ("frmtrm_nm", "frmtrm_q_nm"), "frmtrm_q_amount": ("frmtrm_q_nm", "frmtrm_nm"),
               "frmtrm_add_amount": ("frmtrm_q_nm", "frmtrm_nm"), "bfefrmtrm_amount": ("bfefrmtrm_nm",)}
LEFT_OUT_LIMIT = 5                # documents the gold check may leave out before the run refuses to start


class Stop(Exception):
    pass


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read(path) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_json(path):
    return json.loads(read(path))


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")


def tool_env():
    """The script runs with no DART key and no other cache, so nothing can be fetched."""
    env = dict(os.environ, PYTHONUTF8="1")
    for name in ("RECEIPTS_DART_KEY", "DART_API_KEY", "RECEIPTS_DART_URL", "RECEIPTS_DART_CACHE", "ANTHROPIC_API_KEY",
                 "RECEIPTS_SEC_USER_AGENT", "RECEIPTS_EDGAR_CACHE"):
        env.pop(name, None)
    return env


def script(args, cwd=None):
    """Run the script. Returns (exit code, stdout, stderr)."""
    try:
        run = subprocess.run([sys.executable, TOOL] + args, capture_output=True, env=tool_env(), cwd=cwd,
                             timeout=CHECK_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 2, "", f"the script did not finish in {CHECK_TIMEOUT} seconds"
    return run.returncode, run.stdout.decode("utf-8", "replace"), run.stderr.decode("utf-8", "replace")


# ---------------------------------------------------------------- the set of documents

def set_dir(name):
    return os.path.join(OUT, name)


def prepare_cache(name):
    """A copy of the tool's cache as E4 left it. The copy is made once and E4's folder is never written."""
    cache = os.path.join(set_dir(name), "dart-cache")
    if not os.path.isdir(cache):
        source = E4_TOOL_CACHE[name]
        if not os.path.isdir(source):
            raise Stop(f"E4's tool cache is not there: {source}")
        shutil.copytree(source, cache)
    return cache


def documents(name):
    path = os.path.join(set_dir(name), "manifest.json")
    if not os.path.isfile(path):
        raise Stop(f"no documents: run bench/e5/generate.py{' --held-out' if name == 'heldout' else ''} first")
    return read_json(path)["documents"]


def check_gold(name, cache):
    """With the gold plans the script must judge every planted statement wrong and every other one verified."""
    bad, counts = [], {"documents": 0, "statements": 0, "planted_wrong": 0, "correct_verified": 0}
    for doc in documents(name):
        folder = os.path.join(set_dir(name), "docs", doc["corp_code"])
        out = os.path.join(folder, "gold-check")
        code, _, err = script(["check", os.path.join(folder, "gold-plan.json"), "--dart-cache", cache, "--out", out,
                               "--quiet"])
        if code not in (0, 1) or not os.path.isfile(os.path.join(out, "receipts.json")):
            bad.append({"doc": doc["corp_code"], "problem": f"the script stopped (exit code {code}): {err[:300]}"})
            continue
        receipts = read_json(os.path.join(out, "receipts.json"))
        gold = {s["id"]: s for s in read_json(os.path.join(folder, "gold.json"))["statements"]}
        verdicts = {c["id"]: c["verdict"] for c in receipts["claims"]}
        counts["documents"] += 1
        if receipts["counts"].get("uncovered") or len(verdicts) != len(gold) or receipts["counts"]["numbers"] != len(gold):
            bad.append({"doc": doc["corp_code"], "problem": f"counts {receipts['counts']} for {len(gold)} statements"})
        for sid, s in gold.items():
            want = "wrong" if s["planted"] else "verified"
            counts["statements"] += 1
            if verdicts.get(sid) != want:
                bad.append({"doc": doc["corp_code"], "statement": sid, "planted": s["planted"], "verdict": verdicts.get(sid)})
            else:
                counts["planted_wrong" if s["planted"] else "correct_verified"] += 1
    return counts, bad


def statements_text(cache, doc):
    """Arm B's source: the statements of the report, every row with every amount, as tab-separated text."""
    interim = "annual" if doc["report"] == "annual" else "interim"
    parts = []
    for fs in ("CFS", "OFS"):
        path = os.path.join(cache, "fnlttSinglAcntAll", f"{doc['corp_code']}_{doc['year']}_{REPORT_CODE[doc['report']]}_{fs}.json")
        if not os.path.isfile(path):
            continue
        answer = read_json(path)
        if answer.get("status") != "000":
            continue
        rows = answer["list"]
        for st in ("BS", "IS", "CIS", "CF"):
            mine = [r for r in rows if r.get("sj_div") == st]
            if not mine:
                continue
            group = "BS" if st == "BS" else "CF" if (st == "CF" and interim == "interim") else \
                "flow" if interim == "annual" else "income"
            cols = HOLDS[(interim, group)]
            heads = []
            for col, holds in cols:
                period = next((r[f] for f in PERIOD_NAME[col] for r in mine if r.get(f)), "")
                heads.append(f"{col} = {holds}" + (f" ({period})" if period else ""))
            parts.append(f"--- {BASIS[fs]} statements ({fs}), {mine[0].get('sj_nm', st)} ({st}); business year "
                         f"{mine[0].get('bsns_year', doc['year'])}, report {doc['report']}, receipt number "
                         f"{mine[0].get('rcept_no', '')} ---")
            parts.append("\t".join(["account_id", "account_nm"] + heads))
            for r in mine:
                parts.append("\t".join([r.get("account_id", ""), r.get("account_nm", "")] + [r.get(col, "") or "" for col, _ in cols]))
            parts.append("")
    if not parts:
        raise Stop(f"no statements in the cache for {doc['corp_code']} {doc['report']}")
    return "\n".join(parts)


def build_prompts(name, cache, doc):
    folder = os.path.join(set_dir(name), "docs", doc["corp_code"])
    lines = read(os.path.join(folder, "document.md")).splitlines()
    numbered = "\n".join(f"{i}| {ln}" for i, ln in enumerate(lines, start=1))
    code, out, err = script(["scan", os.path.join(folder, "document.md")])
    if code != 0:
        raise Stop(f"scan failed on {folder}: {err[:300]}")
    scan_doc = json.loads(out)
    scan_doc["document"] = "document.md"
    shared = ("Document `document.md`. The number and bar at the start of each line are its line number and are not "
              f"part of the file.\n\n{numbered}\n\n")
    arm_a = (shared + "Output of `receipts.py scan document.md`:\n\n" + json.dumps(scan_doc, ensure_ascii=False, indent=1)
             + "\n\nCheck the numbers of this document against the company's filings on DART. Write `claims.json` "
               "for it and reply with the JSON only.")
    arm_b = (shared + "The statements of the report the document is about, as DART's API returns them. Each block is one "
             "statement, tab-separated, one row per line. Amounts are in won.\n\n" + statements_text(cache, doc)
             + "\nReview the report. Reply with the JSON object only.")
    return folder, len(scan_doc["numbers"]), arm_a, arm_b


def extract_json(text, key):
    """The reply's JSON object: the outermost object, which must hold `key` as a list. Anything else is None."""
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

    def ask(self, system, messages, doc, arm, request_no, tools=None):
        spent = spent_so_far()
        if spent + RESERVE_USD > self.cap:
            raise Stop(f"the spending cap would be passed: US${spent:.4f} spent of US${self.cap:.2f}. No request made.")
        a = self.anthropic
        extra = {"tools": tools} if tools else {}
        try:
            resp = self.client.messages.create(model=MODEL, max_tokens=MAX_TOKENS, system=system, messages=messages,
                                               output_config={"effort": EFFORT}, **extra)
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
    """Stands in for the API without any request. Arm A: one look-up of the company, then the gold plan, then DONE.
    Arm B: an empty review."""

    def __init__(self):
        self.gold_plan, self.company = None, None

    def ask(self, system, messages, doc, arm, request_no, tools=None):
        row = {"time": utc_now(), "doc": doc, "arm": arm, "request": request_no, "model": "fake", "input_tokens": 0,
               "output_tokens": 0, "usd": 0.0, "stop_reason": "end_turn"}
        if arm == "B":
            content = [SimpleNamespace(type="text", text=json.dumps({"wrong": [], "could_not_check": []}))]
        elif request_no == 1:
            content = [SimpleNamespace(type="tool_use", id="fake_1", name="dart_company", input={"query": self.company})]
            row["stop_reason"] = "tool_use"
        elif request_no == 2:
            content = [SimpleNamespace(type="text", text=json.dumps(self.gold_plan, ensure_ascii=False))]
        else:
            content = [SimpleNamespace(type="text", text="DONE")]
        resp = SimpleNamespace(content=content, stop_reason=row["stop_reason"])
        return resp, "".join(b.text for b in content if b.type == "text"), row


# ---------------------------------------------------------------- the two arms

class Record:
    """What was said, written to disk after every step so that a stopped run loses nothing. Thinking is not kept."""

    def __init__(self, out_dir, system, first_message):
        self.out_dir = out_dir
        self.turns = [{"role": "system", "text": system}, {"role": "user", "text": first_message}]
        self.save()

    def reply(self, resp, text, row):
        calls = [{"name": b.name, "input": b.input} for b in resp.content if b.type == "tool_use"]
        turn = {"role": "assistant", "text": text, "stop_reason": row["stop_reason"],
                "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"], "usd": row["usd"]}
        if calls:
            turn["tool_calls"] = calls
        self.turns.append(turn)
        self.save()

    def tool(self, name, tool_input, output, is_error):
        self.turns.append({"role": "tool", "name": name, "input": tool_input, "output": output, "is_error": is_error})
        self.save()

    def user(self, text):
        self.turns.append({"role": "user", "text": text})
        self.save()

    def save(self):
        write_json(os.path.join(self.out_dir, "conversation.json"), self.turns)


def look_up(name, tool_input, cache):
    """Run one of the three tools. Returns (what the script printed, whether it failed)."""
    if not isinstance(tool_input, dict):
        return "the tool input is not an object", True
    if name == "read_plan_format":
        return read(PLAN_FORMAT), False
    if name == "dart_company":
        args = ["dart", "company", str(tool_input.get("query", ""))]
    elif name == "dart_accounts":
        args = ["dart", "accounts", str(tool_input.get("company", ""))]
        for key in ("year", "report", "fs", "statement", "match", "limit"):
            if tool_input.get(key) not in (None, ""):
                args.append(f"--{key}={tool_input[key]}")
    else:
        return f"there is no tool named {name}", True
    code, out, err = script(args + ["--dart-cache", cache])
    text = out if code == 0 else (err or out)
    if len(text) > LISTING_LIMIT:
        return (f"The listing is {len(text):,} characters long and is not returned (the limit here is "
                f"{LISTING_LIMIT:,}). Narrow it with `statement` or `match`."), True
    return text, code != 0


def run_arm_a(model, system, first_message, doc, folder, out_dir, cache, scan_numbers):
    work = os.path.join(out_dir, "work")
    os.makedirs(work, exist_ok=True)
    shutil.copyfile(os.path.join(folder, "document.md"), os.path.join(work, "document.md"))
    record = Record(out_dir, system, first_message)
    messages = [{"role": "user", "content": first_message}]
    requests, accepted, plans, tool_calls = [], None, 0, 0
    done_word, cut_off, stopped = False, 0, None
    for request_no in range(1, REQUESTS_PER_DOCUMENT + 1):
        resp, text, row = model.ask(system, messages, doc, "A", request_no, tools=TOOLS)
        requests.append(row)
        record.reply(resp, text, row)
        if resp.stop_reason == "refusal":
            stopped = "refusal"
            break
        cut = resp.stop_reason == "max_tokens"
        cut_off += int(cut)
        uses = [b for b in resp.content if b.type == "tool_use"]
        if uses and cut:
            stopped = "cut off at the output limit inside a tool call"
            break
        if uses:
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for use in uses:
                tool_calls += 1
                if tool_calls > TOOL_CALLS:
                    output, failed = (f"The limit of {TOOL_CALLS} tool calls is reached. Reply with the contents of "
                                      "claims.json now."), True
                else:
                    output, failed = look_up(use.name, use.input, cache)
                record.tool(use.name, use.input, output, failed)
                result = {"type": "tool_result", "tool_use_id": use.id, "content": output}
                if failed:
                    result["is_error"] = True
                results.append(result)
            messages.append({"role": "user", "content": results})
            continue
        if says_done(text):
            done_word = True
            break
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
            code, _, err = script(["check", os.path.join(work, "claims.json"), "--dart-cache", cache, "--out", plan_dir,
                                   "--quiet"])
            if code in (0, 1) and os.path.isfile(os.path.join(plan_dir, "receipts.json")):
                accepted = f"plan{plans}"
                feedback = f"`check` ran (exit code {code}). It printed:\n\n" + read(os.path.join(plan_dir, "receipts.md"))
            else:
                feedback = f"`check` stopped (exit code {code}) and checked nothing:\n\n" + err[:2000]
            if plans == PLANS_PER_DOCUMENT:
                break
            left = PLANS_PER_DOCUMENT - plans
            feedback += (f"\n\nIf your plan needs a correction, reply with the full corrected claims.json "
                         f"({left} correction{'s' if left > 1 else ''} left). If it needs none, reply DONE.")
        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user", "content": feedback})
        record.user(feedback)
    else:
        stopped = f"the limit of {REQUESTS_PER_DOCUMENT} requests"
    result = {"doc": doc, "arm": "A", "requests": requests, "plans_sent": plans,
              "tool_calls": min(tool_calls, TOOL_CALLS), "tool_calls_refused_at_the_limit": max(tool_calls - TOOL_CALLS, 0),
              "said_done": done_word, "cut_off_replies": cut_off, "stopped": stopped, "accepted_plan": accepted,
              "scan_numbers": scan_numbers, "refused": stopped == "refusal",
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


def export_records(run_dir, set_name, docs, docs_dir):
    """Copy what a reader needs to check the run into bench/e5/records/<set>/ (tracked): each document with its gold
    files, the conversations, and every plan with the script's answer (planN.claims.json, planN.check.json).
    No thinking data, no keys."""
    target = os.path.join(RECORDS, set_name)
    for doc in docs:
        os.makedirs(os.path.join(target, doc), exist_ok=True)
        for name in ("document.md", "gold.json", "gold-plan.json"):
            shutil.copyfile(os.path.join(docs_dir, doc, name), os.path.join(target, doc, name))
        for arm in ("A", "B"):
            source, dest = os.path.join(run_dir, doc, arm), os.path.join(target, doc, arm)
            if not os.path.isfile(os.path.join(source, "result.json")):
                continue
            os.makedirs(dest, exist_ok=True)
            for name in ("result.json", "conversation.json"):
                shutil.copyfile(os.path.join(source, name), os.path.join(dest, name))
            plans_sent = read_json(os.path.join(source, "result.json")).get("plans_sent", 0) if arm == "A" else 0
            for n in range(1, plans_sent + 1):
                shutil.copyfile(os.path.join(source, f"plan{n}", "claims.json"), os.path.join(dest, f"plan{n}.claims.json"))
                if os.path.isfile(os.path.join(source, f"plan{n}", "receipts.json")):   # the script's answer to the plan
                    shutil.copyfile(os.path.join(source, f"plan{n}", "receipts.json"), os.path.join(dest, f"plan{n}.check.json"))
        slim_conversation(os.path.join(target, doc, "A", "conversation.json"))
    for name in ("run.json",):
        if os.path.isfile(os.path.join(run_dir, name)):
            shutil.copyfile(os.path.join(run_dir, name), os.path.join(target, name))
    return target


def slim_conversation(path):
    """In the committed copy, the two files of the skill that every conversation holds in full are named instead:
    SKILL.md at the start of the system prompt, and plan-format.md as the answer of `read_plan_format`."""
    if not os.path.isfile(path):
        return
    turns, skill, reference = read_json(path), read(SKILL), read(PLAN_FORMAT)
    for turn in turns:
        if turn["role"] == "system" and turn["text"].startswith(skill):
            turn["text"] = (f"[skills/receipts/SKILL.md as shipped at the run's commit, {len(skill):,} characters]"
                            + turn["text"][len(skill):])
        elif turn["role"] == "tool" and turn.get("output") == reference:
            turn["output"] = f"[skills/receipts/references/plan-format.md as shipped at the run's commit, {len(reference):,} characters]"
    write_json(path, turns)


# ---------------------------------------------------------------- the estimate

def tokens_guess(text: str) -> int:
    """A deliberately high guess, used only before the first request. Real counts come from the API's usage."""
    plain = sum(1 for ch in text if ord(ch) < 128)
    return int(plain / 3.0 + (len(text) - plain) * 1.2) + 1


def estimate(prompts, system_a, cache):
    """prompts: list of (doc, arm A message, arm B message, gold plan text). Arm A is priced as five look-ups (the
    company, three statements of the document's own kind and one of the other kind), two plans and the word DONE."""
    rate_in, rate_out = RATES["short"]
    tools_text = json.dumps(TOOLS)
    feedback, thinking, call_out = 2500, 2500, 600
    rows, expected, upper = [], 0.0, 0.0
    for doc, a, b, plan in prompts:
        base = tokens_guess(system_a) + tokens_guess(tools_text) + tokens_guess(a)
        listings = [tokens_guess(look_up("dart_company", {"query": doc["company"]}, cache)[0])]
        other = "OFS" if doc["fs"] == "CFS" else "CFS"
        for fs, st in ((doc["fs"], "IS"), (doc["fs"], "BS"), (doc["fs"], "CF"), (other, "IS")):
            listings.append(tokens_guess(look_up("dart_accounts", {"company": doc["corp_code"], "year": doc["year"],
                                                                   "report": doc["report"], "fs": fs, "statement": st},
                                                 cache)[0]))
        out_plan = int(tokens_guess(plan) * 1.2) + thinking
        context, exp_in, exp_out = base, 0, 0
        for size in listings:                                    # one request per look-up
            exp_in += context
            exp_out += call_out
            context += call_out + size
        for _ in range(2):                                       # two plans
            exp_in += context
            exp_out += out_plan
            context += out_plan + feedback
        exp_in += context                                        # DONE
        exp_out += 300
        in_b = tokens_guess(BASELINE_SYSTEM) + tokens_guess(b)
        exp_in += in_b
        exp_out += 4000
        up_in = REQUESTS_PER_DOCUMENT * 100_000 + in_b           # every arm A request at the 100,000-token line
        up_out = (REQUESTS_PER_DOCUMENT + 1) * MAX_TOKENS
        e = (exp_in * rate_in + exp_out * rate_out) / 1e6
        u = (up_in * rate_in + up_out * rate_out) / 1e6
        rows.append({"doc": doc["corp_code"], "arm_a_first_prompt_tokens": base, "arm_a_listing_tokens": listings,
                     "arm_b_prompt_tokens": in_b, "expected_usd": round(e, 5), "upper_usd": round(u, 5)})
        expected += e
        upper += u
    return {"documents": len(rows), "expected_usd": round(expected, 4), "upper_usd": round(upper, 4),
            "expected_usd_per_document": round(expected / max(len(rows), 1), 5),
            "upper_usd_per_document": round(upper / max(len(rows), 1), 5), "rows": rows,
            "note": "token counts are guessed high from character counts; no request was made"}


# ---------------------------------------------------------------- main

def git(*args):
    try:
        return subprocess.run(["git"] + list(args), capture_output=True, text=True, cwd=ROOT).stdout.strip()
    except OSError:
        return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="E5 harness (docs/eval-plan.md, section E5)")
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--check-gold", action="store_true", help="the gold plans of the development documents")
    which.add_argument("--rehearsal", action="store_true", help=f"{REHEARSAL_DOCUMENTS} development documents")
    which.add_argument("--held-out", action="store_true", help="the held-out documents (release run only)")
    ap.add_argument("--docs", help="comma-separated company codes instead of the default choice (rehearsal only)")
    ap.add_argument("--dry-run", action="store_true", help="no request: prompt sizes and a cost estimate")
    ap.add_argument("--fake", action="store_true", help="no request: the gold plan stands in for the model")
    ap.add_argument("--approved-estimate", type=float, help="US$ per document the user approved (needed for paid runs)")
    ap.add_argument("--resume", help="a run folder that was cut: documents that finished are kept")
    ap.add_argument("--key-file", help="file that holds the API key (default: the SDK's own lookup)")
    ap.add_argument("--cap", type=float, default=CAP_USD)
    ap.add_argument("--records-name", default="rehearsal", help="folder under bench/e5/records/ for a rehearsal's records")
    args = ap.parse_args(argv)
    name = "heldout" if args.held_out else "dev"
    try:
        if args.cap > CAP_USD:
            raise Stop(f"the cap is US${CAP_USD:.2f} (docs/eval-plan.md, E5). A higher one needs a change of the plan.")
        cache = prepare_cache(name)
        counts, bad = check_gold(name, cache)
        if args.check_gold:
            print(json.dumps({"gold_check": counts, "problems": bad}, ensure_ascii=False, indent=1))
            return 2 if bad else 0
        # S7: a document whose gold plan does not give the expected verdicts is left out and listed
        left_out = sorted({b["doc"] for b in bad})
        if len(left_out) > LEFT_OUT_LIMIT:
            print(json.dumps({"gold_check": counts, "problems": bad}, ensure_ascii=False, indent=1))
            raise Stop(f"the gold check leaves out {len(left_out)} documents, more than {LEFT_OUT_LIMIT}. Nothing was run.")
        docs = [d for d in documents(name) if d["corp_code"] not in left_out]
        if args.rehearsal:
            if args.docs:
                wanted = args.docs.split(",")
                docs = [d for d in docs if d["corp_code"] in wanted]
            else:                                  # one third-quarter, one half-year, one annual, then the next
                picked = []
                for report in ("q3", "half", "annual"):
                    picked += [d for d in docs if d["report"] == report][:1]
                picked += [d for d in docs if d not in picked][:REHEARSAL_DOCUMENTS - len(picked)]
                docs = picked[:REHEARSAL_DOCUMENTS]
        elif args.docs:
            raise Stop("--docs is for the rehearsal only")
        system_a = read(SKILL) + HARNESS_NOTE
        prompts = []
        for doc in docs:
            folder, scan_numbers, a, b = build_prompts(name, cache, doc)
            prompts.append((doc, folder, scan_numbers, a, b))
        est = estimate([(d, a, b, read(os.path.join(f, "gold-plan.json"))) for d, f, n, a, b in prompts], system_a, cache)
        if args.dry_run:
            print(json.dumps({"set": name, "gold_check": counts, "estimate": est,
                              "spent_so_far_usd": round(spent_so_far(), 4), "cap_usd": args.cap}, ensure_ascii=False, indent=1))
            return 0
        if args.fake:
            model = FakeModel()
        else:
            if args.approved_estimate is None:
                raise Stop("a paid run needs --approved-estimate (US$ per document, as approved). See --dry-run.")
            model = Model(args.key_file, "heldout" if args.held_out else "rehearsal", args.cap)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        if args.held_out and not args.fake and not args.resume and (
                glob.glob(os.path.join(set_dir(name), "run-*")) or os.path.isdir(os.path.join(RECORDS, "heldout"))):
            raise Stop("the held-out run was already started. It is made once: finish it with --resume <its run folder>.")
        run_dir = args.resume or os.path.join(set_dir(name), ("fake-" if args.fake else "run-") + stamp)
        os.makedirs(run_dir, exist_ok=True)
        this_start = {"time": utc_now(), "commit": git("rev-parse", "--short", "HEAD"),
                      "dirty": bool(git("status", "--porcelain"))}
        meta_path = os.path.join(run_dir, "run.json")
        if args.resume:                              # the first start is kept; each resume is added to a list
            if not os.path.isfile(meta_path):
                raise Stop(f"{run_dir} is not a run folder (no run.json)")
            meta = read_json(meta_path)
            if meta.get("set") != name or (meta.get("model") == "fake") != bool(args.fake):
                raise Stop(f"{run_dir} is a run of another kind ({meta.get('set')}, {meta.get('model')})")
            meta.setdefault("resumes", []).append(this_start)
        else:
            meta = {"set": name, "started": this_start["time"], "model": "fake" if args.fake else MODEL, "effort": EFFORT,
                    "max_tokens": MAX_TOKENS, "commit": this_start["commit"], "dirty": this_start["dirty"],
                    "approved_estimate_usd_per_document": args.approved_estimate,
                    "estimate": {k: v for k, v in est.items() if k != "rows"}, "gold_check": counts,
                    "left_out_by_the_gold_check": [b for b in bad], "documents": [d["corp_code"] for d in docs],
                    "resumes": []}
        write_json(meta_path, meta)
        first_five = [d["corp_code"] for d in docs[:5]]

        def first_five_rule():
            """S5: the cost of the first five documents, from the spending record, whichever start paid for it."""
            if not args.held_out or args.fake or len(first_five) < 5 or not all(
                    os.path.isfile(os.path.join(run_dir, c, arm, "result.json")) for c in first_five for arm in ("A", "B")):
                return
            cost = sum(float(r["usd"]) for r in spend_rows() if r.get("set") == "heldout" and r.get("doc") in first_five)
            if cost > 1.5 * 5 * args.approved_estimate:
                raise Stop(f"the first 5 documents cost US${cost:.4f}, more than 1.5 times the approved estimate "
                           f"(US${5 * args.approved_estimate:.4f}). The run stops here.")

        first_five_rule()
        start_spend = spent_so_far()
        for doc, folder, scan_numbers, a, b in prompts:
            corp = doc["corp_code"]
            done_a = os.path.isfile(os.path.join(run_dir, corp, "A", "result.json"))
            done_b = os.path.isfile(os.path.join(run_dir, corp, "B", "result.json"))
            for arm, done in (("A", done_a), ("B", done_b)):        # B1: a cut attempt is kept, out of the way
                arm_dir = os.path.join(run_dir, corp, arm)
                if not done and os.path.isdir(arm_dir):
                    os.rename(arm_dir, f"{arm_dir}.cut-{stamp}")
            if isinstance(model, FakeModel):
                model.gold_plan, model.company = read_json(os.path.join(folder, "gold-plan.json")), doc["company"]
            if not done_a:
                ra = run_arm_a(model, system_a, a, corp, folder, os.path.join(run_dir, corp, "A"), cache, scan_numbers)
            if not done_b:
                rb = run_arm_b(model, b, corp, os.path.join(run_dir, corp, "B"))
            spent = spent_so_far() - start_spend
            print(f"{corp} {doc['company']}: " + ("kept" if done_a and done_b else
                  f"A plans {ra['plans_sent'] if not done_a else '-'}, tool calls {ra['tool_calls'] if not done_a else '-'}, "
                  f"accepted {ra['accepted_plan'] if not done_a else '-'}; B parsed {rb['parsed'] if not done_b else '-'}")
                  + f"; spent in this run US${spent:.4f}", flush=True)
            first_five_rule()
        meta.update(finished=utc_now())
        write_json(meta_path, meta)
        if not args.fake:
            print("records copied to", export_records(run_dir, "heldout" if args.held_out else args.records_name,
                                                      [d["corp_code"] for d in docs], os.path.join(set_dir(name), "docs")))
        print("run folder:", run_dir)
        return 0
    except Stop as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
