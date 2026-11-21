#!/usr/bin/env python3
"""Offline self-test of the E4 builder and scorer on made-up data. No request leaves the machine.

  PYTHONUTF8=1 python bench/dart/evaluate.py --self-test        (or: python bench/dart/testdata/run_selftest.py)

1. build.py --self-test (ways of writing, tolerance, tables, fetch rules, XBRL reading, a full offline build).
2. A made-up DART (fake_dart.py) is served on 127.0.0.1. build.py fetches from it with a made-up key and builds
   the cases; the refusals of held-out companies are tried on the command line.
3. evaluate.py fills and scores those cases with the stub tool and with each broken stub. The bars each stub must
   fail are asserted. A stub that passes a bar it should fail, or fails one it should pass, fails the self-test.
4. The held-out path (the draw included) and a made-up API that disagrees with its XBRL file in one cell of
   every set are scored too.

Each stub is scored in its own copy of the cases, with its own tool cache and its own made-up DART, four at a
time. Everything is written under bench/dart/out/selftest/run-*/ and nothing is logged to bench/dart/runs.jsonl.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

TD = os.path.dirname(os.path.abspath(__file__))
DART = os.path.dirname(TD)
ROOT = os.path.dirname(os.path.dirname(DART))
PY = sys.executable
sys.path.insert(0, TD)
import fake_dart  # noqa: E402

KEY_ENV, URL_ENV = "RECEIPTS_DART_KEY", "RECEIPTS_DART_URL"
BUILD, EVALUATE = os.path.join(DART, "build.py"), os.path.join(DART, "evaluate.py")
PRINT = threading.Lock()

# stub file -> bars it must fail (all others must be met)
EXPECT = {
    "stub_tool.py": set(),
    "stub_column_ignored.py": {1, 2, 3, 4, 5},
    "stub_fs_unchecked.py": {4},
    "stub_receipt_ignored.py": {4, 5},
    "stub_status_as_no_data.py": {6},
    "stub_name_substring.py": {2},
    "stub_statement_ignored.py": {2, 4},
    "stub_cfq_allowed.py": {4},
    "stub_key_leak.py": {7},
    "stub_key_in_cache.py": {7},
    "stub_always_verified.py": {3, 4, 5},
    "stub_no_stop.py": {6},
}


def run(args, env=None):
    e = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    for k in (KEY_ENV, URL_ENV, "RECEIPTS_DART_CACHE", "DART_API_KEY"):
        e.pop(k, None)             # the self-test never sees a real key
    e.update(env or {})
    p = subprocess.run([PY] + args, capture_output=True, env=e, cwd=ROOT)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def check(cond, what, detail=""):
    if not cond:
        raise SystemExit(f"SELF-TEST FAILED: {what}\n{detail[-3000:]}")
    with PRINT:
        print(f"ok  {what}", flush=True)


def last_line(text):
    lines = text.strip().splitlines()
    return lines[-1] if lines else ""


def files_with(folder, secret):
    hits = []
    for base, _, names in os.walk(folder):
        for name in names:
            with open(os.path.join(base, name), "rb") as fh:
                if secret.encode() in fh.read() or secret in name:
                    hits.append(os.path.join(base, name))
    return hits


def scenario(work, name, src_out, set_name, stub, world, extra=()):
    """Fill and score one copy of the cases with one stub against its own made-up DART.
    Returns (failing bars, result)."""
    out = os.path.join(work, "cases-" + name)
    shutil.copytree(os.path.join(src_out, set_name), os.path.join(out, set_name))
    tool = os.path.join(TD, stub)
    tc = os.path.join(work, f"tool-cache-{name}")
    results = os.path.join(work, f"results-{name}")
    runs = os.path.join(work, f"runs-{name}.jsonl")
    common = ["--set", set_name, "--out", out, "--tool", tool, "--tool-cache", tc] + list(extra)
    server = fake_dart.FakeDart(world)
    server.start()
    try:
        env = {KEY_ENV: fake_dart.FAKE_KEY, URL_ENV: server.url}
        code, o, e = run([EVALUATE, "--fill"] + common, env=env)
        check(code == 0 and server.count() > 0, f"{name}: fill step ({last_line(o).split('; record')[0]})", o + e)
        before = server.count()
        # the scorer is handed the key and the address on purpose: it must keep both from the tool
        code, o, e = run([EVALUATE, "--jobs", "4", "--results-dir", results, "--runs", runs] + common, env=env)
        check(code in (0, 1), f"{name}: scorer ran (exit {code})", o + e)
        check(server.count() == before, f"{name}: scoring makes no request to the made-up DART")
    finally:
        server.stop()
    found = sorted(f for f in os.listdir(results) if f.endswith(".json"))
    check(len(found) == 1 and os.path.exists(os.path.join(results, found[0][:-5] + ".md")),
          f"{name}: one results file with its .md")
    with open(os.path.join(results, found[0]), encoding="utf-8") as fh:
        text = fh.read()
    result = json.loads(text)
    with open(runs, encoding="utf-8") as fh:
        logged = [json.loads(x) for x in fh if x.strip()]
    check(len(logged) == 1 and logged[0]["commit"] == result["commit"] and logged[0]["dirty"] == result["dirty"]
          and logged[0]["set"] == set_name and "measures" in logged[0] and logged[0]["made_up_data"],
          f"{name}: one line in the run log, with commit, dirty flag, set and results")
    failing = {m["n"] for m in result["measures"] if not m["ok"]}
    check((code == 0) == (not failing) and result["pass"] == (not failing), f"{name}: exit code follows the bars")
    check(fake_dart.FAKE_KEY not in text and not files_with(results, fake_dart.FAKE_KEY),
          f"{name}: the results never hold a key")
    return failing, result


def main():
    code, out, err = run([BUILD, "--self-test"])
    check(code == 0, "build.py --self-test: " + last_line(out), out + err)

    real_runs = os.path.join(DART, "runs.jsonl")
    real_size = os.path.getsize(real_runs) if os.path.exists(real_runs) else -1
    base = os.path.join(DART, "out", "selftest")
    os.makedirs(base, exist_ok=True)
    work = tempfile.mkdtemp(prefix="run-", dir=base)
    cache, out_root = os.path.join(work, "cache"), os.path.join(work, "out")
    qcache, qout = os.path.join(work, "quirk-cache"), os.path.join(work, "quirk-out")
    registry = os.path.join(work, "registry.json")
    with open(registry, "w", encoding="utf-8") as fh:
        json.dump(fake_dart.REGISTRY, fh, ensure_ascii=False)
    server = fake_dart.FakeDart(fake_dart.World())
    server.start()
    differs = fake_dart.FakeDart(fake_dart.World(api_differs=True))
    differs.start()
    key = {KEY_ENV: fake_dart.FAKE_KEY}
    made_up = ["--registry", registry, "--api-base", server.url, "--cache", cache]
    offline = ["--offline", "--registry", registry, "--cache", cache, "--out", out_root]
    tool = os.path.join(TD, "stub_tool.py")
    try:
        # refusals: a real held-out code without a key in the environment, and a made-up held-out code
        code, o, e = run([BUILD, "--fetch-only", "--companies", "00106641", "--cache", os.path.join(work, "none")])
        check(code == 2 and "--held-out" in e and not os.path.exists(os.path.join(work, "none")),
              "build.py refuses a held-out company without --held-out, before any request", o + e)
        code, o, e = run([BUILD, "--fetch-only", "--companies", "99000003"] + made_up, env=key)
        check(code == 2 and server.count() == 0, "the made-up held-out company is refused too, no request made", o + e)
        code, o, e = run([BUILD, "--fetch-only"] + made_up)
        check(code == 2 and server.count() == 0 and KEY_ENV in e, "without a key the fetch stops, no request made")
        code, o, e = run([BUILD] + made_up)
        check(code == 2, "build.py wants --fetch-only or --offline")

        code, o, e = run([BUILD, "--fetch-only"] + made_up, env=key)
        check(code == 0 and server.count() > 0, "fetch from the made-up DART: " + last_line(o), o + e)
        check(server.asked_companies() == {c for c, _ in fake_dart.REGISTRY["dev"]},
              "the development fetch asks for development companies only")
        n = server.count()
        code, o, e = run([BUILD, "--fetch-only"] + made_up, env=key)
        check(code == 0 and server.count() == n, "a second fetch makes no request")
        code, o, e = run([BUILD] + offline)
        check(code == 0 and server.count() == n, "offline build, no key, no request", o + e)
        code, o, e = run([BUILD, "--held-out"] + offline)
        check(code == 2, "the held-out build stops before its own fetch")

        # the held-out path, on made-up companies
        code, o, e = run([BUILD, "--fetch-only", "--held-out"] + made_up, env=key)
        check(code == 0, "held-out fetch with the draw: " + last_line(o), o + e)
        code, o, e = run([BUILD, "--held-out"] + offline)
        check(code == 0, "held-out build", o + e)
        for args in (["--fill"], []):
            code, o, e = run([EVALUATE, "--set", "heldout", "--out", out_root, "--tool", tool] + args,
                             env={KEY_ENV: fake_dart.FAKE_KEY, URL_ENV: server.url})
            check(code == 2 and "--held-out" in e, f"evaluate.py refuses the held-out set without --held-out {args}")

        # a made-up API that disagrees with its XBRL file in one cell of every set
        code, o, e = run([BUILD, "--fetch-only", "--registry", registry, "--api-base", differs.url,
                          "--cache", qcache], env=key)
        check(code == 0, "fetch from the made-up DART whose API disagrees with its XBRL file", o + e)
        code, o, e = run([BUILD, "--offline", "--registry", registry, "--cache", qcache, "--out", qout])
        check(code == 0, "build on it", o + e)
        check(not files_with(work, fake_dart.FAKE_KEY), "the made-up key is in no file of the caches or the cases")
    finally:
        server.stop()
        differs.stop()

    jobs = [(stub[5:-3], out_root, "dev", stub, fake_dart.World(), ()) for stub in EXPECT]
    jobs.append(("heldout", out_root, "heldout", "stub_tool.py", fake_dart.World(), ("--held-out",)))
    jobs.append(("api-differs", qout, "dev", "stub_tool.py", fake_dart.World(api_differs=True), ()))
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {j[0]: pool.submit(scenario, work, *j) for j in jobs}
        got = {name: f.result() for name, f in futures.items()}

    for stub, want in EXPECT.items():
        name = stub[5:-3]
        failing, result = got[name]
        check(failing == want, f"{name}: fails bars {sorted(failing)}, expected {sorted(want)}",
              json.dumps(result["measures"], ensure_ascii=False, indent=1))
        check(result["made_up_data"] and not result["held_out"], f"{name}: the results say the data is made up")
    _, result = got["tool"]
    c = result["counts"]
    check(c["sets"] == 9 and c["stop_cases"] == 25 and c["amended"] == 10 and c["abstain"] == 9 * 35 + 28
          and c["swap"] > 0 and 0 < c["by_name"] < c["by_id"] == c["pinned"], f"counts: {c}")
    by_id = result["measures"][1]["arms"]["by_id"]
    check(by_id["unverifiable"] == 2 and by_id["other"] == 0,
          "an amount the API leaves empty is unverifiable and the bar still holds")
    ks = result["reported"]["key_search"]
    check(ks["fill_tool_is_this_tool"] and ks["fill_started_with_empty_cache"] and ks["stop_cases_searched"] == 15
          and ks["fill_run"]["files_searched"] > 0,
          "the key search covers the fill run and the stop cases with a made-up key")
    check(result["reported"]["stop_requests_seen_without_a_key"] == 0, "no request without a key")
    m7 = got["key_leak"][1]["measures"][6]
    check(m7["fill_run"] > 0 and m7["stop_cases"] > 0, "a printed key is found in the fill run and in the stop cases")
    kinds = got["cfq_allowed"][1]["reported"]["abstain_by_kind"]
    check(kinds["cfq"]["n"] == 28 and kinds["cfq"].get("verdict:unverifiable", 0) < 28
          and all(v["n"] == v.get("verdict:unverifiable") for k, v in kinds.items() if k != "cfq"),
          "a tool that reads frmtrm_q_amount of an interim cash flow statement fails the cfq cases, and only those")
    kinds = result["reported"]["abstain_by_kind"]
    check(kinds["cfq"]["n"] == 28 == kinds["cfq"].get("reason:unsupported"),
          "the stub refuses frmtrm_q_amount of an interim cash flow statement with the reason `unsupported`")
    places = got["key_in_cache"][1]["reported"]["key_search"]["fill_run"]["places"]
    check(any("fetched.json" in p["where"] for p in places), "a key written to the cache is found")
    check(got["fs_unchecked"][1]["failures"] and all("abstain-fs.json" in f["where"]
                                                     for f in got["fs_unchecked"][1]["failures"]),
          "a tool that would ask DART while scoring is stopped: the plan counts as not answered")

    failing, result = got["heldout"]
    check(failing == set() and result["held_out"] and result["draw"]["complete"]
          and len(result["draw"]["passed_over"]) > 0, "held-out set: all bars met, the draw is in the results")
    m5 = result["measures"][4]
    check(m5["cases"] == 0 and m5["ok"] and "counted as met" in m5["note"],
          "a run without an amended report counts measure 5 as met and says so")

    failing, result = got["api-differs"]
    ws = result["reported"]["wrong_split"]
    wrong = result["measures"][0]["value"]
    check(failing == {1, 2, 3}, f"API and XBRL differ: bars {sorted(failing)} fail",
          json.dumps(result["measures"], ensure_ascii=False, indent=1))
    check(wrong == 27 and ws["counts"]["tool_returned_the_api_value"] == wrong == ws["api_differs_from_gold"]
          and ws["counts"]["tool_returned_another_value"] == 0 and ws["counts"]["not_known"] == 0
          and all(w["column"] == "thstrm_amount" and w["account"] == "dart_OperatingIncomeLoss"
                  for w in ws["claims"]),
          f"every one of the {wrong} wrong verdicts is traced to the API, none to the tool")

    now = os.path.getsize(real_runs) if os.path.exists(real_runs) else -1
    check(now == real_size, "bench/dart/runs.jsonl was not touched")
    print(f"self-test passed (files in {work})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
