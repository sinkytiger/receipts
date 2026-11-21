"""Tests for the DART connection, against a local server that stands in for OpenDART.

No request leaves the machine. The companies, the codes and the key are made up.
Run: python -m unittest discover -s tests
"""
from __future__ import annotations

import contextlib
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
ANNUAL, Q3 = "20260310900001", "20261116900002"   # receipt numbers
NO_CODE = "-표준계정코드 미사용-"


def row(sj, account, name, rcept, code, year, **amounts):
    out = {"rcept_no": rcept, "reprt_code": code, "bsns_year": str(year), "corp_code": CORP, "sj_div": sj,
           "sj_nm": sj, "account_id": account, "account_nm": name, "account_detail": "-", "ord": "1", "currency": "KRW"}
    out.update(amounts)
    return out


def annual_rows(fs):
    k = 1 if fs == "CFS" else 2   # the separate statements hold half of everything
    names = {"thstrm_nm": "제 10 기", "frmtrm_nm": "제 9 기", "bfefrmtrm_nm": "제 8 기"}

    def r(sj, account, name, a, b, c, **more):
        return row(sj, account, name, ANNUAL, "11011", 2025, thstrm_amount=str(a // k), frmtrm_amount=str(b // k),
                   bfefrmtrm_amount=str(c // k), **names, **more)

    return [
        r("BS", "ifrs-full_Assets", "자산총계", 5000000000000, 4200000000000, 4000000000000),
        r("BS", "ifrs-full_Liabilities", "부채총계", 2000000000000, 1900000000000, 1800000000000),
        r("IS", "ifrs-full_Revenue", "매출액", 1234500000000, 1100000000000, 1000000000000, thstrm_add_amount=""),
        r("IS", "dart_OperatingIncomeLoss", "영업이익", 107440482000000, 90000000000000, 80000000000000,
          thstrm_add_amount=""),
        r("IS", "ifrs-full_ProfitLoss", "당기순이익(손실)", -45000000000, 30000000000, 20000000000),
        r("IS", "ifrs-full_BasicEarningsLossPerShare", "기본주당이익(손실)", 2310, 1500, 1000),
        r("CF", "ifrs-full_ProfitLoss", "당기순이익(손실)", -45000000000, 30000000000, 20000000000),
        r("CF", NO_CODE, "단기금융상품의 순감소(증가)", -670000000000, 120000000000, 0),
        r("CF", NO_CODE, "매각예정분류", 11000000000, 0, 0),
        r("IS", "dart_OtherGains", "기타수익", 7000000000, 6000000000, 5000000000),
        r("IS", "dart_OtherGains", "기타이익", 3000000000, 2000000000, 1000000000),      # the same id twice
        {**r("IS", "dart_OtherLosses", "기타비용", 1, 1, 1), "thstrm_amount": "해당없음"},  # not a number
        r("SCE", "ifrs-full_Equity", "자본총계", 3000000000000, 2300000000000, 2200000000000),
    ]


def q3_rows():
    def bs(account, name, a, b):
        return row("BS", account, name, Q3, "11014", 2026, thstrm_nm="제 11 기 3분기말", thstrm_amount=str(a),
                   frmtrm_nm="제 10 기말", frmtrm_amount=str(b))

    def pl(account, name, q, cum, pq, pcum):
        return row("IS", account, name, Q3, "11014", 2026, thstrm_nm="제 11 기 3분기", thstrm_amount=str(q),
                   thstrm_add_amount=str(cum), frmtrm_q_nm="제 10 기 3분기", frmtrm_q_amount=str(pq),
                   frmtrm_add_amount=str(pcum))

    return [bs("ifrs-full_Assets", "자산총계", 5300000000000, 5000000000000),
            pl("ifrs-full_Revenue", "매출액", 350000000000, 1020000000000, 300000000000, 900000000000),
            row("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", Q3, "11014", 2026,
                thstrm_nm="제 11 기 3분기", thstrm_amount="410000000000", frmtrm_q_nm="제 10 기 3분기",
                frmtrm_q_amount="390000000000", frmtrm_amount="390000000000")]


STATEMENTS = {(CORP, "2025", "11011", "CFS"): annual_rows("CFS"), (CORP, "2025", "11011", "OFS"): annual_rows("OFS"),
              (CORP, "2026", "11014", "CFS"): q3_rows()}
COMPANIES = [(CORP, "예시전자", STOCK), ("99900200", "예시전자서비스", ""), ("99900300", "다른 & 회사", "9990B0"),
             ("99900400", "같은코드하나", "9990C0"), ("99900500", "같은코드둘", "9990C0")]


def code_file(companies=COMPANIES) -> bytes:
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<result>\n' + "".join(
        f"    <list>\n        <corp_code>{c}</corp_code>\n        <corp_name>{n.replace('&', '&amp;')}</corp_name>\n"
        f"        <corp_eng_name>X</corp_eng_name>\n        <stock_code>{s or ' '}</stock_code>\n"
        f"        <modify_date>20260101</modify_date>\n    </list>\n" for c, n, s in companies) + "</result>\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        srv = self.server
        url = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        srv.seen.append((url.path, q))
        if getattr(srv, "redirect_to", None):
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
        if srv.raw is not None:
            body = srv.raw
        elif srv.status:
            body = json.dumps({"status": srv.status, "message": "사유"}, ensure_ascii=False).encode()
        elif q.get("crtfc_key") != KEY:
            body = json.dumps({"status": "010", "message": "등록되지 않은 키입니다."}, ensure_ascii=False).encode()
        elif url.path == "/api/corpCode.xml":
            body = code_file()
        elif url.path == "/api/fnlttSinglAcntAll.json":
            rows = STATEMENTS.get((q.get("corp_code"), q.get("bsns_year"), q.get("reprt_code"), q.get("fs_div")))
            if q.get("fs_div") not in ("CFS", "OFS"):   # DART answers such a request with both sets mixed
                rows = annual_rows("CFS") + annual_rows("OFS")
            doc = {"status": "000", "message": "정상", "list": rows} if rows else \
                {"status": "013", "message": "조회된 데이타가 없습니다."}
            body = json.dumps(doc, ensure_ascii=False).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def dart(account="ifrs-full_Assets", column="thstrm_amount", **extra):
    loc = {"dart": STOCK, "year": 2025, "report": "annual", "fs": "CFS", "statement": "BS", "account": account,
           "column": column}
    loc.update(extra)
    return {k: v for k, v in loc.items() if v is not None}


def q3(account="ifrs-full_Revenue", column="thstrm_amount", statement="IS", **extra):
    return dart(account, column, year=2026, report="q3", statement=statement, **extra)


class DartTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.seen = []
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.saved_gap, R.DART_GAP = R.DART_GAP, 0.0
        cls.saved_sleep, R.time.sleep = R.time.sleep, lambda s: None   # no waiting in tests

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        R.DART_GAP, R.time.sleep = cls.saved_gap, cls.saved_sleep

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="receipts-dart-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        srv = self.server
        srv.seen.clear()
        srv.fail_with, srv.fail_times, srv.raw, srv.status, srv.redirect_to = 0, None, None, "", None
        self.saved_env = dict(os.environ)
        self.addCleanup(self.restore_env)
        os.environ.update({"RECEIPTS_DART_KEY": KEY, "RECEIPTS_DART_URL": self.url})
        os.environ.pop("RECEIPTS_DART_CACHE", None)
        R._CACHE.clear()

    def restore_env(self):
        os.environ.clear()
        os.environ.update(self.saved_env)

    def check(self, lines, claims, **kw):
        (self.dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (self.dir / "claims.json").write_text(json.dumps({"document": "report.md", "claims": claims}), encoding="utf-8")
        R._CACHE.clear()
        return R.run_check(str(self.dir / "claims.json"), **kw)

    def one(self, sentence, number, loc=None, **claim):
        body = {"id": "c1", "line": 1, "quote": sentence, "number": number, "kind": "lookup"}
        if loc is not None:
            body["operands"] = {"of": loc}
        body.update(claim)
        res = self.check([sentence], [body])
        return res["claims"][0], res

    def verdict(self, loc, sentence="자산총계는 5조원이다.", number="5조원"):
        r, _ = self.one(sentence, number, loc)
        return r["verdict"], r["reason"]

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = R.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def paths(self):
        return [p for p, _ in self.server.seen]

    def written(self):
        """Everything the script wrote under the plan's folder, as one text."""
        out = []
        for p in self.dir.rglob("*"):
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
            {"file": "corpCode.zip", "fetched": res["dart"]["files"][0]["fetched"]},
            {"file": "fnlttSinglAcntAll/99900100_2025_11011_CFS.json", "fetched": res["dart"]["files"][1]["fetched"],
             "rcept_no": ANNUAL}])
        self.assertEqual(res["dart"]["address"], self.url)
        for ident in (CORP, "9990a0", " 9990A0 "):
            self.assertEqual(self.verdict(dart(dart=ident)), ("verified", "match"), ident)
        r, _ = self.one("자산총계는 5조원이다.", "5조원", dart(dart=CORP))
        self.assertIn("DART 99900100 2025 annual CFS BS", r["sources"][0])   # no name without the code file

    def test_a_company_code_needs_no_code_file(self):
        self.verdict(dart(dart=CORP))
        self.assertEqual(self.paths(), ["/api/fnlttSinglAcntAll.json"])
        self.assertEqual(self.server.seen[0][1], {"crtfc_key": KEY, "corp_code": CORP, "bsns_year": "2025",
                                                  "reprt_code": "11011", "fs_div": "CFS"})

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
        r, _ = self.one("전년 누적 매출은 9,000억원이었다.", "9,000억원", q3(column="frmtrm_add_amount"))
        self.assertIn("[제 10 기 3분기, the same cumulative period a year earlier]", r["sources"][0])

    def test_interim_balance_sheet_compares_with_the_previous_year_end(self):
        r, _ = self.one("작년 말 자산총계는 5조원이었다.", "5조원",
                        q3("ifrs-full_Assets", "frmtrm_amount", "BS"))
        self.assertEqual(r["verdict"], "verified")
        self.assertIn("[제 10 기말, the end of the previous business year]", r["sources"][0])

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

    def test_formulas_take_dart_cells(self):
        body = {"id": "c1", "line": 1, "quote": "부채비율은 66.7%다.", "number": "66.7%", "kind": "derived",
                "formula": "ratio", "operands": {"num": dart("ifrs-full_Liabilities"),
                                                 "den": {"dart": CORP, "year": 2025, "report": "annual", "fs": "CFS",
                                                         "statement": "SCE", "account": "ifrs-full_Equity",
                                                         "column": "thstrm_amount"}}}
        res = self.check(["부채비율은 66.7%다."], [body])
        self.assertEqual((res["claims"][0]["verdict"], res["claims"][0]["reason"]), ("unverifiable", "unsupported"))
        body["formula"], body["operands"] = "pct_change", {"new": dart(), "old": dart(column="frmtrm_amount")}
        body["quote"], body["number"] = "자산은 19.0% 늘었다.", "19.0%"
        res = self.check(["자산은 19.0% 늘었다."], [body])
        self.assertEqual(res["claims"][0]["verdict"], "verified")
        body["formula"], body["operands"] = "sum", {"items": [dart(), dart("ifrs-full_Liabilities")]}
        body["quote"], body["number"] = "합계는 7조원이다.", "7조원"
        self.assertEqual(self.check(["합계는 7조원이다."], [body])["claims"][0]["verdict"], "verified")
        body["operands"] = {"cells": {**dart(), "where": {}}}
        res = self.check(["합계는 7조원이다."], [body])
        self.assertEqual((res["claims"][0]["verdict"], res["claims"][0]["reason"]), ("unverifiable", "unsupported"))
        self.assertEqual(self.verdict({**dart(), "rows": {}}), ("unverifiable", "unsupported"))

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
                         ("unverifiable", "locator_no_match"))                        # present and empty
        self.assertEqual(self.verdict(dart("ifrs-full_Revenue", "frmtrm_q_amount", **rev)),
                         ("unverifiable", "locator_no_match"))                        # absent in an annual report
        self.assertEqual(self.verdict(q3(column="bfefrmtrm_amount")), ("unverifiable", "locator_no_match"))
        self.assertEqual(self.verdict(dart("dart_OtherLosses", **rev)), ("unverifiable", "cell_not_numeric"))
        for column in ("thstrm", "THSTRM_AMOUNT", "account_nm", "ord", None, 3):
            self.assertEqual(self.verdict(dart(column=column)), ("unverifiable", "locator_no_match"), column)

    # ---- fields that are not written as the contract says make no request

    def test_fields_outside_the_lists_are_refused_before_any_request(self):
        bad = [dart(year=2014), dart(year="25"), dart(year=None), dart(year=True), dart(year=2025.0),
               dart(report="11011"), dart(report="FY"), dart(report=None), dart(fs="XXX"), dart(fs=None), dart(fs=""),
               dart(rcept_no="2026031090000"), dart(rcept_no="abc"), dart(rcept_no=True),
               dart(dart=5930), dart(dart="예시전자"), dart(dart="12345"), dart(dart="1234567"), dart(dart=None)]
        for loc in bad:
            self.server.seen.clear()
            self.assertEqual(self.verdict(loc), ("unverifiable", "locator_no_match"), loc)
            self.assertEqual(self.server.seen, [], loc)
        self.assertEqual(self.verdict(dart(year="2025", report=" Annual ", fs="cfs"))[0], "verified")

    def test_code_file_with_fields_in_another_order(self):
        xml = ("<result><list><stock_code>9990A0</stock_code><modify_date>20260101</modify_date>"
               "<corp_name>예시전자</corp_name><corp_code>99900100</corp_code></list></result>")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("CORPCODE.xml", xml)
        self.assertEqual(R._dart_companies_in(buf.getvalue()), [("99900100", "예시전자", "9990A0")])
        self.assertEqual(R._dart_companies_in(code_file())[2], ("99900300", "다른 & 회사", "9990B0"))
        self.assertIsNone(R._dart_companies_in(b"not a zip"))

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
                                                 "fetched": res["dart"]["files"][0]["fetched"]}])
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

    # ---- access

    def test_cache_and_refresh(self):
        self.verdict(dart())
        self.assertEqual(self.paths(), ["/api/corpCode.xml", "/api/fnlttSinglAcntAll.json"])
        cache = self.dir / ".receipts" / "dart"
        self.assertEqual(sorted(p.relative_to(cache).as_posix() for p in cache.rglob("*") if p.is_file()),
                         ["corpCode.zip", "fetched.json", "fnlttSinglAcntAll/99900100_2025_11011_CFS.json"])
        self.assertEqual(sorted(json.loads((cache / "fetched.json").read_text())),
                         ["corpCode.zip", "fnlttSinglAcntAll/99900100_2025_11011_CFS.json"])
        self.server.seen.clear()
        os.environ.pop("RECEIPTS_DART_KEY")            # a cached answer needs no key and no request
        self.assertEqual(self.verdict(dart())[0], "verified")
        self.assertEqual(self.server.seen, [])
        os.environ["RECEIPTS_DART_KEY"] = KEY
        res = self.check(["자산총계는 5조원, 부채총계는 2조원이다."],
                         [{"id": "a", "line": 1, "quote": "자산총계는 5조원", "number": "5조원", "kind": "lookup",
                           "operands": {"of": dart()}},
                          {"id": "b", "line": 1, "quote": "부채총계는 2조원", "number": "2조원", "kind": "lookup",
                           "operands": {"of": dart("ifrs-full_Liabilities")}}], refresh=True)
        self.assertEqual([c["verdict"] for c in res["claims"]], ["verified", "verified"])
        self.assertEqual(self.paths(), ["/api/corpCode.xml", "/api/fnlttSinglAcntAll.json"])   # once per file

    def test_cache_folder_from_option_and_environment(self):
        other = self.dir / "elsewhere"
        self.check(["자산총계는 5조원이다."], [{"id": "a", "line": 1, "quote": "자산총계는 5조원", "number": "5조원",
                                          "kind": "lookup", "operands": {"of": dart()}}], dart_cache=str(other))
        self.assertTrue((other / "fnlttSinglAcntAll" / "99900100_2025_11011_CFS.json").exists())
        self.assertFalse((self.dir / ".receipts").exists())
        os.environ["RECEIPTS_DART_CACHE"] = str(self.dir / "from-env")
        self.verdict(dart())
        self.assertTrue((self.dir / "from-env" / "corpCode.zip").exists())

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
        for status in ("010", "011", "012", "020", "100", "101", "800", "900", "901"):
            self.server.status = status
            with self.assertRaises(R.StopError) as cm:
                self.verdict(dart(dart=CORP))
            self.assertIn(f"DART answered status {status} for fnlttSinglAcntAll.json (사유)", str(cm.exception))
        self.assertFalse((self.dir / ".receipts" / "dart" / "fnlttSinglAcntAll").exists())
        self.server.status = ""
        self.assertEqual(self.verdict(dart(dart=CORP))[0], "verified")

    def test_a_refused_key_stops(self):
        os.environ["RECEIPTS_DART_KEY"] = "x" * 40
        with self.assertRaises(R.StopError) as cm:
            self.verdict(dart(dart=CORP))
        self.assertIn("status 010", str(cm.exception))
        self.server.raw = b"<?xml version='1.0'?><result><status>020</status><message>limit</message></result>"
        with self.assertRaises(R.StopError) as cm:   # the code file answers an error as XML, not as a zip
            self.verdict(dart())
        self.assertIn("DART answered status 020 for corpCode.xml (limit)", str(cm.exception))

    def test_answers_that_are_not_what_dart_sends_stop(self):
        for raw in (b"<html>busy</html>", b"{}", b"[1]", b'{"status": "000"}', b'{"status": "000", "list": []}',
                    b'{"status": "000", "list": [1]}', b'{"status": 0, "list": [{}]}', b"\xff\xfe"):
            self.server.raw = raw
            with self.assertRaises(R.StopError, msg=raw):
                self.verdict(dart(dart=CORP))
        self.server.raw = b"PK\x03\x04 not a zip"
        with self.assertRaises(R.StopError):
            self.verdict(dart())
        self.assertFalse((self.dir / ".receipts" / "dart" / "corpCode.zip").exists())

    def test_a_corrupt_cache_stops(self):
        self.verdict(dart())
        cache = self.dir / ".receipts" / "dart"
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
        self.server.fail_with, self.server.fail_times = 503, 1
        self.assertEqual(self.verdict(dart(dart=CORP))[0], "verified")
        self.assertEqual(len(self.server.seen), 2)
        self.server.seen.clear()
        R._CACHE.clear()
        shutil.rmtree(self.dir / ".receipts")
        self.server.fail_with, self.server.fail_times = 500, None
        with self.assertRaises(R.StopError) as cm:
            self.verdict(dart(dart=CORP))
        self.assertEqual(str(cm.exception), "DART answered HTTP 500 for fnlttSinglAcntAll.json")
        self.assertEqual(len(self.server.seen), 2)
        self.server.seen.clear()
        self.server.fail_with = 404                      # not retried, and not an answer
        with self.assertRaises(R.StopError):
            self.verdict(dart(dart=CORP))
        self.assertEqual(len(self.server.seen), 1)

    def test_no_connection_stops(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        os.environ["RECEIPTS_DART_URL"] = f"http://127.0.0.1:{port}"
        with self.assertRaises(R.StopError) as cm:
            self.verdict(dart(dart=CORP))
        self.assertIn("cannot read DART (fnlttSinglAcntAll.json: URLError", str(cm.exception))
        os.environ["RECEIPTS_DART_URL"] = "ftp://127.0.0.1/x"
        with self.assertRaises(R.StopError):
            self.verdict(dart(dart=CORP))

    def test_a_redirect_to_another_host_is_not_followed(self):
        other = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        other.seen, other.fail_with, other.fail_times, other.raw, other.status, other.redirect_to = [], 0, None, None, "", None
        threading.Thread(target=other.serve_forever, daemon=True).start()
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
        self.assertTrue(all(q.get("crtfc_key") == KEY for _, q in self.server.seen))
        code, out, err = self.main("check", str(self.dir / "claims.json"))
        self.assertEqual(code, 0)
        code2, out2, err2 = self.main("dart", "accounts", STOCK, "--year", "2025", "--report", "annual", "--fs", "CFS",
                                      "--dart-cache", str(self.dir / ".receipts" / "dart"))
        self.assertEqual(code2, 0)
        for text in (json.dumps(res, ensure_ascii=False), out, err, out2, err2, self.written()):
            self.assertNotIn(KEY, text)
            self.assertNotIn("crtfc_key", text)

    def test_the_key_is_not_in_any_stop_message(self):
        messages = []
        for setup in ("status", "http", "raw", "port", "redirect"):
            self.setUp()
            if setup == "status":
                self.server.status = "020"
            elif setup == "http":
                self.server.fail_with = 500
            elif setup == "raw":
                self.server.raw = b"not json"
            elif setup == "port":
                with socket.socket() as s:
                    s.bind(("127.0.0.1", 0))
                    os.environ["RECEIPTS_DART_URL"] = f"http://127.0.0.1:{s.getsockname()[1]}"
            else:
                self.server.redirect_to = "http://127.0.0.1:9"
            (self.dir / "report.md").write_text("자산총계는 5조원이다.\n", encoding="utf-8")
            (self.dir / "claims.json").write_text(json.dumps({"document": "report.md", "claims": [
                {"id": "a", "line": 1, "quote": "자산총계는 5조원", "number": "5조원", "kind": "lookup",
                 "operands": {"of": dart()}}]}), encoding="utf-8")
            code, out, err = self.main("check", str(self.dir / "claims.json"))
            self.assertEqual(code, 2, setup)
            self.assertTrue(err.startswith("receipts: stopped: "), setup)
            messages.append(out + err + self.written())
        for text in messages:
            self.assertNotIn(KEY, text)
            self.assertNotIn("crtfc_key", text)
            self.assertNotIn("127.0.0.1", text)   # no request address in a message

    def test_a_message_that_carried_the_key_would_be_scrubbed(self):
        self.assertEqual(R._no_key(f"oops {KEY} oops"), "oops [key] oops")
        os.environ.pop("RECEIPTS_DART_KEY")
        self.assertEqual(R._no_key("nothing to hide"), "nothing to hide")

    # ---- the receipt

    def test_the_receipt_lists_the_dart_files(self):
        self.one("자산총계는 5조원이다.", "5조원", dart())
        code, out, err = self.main("check", str(self.dir / "claims.json"))
        self.assertEqual(code, 0)
        self.assertIn("### DART data used (2)", out)
        self.assertIn("| fnlttSinglAcntAll/99900100_2025_11011_CFS.json |", out)
        self.assertIn(f"| {ANNUAL} |", out)
        self.assertIn("Not read from DART's own address but from: " + self.url, out)
        saved = json.loads((self.dir / "receipts.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["dart"]["files"][1]["rcept_no"], ANNUAL)

    def test_exit_codes(self):
        claim = {"id": "a", "line": 1, "quote": "자산총계는 9조원", "number": "9조원", "kind": "lookup",
                 "operands": {"of": dart()}}
        (self.dir / "report.md").write_text("자산총계는 9조원이다.\n", encoding="utf-8")
        (self.dir / "claims.json").write_text(json.dumps({"document": "report.md", "claims": [claim]}), encoding="utf-8")
        self.assertEqual(self.main("check", str(self.dir / "claims.json"), "--quiet")[0], 1)
        self.server.status = "800"
        code, out, err = self.main("check", str(self.dir / "claims.json"), "--refresh")
        self.assertEqual(code, 2)
        self.assertIn("DART answered status 800", err)

    # ---- looking things up

    def test_company_lookup(self):
        os.chdir(self.dir)
        self.addCleanup(os.chdir, ROOT)
        code, out, _ = self.main("dart", "company", "예시전자")
        got = json.loads(out)
        self.assertEqual((code, got["matched"]), (0, 2))
        self.assertEqual(got["companies"], [{"corp_code": CORP, "name": "예시전자", "stock_code": STOCK},
                                            {"corp_code": "99900200", "name": "예시전자서비스", "stock_code": None}])
        self.assertEqual(json.loads(self.main("dart", "company", "9990a0")[1])["companies"][0]["corp_code"], CORP)
        self.assertEqual(json.loads(self.main("dart", "company", "99900300")[1])["companies"][0]["name"], "다른 & 회사")
        self.assertEqual(json.loads(self.main("dart", "company", "같은코드", "--limit", "1")[1])["matched"], 2)
        code, out, err = self.main("dart", "company", "없는회사")
        self.assertEqual((code, out), (2, ""))
        self.assertTrue((self.dir / ".receipts" / "dart" / "corpCode.zip").exists())

    def test_accounts_lookup(self):
        cache = str(self.dir / "c")
        code, out, _ = self.main("dart", "accounts", STOCK, "--year", "2026", "--report", "q3", "--fs", "CFS",
                                 "--statement", "IS", "--dart-cache", cache)
        got = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual((got["corp_code"], got["rcept_no"], got["currency"], got["matched"]), (CORP, Q3, "KRW", 1))
        self.assertEqual(got["rows"], [{"statement": "IS", "account": "ifrs-full_Revenue", "name": "매출액", "columns": {
            "thstrm_amount": {"value": "350000000000", "period": "제 11 기 3분기", "holds": "the three months"},
            "thstrm_add_amount": {"value": "1020000000000", "period": "제 11 기 3분기",
                                  "holds": "from the start of the business year to the period end"},
            "frmtrm_q_amount": {"value": "300000000000", "period": "제 10 기 3분기",
                                "holds": "the same three months a year earlier"},
            "frmtrm_add_amount": {"value": "900000000000", "period": "제 10 기 3분기",
                                  "holds": "the same cumulative period a year earlier"}}}])
        annual = ["dart", "accounts", CORP, "--year", "2025", "--report", "annual", "--fs", "CFS", "--dart-cache", cache]
        got = json.loads(self.main(*annual)[1])
        self.assertEqual((got["matched"], got["rows_not_listed_other_statements"]), (12, 1))
        self.assertIsNone(next(r for r in got["rows"] if r["name"] == "매각예정분류")["account"])
        self.assertEqual(json.loads(self.main(*annual, "--match", "기타", "--limit", "2")[1])["matched"], 3)
        self.assertEqual(len(json.loads(self.main(*annual, "--match", "기타", "--limit", "2")[1])["rows"]), 2)
        self.assertEqual(json.loads(self.main(*annual, "--match", "ASSETS")[1])["matched"], 1)
        for bad in (["dart", "accounts", CORP, "--year", "2031", "--report", "annual", "--fs", "CFS"],
                    ["dart", "accounts", CORP, "--year", "2025", "--report", "annual"],
                    ["dart", "accounts", CORP, "--year", "2025", "--report", "annual", "--fs", "XXX"],
                    ["dart", "accounts", "9990Z9", "--year", "2025", "--report", "annual", "--fs", "CFS"],
                    ["dart", "accounts", CORP, "--year", "2025", "--report", "annual", "--fs", "CFS", "--statement", "SCE"]):
            code, out, err = self.main(*bad, "--dart-cache", cache)
            self.assertEqual((code, out), (2, ""), bad)


if __name__ == "__main__":
    unittest.main()
