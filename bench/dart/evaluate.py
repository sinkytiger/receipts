#!/usr/bin/env python3
"""E4 scorer for receipts (see docs/eval-plan.md, section "E4: DART connection").

Usage:
  python bench/dart/evaluate.py --fill [--set dev|heldout] [--held-out] [--tool PATH]   # needs RECEIPTS_DART_KEY
  python bench/dart/evaluate.py        [--set dev|heldout] [--held-out] [--tool PATH]   # scoring: no key, no request
  python bench/dart/evaluate.py --self-test                                             # made-up data, stub tools

The fill step runs the tool once, with the key, on the by-id plans and on the plans that ask for year 2031, so that
the tool's own cache holds every answer scoring needs, the answers "no data" included. What the tool printed and
every file under its cache and output folders are searched for the key. Only counts and places are recorded.

Scoring runs the tool as a black box (`check PLAN --out DIR --dart-cache DIR`) on the cases written by
bench/dart/build.py, with no key in its environment and RECEIPTS_DART_URL pointing at a closed local port: a plan
that makes the tool want a request stops and counts as a failure. It computes the seven E4 measures, compares them
with the registered bars, writes bench/dart/results/<set>-<commit>-<UTC>.json and .md and appends one line to
bench/dart/runs.jsonl. Exit 0 if all seven bars are met, 1 otherwise, 2 when it cannot start.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
KEY_ENV, URL_ENV, CACHE_ENV = "RECEIPTS_DART_KEY", "RECEIPTS_DART_URL", "RECEIPTS_DART_CACHE"
OTHER_KEY_ENVS = ("DART_API_KEY",)      # never handed to the tool while scoring
FACT_PLANS = (("by_id", "by-id.json"), ("by_name", "by-name.json"), ("pinned", "pinned.json"))
ABSTAIN_KINDS = ("account", "column", "year", "fs", "sce", "receipt", "statement", "cfq")
FILL_PLANS = ("by-id.json", "abstain-year.json")
STOP_VARIANTS = ("no_key", "corrupt_cache", "closed_port", "status_020", "status_800")
STOP_SETS = 5
STATUS_MESSAGES = {"020": "요청 제한을 초과하였습니다.", "800": "시스템 점검으로 인한 서비스가 중지 중입니다."}


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def git(*args):
    try:
        p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=30)
        return p.stdout.decode("utf-8", "replace").strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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


# ---------------------------------------------------------------- looking for a key

def _variants(secret):
    return {v for v in (secret.encode("utf-8"), secret.lower().encode("utf-8"), secret.upper().encode("utf-8")) if v}


def count_in(blob, secret):
    return max(blob.count(v) for v in _variants(secret)) if secret and blob else 0


def secret_places(secret, streams, folders):
    """Places where `secret` appears: in the given byte streams, and in the names and contents (zip members too)
    of every file under the given folders. Returns (places, files searched). A place never holds the secret."""
    places, searched = [], 0
    for name, blob in streams:
        n = count_in(blob, secret)
        if n:
            places.append({"where": name, "count": n})
    for label, folder in folders:
        for base, _, names in os.walk(folder):
            for name in sorted(names):
                path = os.path.join(base, name)
                rel = label + "/" + os.path.relpath(path, folder).replace(os.sep, "/")
                shown = rel
                for v in _variants(secret):
                    shown = shown.replace(v.decode("utf-8"), "<key>")
                if shown != rel:
                    places.append({"where": "file name: " + shown, "count": 1})
                try:
                    with open(path, "rb") as fh:
                        blob = fh.read()
                except OSError:
                    places.append({"where": "unreadable file: " + shown, "count": 1})
                    continue
                searched += 1
                n = count_in(blob, secret)
                if n:
                    places.append({"where": "file: " + shown, "count": n})
                if blob[:2] == b"PK":
                    try:
                        with zipfile.ZipFile(io.BytesIO(blob)) as z:
                            for info in z.infolist():
                                if info.file_size <= 300 * 1024 * 1024:
                                    m = count_in(z.read(info), secret) + count_in(info.filename.encode("utf-8"), secret)
                                    if m:
                                        places.append({"where": f"zip member of {shown}", "count": m})
                    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
                        pass
    return places, searched


def scrubbed(blob, *secrets):
    text = blob.decode("utf-8", "replace") if isinstance(blob, bytes) else str(blob)
    for s in secrets:
        if s:
            for v in (s, s.lower(), s.upper()):
                text = text.replace(v, "<key>")
    return text


# ---------------------------------------------------------------- running the tool

def run_tool(py, tool, plan, outdir, cache, env, timeout):
    """Run `check`. Returns (exit code or None, stdout, stderr, receipts.json data or None, note)."""
    cmd = [py, tool, "check", plan, "--out", outdir, "--dart-cache", cache]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env, cwd=ROOT)
        code, out, err, note = p.returncode, p.stdout, p.stderr, ""
    except subprocess.TimeoutExpired as e:
        code, out, err, note = None, e.stdout or b"", e.stderr or b"", "timeout"
    except OSError as e:
        code, out, err, note = None, b"", b"", f"could not start the tool: {type(e).__name__}"
    data, path = None, os.path.join(outdir, "receipts.json")
    if os.path.exists(path):
        try:
            data = load_json(path)
            if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
                raise ValueError("no claims list")
        except (ValueError, OSError) as e:
            data, note = None, f"invalid receipts.json: {type(e).__name__}"
    return code, out, err, data, note


def index_claims(data):
    """id -> (verdict, reason, claim) for one receipts.json, or None when the plan was not answered."""
    if data is None:
        return None
    out = {}
    for u in data.get("unbound_claims") or []:
        if isinstance(u, dict):
            out[u.get("id")] = ("unbound", u.get("reason"), None)
    for c in data.get("claims", []):
        if isinstance(c, dict):
            out[c.get("id")] = (str(c.get("verdict")), c.get("reason"), c)
    return out


def lookup(index, cid):
    if index is None:
        return "error", None, None
    return index.get(cid, ("missing", None, None))


# ---------------------------------------------------------------- the local stand-in for the stop cases

class StandIn:
    """Answers every request with one DART status, in JSON or XML as the endpoint asks. It remembers which
    endpoints were asked, never the address of a request."""

    def __init__(self):
        self.status = "020"
        self.hits = []
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                endpoint = self.path.split("?", 1)[0].rsplit("/", 1)[-1]
                with outer.lock:
                    outer.hits.append(endpoint)
                    status = outer.status
                message = STATUS_MESSAGES.get(status, "")
                if endpoint.endswith(".json"):
                    body = json.dumps({"status": status, "message": message}, ensure_ascii=False).encode("utf-8")
                    kind = "application/json;charset=UTF-8"
                else:
                    body = (f'<?xml version="1.0" encoding="UTF-8"?><result><status>{status}</status>'
                            f"<message>{message}</message></result>").encode("utf-8")
                    kind = "application/xml;charset=UTF-8"
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_POST = do_GET

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def arm(self, status):
        with self.lock:
            self.status, self.hits = status, []

    def seen(self):
        with self.lock:
            return list(self.hits)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def made_up_key(*parts):
    """A key for a stop case: 40 characters, made up from the case's name, different for every case."""
    return "madeup" + hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:34]


def stop_cases(py, tool, root, sets, normal_cache, base_env, timeout, say):
    """The plan's stop cases, each on the by-id plan of the first sets of the run, each in a fresh cache."""
    stand_in = StandIn()
    port = closed_port()
    out = []
    try:
        for s in sets[:STOP_SETS]:
            plan = os.path.join(root, "sets", s["folder"], "by-id.json")
            for variant in STOP_VARIANTS:
                with tempfile.TemporaryDirectory(prefix="receipts-e4-stop-", ignore_cleanup_errors=True) as tmp:
                    cache, outdir = os.path.join(tmp, "cache"), os.path.join(tmp, "out")
                    os.makedirs(cache)
                    os.makedirs(outdir)
                    env = dict(base_env)
                    key, note = None, ""
                    stand_in.arm("020")
                    if variant == "no_key":
                        # nothing may be requested: the stand-in is there so that a tool that asks anyway does not
                        # reach DART, and so that the request is seen
                        env[URL_ENV] = stand_in.url
                    elif variant == "corrupt_cache":
                        env[URL_ENV] = f"http://127.0.0.1:{port}"
                        rel = os.path.join("fnlttSinglAcntAll",
                                           f"{s['corp_code']}_{s['year']}_{s['reprt_code']}_{s['fs']}.json")
                        os.makedirs(os.path.join(cache, "fnlttSinglAcntAll"))
                        with open(os.path.join(cache, rel), "w", encoding="utf-8") as fh:
                            fh.write('{"status": "000", "message": "정상", "list": [{"rcept_no": "' + s["rcept_no"][:6])
                        if s["company_in_plans"] != s["corp_code"]:
                            src = os.path.join(normal_cache, "corpCode.zip")
                            if os.path.isfile(src):
                                shutil.copyfile(src, os.path.join(cache, "corpCode.zip"))
                            else:
                                note = "the plan names a stock code and the tool's cache has no corpCode.zip to copy"
                    else:
                        key = made_up_key(s["folder"], variant)
                        env[KEY_ENV] = key
                        if variant == "closed_port":
                            env[URL_ENV] = f"http://127.0.0.1:{port}"
                        else:
                            stand_in.arm(variant[-3:])
                            env[URL_ENV] = stand_in.url
                    code, so, se, _, run_note = run_tool(py, tool, plan, outdir, cache, env, timeout)
                    places, searched = ([], 0)
                    if key:
                        places, searched = secret_places(key, [("stdout", so), ("stderr", se)],
                                                         [("cache", cache), ("out", outdir)])
                    hits = stand_in.seen() if variant in ("no_key", "status_020", "status_800") else None
                    left = [os.path.relpath(os.path.join(b, n), cache).replace(os.sep, "/")
                            for b, _, names in os.walk(cache) for n in names]
                    stopped_cached = [n for n in left if n.startswith("fnlttSinglAcntAll/")] \
                        if variant in ("status_020", "status_800") else []
                out.append({"set": s["folder"], "variant": variant, "exit": code, "ok": code == 2,
                            "note": "; ".join(x for x in (note, run_note) if x), "made_up_key": bool(key),
                            "key_places": places, "files_searched": searched,
                            "requests_seen": None if hits is None else len(hits),
                            "endpoints_seen": None if hits is None else sorted(set(hits)),
                            "stopped_answers_cached": stopped_cached,
                            "stderr": "" if code == 2 else scrubbed(se[-600:], key, os.environ.get(KEY_ENV, ""))})
                say(f"[stop] {s['folder']} {variant}: exit {code}"
                    + (f", key found in {len(places)} places" if places else ""))
    finally:
        stand_in.stop()
    return out


# ---------------------------------------------------------------- the fill step

def fill_paths(cache):
    base = cache.rstrip("/\\")
    return base + "-fill.json", base + "-fill-out"


def fill(a, manifest, root, tool, cache):
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        print(f"error: {KEY_ENV} is empty. The fill step runs the tool once with your own OpenDART key.",
              file=sys.stderr)
        return 2
    if os.environ.get(URL_ENV) and not manifest.get("made_up"):
        print(f"error: {URL_ENV} is set, so the tool would send the key to that address instead of DART's. "
              "Unset it for the fill step.", file=sys.stderr)
        return 2
    record_path, fill_out = fill_paths(cache)
    os.makedirs(cache, exist_ok=True)
    before = sum(len(names) for _, _, names in os.walk(cache))
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    env.pop(CACHE_ENV, None)
    for k in OTHER_KEY_ENVS:
        env.pop(k, None)
    py = sys.executable
    runs, streams_places = [], []
    sets = manifest["sets"]
    for i, s in enumerate(sets):
        for plan in FILL_PLANS:
            if plan not in s["plans"]:
                continue
            outdir = os.path.join(fill_out, s["folder"], plan[:-5])
            os.makedirs(outdir, exist_ok=True)
            stale = os.path.join(outdir, "receipts.json")
            if os.path.exists(stale):
                os.remove(stale)
            t0 = time.monotonic()
            code, so, se, data, note = run_tool(py, tool, os.path.join(root, "sets", s["folder"], plan), outdir,
                                                cache, env, a.timeout)
            where = f"{s['folder']}/{plan}"
            for name, blob in (("stdout", so), ("stderr", se)):
                n = count_in(blob, key)
                if n:
                    streams_places.append({"where": f"{name} of {where}", "count": n})
            runs.append({"set": s["folder"], "plan": plan, "exit": code, "answered": code in (0, 1) and data is not None,
                         "seconds": round(time.monotonic() - t0, 2), "note": note,
                         "stderr": "" if code in (0, 1) else scrubbed(se[-600:], key)})
            print(f"[fill {i + 1}/{len(sets)}] {where}: exit {code}", flush=True)
    places, searched = secret_places(key, [], [("tool-cache", cache), ("fill-out", fill_out)])
    places = streams_places + places
    exits = {}
    for r in runs:
        exits[str(r["exit"])] = exits.get(str(r["exit"]), 0) + 1
    record = {"timestamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), "set": manifest.get("set"),
              "commit": git("rev-parse", "--short", "HEAD") or "nogit", "dirty": bool(git("status", "--porcelain")),
              "tool": tool_label(tool), "tool_sha256": sha256_file(tool), "plans": list(FILL_PLANS),
              "cache_files_before": before, "runs": len(runs), "exits": exits,
              "not_answered": [r for r in runs if not r["answered"]],
              "seconds": round(sum(r["seconds"] for r in runs), 1),
              "key_search": {"searched": "what the tool printed in each run (stdout and stderr), and the names and "
                                         "contents of every file under its cache and output folders",
                             "files_searched": searched, "places": places,
                             "places_count": sum(p["count"] for p in places)},
              "address_variable_set": bool(os.environ.get(URL_ENV))}
    write_json(record_path, record)
    bad = len(record["not_answered"])
    print(f"fill: {len(runs)} runs, exit codes {exits}, {bad} not answered; key found in "
          f"{record['key_search']['places_count']} places ({searched} files searched); record: {record_path}")
    return 0 if bad == 0 else 1


def tool_label(tool):
    return os.path.relpath(tool, ROOT).replace(os.sep, "/") if os.path.abspath(tool).startswith(ROOT) else tool


# ---------------------------------------------------------------- what the API holds (read after scoring only)

class ApiCells:
    """The cached statements answers, read after scoring and only to explain `wrong` verdicts."""

    def __init__(self, tool_cache, builder_cache):
        self.folders = (tool_cache, builder_cache)
        self.memo = {}

    def rows(self, s):
        name = f"{s['corp_code']}_{s['year']}_{s['reprt_code']}_{s['fs']}.json"
        if name not in self.memo:
            self.memo[name] = None
            for folder in self.folders:
                try:
                    data = load_json(os.path.join(folder, "fnlttSinglAcntAll", name))
                    if isinstance(data, dict) and isinstance(data.get("list"), list):
                        self.memo[name] = [r for r in data["list"] if isinstance(r, dict)]
                        break
                except (OSError, ValueError):
                    continue
        return self.memo[name]

    def cell(self, s, statement, account, column):
        rows = self.rows(s)
        if rows is None:
            return None, "the cached statements answer could not be read"
        hits = [r for r in rows if r.get("sj_div") == statement and r.get("account_id") == account]
        if len(hits) != 1:
            return None, f"{len(hits)} rows with that statement and account id"
        text = hits[0].get(column)
        if text is None or str(text).strip() == "":
            return None, "the API cell is empty"
        try:
            return Decimal(str(text).replace(",", "").strip()), None
        except InvalidOperation:
            return None, "the API cell is not a number"


def to_decimal(text):
    try:
        return Decimal(str(text))
    except (InvalidOperation, TypeError, ValueError):
        return None


# ---------------------------------------------------------------- scoring

def score(a, manifest, root, tool, cache):
    record_path, _ = fill_paths(cache)
    if not os.path.isfile(record_path):
        print(f"error: no fill record at {record_path}. Run the fill step first: evaluate.py --fill (it needs "
              f"{KEY_ENV}).", file=sys.stderr)
        return 2
    fill_record = load_json(record_path)
    sets = manifest["sets"]
    py = sys.executable
    port = closed_port()
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    for k in (KEY_ENV, CACHE_ENV) + OTHER_KEY_ENVS:
        env.pop(k, None)
    env[URL_ENV] = f"http://127.0.0.1:{port}"
    key_at_start = os.environ.get(KEY_ENV, "").strip()

    # 1. every plan of every set, from the tool's cache
    jobs = [(s, plan) for s in sets for plan in s["plans"]]
    answers, failures, exits = {}, [], {}
    done = [0]
    lock = threading.Lock()

    def one(job):
        s, plan = job
        folder = os.path.join(root, "sets", s["folder"])
        keep = os.path.join(folder, "receipts-" + plan)
        if os.path.exists(keep):
            os.remove(keep)
        with tempfile.TemporaryDirectory(prefix="receipts-e4-", ignore_cleanup_errors=True) as tmp:
            code, so, se, data, note = run_tool(py, tool, os.path.join(folder, plan), tmp, cache, env, a.timeout)
            if data is not None:
                shutil.copyfile(os.path.join(tmp, "receipts.json"), keep)
        with lock:
            exits.setdefault(plan, {})
            exits[plan][str(code)] = exits[plan].get(str(code), 0) + 1
            if code not in (0, 1) or data is None:
                failures.append({"where": f"{s['folder']}/{plan}", "exit": code, "note": note,
                                 "stderr": scrubbed(se[-600:], key_at_start)})
                data = None
            answers[(s["folder"], plan)] = index_claims(data)
            done[0] += 1
            if done[0] % 25 == 0 or done[0] == len(jobs):
                print(f"[check] {done[0]}/{len(jobs)} plans", flush=True)
    if a.jobs > 1:
        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            list(pool.map(one, jobs))
    else:
        for job in jobs:
            one(job)

    # 2. verdicts against the gold
    facts, swaps, abst, amended = [], [], [], []
    golds = {}
    for s in sets:
        gold = golds[s["folder"]] = load_json(os.path.join(root, "sets", s["folder"], "gold.json"))
        tag = {"set": s["folder"], "company": s["company"], "corp_code": s["corp_code"], "report": s["report"],
               "fs": s["fs"]}
        by_id = {}
        for g in gold["facts"]:
            for arm, plan in FACT_PLANS:
                if arm == "by_name" and not g["by_name"]:
                    continue
                v, reason, claim = lookup(answers.get((s["folder"], plan)), g["id"])
                if arm == "by_id":
                    by_id[g["id"]] = v
                facts.append(dict(tag, arm=arm, id=g["id"], statement=g["statement"], account=g["account"],
                                  column=g["column"], way=g["way"], value=g["value"], written=g["written"],
                                  verdict=v, reason=reason,
                                  tool_value=(claim or {}).get("recomputed_raw")))
        for x in gold["swap"]:
            v, reason, _ = lookup(answers.get((s["folder"], "swap.json")), x["id"])
            group = "fact_verified" if by_id.get(x["fact"]) == "verified" else "fact_not_verified"
            swaps.append(dict(tag, id=x["id"], fact=x["fact"], kind=x["kind"], shown=x["shown"], verdict=v,
                              reason=reason, group=group,
                              ok=(v == "wrong") if group == "fact_verified" else (v == "unverifiable")))
        for kind in ABSTAIN_KINDS:
            for item in gold["abstain"].get(kind, []):
                v, reason, _ = lookup(answers.get((s["folder"], f"abstain-{kind}.json")), item["fact"])
                abst.append(dict(tag, kind=kind, id=item["fact"], verdict=v, reason=reason))
        if gold.get("amended"):
            for fid in gold["amended"]["facts"]:
                ev, er, _ = lookup(answers.get((s["folder"], "amended.json")), fid)
                sv, sr, _ = lookup(answers.get((s["folder"], "pinned.json")), fid)
                amended.append(dict(tag, id=fid, earliest_rcept_no=gold["amended"]["earliest_rcept_no"],
                                    served_rcept_no=gold["amended"]["served_rcept_no"], earlier_verdict=ev,
                                    earlier_reason=er, served_verdict=sv, served_reason=sr,
                                    ok=ev == "unverifiable" and sv == "verified"))

    # 3. stop cases and the key
    stops = stop_cases(py, tool, root, sets, cache, env, a.timeout, lambda m: print(m, flush=True))

    # 4. after scoring: what the API holds, for the wrong verdicts only
    api = ApiCells(cache, a.cache)
    by_set = {s["folder"]: s for s in sets}
    wrong_rows, split = [], {"tool_returned_the_api_value": 0, "tool_returned_another_value": 0, "not_known": 0}
    for r in facts:
        if r["verdict"] != "wrong":
            continue
        cell, why = api.cell(by_set[r["set"]], r["statement"], r["account"], r["column"])
        gold_value, tool_value = to_decimal(r["value"]), to_decimal(r["tool_value"])
        if cell is None or tool_value is None:
            cls = "not_known"
        elif tool_value == cell:
            cls = "tool_returned_the_api_value"
        else:
            cls = "tool_returned_another_value"
        split[cls] += 1
        wrong_rows.append({k: r[k] for k in ("set", "arm", "id", "statement", "account", "column", "written")}
                          | {"gold": r["value"], "tool_value": r["tool_value"],
                             "api_value": None if cell is None else str(cell), "api_note": why,
                             "api_differs_from_gold": None if cell is None or gold_value is None
                             else cell != gold_value, "class": cls})

    # 5. the measures
    def cnt(items, v, key="verdict"):
        return sum(1 for r in items if r[key] == v)
    arms = {arm: [r for r in facts if r["arm"] == arm] for arm, _ in FACT_PLANS}
    wrong = cnt(facts, "wrong")
    m2 = {}
    for arm, items in arms.items():
        ver, unv = cnt(items, "verified"), cnt(items, "unverifiable")
        m2[arm] = {"value": rate(ver, len(items)), "num": ver, "den": len(items), "unverifiable": unv,
                   "other": len(items) - ver - unv,
                   "ok": len(items) > 0 and ver * 100 >= 98 * len(items) and ver + unv == len(items)}
    sw_a = [r for r in swaps if r["group"] == "fact_verified"]
    sw_b = [r for r in swaps if r["group"] == "fact_not_verified"]
    sw_a_ok, sw_b_ok = sum(1 for r in sw_a if r["ok"]), sum(1 for r in sw_b if r["ok"])
    a_unv = cnt(abst, "unverifiable")
    am_ok = sum(1 for r in amended if r["ok"])
    s_ok = sum(1 for r in stops if r["ok"])
    fill_places = fill_record.get("key_search", {}).get("places_count")
    stop_places = sum(p["count"] for r in stops for p in r["key_places"])
    fill_matches = fill_record.get("tool_sha256") == sha256_file(tool)
    fill_complete = not fill_record.get("not_answered")
    key_places = (fill_places or 0) + stop_places
    measures = [
        dict(n=1, name="fact claims judged wrong, by id, by name or pinned", value=wrong, den=len(facts),
             by_arm={arm: cnt(items, "wrong") for arm, items in arms.items()}, bar="0",
             ok=wrong == 0 and len(facts) > 0),
        dict(n=2, name="by id, by name and pinned, each on its own: judged verified",
             value={arm: m2[arm]["value"] for arm in m2}, arms=m2, bar=">= 98% each, rest unverifiable",
             ok=all(x["ok"] for x in m2.values())),
        dict(n=3, name="swap cases judged wrong, among the cases whose fact was verified by id",
             value=rate(sw_a_ok, len(sw_a)), num=sw_a_ok, den=len(sw_a), other_cases=len(sw_b),
             other_cases_unverifiable=sw_b_ok, bar="100%, the other swap cases unverifiable",
             ok=len(sw_a) > 0 and sw_a_ok == len(sw_a) and sw_b_ok == len(sw_b)),
        dict(n=4, name="abstention cases judged unverifiable", value=rate(a_unv, len(abst)), num=a_unv,
             den=len(abst), bar="100%", ok=len(abst) > 0 and a_unv == len(abst)),
        dict(n=5, name="amended reports: pinned to the earlier receipt number unverifiable, pinned to the served "
                       "number verified", value=rate(am_ok, len(amended)), num=am_ok, den=len(amended),
             cases=len(amended), bar="100%", ok=am_ok == len(amended),
             note="no amended report in this run: counted as met" if not amended else ""),
        dict(n=6, name="stop cases that exit with code 2", value=rate(s_ok, len(stops)), num=s_ok, den=len(stops),
             bar="100%", ok=len(stops) > 0 and s_ok == len(stops)),
        dict(n=7, name="places where a key appears in what the tool printed or wrote", value=key_places,
             fill_run=fill_places, stop_cases=stop_places, bar="0",
             ok=fill_places == 0 and stop_places == 0,
             note="; ".join(x for x in (
                 "" if fill_matches else "the fill step was run with another version of the tool file",
                 "" if fill_complete else "the fill step left plans unanswered") if x)),
    ]

    # 6. reported without a bar
    breakdown = {}
    for key in ("company", "report", "fs", "statement", "column", "way"):
        t = {}
        for r in facts:
            k = f"{r[key]} / {r['arm']}"
            bump(t, k, "n")
            bump(t, k, "verdict:" + r["verdict"])
        breakdown[key] = dict(sorted(t.items()))
    reasons = {}
    for r in facts:
        if r["verdict"] != "verified":
            bump(reasons, r["arm"], f"{r['verdict']}:{r['reason']}")
    abst_by, swap_by, stop_by = {}, {}, {}
    for r in abst:
        bump(abst_by, r["kind"], "n")
        bump(abst_by, r["kind"], "verdict:" + r["verdict"])
        bump(abst_by, r["kind"], "reason:" + str(r["reason"]))
    for r in swaps:
        k = f"{r['kind']} / {r['group']}"
        bump(swap_by, k, "n")
        bump(swap_by, k, "verdict:" + r["verdict"])
    for r in stops:
        bump(stop_by, r["variant"], "n")
        bump(stop_by, r["variant"], "exit:" + str(r["exit"]))
    totals = manifest.get("totals", {})
    counts = {"companies": len(manifest.get("companies", [])), "sets": len(sets),
              "missing_sets": len(manifest.get("missing_sets", [])), "facts": len(arms["by_id"]),
              "by_id": len(arms["by_id"]), "by_name": len(arms["by_name"]), "pinned": len(arms["pinned"]),
              "swap": len(swaps), "abstain": len(abst), "amended": len(amended), "stop_cases": len(stops),
              "plans_run": len(jobs)}
    reported = {
        "wrong_split": {"counts": split, "api_differs_from_gold": sum(1 for w in wrong_rows
                                                                      if w["api_differs_from_gold"]),
                        "claims": wrong_rows[:400]},
        "facts_without_gold": totals.get("no_gold", {}), "rows_left_out": totals.get("rows_left_out", {}),
        "not_verified_by_reason": reasons, "swap_by_kind": dict(sorted(swap_by.items())),
        "swap_not_built": totals.get("counts", {}).get("swap_not_built_same_as_written", {}),
        "abstain_by_kind": abst_by, "amended": amended, "stop_by_variant": stop_by,
        "stop_requests_seen_without_a_key": sum(r["requests_seen"] or 0 for r in stops if r["variant"] == "no_key"),
        "stop_stopped_answers_cached": sum(len(r["stopped_answers_cached"]) for r in stops),
        "key_search": {"fill_run": fill_record.get("key_search"), "fill_record": os.path.basename(record_path),
                       "fill_timestamp": fill_record.get("timestamp"), "fill_commit": fill_record.get("commit"),
                       "fill_tool_is_this_tool": fill_matches, "fill_complete": fill_complete,
                       "fill_started_with_empty_cache": fill_record.get("cache_files_before") == 0,
                       "stop_cases_searched": sum(1 for r in stops if r["made_up_key"]),
                       "stop_case_places": [dict(p, set=r["set"], variant=r["variant"]) for r in stops
                                            for p in r["key_places"]]},
    }

    commit = git("rev-parse", "--short", "HEAD") or "nogit"
    porcelain = git("status", "--porcelain")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    all_ok = all(m["ok"] for m in measures)
    receipts = sorted({f"{s['corp_code']} {s['report']} {s['rcept_no']}" for s in sets})
    not_passing = ([r for r in facts if r["verdict"] != "verified"][:300]
                   + [dict(r, arm="swap") for r in swaps if not r["ok"]][:150]
                   + [dict(r, arm="abstain") for r in abst if r["verdict"] != "unverifiable"][:150])
    result = {"timestamp": stamp, "set": manifest.get("set"), "held_out": bool(manifest.get("held_out")),
              "made_up_data": bool(manifest.get("made_up")), "commit": commit, "dirty": bool(porcelain),
              "tool": tool_label(tool), "tool_sha256": sha256_file(tool),
              "builder_version": manifest.get("builder_version"), "fetch_dates": manifest.get("fetch_dates"),
              "company_code_file_fetched": manifest.get("company_code_file_fetched"),
              "sets": [{k: s.get(k) for k in ("folder", "company", "corp_code", "report", "year", "fs", "rcept_no",
                                              "company_in_plans", "statements_fetched", "xbrl_fetched")}
                       | {"amended": bool(s.get("amended", {}).get("amended")),
                          "earliest_rcept_no": s.get("amended", {}).get("earliest_rcept_no"),
                          "facts": s["counts"]["facts"]} for s in sets],
              "missing_sets": manifest.get("missing_sets", []),
              "sets_without_gold": manifest.get("sets_without_gold", []), "draw": manifest.get("draw"),
              "counts": counts, "measures": measures, "pass": all_ok, "reported": reported,
              "breakdown": breakdown, "stop_cases": stops, "check_exit_codes": exits, "failures": failures,
              "not_passing": not_passing}
    os.makedirs(a.results_dir, exist_ok=True)
    base = os.path.join(a.results_dir, f"{manifest.get('set')}-{commit}-{stamp}")
    write_json(base + ".json", result)
    with open(base + ".md", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_md(result))
    line = {"timestamp": stamp, "set": manifest.get("set"), "commit": commit, "dirty": bool(porcelain),
            "tool": tool_label(tool), "made_up_data": bool(manifest.get("made_up")), "counts": counts,
            "measures": {str(m["n"]): m["value"] for m in measures},
            "pass": {str(m["n"]): m["ok"] for m in measures}, "all_pass": all_ok, "failures": len(failures),
            "wrong_split": split, "fetch_dates": manifest.get("fetch_dates"), "receipts": receipts}
    os.makedirs(os.path.dirname(os.path.abspath(a.runs)), exist_ok=True)
    with open(a.runs, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    for m in measures:
        print(f"  {m['n']}. {'PASS' if m['ok'] else 'FAIL'}  {shown_value(m)}  (bar {m['bar']})  {m['name']}"
              + (f"  [{m['note']}]" if m.get("note") else ""))
    print(f"plans the tool did not answer: {len(failures)}; results: {base}.json")
    return 0 if all_ok else 1


def shown_value(m):
    if m["n"] == 1:
        return f"{m['value']} of {m['den']}"
    if m["n"] == 2:
        return "; ".join(f"{arm.replace('_', ' ')} "
                         + ("n/a (0 cases)" if x["value"] is None else f"{x['value']:.2%} ({x['num']}/{x['den']})")
                         for arm, x in m["arms"].items())
    if m["n"] == 7:
        return f"{m['value']} (fill run {m['fill_run']}, stop cases {m['stop_cases']})"
    if m.get("den") == 0:
        return "n/a (0 cases)"
    text = f"{m['value']:.2%} ({m['num']}/{m['den']})"
    if m["n"] == 3:
        text += f"; other cases unverifiable {m['other_cases_unverifiable']}/{m['other_cases']}"
    return text


def render_md(r):
    c = r["counts"]
    kind = "made-up data, not an E4 result" if r["made_up_data"] else ("held-out" if r["held_out"] else "development")
    out = [f"# E4 results: {r['set']} set ({kind}), commit {r['commit']}{' (dirty)' if r['dirty'] else ''}", "",
           f"Run {r['timestamp']} with `{r['tool']}`. Data fetched {', '.join(r['fetch_dates'] or ['?'])}. "
           f"Companies {c['companies']}, sets {c['sets']} (DART has no data for {c['missing_sets']}), facts with a "
           f"gold {c['facts']}: by id {c['by_id']}, by name {c['by_name']}, pinned {c['pinned']}. Swap cases "
           f"{c['swap']}, abstention cases {c['abstain']}, amended cases {c['amended']}, stop cases "
           f"{c['stop_cases']}.", "",
           f"Overall: {'PASS' if r['pass'] else 'FAIL'}. Plans the tool did not answer (crash, timeout, exit 2, no "
           f"receipts.json): {len(r['failures'])} of {c['plans_run']}.", "",
           "| # | Measure | Result | Bar | Met |", "|---|---|---|---|---|"]
    for m in r["measures"]:
        note = f" ({m['note']})" if m.get("note") else ""
        out.append(f"| {m['n']} | {m['name']} | {shown_value(m)}{note} | {m['bar']} | {'yes' if m['ok'] else 'no'} |")
    rep = r["reported"]
    ws = rep["wrong_split"]
    out += ["", "## Reported without a bar", "",
            f"- `wrong` verdicts on fact claims: the tool returned the value the API holds "
            f"{ws['counts']['tool_returned_the_api_value']}, another value "
            f"{ws['counts']['tool_returned_another_value']}, not known {ws['counts']['not_known']}. Of these, the "
            f"API cell differs from the XBRL value in {ws['api_differs_from_gold']}.",
            "- Facts without a gold, by reason: "
            + (", ".join(f"{k} {v}" for k, v in sorted(rep["facts_without_gold"].items())) or "none") + ".",
            "- Rows left out, by reason: "
            + (", ".join(f"{k} {v}" for k, v in sorted(rep["rows_left_out"].items())) or "none") + ".",
            "- Swap cases not built because the neighbour as written passes for the fact: "
            + (", ".join(f"{k} {v}" for k, v in sorted(rep["swap_not_built"].items())) or "none") + ".",
            "- Fact claims not verified, by arm and reason: "
            + ("; ".join(f"{arm}: " + ", ".join(f"{k} {v}" for k, v in sorted(t.items()))
                         for arm, t in rep["not_verified_by_reason"].items()) or "none") + ".",
            f"- Stop cases without a key in which the stand-in saw a request: "
            f"{rep['stop_requests_seen_without_a_key']} requests. Stopped answers left in a cache: "
            f"{rep['stop_stopped_answers_cached']}.",
            f"- Key search: fill run of {rep['key_search']['fill_timestamp']} (commit "
            f"{rep['key_search']['fill_commit']}, same tool file: "
            f"{'yes' if rep['key_search']['fill_tool_is_this_tool'] else 'no'}, started with an empty cache: "
            f"{'yes' if rep['key_search']['fill_started_with_empty_cache'] else 'no'}), "
            f"{(rep['key_search']['fill_run'] or {}).get('files_searched', '?')} files searched; "
            f"{rep['key_search']['stop_cases_searched']} stop cases with a made-up key searched."]
    if ws["claims"]:
        out += ["", "### `wrong` verdicts on fact claims" + (" (first 40)" if len(ws["claims"]) > 40 else ""), "",
                "| set | arm | statement | account | column | written | XBRL | API | tool | class |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        out += [f"| {w['set']} | {w['arm']} | {w['statement']} | `{w['account']}` | {w['column']} | {w['written']} | "
                f"{w['gold']} | {w['api_value']} | {w['tool_value']} | {w['class']} |" for w in ws["claims"][:40]]
    for key in ("company", "report", "fs", "statement", "column", "way"):
        out += ["", f"## By {key if key != 'fs' else 'consolidated or separate'}"
                    f"{' of writing' if key == 'way' else ''}", "",
                "| key / plan | n | verified | wrong | unverifiable | other |", "|---|---|---|---|---|---|"]
        for k, row in r["breakdown"][key].items():
            v = {x: row.get("verdict:" + x, 0) for x in ("verified", "wrong", "unverifiable")}
            out.append(f"| {k} | {row['n']} | {v['verified']} | {v['wrong']} | {v['unverifiable']} | "
                       f"{row['n'] - sum(v.values())} |")
    out += ["", "## Swap cases by kind", "", "| kind / fact by id | n | wrong | unverifiable | other |",
            "|---|---|---|---|---|"]
    for k, row in rep["swap_by_kind"].items():
        v = {x: row.get("verdict:" + x, 0) for x in ("wrong", "unverifiable")}
        out.append(f"| {k} | {row['n']} | {v['wrong']} | {v['unverifiable']} | {row['n'] - sum(v.values())} |")
    out += ["", "## Abstention cases by kind", ""]
    out += [f"- {k}: " + ", ".join(f"{x} {n}" for x, n in sorted(v.items())) for k, v in rep["abstain_by_kind"].items()]
    out += ["", "## Amended reports", ""]
    if rep["amended"]:
        out += ["| set | fact | earlier receipt | verdict | served receipt | verdict | met |", "|---|---|---|---|---|---|---|"]
        out += [f"| {x['set']} | {x['id']} | {x['earliest_rcept_no']} | {x['earlier_verdict']} "
                f"{x['earlier_reason'] or ''} | {x['served_rcept_no']} | {x['served_verdict']} | "
                f"{'yes' if x['ok'] else 'no'} |" for x in rep["amended"]]
    else:
        out += ["- none in this run (0 cases; the measure counts as met)"]
    out += ["", "## Stop cases", "", "| set | case | exit | met | requests seen | key places |", "|---|---|---|---|---|---|"]
    out += [f"| {s['set']} | {s['variant']} | {s['exit']} | {'yes' if s['ok'] else 'no'} | "
            f"{'' if s['requests_seen'] is None else s['requests_seen']} | "
            f"{sum(p['count'] for p in s['key_places']) if s['made_up_key'] else ''} |" for s in r["stop_cases"]]
    out += ["", "## Sets", "", "| company | report | set | receipt | statements fetched | XBRL fetched | facts | amended |",
            "|---|---|---|---|---|---|---|---|"]
    out += [f"| {s['company']} ({s['corp_code']}) | {s['report']} {s['year']} | {s['fs']} | {s['rcept_no']} | "
            f"{s['statements_fetched']} | {s['xbrl_fetched']} | {s['facts']} | "
            f"{'earlier ' + str(s['earliest_rcept_no']) if s['amended'] else ''} |" for s in r["sets"]]
    if r["missing_sets"]:
        out += ["", "Sets DART has no data for: " + "; ".join(
            f"{m['company']} {m['report']} {m['year']} {m['fs']} ({m['reason']})" for m in r["missing_sets"])]
    if r.get("draw"):
        d = r["draw"]
        out += ["", f"Draw: {len(d['kept'])} kept of {d['wanted']}, {d['candidates']} candidates. Passed over: "
                + ("; ".join(f"{p['corp_code']} {p['corp_name']} ({p['reason']})" for p in d["passed_over"])
                   or "none") + "."]
    out += ["", f"`check` exit codes by plan file: {r['check_exit_codes']}.", ""]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fill", action="store_true", help="run the tool once with the key to fill its cache")
    ap.add_argument("--self-test", action="store_true", help="score made-up data with stub tools")
    ap.add_argument("--set", default="dev", help="dev or heldout (the builder's folder under --out)")
    ap.add_argument("--held-out", action="store_true", help="required for a held-out set")
    ap.add_argument("--out", default=os.path.join(HERE, "out"), help="the builder's output root")
    ap.add_argument("--tool", default=os.path.join(ROOT, "skills", "receipts", "scripts", "receipts.py"))
    ap.add_argument("--tool-cache", help="the tool's DART cache (default <out>/<set>/tool-cache)")
    ap.add_argument("--cache", default=os.path.join(HERE, "cache"), help="the builder's cache")
    ap.add_argument("--results-dir", default=os.path.join(HERE, "results"))
    ap.add_argument("--runs", default=os.path.join(HERE, "runs.jsonl"))
    ap.add_argument("--timeout", type=int, default=900, help="seconds per tool call")
    ap.add_argument("--jobs", type=int, default=1, help="plans checked at the same time while scoring")
    a = ap.parse_args(argv)
    if a.self_test:
        sys.path.insert(0, os.path.join(HERE, "testdata"))
        import run_selftest
        return run_selftest.main()
    root = os.path.join(a.out, a.set)
    mpath = os.path.join(root, "manifest.json")
    if not os.path.isfile(mpath):
        print(f"error: no cases in {root}. Run: python bench/dart/build.py --offline"
              + (" --held-out" if a.held_out or a.set == "heldout" else ""), file=sys.stderr)
        return 2
    manifest = load_json(mpath)
    if (manifest.get("held_out") or a.set == "heldout") and not a.held_out:
        print("refusing a held-out set: pass --held-out only for the release run.", file=sys.stderr)
        return 2
    if not os.path.isfile(a.tool):
        print(f"error: the receipts tool was not found at {a.tool}. Pass --tool PATH.", file=sys.stderr)
        return 2
    sets = manifest.get("sets") or []
    missing = [os.path.join(s["folder"], n) for s in sets for n in list(s["plans"]) + ["gold.json", "document.md"]
               if not os.path.isfile(os.path.join(root, "sets", s["folder"], n))]
    if not sets or missing:
        print(f"error: cases missing in {root}: {missing[:5] or 'no sets'}", file=sys.stderr)
        return 2
    tool = os.path.abspath(a.tool)
    cache = os.path.abspath(a.tool_cache or os.path.join(root, "tool-cache"))
    if a.fill:
        return fill(a, manifest, root, tool, cache)
    return score(a, manifest, root, tool, cache)


if __name__ == "__main__":
    sys.exit(main())
