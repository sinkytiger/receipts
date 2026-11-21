"""Offline checks of the E4 builder on made-up data (python bench/dart/build.py --self-test).

No request leaves the machine: the Fetcher is given an opener that asks the made-up world in fake_dart.py. The
key is the made-up one of that file. Nothing is written outside a temporary folder.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import tempfile
import urllib.error
from decimal import Decimal

import fake_dart

CHECKS = []


def ok(cond, what):
    if not cond:
        raise AssertionError(what)
    CHECKS.append(what)


def run(build):
    saved = os.environ.get(build.KEY_ENV)
    try:
        with tempfile.TemporaryDirectory(prefix="receipts-e4-selftest-", ignore_cleanup_errors=True) as tmp:
            _writing(build)
            _tables(build)
            _companies(build)
            _fetch_rules(build, tmp)
            _instance(build)
            _end_to_end(build, tmp)
    except AssertionError as e:
        print(f"SELF-TEST FAILED: {e}")
        return 1
    finally:
        if saved is None:
            os.environ.pop(build.KEY_ENV, None)
        else:
            os.environ[build.KEY_ENV] = saved
    print(f"self-test passed: {len(CHECKS)} checks")
    return 0


# ---------------------------------------------------------------- writing and tolerance

def _writing(build):
    D = Decimal
    v = D("107440482000000")
    texts = [build.write_value(v, w, "KRW")["text"] for w in build.WAYS]
    ok(texts == ["107,440,482,000,000원", "107,440,482백만원", "1,074,405억원", "107조 4,405억원"],
       f"the plan's four ways of writing: {texts}")
    cases = [
        (D("-3454670000000"), "jo_eok", "-3조 4,547억원", "jo_eok"),
        (D("-3454670000000"), "eok", "-34,547억원", "eok"),
        (D("12345678"), "million", "12,345,678원", "won"),
        (D("12345678"), "eok", "12,345,678원", "won"),
        (D("99999999"), "eok", "99,999,999원", "won"),
        (D("100000000"), "eok", "1억원", "eok"),
        (D("150000000"), "eok", "2억원", "eok"),
        (D("-150000000"), "eok", "-2억원", "eok"),
        (D("999950000000"), "jo_eok", "999,950,000,000원", "won"),
        (D("1000000000000"), "jo_eok", "1조원", "jo_eok"),
        (D("1999960000000"), "jo_eok", "2조원", "jo_eok"),
        (D("1000049999999"), "jo_eok", "1조원", "jo_eok"),
        (D("1000050000000"), "jo_eok", "1조 1억원", "jo_eok"),
        (D("0"), "million", "0원", "won"),
        (D("0"), "jo_eok", "0원", "won"),
        (D("5000000"), "million", "5백만원", "million"),
    ]
    for value, want, text, way in cases:
        w = build.write_value(value, want, "KRW")
        ok(w["text"] == text and w["way"] == way, f"write {value} as {want}: {w['text']} ({w['way']})")
    w = build.write_value(D("6605"), "eok", "KRW/shares")
    ok(w["text"] == "6,605원" and w["number"] == "6,605", "won per share is written in won")
    w = build.write_value(D("0.520"), "million", "USD/shares")
    ok(w["text"] == "0.52 USD" and w["quantum"] == D("0.01"), f"a decimal amount in another currency: {w['text']}")
    ok(build.write_value(D("1234"), "won", "shares")["text"] == "1,234주", "a share count")
    ok(build.write_value(D("1234"), "won", "pure")["text"] == "1,234", "a pure number has no unit word")
    for value in (v, D("-3454670000000"), D("150000000"), D("250000000"), D("1999960000000"), D("7"), D("0")):
        for want in build.WAYS:
            w = build.write_value(value, want, "KRW")
            ok(build.matches(w["written"], w["quantum"], value), f"{value} written as {want} matches itself")
            ok(w["number"] in w["text"] and w["text"].startswith(w["number"]), "the number opens its text")
    ok(build.matches(D("107440400000000"), build.EOK, v), "a value cut off at the written digit is accepted")
    ok(not build.matches(D("107440600000000"), build.EOK, v), "one unit off is not accepted")
    ok(build.matches(D("200000000"), build.EOK, D("250000000")), "a tie may go to the even digit")
    ok(build.matches(D("300000000"), build.EOK, D("250000000")), "a tie may go away from zero")
    ok(not build.matches(D("-107440482000000"), D(1), v), "the sign counts")
    line, quote = build._line("BS · ifrs-full_Assets · 자산`총계 · thstrm_amount", "1조 1억원")
    ok(line == "- `BS · ifrs-full_Assets · 자산'총계 · thstrm_amount` 값 1조 1억원" and quote == "값 1조 1억원",
       "a document line keeps everything but the number in inline code")


# ---------------------------------------------------------------- tables of the contract and the plan

def _tables(build):
    for cal in ("dec", "mar"):
        for report in ("annual", "half", "q3"):
            for st in build.STATEMENTS:
                mine = [c for c, _ in build.columns_for(report, st)]
                theirs = [c for c, _ in fake_dart.column_periods(cal, report, st)]
                ok(sorted(mine) == sorted(theirs), f"columns of {report} {st}: {mine}")
    n = build.neighbours
    ok(n("annual", "BS", "thstrm_amount") == [("b", "frmtrm_amount")] and n("annual", "IS", "bfefrmtrm_amount") == []
       and n("annual", "CF", "frmtrm_amount") == [("b", "thstrm_amount")], "swap neighbours in annual sets")
    ok(n("half", "IS", "thstrm_amount") == [("a", "thstrm_add_amount"), ("b", "frmtrm_q_amount")]
       and n("q3", "CIS", "frmtrm_add_amount") == [("a", "frmtrm_q_amount"), ("b", "thstrm_add_amount")]
       and n("q3", "CIS", "frmtrm_q_amount") == [("a", "frmtrm_add_amount"), ("b", "thstrm_amount")]
       and n("half", "IS", "thstrm_add_amount") == [("a", "thstrm_amount"), ("b", "frmtrm_add_amount")],
       "swap neighbours in interim income statements")
    ok(n("half", "BS", "frmtrm_amount") == [("b", "thstrm_amount")] and n("q3", "CF", "thstrm_amount")
       == [("b", "frmtrm_amount")] and n("q3", "CF", "frmtrm_q_amount") == [], "swap neighbours in interim BS and CF")
    ok(build.report_name("[기재정정]사업보고서 (2025.12)") == "사업보고서 (2025.12)"
       and build.report_name(" [첨부추가] [기재정정] 분기보고서  (2026.09) ") == "분기보고서 (2026.09)"
       and build.report_name("사업보고서 (2025.12)") == "사업보고서 (2025.12)", "leading marks are removed")
    rows = [{"rcept_no": "20990701000003", "rcept_dt": "20990701", "report_nm": "[기재정정]사업보고서 (2025.12)"},
            {"rcept_no": "20990401000002", "rcept_dt": "20990401", "report_nm": "[기재정정]사업보고서 (2025.12)"},
            {"rcept_no": "20990310000001", "rcept_dt": "20990310", "report_nm": "사업보고서 (2025.12)"},
            {"rcept_no": "20980310000001", "rcept_dt": "20980310", "report_nm": "사업보고서 (2024.12)"},
            {"rcept_no": "20991114000009", "rcept_dt": "20991114", "report_nm": "[첨부추가]분기보고서 (2026.09)"}]
    a = build.amended_info(rows, "20990701000003")
    ok(a["amended"] and a["earliest_rcept_no"] == "20990310000001" and len(a["earlier"]) == 2,
       "an amended report is pinned to the earliest filing of the same name")
    ok(not build.amended_info(rows, "20990310000001")["amended"], "the first filing is not an amended set")
    ok(not build.amended_info(rows, "20991114000009")["amended"], "a mark on the only version is not an amendment")
    ok(build.amended_info(rows, "20990101000000") == {"in_filing_list": False, "amended": False},
       "a receipt number the list does not show")
    rows_in = [("BS", "ifrs-full_Assets", " 자산총계 "), ("BS", "dart_A", "기타"), ("BS", "dart_B", "기타"),
               ("BS", fake_dart.NOSTD, "잡"), ("BS", "ifrs-full_X", "하나"), ("BS", "ifrs-full_X", "둘"),
               ("BS", "entity99_Own", "자체"), ("SCE", "ifrs-full_Equity", "자본"), ("XX", "ifrs-full_Y", "y"),
               ("IS", "ifrs-full_Assets", "자산총계")]
    elig, left, ids = build.eligible_rows(rows_in)
    ok([(e["statement"], e["account"], e["name_once"]) for e in elig]
       == [("BS", "ifrs-full_Assets", True), ("BS", "dart_A", False), ("BS", "dart_B", False),
           ("IS", "ifrs-full_Assets", True)], "eligible rows and names that occur once")
    ok(dict(left) == {"no_standard_account_id": 1, "account_id_repeats": 2, "other_id_prefix": 1,
                      "statement_of_changes_in_equity": 1, "other_statement": 1}, f"rows left out: {dict(left)}")
    # ambiguous rows (measure 8): a name or a standard id that several rows of one statement share
    amb = build.ambiguous_rows(rows_in, "annual")
    ok([(x["kind"], x["statement"], x.get("name") or x.get("account"), x["column"], x["rows_sharing"]) for x in amb]
       == [("name", "BS", "기타", "thstrm_amount", 2), ("name", "BS", "기타", "thstrm_amount", 2),
           ("id", "BS", "ifrs-full_X", "thstrm_amount", 2), ("id", "BS", "ifrs-full_X", "thstrm_amount", 2)],
       "ambiguous rows: a shared name, then a shared id, in DART's order")
    many = [("CF", fake_dart.NOSTD, " 같은 이름 ")] * 7 + [("CF", "dart_Same", "같은 이름")] * 6 \
        + [("CF", fake_dart.NOSTD, "")] * 3 + [("SCE", "dart_Same", "같은 이름")] * 2 \
        + [("IS", "dart_Same", "같은 이름"), ("IS", "entity99_Own", "자체"), ("IS", "entity99_Own", "자체 둘")]
    amb = build.ambiguous_rows(many, "q3")
    names = [x for x in amb if x["kind"] == "name"]
    by_id = [x for x in amb if x["kind"] == "id"]
    ok(len(names) == 10 and all(x["statement"] == "CF" and x["rows_sharing"] == 13 for x in names)
       and [x["name"] for x in names] == [" 같은 이름 "] * 7 + ["같은 이름"] * 3,
       "ambiguous by name: names are compared trimmed, any account id counts, at most ten rows")
    ok(len(by_id) == 6 and all(x["account"] == "dart_Same" and x["statement"] == "CF" and x["rows_sharing"] == 6
                              and x["column"] == "thstrm_amount" for x in by_id),
       "ambiguous by id: standard ids only, counted inside one statement, empty names and SCE rows left out")
    ok(build.ambiguous_rows([("BS", "ifrs-full_Assets", "자산총계"), ("IS", "ifrs-full_Assets", "자산총계")],
                            "annual") == [], "the same name or id in two statements is not ambiguous")


# ---------------------------------------------------------------- companies and the draw

def _companies(build):
    reg = build.registry()
    ok(len(reg["dev"]) == 10 and len(reg["heldout"]) == 11 and reg["draw"] == 20, "the registered lists")
    ok([c for c, _ in build.choose_companies(reg, False)] == [c for c, _ in build.DEV], "development by default")
    for code, _ in build.HELD_OUT_NAMED:
        try:
            build.choose_companies(reg, False, [code])
            ok(False, f"held-out company {code} was not refused")
        except build.Stop as e:
            ok("--held-out" in str(e), f"held-out company {code} is refused without --held-out")
    for code in ("99999999", "00000000"):
        try:
            build.choose_companies(reg, False, [code])
            ok(False, "a company outside the lists was not refused")
        except build.Stop:
            ok(True, f"a company outside the lists is refused ({code})")
    try:
        build.choose_companies(reg, True, ["00126380"])
        ok(False, "--companies with --held-out was not refused")
    except build.Stop:
        ok(True, "--companies cannot narrow the held-out run")
    rows = [{"corp_code": f"9900{i:04d}", "corp_name": f"c{i}", "stock_code": "" if i % 3 == 0 else f"Z{i:05d}"}
            for i in range(1, 40)]
    order = build.draw_order(rows, {"99000001", "99000002"})
    want = sorted((r for r in rows if r["stock_code"] and r["corp_code"] not in ("99000001", "99000002")),
                  key=lambda r: hashlib.sha256(("receipts-E4:" + r["corp_code"]).encode()).hexdigest())
    ok([r["corp_code"] for r in order] == [r["corp_code"] for r in want] and len(order) == 24,
       "the draw order is the SHA-256 order of 'receipts-E4:' + company code, named companies left out")


# ---------------------------------------------------------------- fetch rules

class _Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def _fetch_rules(build, tmp):
    world = fake_dart.World()
    key = fake_dart.FAKE_KEY
    os.environ.pop(build.KEY_ENV, None)
    log, said = [], []
    clock = _Clock()

    def fetcher(cache, opener=None, **kw):
        return build.Fetcher(cache, opener=opener or fake_dart.in_process_opener(world, key, log),
                             sleep=clock.sleep, clock=clock.now, log=said.append, **kw)
    cache = os.path.join(tmp, "rules-cache")
    f = fetcher(cache)
    try:
        f.statements("99000001", 2025, "11011", "CFS")
        ok(False, "a request without a key was not refused")
    except build.Stop as e:
        ok(build.KEY_ENV in str(e) and not log, "without a key nothing is requested")
    os.environ[build.KEY_ENV] = key
    data, fetched = f.statements("99000001", 2025, "11011", "CFS")
    ok(fetched and data["status"] == "000" and len(log) == 1, "a statements answer is fetched")
    data, fetched = f.statements("99000001", 2025, "11011", "CFS")
    ok(not fetched and len(log) == 1, "a cached answer is not fetched again")
    data, fetched = f.statements("99000001", 2031, "11011", "CFS")
    ok(fetched and data["status"] == "013", "DART's answer 'no data' is an answer")
    data, fetched = f.statements("99000001", 2031, "11011", "CFS")
    ok(not fetched and len(log) == 2, "the answer 'no data' is cached too")
    ok(len(clock.slept) >= 1 and all(abs(s - f.MIN_GAP) < 1e-9 for s in clock.slept),
       f"requests are at least {f.MIN_GAP} s apart (at most 4 a second)")
    try:
        f.statements("99000001", 2025, "11011", "XXX")
        ok(False, "an unknown statement type was requested")
    except build.Stop:
        ok(len(log) == 2, "only CFS and OFS are ever requested")
    body, status, fetched = f.xbrl(world.receipt("99000001", "annual"), "11011")
    ok(body[:2] == b"PK" and status == "000" and fetched, "the XBRL zip is fetched")
    body, status, fetched = f.xbrl("20990310009999", "11011")
    ok(body is None and status == "014" and fetched, "'no such file' is an answer")
    n = len(log)
    f.xbrl("20990310009999", "11011")
    f.xbrl(world.receipt("99000001", "annual"), "11011")
    ok(len(log) == n, "XBRL answers are cached, the answer 'no file' included")
    rows, info, made = f.filing_list("99000001")
    ok(len(rows) == len(world.filings("99000001")) and made == (len(rows) + fake_dart.PAGE - 1) // fake_dart.PAGE
       and info.get("list_from") == build.LIST_FROM, "every page of the filing list is read")
    rows2, _, made = f.filing_list("99000001")
    ok(made == 0 and rows2 == rows, "the filing list is cached")
    codes, fetched = f.corp_codes()
    ok(fetched and len(codes) == len(fake_dart.COMPANIES), "the company code file is read")
    ok(f.requests == len(log), "the request count is the number of requests made")

    # statuses that stop, retries, and what a message may show
    def status_opener(status):
        def open_(req, timeout=None):
            log.append(("stop", {}))
            body = json.dumps({"status": status, "message": "made-up"}).encode()
            return fake_dart._Answer(body)
        return open_
    for status in ("010", "011", "012", "020", "100", "101", "800", "900", "901"):
        cache2 = os.path.join(tmp, f"stop-{status}")
        try:
            fetcher(cache2, opener=status_opener(status)).statements("99000001", 2025, "11011", "CFS")
            ok(False, f"status {status} did not stop the fetch")
        except build.Stop as e:
            ok(status in str(e) and key not in str(e) and "://" not in str(e)
               and not os.path.isdir(os.path.join(cache2, "fnlttSinglAcntAll")),
               f"status {status} stops the fetch and is not cached")
    calls = []

    def flaky(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", None, None)
        return fake_dart.in_process_opener(world, key, log)(req, timeout)
    before = len(clock.slept)
    data, fetched = fetcher(os.path.join(tmp, "flaky"), opener=flaky).statements("99000001", 2025, "11011", "OFS")
    ok(fetched and len(calls) == 2 and build.Fetcher.RETRY_WAIT in clock.slept[before:],
       "HTTP 503 is tried once more after two seconds")
    for error in (urllib.error.URLError(ConnectionRefusedError("refused")),
                  urllib.error.HTTPError("http://x/?crtfc_key=" + key, 404, "nf", None, None),
                  urllib.error.HTTPError("http://x/?crtfc_key=" + key, 302, "moved", None, None),
                  TimeoutError("timed out"), ValueError("bad " + key)):
        def broken(req, timeout=None, error=error):
            raise error
        try:
            fetcher(os.path.join(tmp, "broken"), opener=broken).statements("99000001", 2025, "11011", "CFS")
            ok(False, "a failed request did not stop the fetch")
        except build.Stop as e:
            text = build.scrub(e)
            ok(key not in str(e) and "crtfc_key" not in text and "://" not in text
               and "fnlttSinglAcntAll.json" in text, f"a failed request names the endpoint only: {text}")
    off = build.Fetcher(os.path.join(tmp, "empty"), offline=True, opener=fake_dart.in_process_opener(world, key, log))
    n = len(log)
    try:
        off.statements("99000001", 2025, "11011", "CFS")
        ok(False, "an offline fetcher made a request")
    except build.Stop:
        ok(len(log) == n, "offline, nothing is requested")
    ok(build.scrub(f"GET http://h/api/x.json?crtfc_key={key}&corp_code=1") ==
       "GET http://h/api/x.json?crtfc_key=<key>&corp_code=1", "scrub removes a key from a text")
    for folder, _, names in os.walk(tmp):
        for name in names:
            with open(os.path.join(folder, name), "rb") as fh:
                ok(key.encode() not in fh.read() and key not in name, f"no key in the cache file {name}")
    ok(not any(key in str(s) for s in said), "no key in what the fetcher says")


# ---------------------------------------------------------------- the instance, periods and gold

def _instance(build):
    world = fake_dart.World()
    for corp in ("99000001", "99000002"):
        c = fake_dart.COMPANIES[corp]
        for report in c["reports"]:
            data, name = world.instance(corp, report)
            inst = build.Instance(data)
            ok(not inst.ns_conflict and "ifrs-full" in inst.ns, f"{name}: prefixes are read from the file")
            for fs in c["fs"]:
                periods, notes = build.set_periods(inst, report, fs)
                ok(not notes, f"{corp} {report} {fs}: every period is found ({notes})")
                for st in build.STATEMENTS:
                    theirs = dict(fake_dart.column_periods(c["cal"], report, st))
                    for col, pk in build.columns_for(report, st):
                        ok(tuple(periods[pk]) == tuple(theirs[col]),
                           f"{corp} {report} {fs} {st} {col}: period {periods[pk]}")
                for row in c["rows"]:
                    st, account, rname, kind, size, opts = row
                    if st == "SCE" or account == fake_dart.NOSTD or ("cfs" in opts and fs != "CFS"):
                        continue
                    theirs = fake_dart.column_periods(c["cal"], report, st)
                    for i, (col, pk) in enumerate(build.columns_for(report, st)):
                        value, unit, why = build.gold_of(inst, account, fs, periods.get(pk))
                        period = dict(theirs)[col]
                        if kind == "cash":
                            ok(why == "only_instant_facts", f"{account} {col}: a balance in a flow statement")
                        elif "current" in opts and col != "thstrm_amount":
                            ok(why == "no_fact_in_period", f"{account} {col}: no fact in that period")
                        elif "differ" in opts and col == theirs[1][0]:
                            ok(why == "values_differ", f"{account} {col}: two different values")
                        else:
                            want = Decimal(world.cell(corp, fs, row, period, current=True))
                            ok(why is None and value == want, f"{corp} {report} {fs} {st} {account} {col}: {value}")
                            ok(unit == ("KRW/shares" if "eps" in opts else "KRW"), f"{account}: unit {unit}")
    data, _ = world.instance("99000001", "annual")
    inst = build.Instance(data)
    periods, _ = build.set_periods(inst, "annual", "CFS")
    ok(build.gold_of(inst, "ifrs-full_NoSuchElement", "CFS", periods["year0"])[2] == "no_fact", "an unknown element")
    ok(build.gold_of(inst, "other_Thing", "CFS", periods["year0"])[2] == "prefix_not_declared", "an unknown prefix")
    ok(build.gold_of(inst, "ifrs-full_Revenue", "CFS", None)[2] == "period_not_known", "an unknown period")
    ok(build.gold_of(inst, "ifrs-full_Assets", "CFS", periods["year0"])[2] == "only_instant_facts"
       and build.gold_of(inst, "ifrs-full_Revenue", "CFS", periods["end0"])[2] == "only_duration_facts",
       "a balance asked as a flow and a flow asked as a balance")
    rev = build.gold_of(inst, "ifrs-full_Revenue", "CFS", periods["year0"])[0]
    ok(rev not in (Decimal(111000000), Decimal(222000000), Decimal(333000000)),
       "facts on a breakdown axis, in a typed context or without a member are not the gold")
    try:
        build.Instance(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>')
        ok(False, "a DOCTYPE was read")
    except ValueError:
        ok(True, "an instance with a DOCTYPE is not read")
    broken = data.replace(b"PeriodCoveredbytheReportofCurrentFiscalYear", b"SomethingElse")
    periods, notes = build.set_periods(build.Instance(broken), "annual", "CFS")
    ok(periods == {} and notes, "an instance that does not state its period gives no period")


# ---------------------------------------------------------------- fetch, build, held-out

LINE_RE = re.compile(r"^- 값 -?\d{1,3}(,\d{3})*(백만|억|조( \d{1,3}(,\d{3})*억)?)?원$")


def _expect_requests(world, corps):
    n = 0
    for corp in corps:
        receipts = set()
        for report, (year, code) in fake_dart.REPORTS.items():
            n += 2
            for fs in ("CFS", "OFS"):
                if world.statements(corp, year, code, fs)["status"] == "000":
                    receipts.add(world.receipt(corp, report))
        n += len(receipts)
        n += max(1, (len(world.filings(corp)) + fake_dart.PAGE - 1) // fake_dart.PAGE)
    return n


def _end_to_end(build, tmp):
    world = fake_dart.World()
    key = fake_dart.FAKE_KEY
    os.environ[build.KEY_ENV] = key
    reg_path = os.path.join(tmp, "registry.json")
    with open(reg_path, "w", encoding="utf-8") as fh:
        json.dump(fake_dart.REGISTRY, fh, ensure_ascii=False)
    reg = build.registry(reg_path)
    cache, out, log, said = os.path.join(tmp, "cache"), os.path.join(tmp, "out"), [], []

    def fetcher(offline=False):
        return build.Fetcher(cache, offline=offline, opener=fake_dart.in_process_opener(world, key, log),
                             sleep=lambda s: None, log=said.append)
    # a held-out company is refused before any request
    for code in ("99000003", "00106641"):
        try:
            build.fetch_only(reg, False, [code], fetcher(), said.append)
            ok(False, "a held-out company was fetched without --held-out")
        except build.Stop:
            ok(not log, f"held-out company {code}: refused, no request made")
    f = fetcher()
    build.fetch_only(reg, False, None, f, said.append)
    dev = [c for c, _ in reg["dev"]]
    ok(f.requests == 1 + _expect_requests(world, dev) == len(log),
       f"the development fetch makes {f.requests} requests: 6 statements, the XBRL files and the list per company")
    ok({q.get("corp_code") for _, q in log if q.get("corp_code")} == set(dev),
       "the development fetch asks for development companies only")
    f2 = fetcher()
    build.fetch_only(reg, False, None, f2, said.append)
    ok(f2.requests == 0, "a second fetch makes no request")
    ok(not os.path.exists(os.path.join(cache, "corpCode-heldout.zip")),
       "the held-out company code file is not fetched by a development run")
    ok(not any(key in str(s) for s in said), "the fetch step never says the key")

    n = len(log)
    os.environ.pop(build.KEY_ENV, None)
    with contextlib.redirect_stdout(io.StringIO()):
        code = build.build(reg, False, None, fetcher(offline=True), out, "dev")
    ok(code == 0 and len(log) == n, "the build makes no request and needs no key")
    m = build.load_json(os.path.join(out, "dev", "manifest.json"))
    folders = [s["folder"] for s in m["sets"]]
    ok(folders == ["99000001-annual-CFS", "99000001-annual-OFS", "99000001-half-CFS", "99000001-half-OFS",
                   "99000001-q3-CFS", "99000001-q3-OFS", "99000002-annual-OFS", "99000002-half-OFS",
                   "99000002-q3-OFS"], f"the sets in run order: {folders}")
    ok([(x["corp_code"], x["report"], x["fs"]) for x in m["missing_sets"]]
       == [("99000002", r, "CFS") for r in ("annual", "half", "q3")], "sets DART has no data for are reported")
    total_facts = total_cfq = 0
    total_amb = {"name": 0, "id": 0}
    for s in m["sets"]:
        corp, report, fs = s["corp_code"], s["report"], s["fs"]
        c = fake_dart.COMPANIES[corp]
        folder = os.path.join(out, "dev", "sets", s["folder"])
        gold = build.load_json(os.path.join(folder, "gold.json"))
        year, reprt = fake_dart.REPORTS[report]
        api = world.statements(corp, year, reprt, fs)["list"]
        ok(s["rcept_no"] == world.receipt(corp, report), f"{s['folder']}: the receipt number DART serves")
        ok(s["company_in_plans"] == (c["stock"] if report == "annual" else corp),
           f"{s['folder']}: stock code in annual plans, company code in the others")
        with open(os.path.join(folder, "document.md"), encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        ok(lines[-1] == "" and len(lines) - 1 == len(gold["facts"]) == s["counts"]["facts"],
           f"{s['folder']}: one line per fact")
        # expected facts, worked out from the made-up world on its own
        want = {}
        for row in c["rows"]:
            st, account, name, kind, size, opts = row
            if st == "SCE" or account == fake_dart.NOSTD or "twice" in opts or kind == "cash" \
                    or ("cfs" in opts and fs != "CFS"):
                continue
            cols = fake_dart.column_periods(c["cal"], report, st)
            for i, (col, period) in enumerate(cols):
                if ("current" in opts and i > 0) or ("differ" in opts and i == 1):
                    continue
                want[(st, account, col)] = Decimal(world.cell(corp, fs, row, period, current=True))
        got = {(g["statement"], g["account"], g["column"]): Decimal(g["value"]) for g in gold["facts"]}
        ok(got == want, f"{s['folder']}: {len(got)} facts with the gold of the instance")
        total_facts += len(got)
        by_id = build.load_json(os.path.join(folder, "by-id.json"))
        ok(by_id["document"] == "document.md" and len(by_id["claims"]) == len(got), f"{s['folder']}: by-id plan")
        cells = {(r["sj_div"], r["account_id"]): r for r in api}
        ways = set()
        for g, claim, line in zip(gold["facts"], by_id["claims"], lines):
            loc = claim["operands"]["of"]
            ok(claim["id"] == g["id"] and claim["line"] == g["line"] and lines[claim["line"] - 1] == line
               and claim["quote"] in line and claim["number"] in claim["quote"] and claim["kind"] == "lookup",
               f"{s['folder']} {g['id']}: the claim points at its line")
            ok(loc == {"dart": s["company_in_plans"], "year": year, "report": report, "fs": fs,
                       "statement": g["statement"], "account": g["account"], "column": g["column"]},
               f"{s['folder']} {g['id']}: the locator")
            bare = re.sub(r"`[^`]*`", "", line).replace("  ", " ")
            ok(LINE_RE.match(bare) is not None, f"{s['folder']} {g['id']}: only the number is outside code: {bare}")
            ok(line.endswith(g["written"]) and claim["quote"] == "값 " + g["written"],
               f"{s['folder']} {g['id']}: the line shows the written gold")
            ways.add(g["way"])
            cell = cells[(g["statement"], g["account"])].get(g["column"])
            if cell not in (None, ""):
                ok(Decimal(cell) == Decimal(g["value"]), f"{s['folder']} {g['id']}: the made-up API agrees")
        ok(ways == set(build.WAYS) if c["step"] == 10**6 else "won" in ways, f"{s['folder']}: ways used {ways}")
        pinned = build.load_json(os.path.join(folder, "pinned.json"))
        ok(all(p["operands"]["of"].get("rcept_no") == s["rcept_no"] for p in pinned["claims"])
           and len(pinned["claims"]) == len(got), f"{s['folder']}: pinned plan")
        named = build.load_json(os.path.join(folder, "by-name.json"))
        names = {}
        for r in api:
            names.setdefault(r["sj_div"], []).append(r["account_nm"].strip())
        want_named = [g["id"] for g in gold["facts"] if names[g["statement"]].count(g["name"].strip()) == 1]
        ok([x["id"] for x in named["claims"]] == want_named
           and all("account" not in x["operands"]["of"] and x["operands"]["of"]["name"] for x in named["claims"]),
           f"{s['folder']}: by-name plan holds the rows whose name occurs once ({len(want_named)})")
        if corp == "99000001":
            ok(len(want_named) < len(got), "a name that occurs twice is left out of the by-name plan")
        # swap
        swap = build.load_json(os.path.join(folder, "swap.json"))
        with open(os.path.join(folder, "swap.md"), encoding="utf-8") as fh:
            slines = fh.read().split("\n")
        ok(len(slines) - 1 == len(swap["claims"]) == len(gold["swap"]), f"{s['folder']}: one swap line per case")
        facts = {g["id"]: g for g in gold["facts"]}
        kinds = set()
        for x, claim in zip(gold["swap"], swap["claims"]):
            g = facts[x["fact"]]
            ok(claim["operands"]["of"]["account"] == g["account"] and claim["operands"]["of"]["column"] == g["column"]
               and claim["operands"]["of"]["fs"] == fs, f"{s['folder']} {x['id']}: the locator names the fact")
            ok(Decimal(x["shown_value"]) != Decimal(g["value"]) and slines[x["line"] - 1].endswith(x["text"]),
               f"{s['folder']} {x['id']}: the line shows another amount")
            kinds.add(x["kind"])
            if x["kind"] == "c":
                other = "OFS" if fs == "CFS" else "CFS"
                row = next(r for r in c["rows"] if r[0] == g["statement"] and r[1] == g["account"])
                period = dict(fake_dart.column_periods(c["cal"], report, g["statement"]))[g["column"]]
                ok(Decimal(x["shown_value"]) == Decimal(world.cell(corp, other, row, period, current=True)),
                   f"{s['folder']} {x['id']}: kind c shows the other set's amount")
            else:
                ok(x["shown"] in dict(build.neighbours(report, g["statement"], g["column"])).values(),
                   f"{s['folder']} {x['id']}: kind {x['kind']} shows a neighbour column")
        want_kinds = {"b"} | ({"c"} if len(c["fs"]) == 2 else set()) | ({"a"} if report != "annual" else set())
        ok(kinds == want_kinds, f"{s['folder']}: swap kinds {sorted(kinds)}")
        ok(not any(facts[x["fact"]]["account"] == "ifrs-full_IssuedCapital" for x in gold["swap"])
           and sum(s["counts"]["swap_not_built_same_as_written"].values()) > 0,
           f"{s['folder']}: no swap case where the neighbour holds the same amount")
        # abstention
        first = [g["id"] for g in gold["facts"][:5]]
        ok(build.ABSTAIN_KINDS[-1] == "cfq" and len(build.ABSTAIN_KINDS) == 8, "eight kinds of abstention cases")
        cash = [g for g in gold["facts"] if g["statement"] == "CF"][:5] if report != "annual" else []
        cfq_path = os.path.join(folder, "abstain-cfq.json")
        if cash:
            p = build.load_json(cfq_path)
            ok([x["id"] for x in p["claims"]] == [g["id"] for g in cash] and "abstain-cfq.json" in s["plans"]
               and s["counts"]["abstain_by_kind"]["cfq"] == len(cash),
               f"{s['folder']}: abstain-cfq takes the first {len(cash)} cash flow facts")
            for x, g in zip(p["claims"], cash):
                loc = x["operands"]["of"]
                ok(loc == {"dart": corp, "year": year, "report": report, "fs": fs, "statement": "CF",
                           "account": g["account"], "column": "frmtrm_q_amount"} and x["line"] == g["line"],
                   f"{s['folder']}: abstain-cfq {x['id']} asks the cash flow fact under frmtrm_q_amount")
                ok(cells[("CF", g["account"])].get("frmtrm_q_amount") not in (None, ""),
                   f"{s['folder']}: the made-up API does fill that column, as DART does")
            profit = cells[("CF", "ifrs-full_ProfitLoss")]
            ok(profit["frmtrm_q_amount"] != profit["frmtrm_amount"]
               and all(r["frmtrm_q_amount"] == r["frmtrm_amount"] for r in api
                       if r["sj_div"] == "CF" and r["account_id"] != "ifrs-full_ProfitLoss"
                       and r.get("frmtrm_q_amount") not in (None, "")),
               f"{s['folder']}: in that column the profit row holds three months, the other rows the same period")
            total_cfq += len(cash)
        else:
            ok(report == "annual" and not os.path.exists(cfq_path) and "abstain-cfq.json" not in s["plans"]
               and s["counts"]["abstain_by_kind"]["cfq"] == 0, f"{s['folder']}: an annual set has no cfq cases")
        ok(not any(g["statement"] == "CF" and g["column"] == "frmtrm_q_amount" for g in gold["facts"])
           or report == "annual", f"{s['folder']}: frmtrm_q_amount of an interim cash flow statement is no fact")
        for kind in build.ABSTAIN_KINDS[:-1]:
            p = build.load_json(os.path.join(folder, f"abstain-{kind}.json"))
            ok([x["id"] for x in p["claims"]] == first, f"{s['folder']}: abstain-{kind} takes the first five facts")
            for x, g in zip(p["claims"], gold["facts"]):
                loc = x["operands"]["of"]
                good = {"account": loc.get("account") == g["account"] + "Zz",
                        "column": loc["column"] == ("frmtrm_q_amount" if report == "annual" else "bfefrmtrm_amount"),
                        "year": loc["year"] == 2031, "fs": loc["fs"] == "XXX", "sce": loc["statement"] == "SCE",
                        "receipt": loc.get("rcept_no", "")[:-1] == s["rcept_no"][:-1]
                        and loc.get("rcept_no") != s["rcept_no"] and len(loc.get("rcept_no", "")) == 14,
                        "statement": loc["statement"] != g["statement"] and loc["statement"] in build.STATEMENTS
                        and not any(r["sj_div"] == loc["statement"] and r["account_id"] == g["account"]
                                    for r in api)}[kind]
                ok(good, f"{s['folder']}: abstain-{kind} {x['id']}")
        # amended
        if corp == "99000001" and report == "annual":
            p = build.load_json(os.path.join(folder, "amended.json"))
            ok([x["id"] for x in p["claims"]] == first and all(
                x["operands"]["of"]["rcept_no"] == world.earlier_receipt(corp, report) for x in p["claims"])
               and "amended.json" in s["plans"], f"{s['folder']}: amended plan pins the earliest receipt number")
        else:
            ok(not os.path.exists(os.path.join(folder, "amended.json")) and "amended.json" not in s["plans"],
               f"{s['folder']}: no amended plan")
        # ambiguous rows (measure 8), worked out from the made-up rows on their own
        listed = [(r["sj_div"], r["account_id"], r["account_nm"]) for r in api if r["sj_div"] in build.STATEMENTS]
        n_name = {k: sum(1 for st, _, nm in listed if (st, nm.strip()) == k)
                  for k in {(st, nm.strip()) for st, _, nm in listed}}
        n_id = {k: sum(1 for st, aid, _ in listed if (st, aid) == k) for k in {(st, aid) for st, aid, _ in listed}}
        want_amb = [("name", st, nm) for st, _, nm in listed if nm.strip() and n_name[(st, nm.strip())] > 1][:10] \
            + [("id", st, aid) for st, aid, _ in listed
               if aid.startswith(("ifrs-full_", "dart_")) and n_id[(st, aid)] > 1][:10]
        amb_path = os.path.join(folder, "ambiguous.json")
        if want_amb:
            p = build.load_json(amb_path)
            with open(os.path.join(folder, "ambiguous.md"), encoding="utf-8") as fh:
                alines = fh.read().split("\n")
            ok(p["document"] == "ambiguous.md" and len(alines) - 1 == len(p["claims"]) == len(want_amb)
               == len(gold["ambiguous"]) == s["counts"]["ambiguous"] and "ambiguous.json" in s["plans"],
               f"{s['folder']}: {len(want_amb)} ambiguous rows, one line each in a document of their own")
            shown = gold["facts"][0]["written"]
            for (kind, st, what), x, g in zip(want_amb, p["claims"], gold["ambiguous"]):
                loc = x["operands"]["of"]
                key_ = "name" if kind == "name" else "account"
                ok(loc == {"dart": s["company_in_plans"], "year": year, "report": report, "fs": fs, "statement": st,
                           key_: what, "column": "thstrm_amount"} and g["kind"] == kind and g["id"] == x["id"],
                   f"{s['folder']} {x['id']}: an ambiguous row is asked by its {key_} alone")
                line = alines[x["line"] - 1]
                bare = re.sub(r"`[^`]*`", "", line).replace("  ", " ")
                ok(line.endswith(shown) and x["quote"] == "값 " + shown and x["number"] in x["quote"]
                   and LINE_RE.match(bare) is not None,
                   f"{s['folder']} {x['id']}: the line shows the amount of the set's first fact")
                hits = [r for r in api if r["sj_div"] == st and (r["account_nm"].strip() == what.strip()
                                                                 if kind == "name" else r["account_id"] == what)]
                ok(len(hits) > 1 and g["rows_sharing"] == len(hits),
                   f"{s['folder']} {x['id']}: {len(hits)} rows of the statement match")
            total_amb["name"] += sum(1 for k, _, _ in want_amb if k == "name")
            total_amb["id"] += sum(1 for k, _, _ in want_amb if k == "id")
        else:
            ok(not os.path.exists(amb_path) and not os.path.exists(os.path.join(folder, "ambiguous.md"))
               and "ambiguous.json" not in s["plans"] and s["counts"]["ambiguous"] == 0 and gold["ambiguous"] == [],
               f"{s['folder']}: no ambiguous row, no plan")
    ok(total_amb == {"name": 6 * 4, "id": 6 * 2} == m["totals"]["counts"]["ambiguous_by_kind"]
       and m["totals"]["counts"]["ambiguous"] == 36,
       f"totals: ambiguous rows by name {total_amb['name']}, by id {total_amb['id']}")
    c = m["totals"]["counts"]
    ok(c["facts"] == total_facts and c["amended"] == 10 and c["abstain"] == 9 * 7 * 5 + total_cfq
       and total_cfq == 4 * 5 + 2 * 4 == c["abstain_by_kind"]["cfq"],
       f"totals: {c['facts']} facts, {c['abstain']} abstention cases ({total_cfq} of kind cfq), "
       f"{c['amended']} amended cases")
    no_gold = m["totals"]["no_gold"]
    ok(set(no_gold) == {"only_instant_facts", "no_fact_in_period", "values_differ"}, f"no gold by reason: {no_gold}")
    ok(m["totals"]["rows_left_out"].get("account_id_repeats", 0) > 0
       and m["totals"]["rows_left_out"].get("no_standard_account_id", 0) > 0
       and m["totals"]["rows_left_out"].get("statement_of_changes_in_equity", 0) > 0, "rows left out by reason")
    ok(c["facts_possible"] == c["facts"] + sum(no_gold.values()), "every possible fact has a gold or a reason")

    # the held-out path, on made-up companies only
    os.environ[build.KEY_ENV] = key
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            build.build(reg, True, None, fetcher(offline=True), out, "heldout")
        ok(False, "the held-out build ran without its cache")
    except build.Stop:
        ok(True, "the held-out build stops when the held-out company code file was not fetched")
    n = len(log)
    fh_ = fetcher()
    build.fetch_only(reg, True, None, fh_, said.append)
    excluded = {"99000001", "99000002", "99000003"}
    order = sorted((corp for corp, c in fake_dart.COMPANIES.items() if c["stock"] and corp not in excluded),
                   key=lambda corp: hashlib.sha256(("receipts-E4:" + corp).encode()).hexdigest())
    kept, passed = [], []
    for corp in order:
        if len(kept) == 2:
            break
        c = fake_dart.COMPANIES[corp]
        if "annual" not in c["reports"] or not c["fs"]:
            passed.append(corp)
        elif c.get("no_xbrl"):
            passed.append(corp)
        else:
            kept.append(corp)
    asked = {q.get("corp_code") for _, q in log[n:] if q.get("corp_code")}
    ok(asked == {"99000003"} | set(kept) | set(passed),
       f"the held-out fetch asks for the named company and the walked ones only: {sorted(asked)}")
    ok(os.path.isfile(os.path.join(cache, "corpCode-heldout.zip")), "the held-out run fetches its own code file")
    os.environ.pop(build.KEY_ENV, None)
    n = len(log)
    with contextlib.redirect_stdout(io.StringIO()):
        code = build.build(reg, True, None, fetcher(offline=True), out, "heldout")
    m = build.load_json(os.path.join(out, "heldout", "manifest.json"))
    ok(code == 0 and len(log) == n and m["held_out"], "the held-out build runs offline")
    ok([k["corp_code"] for k in m["draw"]["kept"]] == kept and [p["corp_code"] for p in m["draw"]["passed_over"]]
       == passed and m["draw"]["complete"], f"the draw keeps {kept} and passes over {passed}")
    ok(all(p["reason"] for p in m["draw"]["passed_over"]), "companies passed over are listed with the reason")
    ok([x["corp_code"] for x in m["companies"]] == ["99000003"] + kept, "named companies first, then the drawn ones")
    ok(len(m["sets"]) > 0 and all(s["counts"]["facts"] > 0 for s in m["sets"]), "held-out sets have facts")
    for folder, _, names in os.walk(tmp):
        for name in names:
            with open(os.path.join(folder, name), "rb") as fh:
                ok(key.encode() not in fh.read(), f"no key in {name}")
