"""Tests for the DART connection, against a local server that stands in for OpenDART.

No request leaves the machine. The companies, the codes and the key are made up.
Run: python -m unittest discover -s tests
"""
from __future__ import annotations

import contextlib
import gzip
import http.server
import importlib.util
import io
import json
import os
import pathlib
import shutil
import socket
import tempfile
import threading
import time
import unittest
import urllib.parse
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "receipts" / "scripts" / "receipts.py"
_spec = importlib.util.spec_from_file_location("receipts_dart", SCRIPT)
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

KEY = "k" * 8 + "TESTKEYTESTKEYTESTKEYTESTKEY" + "9" * 4   # 40 characters, made up
CORP, STOCK = "99900100", "9990A0"
ANNUAL, Q3, Q1, HALF, Y2015 = ("20260310900001", "20261116900002", "20260515900003", "20260814900004",
                               "20160330900005")   # receipt numbers
NO_CODE = "-표준계정코드 미사용-"
CASH = "ifrs-full_CashFlowsFromUsedInOperatingActivities"


def today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def row(sj, account, name, rcept, code, year, corp=CORP, **amounts):
    out = {"rcept_no": rcept, "reprt_code": code, "bsns_year": str(year), "corp_code": corp, "sj_div": sj,
           "sj_nm": sj, "account_id": account, "account_nm": name, "account_detail": "-", "ord": "1", "currency": "KRW"}
    out.update(amounts)
    return out


def annual_rows(fs, year=2025, rcept=ANNUAL):
    k = 1 if fs == "CFS" else 2   # the separate statements hold half of everything
    names = {"thstrm_nm": "제 10 기", "frmtrm_nm": "제 9 기", "bfefrmtrm_nm": "제 8 기"}

    def r(sj, account, name, a, b, c, **more):
        return row(sj, account, name, rcept, "11011", year, thstrm_amount=str(a // k), frmtrm_amount=str(b // k),
                   bfefrmtrm_amount=str(c // k), **names, **more)

    return [
        r("BS", "ifrs-full_Assets", "자산총계", 5000000000000, 4200000000000, 4000000000000),
        r("BS", "ifrs-full_Liabilities", "부채총계", 2000000000000, 1900000000000, 1800000000000),
        # a row that carries all six columns: three of them have no meaning on an annual balance sheet
        r("BS", "ifrs-full_Equity", "자본총계", 3000000000000, 2300000000000, 2200000000000,
          thstrm_add_amount="111", frmtrm_q_amount="222", frmtrm_add_amount="333"),
        r("BS", "dart_NullName", None, 5, 4, 3),
        r("BS", None, "이름만 있는 줄", 7, 6, 5),
        r("IS", "ifrs-full_Revenue", "매출액", 1234500000000, 1100000000000, 1000000000000, thstrm_add_amount=""),
        r("IS", "dart_OperatingIncomeLoss", "영업이익", 107440482000000, 90000000000000, 80000000000000,
          thstrm_add_amount=""),
        r("IS", "ifrs-full_ProfitLoss", "당기순이익(손실)", -45000000000, 30000000000, 20000000000),
        r("IS", "ifrs-full_BasicEarningsLossPerShare", "기본주당이익(손실)", 2310, 1500, 1000),
        r("CIS", "ifrs-full_ComprehensiveIncome", "총포괄손익", 50000000000, 40000000000, 30000000000),
        r("CF", "ifrs-full_ProfitLoss", "당기순이익(손실)", -45000000000, 30000000000, 20000000000),
        r("CF", NO_CODE, "단기금융상품의 순감소(증가)", -670000000000, 120000000000, 0),
        r("CF", NO_CODE, "매각예정분류", 11000000000, 0, 0),
        r("IS", "dart_OtherGains", "기타수익", 7000000000, 6000000000, 5000000000),
        r("IS", "dart_OtherGains", "기타이익", 3000000000, 2000000000, 1000000000),      # the same id twice
        {**r("IS", "dart_OtherLosses", "기타비용", 1, 1, 1), "thstrm_amount": "해당없음"},  # not a number
        r("SCE", "ifrs-full_Equity", "자본총계", 3000000000000, 2300000000000, 2200000000000),
    ]


def interim_rows(code, rcept, label, assets, revenue, cash):
    q, cum, pq, pcum = revenue
    return [
        row("BS", "ifrs-full_Assets", "자산총계", rcept, code, 2026, thstrm_nm=f"제 11 기 {label}말",
            thstrm_amount=str(assets), frmtrm_nm="제 10 기말", frmtrm_amount="5000000000000"),
        row("IS", "ifrs-full_Revenue", "매출액", rcept, code, 2026, thstrm_nm=f"제 11 기 {label}", thstrm_amount=str(q),
            thstrm_add_amount=str(cum), frmtrm_q_nm=f"제 10 기 {label}", frmtrm_q_amount=str(pq),
            frmtrm_add_amount=str(pcum)),
        row("CF", CASH, "영업활동현금흐름", rcept, code, 2026, thstrm_nm=f"제 11 기 {label}", thstrm_amount=str(cash[0]),
            frmtrm_q_nm=f"제 10 기 {label}", frmtrm_q_amount=str(cash[1]), frmtrm_amount=str(cash[1])),
    ]


STATEMENTS = {
    (CORP, "2025", "11011", "CFS"): annual_rows("CFS"), (CORP, "2025", "11011", "OFS"): annual_rows("OFS"),
    (CORP, "2015", "11011", "CFS"): annual_rows("CFS", 2015, Y2015)[:2],
    (CORP, "2026", "11013", "CFS"): interim_rows("11013", Q1, "1분기", 5100000000000,
                                                 (320000000000, 320000000000, 280000000000, 280000000000),
                                                 (100000000000, 90000000000)),
    (CORP, "2026", "11012", "CFS"): interim_rows("11012", HALF, "반기", 5200000000000,
                                                 (345000000000, 665000000000, 290000000000, 570000000000),
                                                 (250000000000, 230000000000)),
    (CORP, "2026", "11014", "CFS"): interim_rows("11014", Q3, "3분기", 5300000000000,
                                                 (350000000000, 1020000000000, 300000000000, 900000000000),
                                                 (410000000000, 390000000000)),
}
COMPANIES = [("99900050", "가나다 예시전자", "", "Early Example"),
             (CORP, "예시전자", STOCK, "Example Electronics"), ("99900200", "예시전자서비스", "", "Example Service"),
             ("99900300", "다른 & 회사", "9990B0", "Other & Co"), ("99900400", "같은코드하나", "9990C0", "Same One"),
             ("99900500", "같은코드둘", "9990C0", "Same Two")]


def zipped(xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


def code_file(companies=COMPANIES) -> bytes:
    return zipped('<?xml version="1.0" encoding="UTF-8"?>\n<result>\n' + "".join(
        f"    <list>\n        <corp_code>{c}</corp_code>\n        <corp_name>{n.replace('&', '&amp;')}</corp_name>\n"
        f"        <corp_eng_name>{e.replace('&', '&amp;')}</corp_eng_name>\n        <stock_code>{s or ' '}</stock_code>\n"
        f"        <modify_date>20260101</modify_date>\n    </list>\n" for c, n, s, e in companies) + "</result>\n")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        srv = self.server
        url = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        srv.seen.append((url.path, q))
        srv.headers.append({k.lower(): v for k, v in self.headers.items()})
        if srv.redirect_to:
            self.send_response(302)
            self.send_header("Location", srv.redirect_to + self.path)
            self.end_headers()
            return
        if srv.fail_with and (srv.fail_times is None or srv.fail_times > 0):
            if srv.fail_times is not None:
                srv.fail_times -= 1
            self.send_response(srv.fail_with)
            self.end_headers()
            return
        message = f"limit for {self.path}" if srv.echo else None   # a server that repeats the request
        if srv.raw is not None:
            body = srv.raw
        elif srv.status:
            body = json.dumps({"status": srv.status, "message": message or "사유"}, ensure_ascii=False).encode()
        elif q.get("crtfc_key") != srv.key:
            body = json.dumps({"status": "010", "message": "등록되지 않은 키입니다."}, ensure_ascii=False).encode()
        elif url.path == "/api/corpCode.xml":
            body = code_file()
        elif url.path == "/api/fnlttSinglAcntAll.json":
            rows = srv.rows if srv.rows is not None else STATEMENTS.get(
                (q.get("corp_code"), q.get("bsns_year"), q.get("reprt_code"), q.get("fs_div")))
            doc = {"status": "000", "message": message or "정상", "list": rows} if rows else \
                {"status": "013", "message": "조회된 데이타가 없습니다."}
            body = json.dumps(doc, ensure_ascii=False).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        if srv.zipped and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body = gzip.compress(body)
            self.send_header("Content-Encoding", "gzip")
        full = len(body)
        if srv.cut:
            body = body[:-srv.cut]
        self.send_header("Content-Length", str(len(body) if srv.cut_quietly else full))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def reset(server):
    server.seen.clear()
    server.headers.clear()
    server.fail_with, server.fail_times, server.raw, server.status, server.redirect_to = 0, None, None, "", None
    server.rows, server.echo, server.zipped, server.key = None, False, False, KEY
    server.cut, server.cut_quietly = 0, False


def new_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.seen, server.headers = [], []
    reset(server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def dart(account="ifrs-full_Assets", column="thstrm_amount", **extra):
    loc = {"dart": STOCK, "year": 2025, "report": "annual", "fs": "CFS", "statement": "BS", "account": account,
           "column": column}
    loc.update(extra)
    return {k: v for k, v in loc.items() if v is not None}


def q3(account="ifrs-full_Revenue", column="thstrm_amount", statement="IS", report="q3", **extra):
    return dart(account, column, year=2026, report=report, statement=statement, **extra)


class DartTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = new_server()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.sleeps = []
        cls.saved_gap, R.DART_GAP = R.DART_GAP, 0.0
        cls.saved_sleep, R.time.sleep = R.time.sleep, cls.sleeps.append   # no waiting in tests, but counted
        cls.saved_env = dict(os.environ)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        R.DART_GAP, R.time.sleep = cls.saved_gap, cls.saved_sleep
        os.environ.clear()
        os.environ.update(cls.saved_env)

    def setUp(self):
        self.fresh()

    def fresh(self):
        """A new empty folder, a server in its normal state, and the environment of a normal run."""
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="receipts-dart-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        reset(self.server)
        self.sleeps.clear()
        os.environ.clear()
        os.environ.update(self.saved_env)
        os.environ.update({"RECEIPTS_DART_KEY": KEY, "RECEIPTS_DART_URL": self.url})
        os.environ.pop("RECEIPTS_DART_CACHE", None)
        R._CACHE.clear()

    def write(self, lines, claims):
        (self.dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (self.dir / "claims.json").write_text(json.dumps({"document": "report.md", "claims": claims}), encoding="utf-8")
        R._CACHE.clear()

    def check(self, lines, claims, **kw):
        self.write(lines, claims)
        return R.run_check(str(self.dir / "claims.json"), **kw)

    def claim(self, sentence, number, loc, **more):
        return {"id": "c1", "line": 1, "quote": sentence, "number": number, "kind": "lookup",
                "operands": {"of": loc}, **more}

    def one(self, sentence, number, loc=None, **kw):
        res = self.check([sentence], [self.claim(sentence, number, loc)], **kw)
        return res["claims"][0], res

    def verdict(self, loc, sentence="자산총계는 5조원이다.", number="5조원"):
        r, _ = self.one(sentence, number, loc)
        return r["verdict"], r["reason"]

    def derived(self, sentence, number, formula, operands):
        body = {"id": "c1", "line": 1, "quote": sentence, "number": number, "kind": "derived", "formula": formula,
                "operands": operands}
        r = self.check([sentence], [body])["claims"][0]
        return r["verdict"], r["reason"]

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = R.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def paths(self):
        return [p for p, _ in self.server.seen]

    def cache(self):
        return self.dir / ".receipts" / "dart"

    def written(self):
        """Everything the script wrote under the plan's folder, names and contents, as one text."""
        out = []
        for p in self.dir.rglob("*"):
            out.append(p.relative_to(self.dir).as_posix())
            if p.is_file():
                out.append(p.read_bytes().decode("utf-8", "replace"))
        return "\n".join(out)

    # ---- values

    def test_lookup_by_stock_code_and_by_company_code(self):
        r, res = self.one("자산총계는 5조원이다.", "5조원", dart())
        self.assertEqual((r["verdict"], r["reason"]), ("verified", "match"))
        self.assertEqual(r["sources"][0], "DART 99900100 (예시전자, 9990A0) 2025 annual CFS BS ifrs-full_Assets 자산총계 "
                         "thstrm_amount [제 10 기, the year end] = 5000000000000 KRW (receipt number 20260310900001)")
        self.assertEqual(res["dart"]["files"], [
            {"file": "corpCode.zip", "fetched": today(), "address": self.url},
            {"file": "fnlttSinglAcntAll/99900100_2025_11011_CFS.json", "fetched": today(), "address": self.url,
             "rcept_no": ANNUAL}])
        for ident in (CORP, "9990a0", " 9990A0 "):
            self.assertEqual(self.verdict(dart(dart=ident)), ("verified", "match"), ident)
        r, _ = self.one("자산총계는 5조원이다.", "5조원", dart(dart=CORP))
        self.assertIn("DART 99900100 2025 annual CFS BS", r["sources"][0])   # no name without the code file

    def test_a_company_code_needs_no_code_file(self):
        self.verdict(dart(dart=CORP))
        self.assertEqual(self.paths(), ["/api/fnlttSinglAcntAll.json"])
        self.assertEqual(self.server.seen[0][1], {"crtfc_key": KEY, "corp_code": CORP, "bsns_year": "2025",
                                                  "reprt_code": "11011", "fs_div": "CFS"})

    def test_each_report_has_its_own_code_and_file(self):
        cases = {"q1": ("11013", "3,200억원"), "half": ("11012", "3,450억원"), "q3": ("11014", "3,500억원")}
        for report, (code, amount) in cases.items():
            self.server.seen.clear()
            got = self.verdict(q3(report=report, dart=CORP), f"매출은 {amount}이었다.", amount)
            self.assertEqual(got, ("verified", "match"), report)
            self.assertEqual(self.server.seen[0][1]["reprt_code"], code)
            self.assertTrue((self.cache() / "fnlttSinglAcntAll" / f"99900100_2026_{code}_CFS.json").exists())
        self.server.seen.clear()
        self.verdict(dart(dart=CORP, report="annual"))
        self.assertEqual(self.server.seen[0][1]["reprt_code"], "11011")
        self.assertEqual(self.verdict(q3(report="q1", dart=CORP), "매출은 3,450억원이었다.", "3,450억원")[0], "wrong")

    def test_business_years_from_2015(self):
        self.assertEqual(self.verdict(dart(dart=CORP, year=2015))[0], "verified")
        self.assertEqual(self.verdict(dart(dart=CORP, year=2014)), ("unverifiable", "locator_no_match"))

    def test_wrong_number_is_wrong(self):
        r, _ = self.one("매출은 1조 3,345억원이었다.", "1조 3,345억원", dart("ifrs-full_Revenue", statement="IS"))
        self.assertEqual((r["verdict"], r["recomputed_raw"]), ("wrong", "1234500000000"))

    def test_korean_ways_of_writing_an_amount(self):
        loc = dart("dart_OperatingIncomeLoss", statement="IS")
        for text in ("107조 4,405억원", "107,440,482백만원", "107,440,482,000,000원", "1,074,405억원", "107.4조원"):
            self.assertEqual(self.verdict(loc, f"영업이익은 {text}이었다.", text), ("verified", "match"), text)
        self.assertEqual(self.verdict(loc, "영업이익은 107조 4,404억원이었다.", "107조 4,404억원"),
                         ("verified", "truncated"))
        self.assertEqual(self.verdict(loc, "영업이익은 107조 4,415억원이었다.", "107조 4,415억원")[0], "wrong")

    def test_three_months_and_cumulative_are_two_columns(self):
        q = self.verdict(q3(), "3분기 매출은 3,500억원이었다.", "3,500억원")
        cum = self.verdict(q3(column="thstrm_add_amount"), "누적 매출은 1조 200억원이었다.", "1조 200억원")
        mixed = self.verdict(q3(), "3분기 매출은 1조 200억원이었다.", "1조 200억원")
        self.assertEqual((q[0], cum[0], mixed[0]), ("verified", "verified", "wrong"))
        r, _ = self.one("3분기 매출은 3,500억원이었다.", "3,500억원", q3())
        self.assertIn("thstrm_amount [제 11 기 3분기, the three months] = 350000000000 KRW", r["sources"][0])
        r, _ = self.one("누적 매출은 1조 200억원이었다.", "1조 200억원", q3(column="thstrm_add_amount"))
        self.assertIn("[제 11 기 3분기, from the start of the business year to the period end]", r["sources"][0])
        r, _ = self.one("전년 3분기 매출은 3,000억원이었다.", "3,000억원", q3(column="frmtrm_q_amount"))
        self.assertEqual(r["verdict"], "verified")
        self.assertIn("[제 10 기 3분기, the same three months a year earlier]", r["sources"][0])
        r, _ = self.one("전년 누적 매출은 9,000억원이었다.", "9,000억원", q3(column="frmtrm_add_amount"))
        self.assertEqual(r["verdict"], "verified")
        self.assertIn("[제 10 기 3분기, the same cumulative period a year earlier]", r["sources"][0])

    def test_interim_balance_sheet_compares_with_the_previous_year_end(self):
        r, _ = self.one("작년 말 자산총계는 5조원이었다.", "5조원", q3("ifrs-full_Assets", "frmtrm_amount", "BS"))
        self.assertEqual(r["verdict"], "verified")
        self.assertIn("[제 10 기말, the end of the previous business year]", r["sources"][0])
        r, _ = self.one("9월 말 자산총계는 5.3조원이었다.", "5.3조원", q3("ifrs-full_Assets", "thstrm_amount", "BS"))
        self.assertEqual(r["verdict"], "verified")
        self.assertIn("[제 11 기 3분기말, the period end]", r["sources"][0])

    def test_interim_cash_flow_has_one_comparison_column(self):
        amounts = {"q1": ("1,000억원", "900억원"), "half": ("2,500억원", "2,300억원"), "q3": ("4,100억원", "3,900억원")}
        for report, (now, before) in amounts.items():
            cf = dict(statement="CF", report=report)
            self.assertEqual(self.verdict(q3(CASH, "thstrm_amount", **cf), f"영업활동현금흐름은 {now}이다.", now)[0],
                             "verified", report)
            r, _ = self.one(f"전년 동기는 {before}이다.", before, q3(CASH, "frmtrm_amount", **cf))
            self.assertEqual(r["verdict"], "verified", report)
            self.assertIn("frmtrm_amount [the same period a year earlier]", r["sources"][0])
            r, _ = self.one(f"전년 동기는 {before}이다.", before, q3(CASH, "frmtrm_q_amount", **cf))
            self.assertEqual((r["verdict"], r["reason"]), ("unverifiable", "unsupported"), report)
            self.assertIn("use frmtrm_amount", r["note"])
        r, _ = self.one("영업활동현금흐름은 4,100억원이다.", "4,100억원", q3(CASH, "thstrm_amount", "CF"))
        self.assertIn("[제 11 기 3분기, from the start of the business year to the period end]", r["sources"][0])

    def test_annual_columns_and_their_labels(self):
        cases = [("BS", "ifrs-full_Assets", "frmtrm_amount", "4.2조원", "제 9 기, the previous year end"),
                 ("BS", "ifrs-full_Assets", "bfefrmtrm_amount", "4조원", "제 8 기, the year end two years back"),
                 ("IS", "ifrs-full_Revenue", "thstrm_amount", "1조 2,345억원", "제 10 기, the year"),
                 ("IS", "ifrs-full_Revenue", "frmtrm_amount", "1.1조원", "제 9 기, the previous year"),
                 ("CIS", "ifrs-full_ComprehensiveIncome", "bfefrmtrm_amount", "300억원", "제 8 기, the year two years back"),
                 ("CF", "ifrs-full_ProfitLoss", "frmtrm_amount", "300억원", "제 9 기, the previous year")]
        for statement, account, column, amount, label in cases:
            r, _ = self.one(f"값은 {amount}이다.", amount, dart(account, column, statement=statement))
            self.assertEqual(r["verdict"], "verified", (statement, column))
            self.assertIn(f"{column} [{label}]", r["sources"][0])

    def test_consolidated_and_separate_are_two_reports(self):
        self.assertEqual(self.verdict(dart(fs="OFS"), "별도 자산총계는 2.5조원이다.", "2.5조원")[0], "verified")
        self.assertEqual(self.verdict(dart(fs="OFS"))[0], "wrong")
        self.assertEqual(self.verdict(dart(fs="ofs"), "별도 자산총계는 2.5조원이다.", "2.5조원")[0], "verified")

    def test_negative_per_share_and_multiplier(self):
        loss = dart("ifrs-full_ProfitLoss", statement="IS")
        self.assertEqual(self.verdict(loss, "순손실 -450억원", "-450억원")[0], "verified")
        self.assertEqual(self.verdict(loss, "순이익 450억원", "450억원")[0], "wrong")
        eps = dart("ifrs-full_BasicEarningsLossPerShare", statement="IS")
        self.assertEqual(self.verdict(eps, "주당이익은 2,310원이다.", "2,310원")[0], "verified")
        r, res = self.one("자산총계는 5,000이다.", "5,000", dart(multiplier="0.000000001"))
        self.assertEqual(r["verdict"], "verified")
        self.assertEqual(res["multipliers"], [{"file": "DART 9990A0", "column": "ifrs-full_Assets thstrm_amount",
                                               "multiplier": "0.000000001", "claims": 1}])
        for bad in ("Infinity", "NaN", "-Infinity", "x", "1e400", "1e-400", "1e31"):
            self.assertEqual(self.verdict(dart(multiplier=bad)), ("unverifiable", "unsupported"), bad)
        self.assertEqual(self.verdict(dart(multiplier=0), "값은 0이다.", "0")[0], "verified")

    def test_every_formula_takes_dart_cells(self):
        assets, debt, before = dart(), dart("ifrs-full_Liabilities"), dart(column="frmtrm_amount")
        self.assertEqual(self.derived("부채는 자산의 0.4배다.", "0.4배", "ratio", {"num": debt, "den": assets})[0], "verified")
        self.assertEqual(self.derived("차이는 3조원이다.", "3조원", "difference", {"a": assets, "b": debt})[0], "verified")
        self.assertEqual(self.derived("비중은 40.0%다.", "40.0%", "share", {"part": debt, "whole": assets})[0], "verified")
        self.assertEqual(self.derived("자산은 19.0% 늘었다.", "19.0%", "pct_change", {"new": assets, "old": before})[0],
                         "verified")
        for formula, amount in (("sum", "7조원"), ("mean", "3.5조원"), ("min", "2조원"), ("max", "5조원")):
            self.assertEqual(self.derived(f"값은 {amount}이다.", amount, formula, {"items": [assets, debt]})[0],
                             "verified", formula)
        self.assertEqual(self.derived("합계는 7조원이다.", "7조원", "sum", {"cells": {**assets, "where": {}}}),
                         ("unverifiable", "unsupported"))
        self.assertEqual(self.derived("줄은 1개다.", "1개", "count", {"rows": {**assets, "where": {}}})[0], "unverifiable")
        self.assertEqual(self.verdict({**assets, "rows": {}}), ("unverifiable", "unsupported"))
        sce = dart("ifrs-full_Equity", statement="SCE")
        self.assertEqual(self.derived("부채비율은 66.7%다.", "66.7%", "ratio", {"num": debt, "den": sce}),
                         ("unverifiable", "unsupported"))

    # ---- rows

    def test_a_row_by_name_by_id_or_by_both(self):
        self.assertEqual(self.verdict(dart(None, name="자산총계"))[0], "verified")
        self.assertEqual(self.verdict(dart(None, name="  자산총계 "))[0], "verified")
        self.assertEqual(self.verdict(dart(name="자산총계"))[0], "verified")
        self.assertEqual(self.verdict(dart(name="부채총계")), ("unverifiable", "locator_no_match"))
        self.assertEqual(self.verdict(dart(None, name="자산 총계")), ("unverifiable", "locator_no_match"))
        self.assertEqual(self.verdict(dart(None, name="자산")), ("unverifiable", "locator_no_match"))   # no substring
        self.assertEqual(self.verdict(dart("ifrs-full_assets")), ("unverifiable", "locator_no_match"))   # case matters
        self.assertEqual(self.verdict(dart("ifrs-full_AssetsZz")), ("unverifiable", "locator_no_match"))
        self.assertEqual(self.verdict(dart(None)), ("unverifiable", "locator_no_match"))                  # neither given
        self.assertEqual(self.verdict(dart(""))[1], "locator_no_match")
        self.assertEqual(self.verdict(dart(5))[1], "locator_no_match")

    def test_a_row_without_a_name_or_an_id_is_not_matched_by_the_word_none(self):
        self.assertEqual(self.verdict(dart(None, name="None")), ("unverifiable", "locator_no_match"))
        self.assertEqual(self.verdict(dart("None")), ("unverifiable", "locator_no_match"))
        self.assertEqual(self.verdict(dart("dart_NullName"), "값은 5원이다.", "5원")[0], "verified")
        self.assertEqual(self.verdict(dart(None, name="이름만 있는 줄"), "값은 7원이다.", "7원")[0], "verified")

    def test_the_statement_is_part_of_the_address(self):
        self.assertEqual(self.verdict(dart(statement="IS")), ("unverifiable", "locator_no_match"))
        self.assertEqual(self.verdict(dart(statement="bs"))[0], "verified")
        self.assertEqual(self.verdict(dart(statement="SCE")), ("unverifiable", "unsupported"))
        self.assertEqual(self.verdict(dart("ifrs-full_Equity", statement="sce")), ("unverifiable", "unsupported"))
        self.assertEqual(self.verdict(dart(statement="XX")), ("unverifiable", "locator_no_match"))
        self.assertEqual(self.verdict(dart(statement=None)), ("unverifiable", "locator_no_match"))
        both = "순손실 -450억원", "-450억원"   # the same id in two statements is two rows
        self.assertEqual(self.verdict(dart("ifrs-full_ProfitLoss", statement="IS"), *both)[0], "verified")
        self.assertEqual(self.verdict(dart("ifrs-full_ProfitLoss", statement="CF"), *both)[0], "verified")
        self.assertEqual(self.verdict(dart("ifrs-full_ComprehensiveIncome", statement="CIS"), "총포괄손익은 500억원이다.",
                                      "500억원")[0], "verified")
        self.assertEqual(self.verdict(dart("ifrs-full_ComprehensiveIncome", statement="IS")),
                         ("unverifiable", "locator_no_match"))

    def test_a_row_without_a_standard_code_is_named(self):
        loc = dart(None, statement="CF", name="단기금융상품의 순감소(증가)")
        self.assertEqual(self.verdict(loc, "순증가 -6,700억원", "-6,700억원")[0], "verified")
        r, _ = self.one("순증가 -6,700억원", "-6,700억원", loc)
        self.assertIn("CF 단기금융상품의 순감소(증가) thstrm_amount", r["sources"][0])
        self.assertNotIn("미사용", r["sources"][0])
        self.assertEqual(self.verdict(dart(NO_CODE, statement="CF")), ("unverifiable", "locator_no_match"))
        self.assertEqual(self.verdict(dart(NO_CODE, statement="CF", name="매각예정분류")),
                         ("unverifiable", "locator_no_match"))

    def test_several_rows_are_not_chosen_between(self):
        self.assertEqual(self.verdict(dart("dart_OtherGains", statement="IS")), ("unverifiable", "locator_multiple"))
        self.assertEqual(self.verdict(dart("dart_OtherGains", statement="IS", name="기타이익"),
                                      "기타이익은 30억원이다.", "30억원")[0], "verified")

    def test_a_column_the_row_does_not_have(self):
        rev = dict(statement="IS")
        self.assertEqual(self.verdict(dart("ifrs-full_Revenue", "thstrm_add_amount", **rev)),
                         ("unverifiable", "locator_no_match"))
        self.assertEqual(self.verdict(dart("ifrs-full_Revenue", "frmtrm_q_amount", **rev)),
                         ("unverifiable", "locator_no_match"))
        self.assertEqual(self.verdict(q3(column="bfefrmtrm_amount")), ("unverifiable", "locator_no_match"))
        self.assertEqual(self.verdict(dart("dart_OtherLosses", **rev)), ("unverifiable", "cell_not_numeric"))
        for column in ("thstrm", "THSTRM_AMOUNT", " thstrm_amount", "account_nm", "ord", None, 3):
            self.assertEqual(self.verdict(dart(column=column)), ("unverifiable", "locator_no_match"), column)

    def test_a_column_the_table_gives_no_meaning_is_refused_even_when_the_row_holds_it(self):
        for column, text in (("thstrm_add_amount", "111원"), ("frmtrm_q_amount", "222원"), ("frmtrm_add_amount", "333원")):
            self.server.seen.clear()
            r, _ = self.one(f"값은 {text}이다.", text, dart("ifrs-full_Equity", column, dart=CORP))
            self.assertEqual((r["verdict"], r["reason"]), ("unverifiable", "locator_no_match"), column)
            self.assertIn("no meaning in the BS of an annual report", r["note"])
            self.assertEqual(self.server.seen, [])   # refused before any request
        self.assertEqual(self.verdict(q3("ifrs-full_Assets", "bfefrmtrm_amount", "BS")), ("unverifiable", "locator_no_match"))
        self.assertEqual(self.verdict(q3(CASH, "thstrm_add_amount", "CF")), ("unverifiable", "locator_no_match"))
        self.assertEqual(self.verdict(q3(column="frmtrm_amount")), ("unverifiable", "locator_no_match"))

    # ---- fields that are not written as the contract says make no request

    def test_fields_outside_the_lists_are_refused_before_any_request(self):
        bad = [dart(year=2014), dart(year="25"), dart(year=None), dart(year=True), dart(year=2025.0),
               dart(year="２０２５"), dart(report="11011"), dart(report="FY"), dart(report=None), dart(fs="XXX"),
               dart(fs=None), dart(fs=""), dart(rcept_no="2026031090000"), dart(rcept_no="abc"), dart(rcept_no=True),
               dart(rcept_no="２０２６０３１０９００００１"), dart(dart=5930), dart(dart="예시전자"), dart(dart="12345"),
               dart(dart="1234567"), dart(dart=None), dart(dart="９９９００１００"), dart(dart="٩٩٩٠٠١٠٠")]
        for loc in bad:
            self.server.seen.clear()
            self.assertEqual(self.verdict(loc), ("unverifiable", "locator_no_match"), loc)
            self.assertEqual(self.server.seen, [], loc)
        self.assertFalse(self.cache().exists())
        self.assertEqual(self.verdict(dart(year="2025", report=" Annual ", fs="cfs"))[0], "verified")

    def test_a_field_the_script_does_not_know_is_refused(self):
        for key in ("rcpNo", "rcp_no", "rcept_No", "receipt_no", "RCEPT_NO"):   # a misspelled pin must not vanish
            self.server.seen.clear()
            r, _ = self.one("자산총계는 5조원이다.", "5조원", dart(**{key: "20991231000001"}))
            self.assertEqual((r["verdict"], r["reason"]), ("unverifiable", "locator_no_match"), key)
            self.assertIn(f"no field '{key}'", r["note"])
            self.assertEqual(self.server.seen, [])
        (self.dir / "s.csv").write_text("k,v\na,5000000000000\n", encoding="utf-8")
        for more in ({"file": "s.csv", "row": {"k": "a"}}, {"row": {"k": "a"}}, {"pointer": "/x"}, {"sheet": "S"},
                     {"cell": "A1"}, {"contains": "x"}, {"period": "2025-12-31"}, {"unit": "KRW"}):
            self.assertEqual(self.verdict(dart(**more)), ("unverifiable", "locator_no_match"), more)
        self.assertEqual(self.verdict(dart(edgar="EXMP", concept="us-gaap:Assets", period="2025-12-31")),
                         ("unverifiable", "locator_no_match"))

    def test_stock_codes(self):
        self.assertEqual(self.verdict(dart(dart="9990Z9")), ("unverifiable", "locator_no_match"))
        self.assertEqual(self.verdict(dart(dart="9990C0")), ("unverifiable", "locator_multiple"))
        self.assertEqual(self.verdict(dart(dart="9990B0")), ("unverifiable", "locator_no_match"))   # no statements

    # ---- DART has nothing

    def test_no_report_is_an_answer_and_is_cached(self):
        for loc in (dart(year=2031), dart(report="half"), dart(dart="88800100")):
            self.assertEqual(self.verdict(loc), ("unverifiable", "locator_no_match"), loc)
        r, res = self.one("자산총계는 5조원이다.", "5조원", dart(dart=CORP, year=2031))
        self.assertIn("DART has no statements for 99900100 2031 annual CFS", r["note"])
        self.assertEqual(res["dart"]["files"], [{"file": "fnlttSinglAcntAll/99900100_2031_11011_CFS.json",
                                                 "fetched": today(), "address": self.url}])
        self.server.seen.clear()
        os.environ.pop("RECEIPTS_DART_KEY")
        self.assertEqual(self.verdict(dart(dart=CORP, year=2031)), ("unverifiable", "locator_no_match"))
        self.assertEqual(self.server.seen, [])

    # ---- the receipt number

    def test_the_receipt_number_pins_the_version(self):
        self.assertEqual(self.verdict(dart(rcept_no=ANNUAL))[0], "verified")
        self.assertEqual(self.verdict(dart(rcept_no=int(ANNUAL)))[0], "verified")
        r, _ = self.one("자산총계는 5조원이다.", "5조원", dart(rcept_no="20260310900009"))
        self.assertEqual((r["verdict"], r["reason"]), ("unverifiable", "dart_receipt_differs"))
        self.assertIn("under receipt number 20260310900001, not 20260310900009", r["note"])
        self.assertEqual(r["sources"], [])
        r, _ = self.one("자산총계는 9조원이다.", "9조원", dart(rcept_no="20260310900009"))
        self.assertEqual(r["verdict"], "unverifiable")   # a wrong number is not called wrong against another version

    def test_an_answer_with_two_receipt_numbers_is_not_chosen_between(self):
        other = "20260501900777"
        self.server.rows = annual_rows("CFS") + [row("BS", "ifrs-full_Assets", "자산총계", other, "11011", 2025,
                                                    thstrm_amount="6000000000000")]
        r, res = self.one("자산총계는 5조원이다.", "5조원", dart(dart=CORP))
        self.assertEqual((r["verdict"], r["reason"]), ("unverifiable", "dart_receipt_differs"))
        self.assertIn(f"carries 2 receipt numbers ({ANNUAL}, {other})", r["note"])
        self.assertEqual(res["dart"]["files"][0]["rcept_no"], f"{ANNUAL}, {other}")
        r, _ = self.one("부채총계는 2조원이다.", "2조원", dart("ifrs-full_Liabilities", dart=CORP))
        self.assertEqual(r["reason"], "dart_receipt_differs")   # also for a row only one of them carries
        self.assertEqual(self.verdict(dart(dart=CORP, rcept_no=ANNUAL))[0], "verified")
        self.assertEqual(self.verdict(dart(dart=CORP, rcept_no=other), "자산총계는 6조원이다.", "6조원")[0], "verified")
        self.assertEqual(self.verdict(dart(dart=CORP, rcept_no=other))[0], "wrong")
        self.assertEqual(self.verdict(dart("ifrs-full_Liabilities", dart=CORP, rcept_no=other)),
                         ("unverifiable", "locator_no_match"))

    # ---- an answer for another report is never used

    def test_an_answer_for_another_company_year_or_report_stops(self):
        for change in ({"corp_code": "11111111"}, {"bsns_year": "2019"}, {"reprt_code": "11012"}, {"corp_code": None}):
            self.fresh()
            self.server.rows = [{**r, **change} for r in annual_rows("CFS")]
            with self.assertRaises(R.StopError) as cm:
                self.verdict(dart(dart=CORP))
            self.assertIn("rows of another company, year or report", str(cm.exception))
            self.assertFalse((self.cache() / "fnlttSinglAcntAll").exists(), change)

    def test_a_row_needs_a_receipt_number_of_14_digits(self):
        for bad in ("2026031090000", "202603109000011", "2026031090000a", "２０２６０３１０９００００１", 20260310900001, None, ""):
            self.fresh()
            self.server.rows = [{**r, "rcept_no": bad} for r in annual_rows("CFS")]
            with self.assertRaises(R.StopError, msg=bad):
                self.verdict(dart(dart=CORP))
            self.assertFalse((self.cache() / "fnlttSinglAcntAll").exists(), bad)

    def test_a_cached_stop_status_is_a_cache_problem(self):
        folder = self.cache() / "fnlttSinglAcntAll"
        folder.mkdir(parents=True)
        (folder / "99900100_2025_11011_CFS.json").write_text('{"status": "020", "message": "m"}', encoding="utf-8")
        os.environ.pop("RECEIPTS_DART_KEY")
        with self.assertRaises(R.StopError) as cm:
            self.verdict(dart(dart=CORP))
        self.assertIn("cannot be used", str(cm.exception))
        self.assertIn("Delete it or run with --refresh", str(cm.exception))
        self.assertEqual(self.server.seen, [])

    def test_a_cached_file_under_the_wrong_name_stops(self):
        self.verdict(dart(dart=CORP))
        folder = self.cache() / "fnlttSinglAcntAll"
        good = folder / "99900100_2025_11011_CFS.json"
        for name in ("88800100_2025_11011_CFS.json", "99900100_2024_11011_CFS.json", "99900100_2025_11012_CFS.json"):
            shutil.copy(good, folder / name)
        os.environ.pop("RECEIPTS_DART_KEY")
        for loc in (dart(dart="88800100"), dart(dart=CORP, year=2024), dart(dart=CORP, report="half")):
            with self.assertRaises(R.StopError, msg=loc) as cm:
                self.verdict(loc)
            self.assertIn("rows of another company, year or report", str(cm.exception))

    # ---- access

    def test_cache_and_refresh(self):
        self.verdict(dart())
        self.assertEqual(self.paths(), ["/api/corpCode.xml", "/api/fnlttSinglAcntAll.json"])
        cache = self.cache()
        self.assertEqual(sorted(p.relative_to(cache).as_posix() for p in cache.rglob("*") if p.is_file()),
                         ["corpCode.zip", "fetched.json", "fnlttSinglAcntAll/99900100_2025_11011_CFS.json", "origin.json"])
        self.assertEqual(json.loads((cache / "fetched.json").read_text()),
                         {"corpCode.zip": today(), "fnlttSinglAcntAll/99900100_2025_11011_CFS.json": today()})
        self.server.seen.clear()
        os.environ.pop("RECEIPTS_DART_KEY")            # a cached answer needs no key and no request
        self.assertEqual(self.verdict(dart())[0], "verified")
        self.assertEqual(self.server.seen, [])
        os.environ["RECEIPTS_DART_KEY"] = KEY
        two = [{**self.claim("자산총계는 5조원", "5조원", dart()), "id": "a"},
               {**self.claim("부채총계는 2조원", "2조원", dart("ifrs-full_Liabilities")), "id": "b"}]
        res = self.check(["자산총계는 5조원, 부채총계는 2조원이다."], two, refresh=True)
        self.assertEqual([c["verdict"] for c in res["claims"]], ["verified", "verified"])
        self.assertEqual(self.paths(), ["/api/corpCode.xml", "/api/fnlttSinglAcntAll.json"])   # once per file

    def test_the_fetch_date_comes_from_the_record_or_the_file(self):
        _, res = self.one("자산총계는 5조원이다.", "5조원", dart(dart=CORP))
        self.assertEqual(res["dart"]["files"][0]["fetched"], today())
        cached = self.cache() / "fnlttSinglAcntAll" / "99900100_2025_11011_CFS.json"
        record = self.cache() / "fetched.json"
        record.write_text(json.dumps({"fnlttSinglAcntAll/99900100_2025_11011_CFS.json": "2026-01-05"}), encoding="utf-8")
        _, res = self.one("자산총계는 5조원이다.", "5조원", dart(dart=CORP))
        self.assertEqual(res["dart"]["files"][0]["fetched"], "2026-01-05")
        stamp = time.mktime(time.strptime("2026-02-03 12:00:00", "%Y-%m-%d %H:%M:%S"))
        os.utime(cached, (stamp, stamp))
        for broken in ('{"fnlttSinglAcntAll/99900100_2025_11011_CFS.json": "2099-99-99"}', "not json", None):
            if broken is None:
                record.unlink()
            else:
                record.write_text(broken, encoding="utf-8")
            _, res = self.one("자산총계는 5조원이다.", "5조원", dart(dart=CORP))
            self.assertEqual(res["dart"]["files"][0]["fetched"], "2026-02-03", broken)   # the file's own date

    def test_cache_folder_from_option_and_environment(self):
        other, env = self.dir / "elsewhere", self.dir / "from-env"
        claim = [self.claim("자산총계는 5조원", "5조원", dart())]
        os.environ["RECEIPTS_DART_CACHE"] = str(env)
        self.check(["자산총계는 5조원이다."], claim, dart_cache=str(other))   # the option wins over the variable
        self.assertTrue((other / "fnlttSinglAcntAll" / "99900100_2025_11011_CFS.json").exists())
        self.assertFalse(env.exists())
        self.check(["자산총계는 5조원이다."], claim)
        self.assertTrue((env / "corpCode.zip").exists())
        self.assertFalse((self.dir / ".receipts").exists())

    def test_no_key_stops_before_any_request(self):
        os.environ.pop("RECEIPTS_DART_KEY")
        with self.assertRaises(R.StopError) as cm:
            self.verdict(dart())
        self.assertIn("RECEIPTS_DART_KEY is not set", str(cm.exception))
        os.environ["RECEIPTS_DART_KEY"] = "   "
        with self.assertRaises(R.StopError):
            self.verdict(dart(dart=CORP))
        self.assertEqual(self.server.seen, [])
        self.assertFalse((self.dir / ".receipts").exists())

    def test_statuses_other_than_data_and_nothing_stop_and_are_not_cached(self):
        said = {"010": "the key is not registered", "020": "the request limit is exceeded", "800": "DART is under maintenance"}
        for status in ("010", "011", "012", "014", "020", "021", "100", "101", "800", "900", "901", "999"):
            self.server.status = status
            with self.assertRaises(R.StopError) as cm:
                self.verdict(dart(dart=CORP))
            self.assertIn(f"DART answered status {status} for fnlttSinglAcntAll.json", str(cm.exception))
            self.assertNotIn("사유", str(cm.exception))   # the server's own message is never shown
            if status in said:
                self.assertIn(said[status], str(cm.exception))
        for status in ("ABC", "", " 000", "13", "secret-text"):
            self.server.raw = json.dumps({"status": status, "message": "m", "list": annual_rows("CFS")}).encode()
            with self.assertRaises(R.StopError, msg=status) as cm:
                self.verdict(dart(dart=CORP))
            self.assertIn("a status that is not a three-digit code", str(cm.exception))
            if status in ("ABC", "secret-text"):
                self.assertNotIn(status, str(cm.exception))   # what the server wrote there is not repeated
        self.assertFalse((self.cache() / "fnlttSinglAcntAll").exists())
        reset(self.server)
        self.assertEqual(self.verdict(dart(dart=CORP))[0], "verified")

    def test_a_refused_key_stops(self):
        os.environ["RECEIPTS_DART_KEY"] = "x" * 40
        with self.assertRaises(R.StopError) as cm:
            self.verdict(dart(dart=CORP))
        self.assertIn("status 010 for fnlttSinglAcntAll.json: the key is not registered", str(cm.exception))
        self.server.raw = b"<?xml version='1.0'?><result><status>020</status><message>limit</message></result>"
        with self.assertRaises(R.StopError) as cm:   # the code file answers an error as XML, not as a zip
            self.verdict(dart())
        self.assertIn("DART answered status 020 for corpCode.xml: the request limit is exceeded", str(cm.exception))

    def test_answers_that_are_not_what_dart_sends_stop(self):
        no_receipt = [{k: v for k, v in r.items() if k != "rcept_no"} for r in annual_rows("CFS")]
        for raw in (b"<html>busy</html>", b"{}", b"[1]", b'{"status": "000"}', b'{"status": "000", "list": []}',
                    b'{"status": "000", "list": [1]}', b'{"status": 0, "list": [{}]}', b"\xff\xfe", b"",
                    json.dumps({"status": "000", "list": no_receipt}).encode(),
                    json.dumps({"status": "000", "list": [{**annual_rows("CFS")[0], "sj_div": None}]}).encode()):
            self.server.raw = raw
            with self.assertRaises(R.StopError, msg=raw[:40]):
                self.verdict(dart(dart=CORP))
        self.server.raw = b"PK\x03\x04 not a zip"
        with self.assertRaises(R.StopError):
            self.verdict(dart())
        self.assertFalse(self.cache().exists() and any(p.is_file() for p in self.cache().rglob("*")))

    def test_a_body_that_is_too_large_is_refused(self):
        saved, R.DART_MAX_BYTES = R.DART_MAX_BYTES, 2000
        self.addCleanup(setattr, R, "DART_MAX_BYTES", saved)
        for packed in (False, True):
            self.server.zipped = packed
            with self.assertRaises(R.StopError) as cm:
                self.verdict(dart(dart=CORP))
            self.assertIn("DART sent more than", str(cm.exception))
        self.assertFalse((self.cache() / "fnlttSinglAcntAll").exists())

    def test_a_body_that_ends_early_is_tried_again_and_stops(self):
        for packed in (False, True):
            self.fresh()
            self.server.zipped, self.server.cut = packed, 6
            with self.assertRaises(R.StopError) as cm:
                self.verdict(dart(dart=CORP))
            self.assertEqual(str(cm.exception), "cannot read DART (fnlttSinglAcntAll.json: EOFError)")
            self.assertEqual(len(self.server.seen), 2, packed)
            self.assertFalse((self.cache() / "fnlttSinglAcntAll").exists())
        self.fresh()                                         # a gzip stream cut short with a matching length
        self.server.zipped, self.server.cut, self.server.cut_quietly = True, 6, True
        with self.assertRaises(R.StopError):
            self.verdict(dart(dart=CORP))
        self.assertEqual(len(self.server.seen), 2)

    def test_the_limits_the_contract_states(self):
        self.assertEqual(self.saved_gap, 0.2)                # at most 5 requests a second
        self.assertEqual(R.DART_MAX_BYTES, 64 * 1024 * 1024)
        self.assertEqual(R.DART_HOME, "https://opendart.fss.or.kr")
        self.assertEqual(R.DART_KEY_ENV, "RECEIPTS_DART_KEY")

    def test_the_fetch_date_is_the_utc_date(self):
        real = R.time.gmtime
        self.addCleanup(setattr, R.time, "gmtime", real)
        fixed = time.struct_time((2031, 7, 8, 23, 30, 0, 1, 189, 0))
        R.time.gmtime = lambda *a: real(*a) if a else fixed
        _, res = self.one("자산총계는 5조원이다.", "5조원", dart(dart=CORP))
        R.time.gmtime = real
        self.assertEqual(res["dart"]["files"][0]["fetched"], "2031-07-08")

    def test_a_gzip_answer_is_unpacked(self):
        self.server.zipped = True
        self.assertEqual(self.verdict(dart())[0], "verified")
        self.assertEqual(self.server.headers[0].get("accept-encoding"), "gzip")
        cached = (self.cache() / "fnlttSinglAcntAll" / "99900100_2025_11011_CFS.json").read_bytes()
        self.assertEqual(json.loads(cached)["status"], "000")   # stored unpacked

    def test_a_corrupt_cache_stops(self):
        self.verdict(dart())
        cache = self.cache()
        (cache / "fnlttSinglAcntAll" / "99900100_2025_11011_CFS.json").write_text('{"status": "000", "li', encoding="utf-8")
        with self.assertRaises(R.StopError) as cm:
            self.verdict(dart())
        self.assertIn("is not what DART sends", str(cm.exception))
        (cache / "fnlttSinglAcntAll" / "99900100_2025_11011_CFS.json").unlink()
        (cache / "corpCode.zip").write_bytes(b"PK broken")
        with self.assertRaises(R.StopError):
            self.verdict(dart())
        self.assertEqual(self.verdict(dart(dart=CORP))[0], "verified")   # the code file is not needed for this one

    def test_http_errors_are_retried_once(self):
        for code in (503, 429, 500, 502, 504):
            self.fresh()
            self.server.fail_with, self.server.fail_times = code, 1
            self.assertEqual(self.verdict(dart(dart=CORP))[0], "verified", code)
            self.assertEqual(len(self.server.seen), 2, code)
            self.assertIn(2, self.sleeps)   # two seconds before the second try
        self.fresh()
        self.server.fail_with = 500
        with self.assertRaises(R.StopError) as cm:
            self.verdict(dart(dart=CORP))
        self.assertEqual(str(cm.exception), "DART answered HTTP 500 for fnlttSinglAcntAll.json")
        self.assertEqual(len(self.server.seen), 2)
        for code in (400, 401, 403, 404):                # not retried, and not an answer
            self.fresh()
            self.server.fail_with = code
            with self.assertRaises(R.StopError):
                self.verdict(dart(dart=CORP))
            self.assertEqual(len(self.server.seen), 1, code)

    def test_no_connection_is_tried_twice_and_stops(self):
        os.environ["RECEIPTS_DART_URL"] = f"http://127.0.0.1:{closed_port()}"
        tries = []
        real = R._OPENER.open
        self.addCleanup(setattr, R._OPENER, "open", real)
        R._OPENER.open = lambda req, *a, **kw: (tries.append(kw.get("timeout")), real(req, *a, **kw))[1]
        with self.assertRaises(R.StopError) as cm:
            self.verdict(dart(dart=CORP))
        self.assertIn("cannot read DART (fnlttSinglAcntAll.json: URLError", str(cm.exception))
        self.assertEqual(tries, [60, 60])               # two tries, each with a time limit
        self.assertEqual(self.sleeps.count(2), 1)
        os.environ["RECEIPTS_DART_URL"] = "ftp://127.0.0.1/x"
        with self.assertRaises(R.StopError):
            self.verdict(dart(dart=CORP))

    def test_requests_are_spaced(self):
        saved, R.DART_GAP = R.DART_GAP, 30.0
        self.addCleanup(setattr, R, "DART_GAP", saved)
        R._DART["last"] = time.monotonic() - 100         # the first request needs no wait
        self.verdict(dart())                             # two requests: the code file, then the statements
        waits = [s for s in self.sleeps if s != 2]
        self.assertEqual(len(waits), 1)
        self.assertTrue(29.0 < waits[0] <= 30.0, waits)

    def test_a_redirect_to_another_host_is_not_followed(self):
        other = new_server()
        self.addCleanup(other.server_close)
        self.addCleanup(other.shutdown)
        self.server.redirect_to = f"http://127.0.0.1:{other.server_address[1]}"
        with self.assertRaises(R.StopError) as cm:
            self.verdict(dart(dart=CORP))
        self.assertEqual(other.seen, [])
        self.assertIn("HTTP 302", str(cm.exception))

    def test_a_redirect_from_https_to_http_is_not_followed(self):
        handler = R._SameHostRedirects()
        req = R.urllib.request.Request("https://opendart.fss.or.kr/api/x?crtfc_key=" + KEY)
        self.assertIsNone(handler.redirect_request(req, None, 302, "Found", {}, "http://opendart.fss.or.kr/api/x"))
        self.assertIsNone(handler.redirect_request(req, None, 302, "Found", {}, "https://example.org/api/x"))
        self.assertIsNotNone(handler.redirect_request(req, None, 302, "Found", {}, "https://opendart.fss.or.kr/api/y"))

    # ---- the key stays out of everything

    def test_the_key_is_sent_only_as_the_request_parameter(self):
        r, res = self.one("자산총계는 5조원이다.", "5조원", dart())
        self.assertEqual(len(self.server.seen), 2)
        for (path, query), headers in zip(self.server.seen, self.server.headers):
            self.assertEqual(query.get("crtfc_key"), KEY)
            self.assertEqual([k for k, v in query.items() if KEY in v], ["crtfc_key"])
            self.assertEqual(sorted(headers), ["accept-encoding", "connection", "host", "user-agent"])
            self.assertFalse([k for k, v in headers.items() if KEY.lower() in v.lower()])
        code, out, err = self.main("check", str(self.dir / "claims.json"))
        self.assertEqual(code, 0)
        code2, out2, err2 = self.main("dart", "accounts", STOCK, "--year", "2025", "--report", "annual", "--fs", "CFS",
                                      "--dart-cache", str(self.cache()))
        self.assertEqual(code2, 0)
        for text in (json.dumps(res, ensure_ascii=False), out, err, out2, err2, self.written()):
            self.assertNotIn(KEY, text)
            self.assertNotIn("crtfc_key", text)

    def test_the_key_is_not_in_any_stop_message(self):
        messages = []
        for setup in ("status", "echo", "http", "raw", "port", "redirect", "other report"):
            self.fresh()
            if setup == "status":
                self.server.status = "020"
            elif setup == "echo":                         # a server that repeats the request in its message
                self.server.status, self.server.echo = "020", True
            elif setup == "http":
                self.server.fail_with = 500
            elif setup == "raw":
                self.server.raw = b"not json"
            elif setup == "port":
                os.environ["RECEIPTS_DART_URL"] = f"http://127.0.0.1:{closed_port()}"
            elif setup == "redirect":
                self.server.redirect_to = "http://127.0.0.1:9"
            else:
                self.server.rows = [{**r, "corp_code": "11111111"} for r in annual_rows("CFS")]
            self.write(["자산총계는 5조원이다."], [self.claim("자산총계는 5조원", "5조원", dart(dart=CORP))])
            code, out, err = self.main("check", str(self.dir / "claims.json"))
            self.assertEqual(code, 2, setup)
            self.assertTrue(err.startswith("receipts: stopped: "), setup)
            messages.append(out + err + self.written())
        for text in messages:
            self.assertNotIn(KEY, text)
            self.assertNotIn("crtfc_key", text)
            self.assertNotIn("127.0.0.1", text)   # no request address in a message
            self.assertNotIn("limit for", text)   # nothing the server said

    def test_an_answer_that_repeats_the_key_is_not_cached(self):
        self.server.echo = True                           # status 000, with the request repeated in the message
        with self.assertRaises(R.StopError) as cm:
            self.verdict(dart(dart=CORP))
        self.assertIn("repeats the key", str(cm.exception))
        self.assertNotIn(KEY, str(cm.exception))
        self.assertFalse((self.cache() / "fnlttSinglAcntAll").exists())
        self.assertNotIn(KEY, self.written())

    def test_a_message_that_carried_the_key_is_scrubbed(self):
        self.assertEqual(R._no_key(f"oops {KEY} oops"), "oops [key] oops")
        odd = "made up&key=with spaces/and#more"
        os.environ["RECEIPTS_DART_KEY"] = odd
        for form in (odd, urllib.parse.quote(odd, safe=""), urllib.parse.quote_plus(odd), urllib.parse.quote(odd)):
            self.assertEqual(R._no_key(f"at ?crtfc_key={form}&x=1"), "at ?crtfc_key=[key]&x=1")
        os.environ.pop("RECEIPTS_DART_KEY")
        self.assertEqual(R._no_key("nothing to hide"), "nothing to hide")

    def test_the_command_line_scrubs_its_stop_messages(self):
        self.write(["자산총계는 5조원이다."], [self.claim("자산총계는 5조원", "5조원", dart(dart=CORP))])
        real = R.run_check
        self.addCleanup(setattr, R, "run_check", real)
        for error, shown in ((R.StopError(f"asked {KEY} for it"), "receipts: stopped: asked [key] for it"),
                             (ValueError(f"bad {KEY}"), "receipts: stopped: internal error (ValueError: bad [key])")):
            def boom(*a, _e=error, **kw):
                raise _e
            R.run_check = boom
            code, out, err = self.main("check", str(self.dir / "claims.json"))
            self.assertEqual((code, err.strip()), (2, shown))

    def test_an_odd_key_is_sent_whole_and_stays_out_of_messages(self):
        odd = "made up&key=with spaces#x"
        os.environ["RECEIPTS_DART_KEY"] = odd
        self.server.key = odd
        self.assertEqual(self.verdict(dart(dart=CORP))[0], "verified")
        self.assertEqual(self.server.seen[0][1]["crtfc_key"], odd)
        self.server.status, self.server.echo = "020", True
        self.write(["자산총계는 5조원이다."], [self.claim("자산총계는 5조원", "5조원", dart(dart=CORP))])
        code, out, err = self.main("check", str(self.dir / "claims.json"), "--refresh")
        self.assertEqual(code, 2)
        for form in (odd, urllib.parse.quote_plus(odd), urllib.parse.quote(odd, safe="")):
            self.assertNotIn(form, out + err)

    # ---- the receipt

    def test_the_receipt_lists_the_dart_files(self):
        self.one("자산총계는 5조원이다.", "5조원", dart())
        code, out, err = self.main("check", str(self.dir / "claims.json"))
        self.assertEqual(code, 0)
        self.assertIn("### DART data used (2)", out)
        self.assertIn(f"| fnlttSinglAcntAll/99900100_2025_11011_CFS.json | {today()} | {ANNUAL} |", out)
        self.assertIn(f"Not fetched from DART's own address but from {self.url}: corpCode.zip, "
                      "fnlttSinglAcntAll/99900100_2025_11011_CFS.json", out)
        saved = json.loads((self.dir / "receipts.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["dart"]["files"][1]["rcept_no"], ANNUAL)

    def test_the_other_address_is_what_was_recorded_at_the_fetch(self):
        self.one("자산총계는 5조원이다.", "5조원", dart(dart=CORP))
        for later in (None, "http://127.0.0.1:9/never-used"):   # the variable of a later run changes nothing
            os.environ.pop("RECEIPTS_DART_URL", None)
            if later:
                os.environ["RECEIPTS_DART_URL"] = later
            _, res = self.one("자산총계는 5조원이다.", "5조원", dart(dart=CORP))
            self.assertEqual(res["dart"]["files"][0]["address"], self.url)
        # a file fetched from DART's own address carries no such note
        self.fresh()
        saved = R.DART_HOME
        self.addCleanup(setattr, R, "DART_HOME", saved)
        R.DART_HOME = self.url
        os.environ.pop("RECEIPTS_DART_URL")
        _, res = self.one("자산총계는 5조원이다.", "5조원", dart(dart=CORP))
        self.assertEqual(res["dart"]["files"], [{"file": "fnlttSinglAcntAll/99900100_2025_11011_CFS.json",
                                                 "fetched": today(), "rcept_no": ANNUAL}])
        self.assertFalse((self.cache() / "origin.json").exists())
        code, out, _ = self.main("check", str(self.dir / "claims.json"))
        self.assertNotIn("Not fetched from", out)
        # fetched again from elsewhere, and then again from DART's own address
        os.environ["RECEIPTS_DART_URL"] = self.url + "/"
        R.DART_HOME = saved
        _, res = self.one("자산총계는 5조원이다.", "5조원", dart(dart=CORP), refresh=True)
        self.assertEqual(res["dart"]["files"][0]["address"], self.url)
        R.DART_HOME = self.url
        _, res = self.one("자산총계는 5조원이다.", "5조원", dart(dart=CORP), refresh=True)
        self.assertNotIn("address", res["dart"]["files"][0])
        self.assertFalse((self.cache() / "origin.json").exists())   # gone with its last entry

    def test_a_file_from_another_address_is_never_cached_without_its_note(self):
        self.cache().mkdir(parents=True)
        (self.cache() / "origin.json").mkdir()               # the note cannot be written
        with self.assertRaises(R.StopError) as cm:
            self.verdict(dart(dart=CORP))
        self.assertIn("cannot write the DART cache", str(cm.exception))
        self.assertFalse((self.cache() / "fnlttSinglAcntAll" / "99900100_2025_11011_CFS.json").exists())

    def test_exit_codes_and_no_stale_receipt_after_a_stop(self):
        self.write(["자산총계는 9조원이다."], [self.claim("자산총계는 9조원", "9조원", dart())])
        self.assertEqual(self.main("check", str(self.dir / "claims.json"), "--quiet")[0], 1)
        self.assertEqual(json.loads((self.dir / "receipts.json").read_text(encoding="utf-8"))["counts"]["wrong"], 1)
        self.server.status = "800"
        code, out, err = self.main("check", str(self.dir / "claims.json"), "--refresh")
        self.assertEqual(code, 2)
        self.assertIn("DART answered status 800", err)
        left = json.loads((self.dir / "receipts.json").read_text(encoding="utf-8"))
        self.assertEqual(list(left), ["stopped"])            # the earlier result is gone
        self.assertIn("DART answered status 800", left["stopped"])
        self.assertTrue((self.dir / "receipts.md").read_text(encoding="utf-8").startswith("The run stopped."))
        self.assertEqual(self.main("report", str(self.dir / "receipts.json"))[0], 2)
        fresh_out = self.dir / "new-out"
        self.assertEqual(self.main("check", str(self.dir / "claims.json"), "--refresh", "--out", str(fresh_out))[0], 2)
        self.assertFalse(fresh_out.exists())                 # nothing is created for a run that stopped
        empty = self.dir / "empty-out"
        empty.mkdir()
        (empty / "notes.md").write_text("mine", encoding="utf-8")
        self.assertEqual(self.main("check", str(self.dir / "claims.json"), "--refresh", "--out", str(empty))[0], 2)
        self.assertEqual(sorted(p.name for p in empty.iterdir()), ["notes.md"])   # nor in a folder that had none

    def test_the_stop_note_hides_the_key_and_says_when_it_cannot_be_written(self):
        out = self.dir / "o"
        out.mkdir()
        (out / "receipts.json").write_text('{"counts": {}}', encoding="utf-8")
        (out / "receipts.md").mkdir()                        # cannot be replaced by a file
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            R._note_stop(str(out), f"asked {KEY} for it")
        self.assertEqual(json.loads((out / "receipts.json").read_text(encoding="utf-8")), {"stopped": "asked [key] for it"})
        self.assertEqual(err.getvalue(), "")                 # a folder of that name is not an earlier result
        (out / "receipts.md").rmdir()
        (out / "receipts.md").write_text("1 numbers / 1 verified", encoding="utf-8")
        real = open

        def no_markdown(path, *a, **kw):
            if str(path).endswith("receipts.md") and a and "w" in a[0]:
                raise PermissionError("read-only")
            return real(path, *a, **kw)

        R.open = no_markdown
        self.addCleanup(delattr, R, "open")
        with contextlib.redirect_stderr(err):
            R._note_stop(str(out), "stopped again")
        self.assertIn("could not replace", err.getvalue())
        self.assertIn("still holds the result of an earlier run", err.getvalue())

    def test_a_result_that_cannot_be_written_is_a_stop(self):
        self.write(["자산총계는 5조원이다."], [self.claim("자산총계는 5조원", "5조원", dart(dart=CORP))])
        self.assertEqual(self.main("check", str(self.dir / "claims.json"), "--quiet")[0], 0)
        (self.dir / "receipts.json").unlink()
        (self.dir / "receipts.json").mkdir()                 # the result cannot be written there
        code, out, err = self.main("check", str(self.dir / "claims.json"), "--quiet")
        self.assertEqual(code, 2)
        self.assertIn("cannot write the receipts", err)
        self.assertTrue((self.dir / "receipts.md").read_text(encoding="utf-8").startswith("The run stopped."))

    def test_the_cache_is_not_read_as_a_plain_file(self):
        def as_file(rel):
            return {"file": rel, "pointer": "/list", "row": {"sj_div": "BS", "account_id": "ifrs-full_Liabilities"},
                    "column": "thstrm_amount"}

        def claims(rel):
            return [{**self.claim("자산총계는 5조원", "5조원", dart(dart=CORP)), "id": "a"},
                    {**self.claim("부채총계는 2조원", "2조원", as_file(rel)), "id": "b"},
                    {**self.claim("부채총계는 2조원", "2조원", dart("ifrs-full_Liabilities", dart=CORP)), "id": "c",
                     "line": 2}]

        lines = ["자산총계는 5조원, 부채총계는 2조원이다.", "부채총계는 2조원이다."]
        for rel in (".receipts/dart/fnlttSinglAcntAll/99900100_2025_11011_CFS.json",
                    ".receipts\\dart\\fnlttSinglAcntAll\\99900100_2025_11011_CFS.json",
                    "sub/.receipts/edgar/companyfacts/CIK0000999001.json"):
            res = self.check(lines, claims(rel))
            self.assertEqual([(c["verdict"], c["reason"]) for c in res["claims"]],
                             [("verified", "match"), ("unverifiable", "unsupported"), ("verified", "match")], rel)
        many = {"file": ".receipts/dart/x.json", "pointer": "/list", "where": {}, "column": "thstrm_amount"}
        self.assertEqual(self.derived("합계는 7조원이다.", "7조원", "sum", {"cells": many}), ("unverifiable", "unsupported"))
        # a cache kept elsewhere is a file like any other, and reading it beside a DART locator breaks nothing
        res = self.check(lines, claims("kept/fnlttSinglAcntAll/99900100_2025_11011_CFS.json"),
                         dart_cache=str(self.dir / "kept"))
        self.assertEqual([(c["verdict"], c["reason"]) for c in res["claims"]], [("verified", "match")] * 3)

    # ---- looking things up

    def test_company_lookup(self):
        os.chdir(self.dir)
        self.addCleanup(os.chdir, ROOT)
        code, out, _ = self.main("dart", "company", "예시전자")
        got = json.loads(out)
        self.assertEqual(got["fetched"], today())
        self.assertEqual((code, got["matched"]), (0, 3))
        self.assertEqual(got["companies"], [   # the one with a stock code first, then by name
            {"corp_code": CORP, "name": "예시전자", "name_en": "Example Electronics", "stock_code": STOCK},
            {"corp_code": "99900050", "name": "가나다 예시전자", "name_en": "Early Example", "stock_code": None},
            {"corp_code": "99900200", "name": "예시전자서비스", "name_en": "Example Service", "stock_code": None}])
        self.assertEqual(json.loads(self.main("dart", "company", "9990a0")[1])["companies"][0]["corp_code"], CORP)
        self.assertEqual(json.loads(self.main("dart", "company", "99900300")[1])["companies"][0]["name"], "다른 & 회사")
        self.assertEqual(json.loads(self.main("dart", "company", "example electronics")[1])["companies"][0]["corp_code"],
                         CORP)   # the English name is searched too
        limited = json.loads(self.main("dart", "company", "같은코드", "--limit", "1")[1])
        self.assertEqual((limited["matched"], len(limited["companies"])), (2, 1))
        self.assertEqual(len(json.loads(self.main("dart", "company", "같은코드", "--limit", "0")[1])["companies"]), 2)
        for bad in (["dart", "company", "없는회사"], ["dart", "company", "예시전자", "--limit", "-1"]):
            code, out, err = self.main(*bad)
            self.assertEqual((code, out), (2, ""), bad)
        self.assertTrue((self.cache() / "corpCode.zip").exists())

    def test_accounts_lookup_prints_no_amount(self):
        cache = str(self.dir / "c")
        base = ["dart", "accounts", STOCK, "--year", "2026", "--report", "q3", "--fs", "CFS", "--dart-cache", cache]
        code, out, _ = self.main(*base, "--statement", "IS")
        got = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual((got["corp_code"], got["rcept_no"], got["currency"], got["matched"], got["fetched"]),
                         (CORP, Q3, "KRW", 1, today()))
        self.assertEqual(got["rows"], [{"statement": "IS", "account": "ifrs-full_Revenue", "name": "매출액", "columns": {
            "thstrm_amount": {"period": "제 11 기 3분기", "holds": "the three months"},
            "thstrm_add_amount": {"period": "제 11 기 3분기", "holds": "from the start of the business year to the period end"},
            "frmtrm_q_amount": {"period": "제 10 기 3분기", "holds": "the same three months a year earlier"},
            "frmtrm_add_amount": {"period": "제 10 기 3분기", "holds": "the same cumulative period a year earlier"}}}])
        everything = self.main(*base)[1]
        for amount in ("350000000000", "1020000000000", "5300000000000", "410000000000", "390000000000"):
            self.assertNotIn(amount, everything)             # a plan is not written from values
        cash = json.loads(self.main(*base, "--statement", "CF")[1])["rows"][0]
        self.assertEqual(sorted(cash["columns"]), ["frmtrm_amount", "thstrm_amount"])   # only what a locator may name
        annual = ["dart", "accounts", CORP, "--year", "2025", "--report", "annual", "--fs", "CFS", "--dart-cache", cache]
        got = json.loads(self.main(*annual, "--limit", "0")[1])
        self.assertEqual((got["matched"], got["rows_not_listed_other_statements"]), (16, 1))
        by_name = {r["name"]: r for r in got["rows"]}
        self.assertIsNone(by_name["매각예정분류"]["account"])
        self.assertIsNone(by_name["이름만 있는 줄"]["account"])
        self.assertEqual(sorted(by_name["자본총계"]["columns"]), ["bfefrmtrm_amount", "frmtrm_amount", "thstrm_amount"])
        self.assertEqual(sorted(by_name["매출액"]["columns"]), ["bfefrmtrm_amount", "frmtrm_amount", "thstrm_amount"])
        self.assertEqual(json.loads(self.main(*annual, "--match", "기타", "--limit", "2")[1])["matched"], 3)
        self.assertEqual(len(json.loads(self.main(*annual, "--match", "기타", "--limit", "2")[1])["rows"]), 2)
        self.assertEqual(json.loads(self.main(*annual, "--match", "ASSETS")[1])["matched"], 1)
        self.server.seen.clear()
        self.assertEqual(self.main(*annual)[0], 0)
        self.assertEqual(self.server.seen, [])               # from the cache
        self.assertEqual(self.main(*annual, "--refresh")[0], 0)
        self.assertEqual(self.paths(), ["/api/fnlttSinglAcntAll.json"])   # --refresh asks again
        self.server.seen.clear()
        for bad in (["dart", "accounts", CORP, "--year", "2025", "--report", "annual"],
                    ["dart", "accounts", CORP, "--year", "2025", "--report", "annual", "--fs", "XXX"],
                    ["dart", "accounts", CORP, "--year", "2030", "--report", "annual", "--fs", "CFS", "--statement", "SCE"],
                    ["dart", "accounts", CORP, "--year", "2030", "--report", "annual", "--fs", "CFS", "--limit", "-1"]):
            code, out, err = self.main(*bad, "--dart-cache", cache)
            self.assertEqual((code, out), (2, ""), bad)
        self.assertEqual(self.server.seen, [])               # each refused before any request
        for bad in (["dart", "accounts", CORP, "--year", "2031", "--report", "annual", "--fs", "CFS"],
                    ["dart", "accounts", "9990Z9", "--year", "2025", "--report", "annual", "--fs", "CFS"]):
            code, out, err = self.main(*bad, "--dart-cache", cache)
            self.assertEqual((code, out), (2, ""), bad)

    def test_the_code_file_is_read_whole_or_not_at_all(self):
        self.assertEqual(R._dart_companies_in(code_file())[3], ("99900300", "다른 & 회사", "9990B0", "Other & Co"))
        for odd in ("<corp_name><![CDATA[가]]></corp_name><stock_code>9990A0</stock_code>",
                    "<corp_name>가</corp_name><stock_code><![CDATA[9990A0]]></stock_code>",
                    "<corp_name>가</corp_name><stock_code/>"):
            unreadable = zipped(f"<result><list><corp_code>99900100</corp_code>{odd}</list></result>")
            self.assertIsNone(R._dart_companies_in(unreadable), odd)   # a name or a stock code that cannot be read
        saved, R.DART_MAX_BYTES = R.DART_MAX_BYTES, 10
        self.addCleanup(setattr, R, "DART_MAX_BYTES", saved)
        self.assertIsNone(R._dart_companies_in(code_file()))            # the file inside the zip is too large
        R.DART_MAX_BYTES = saved
        other_order = zipped("<result><list><stock_code>9990A0</stock_code><modify_date>20260101</modify_date>"
                             "<corp_name>예시전자</corp_name><corp_code>99900100</corp_code></list></result>")
        self.assertEqual(R._dart_companies_in(other_order), [("99900100", "예시전자", "9990A0", "")])
        mixed = zipped("<result>"
                       "<list><corp_code>99900100</corp_code><corp_name>가</corp_name><corp_eng_name>A</corp_eng_name>"
                       "<stock_code>9990A0</stock_code></list>"
                       "<list><corp_code>99900200</corp_code><corp_name>나</corp_name><corp_eng_name/>"
                       "<stock_code>9990B0</stock_code></list>"
                       "<list><corp_code>99900300</corp_code><corp_name>다</corp_name>"
                       "<stock_code> </stock_code></list></result>")
        self.assertEqual([c[0] for c in R._dart_companies_in(mixed)], ["99900100", "99900200", "99900300"])
        broken = zipped("<result><list><corp_code>99900100</corp_code><corp_name>가</corp_name>"
                        "<stock_code>9990A0</stock_code></list><list><corp_name>코드 없음</corp_name></list></result>")
        self.assertIsNone(R._dart_companies_in(broken))      # a company that cannot be read stops the run
        self.assertIsNone(R._dart_companies_in(zipped("<result></result>")))
        self.assertIsNone(R._dart_companies_in(b"not a zip"))
        self.server.raw = broken
        with self.assertRaises(R.StopError):
            self.verdict(dart())


if __name__ == "__main__":
    unittest.main()
