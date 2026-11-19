"""Tests for the SEC EDGAR connection, against a local server that stands in for the SEC.

No request leaves the machine. The company is made up. Run: python -m unittest discover -s tests
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
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "receipts" / "scripts" / "receipts.py"
_spec = importlib.util.spec_from_file_location("receipts_edgar", SCRIPT)
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

AGENT = "receipts-tests local-server-only"
CIK = 999001
K24, K25 = "0000999001-25-000002", "0000999001-26-000002"   # annual reports for 2024 and 2025
Q1, Q2 = "0000999001-26-000010", "0000999001-26-000015"     # quarterly reports of 2026


def fact(val, end, accn, form, filed, start=None):
    row = {"end": end, "val": val, "accn": accn, "fy": 2026, "fp": "FY", "form": form, "filed": filed}
    if start:
        row = {"start": start, **row}
    return row


FACTS = {
    "cik": CIK, "entityName": "Example Corp",
    "facts": {
        "dei": {"EntityCommonStockSharesOutstanding": {"label": "Shares Outstanding", "description": "", "units": {
            "shares": [fact(410000000, "2026-01-31", K25, "10-K", "2026-02-20")]}}},
        "us-gaap": {
            "Assets": {"label": "Assets", "description": "", "units": {"USD": [
                fact(4200000000, "2024-12-31", K24, "10-K", "2025-02-21"),
                fact(4150000000, "2024-12-31", K25, "10-K", "2026-02-20"),   # restated a year later
                fact(5000000000, "2025-12-31", K25, "10-K", "2026-02-20"),
                fact(5000000000, "2025-12-31", Q1, "10-Q", "2026-05-05"),
                fact(5300000000, "2026-06-30", Q2, "10-Q", "2026-08-04")]}},
            "Revenues": {"label": "Revenues", "description": "", "units": {"USD": [
                fact(1100000000, "2024-12-31", K25, "10-K", "2026-02-20", "2024-01-01"),
                fact(1234500000, "2025-12-31", K25, "10-K", "2026-02-20", "2025-01-01"),
                fact(320000000, "2026-03-31", Q1, "10-Q", "2026-05-05", "2026-01-01"),
                fact(350000000, "2026-06-30", Q2, "10-Q", "2026-08-04", "2026-04-01"),
                fact(670000000, "2026-06-30", Q2, "10-Q", "2026-08-04", "2026-01-01")]}},
            "NetIncomeLoss": {"label": "Net Income (Loss)", "description": "", "units": {"USD": [
                fact(-45000000, "2025-12-31", K25, "10-K", "2026-02-20", "2025-01-01")]}},
            "EarningsPerShareDiluted": {"label": "Earnings Per Share, Diluted", "description": "", "units": {"USD/shares": [
                fact(2.31, "2025-12-31", K25, "10-K", "2026-02-20", "2025-01-01")]}},
            "DebtInstrumentFaceAmount": {"label": "Debt Instrument, Face Amount", "description": "", "units": {
                "USD": [fact(750000000, "2025-12-31", K25, "10-K", "2026-02-20")],
                "EUR": [fact(500000000, "2025-12-31", K25, "10-K", "2026-02-20")]}},
            "Liabilities": {"label": "Liabilities", "description": "", "units": {"USD": [
                fact(2000000000, "2024-12-31", K24, "10-K", "2025-02-21"),
                fact(2050000000, "2024-12-31", "0000999001-25-000007", "10-K/A", "2025-03-01"),
                fact(2100000000, "2024-12-31", "0000999001-25-000008", "10-Q", "2025-03-01")]}},   # two on one day
        },
    },
}
TICKERS = {"0": {"cik_str": 1, "ticker": "OTHR", "title": "Other Inc."},
           "1": {"cik_str": CIK, "ticker": "EXMP", "title": "Example Corp"},
           "2": {"cik_str": 999002, "ticker": "EX-B", "title": "No Facts Corp"}}
PATHS = {"/files/company_tickers.json": TICKERS, f"/api/xbrl/companyfacts/CIK{CIK:010d}.json": FACTS}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        srv = self.server
        srv.seen.append((self.path, self.headers.get("User-Agent")))
        if srv.fail_with:
            self.send_response(srv.fail_with)
            self.end_headers()
            return
        if self.path not in PATHS:
            self.send_response(404)
            self.end_headers()
            return
        body = srv.raw if srv.raw is not None else json.dumps(PATHS[self.path]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if srv.zipped and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body = gzip.compress(body)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def edgar(period, concept="us-gaap:Assets", **extra):
    loc = {"edgar": "EXMP", "concept": concept, "unit": "USD", "period": period}
    loc.update(extra)
    return {k: v for k, v in loc.items() if v is not None}


class EdgarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.seen, cls.server.fail_with, cls.server.raw, cls.server.zipped = [], 0, None, False
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.saved_gap, R.EDGAR_GAP = R.EDGAR_GAP, 0.0
        cls.saved_sleep, R.time.sleep = R.time.sleep, lambda s: None   # no waiting in tests

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        R.EDGAR_GAP, R.time.sleep = cls.saved_gap, cls.saved_sleep

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="receipts-edgar-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.server.seen.clear()
        self.server.fail_with, self.server.raw, self.server.zipped = 0, None, False
        self.saved_env = dict(os.environ)
        self.addCleanup(self.restore_env)
        os.environ.update({"RECEIPTS_SEC_USER_AGENT": AGENT, "RECEIPTS_EDGAR_DATA_URL": self.url,
                           "RECEIPTS_EDGAR_WWW_URL": self.url})
        os.environ.pop("RECEIPTS_EDGAR_CACHE", None)
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

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = R.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    # ---- values

    def test_lookup_by_ticker_and_by_cik(self):
        row, res = self.one("Total assets were $5.0 billion.", "$5.0 billion", edgar("2025-12-31"))
        self.assertEqual((row["verdict"], row["reason"]), ("verified", "match"))
        self.assertIn("EDGAR EXMP (Example Corp, CIK 999001) us-gaap:Assets [2025-12-31] USD = 5000000000", row["sources"][0])
        self.assertIn("same in 2 filings", row["sources"][0])
        self.assertEqual([f["file"] for f in res["edgar"]["files"]], ["company_tickers.json", "companyfacts/CIK0000999001.json"])
        for ident in ("999001", "0000999001", "CIK0000999001", 999001, "exmp"):
            row, _ = self.one("Total assets were $5.0 billion.", "$5.0 billion", edgar("2025-12-31", edgar=ident))
            self.assertEqual(row["verdict"], "verified", ident)

    def test_wrong_number_is_wrong(self):
        row, _ = self.one("Revenue was $1,334.5 million in 2025.", "$1,334.5 million",
                          edgar("2025-01-01..2025-12-31", "us-gaap:Revenues"))
        self.assertEqual((row["verdict"], row["recomputed_raw"]), ("wrong", "1234500000"))

    def test_three_months_and_six_months_are_two_periods(self):
        q, _ = self.one("Second-quarter revenue was $350 million.", "$350 million",
                        edgar("2026-04-01..2026-06-30", "us-gaap:Revenues"))
        h, _ = self.one("First-half revenue was $670 million.", "$670 million",
                        edgar("2026-01-01..2026-06-30", "us-gaap:Revenues"))
        mixed, _ = self.one("Second-quarter revenue was $670 million.", "$670 million",
                            edgar("2026-04-01..2026-06-30", "us-gaap:Revenues"))
        self.assertEqual((q["verdict"], h["verdict"], mixed["verdict"]), ("verified", "verified", "wrong"))

    def test_balance_and_flow_do_not_cross(self):
        a, _ = self.one("Revenue was $350 million.", "$350 million", edgar("2026-06-30", "us-gaap:Revenues"))
        b, _ = self.one("Assets were $5.3 billion.", "$5.3 billion", edgar("2026-01-01..2026-06-30"))
        self.assertEqual((a["reason"], b["reason"]), ("locator_no_match", "locator_no_match"))

    def test_negative_per_share_and_shares(self):
        loss, _ = self.one("The net result was -$45 million.", "-$45 million",
                           edgar("2025-01-01..2025-12-31", "us-gaap:NetIncomeLoss"))
        eps, _ = self.one("Diluted EPS was $2.31.", "$2.31",
                          edgar("2025-01-01..2025-12-31", "us-gaap:EarningsPerShareDiluted", unit="USD/shares"))
        shares, _ = self.one("There were 410 million shares.", "410 million",
                             edgar("2026-01-31", "dei:EntityCommonStockSharesOutstanding", unit=None))
        self.assertEqual([r["verdict"] for r in (loss, eps, shares)], ["verified"] * 3)

    def test_formulas_over_edgar_facts(self):
        claims = [
            {"id": "g", "line": 1, "quote": "Revenue grew 12.2% in 2025", "number": "12.2%", "kind": "derived",
             "formula": "pct_change", "operands": {"new": edgar("2025-01-01..2025-12-31", "us-gaap:Revenues"),
                                                   "old": edgar("2024-01-01..2024-12-31", "us-gaap:Revenues")}},
            {"id": "s", "line": 2, "quote": "first-half revenue of $670 million", "number": "$670 million", "kind": "derived",
             "formula": "sum", "operands": {"items": [edgar("2026-01-01..2026-03-31", "us-gaap:Revenues"),
                                                      edgar("2026-04-01..2026-06-30", "us-gaap:Revenues")]}},
            {"id": "m", "line": 3, "quote": "assets of 5,000 (USD millions)", "number": "5,000", "kind": "lookup",
             "operands": {"of": edgar("2025-12-31", multiplier="0.000001")}},
            {"id": "w", "line": 4, "quote": "adds up to $1.0 billion", "number": "$1.0 billion", "kind": "derived",
             "formula": "sum", "operands": {"cells": {"edgar": "EXMP", "concept": "us-gaap:Revenues", "where": {}}}},
        ]
        res = self.check(["Revenue grew 12.2% in 2025.", "That left first-half revenue of $670 million.",
                          "The table shows assets of 5,000 (USD millions).", "It adds up to $1.0 billion."], claims)
        got = {r["id"]: (r["verdict"], r["reason"]) for r in res["claims"]}
        self.assertEqual(got, {"g": ("verified", "match"), "s": ("verified", "match"), "m": ("verified", "match"),
                               "w": ("unverifiable", "unsupported")})
        self.assertEqual(res["multipliers"], [{"file": "EDGAR EXMP", "column": "us-gaap:Assets",
                                               "multiplier": "0.000001", "claims": 1}])

    # ---- the same fact in several filings

    def test_restated_fact_needs_a_choice(self):
        sentence = "Assets were $4,200 million at the end of 2024."
        open_, _ = self.one(sentence, "$4,200 million", edgar("2024-12-31"))
        self.assertEqual((open_["verdict"], open_["reason"]), ("unverifiable", "edgar_versions_differ"))
        self.assertIn("4200000000", open_["note"])
        self.assertIn("4150000000", open_["note"])
        first, _ = self.one(sentence, "$4,200 million", edgar("2024-12-31", version="first"))
        latest, _ = self.one(sentence, "$4,200 million", edgar("2024-12-31", version="latest"))
        pinned, _ = self.one(sentence, "$4,200 million", edgar("2024-12-31", accession=K24))
        pinned_new, _ = self.one(sentence, "$4,200 million", edgar("2024-12-31", accession=K25.replace("-", "")))
        self.assertEqual([r["verdict"] for r in (first, latest, pinned, pinned_new)],
                         ["verified", "wrong", "verified", "wrong"])
        self.assertIn("other filings give another value", first["sources"][0])
        self.assertIn(K24, pinned["sources"][0])

    def test_two_reports_on_the_same_day_stay_undecided(self):
        row, _ = self.one("Liabilities were $2.1 billion.", "$2.1 billion",
                          edgar("2024-12-31", "us-gaap:Liabilities", version="latest"))
        self.assertEqual(row["reason"], "edgar_versions_differ")
        row, _ = self.one("Liabilities were $2.0 billion.", "$2.0 billion",
                          edgar("2024-12-31", "us-gaap:Liabilities", version="first"))
        self.assertEqual(row["verdict"], "verified")

    # ---- locators that match nothing

    def test_no_match_and_multiple(self):
        s, n = "Assets were $5.0 billion.", "$5.0 billion"
        cases = {
            "unknown concept": (edgar("2025-12-31", "us-gaap:AssetsZz"), "locator_no_match"),
            "no taxonomy": (edgar("2025-12-31", "Assets"), "locator_no_match"),
            "no fact on that day": (edgar("2026-01-03"), "locator_no_match"),
            "unit the concept lacks": (edgar("2025-12-31", unit="shares"), "locator_no_match"),
            "two units, none named": (edgar("2025-12-31", "us-gaap:DebtInstrumentFaceAmount", unit=None), "locator_multiple"),
            "unknown ticker": (edgar("2025-12-31", edgar="NOPE"), "locator_no_match"),
            "company without facts": (edgar("2025-12-31", edgar="EX.B"), "locator_no_match"),
            "bad period": (edgar("FY2025"), "locator_no_match"),
            "no period": (edgar(None), "locator_no_match"),
            "bad accession": (edgar("2025-12-31", accession="26-000002"), "locator_no_match"),
            "filing without the fact": (edgar("2025-12-31", accession=K24), "locator_no_match"),
            "bad version": (edgar("2025-12-31", version="newest"), "locator_no_match"),
            "rows": ({"edgar": "EXMP", "concept": "us-gaap:Assets", "period": "2025-12-31", "rows": {}}, "unsupported"),
        }
        for name, (loc, reason) in cases.items():
            row, _ = self.one(s, n, loc)
            self.assertEqual((row["verdict"], row["reason"]), ("unverifiable", reason), name)
        row, _ = self.one(s, n, edgar("2025-12-31", "us-gaap:DebtInstrumentFaceAmount", unit="eur"))
        self.assertEqual(row["verdict"], "wrong")   # 500 million euros, found without regard to case

    # ---- access

    def test_fetch_once_then_use_the_cache(self):
        self.one("Assets were $5.0 billion.", "$5.0 billion", edgar("2025-12-31"))
        self.assertEqual([p for p, _ in self.server.seen],
                         ["/files/company_tickers.json", f"/api/xbrl/companyfacts/CIK{CIK:010d}.json"])
        self.assertTrue(all(agent == AGENT for _, agent in self.server.seen))
        self.assertTrue((self.dir / ".receipts" / "edgar" / "companyfacts" / f"CIK{CIK:010d}.json").exists())
        self.server.seen.clear()
        del os.environ["RECEIPTS_SEC_USER_AGENT"]            # a filled cache needs no request and no contact string
        row, _ = self.one("Assets were $5.0 billion.", "$5.0 billion", edgar("2025-12-31"))
        self.assertEqual((row["verdict"], self.server.seen), ("verified", []))

    def test_refresh_fetches_each_file_once(self):
        claims = [{"id": f"c{i}", "line": i, "quote": "Assets were $5.0 billion.", "number": "$5.0 billion",
                   "kind": "lookup", "operands": {"of": edgar("2025-12-31")}} for i in (1, 2)]
        self.check(["Assets were $5.0 billion."] * 2, claims)
        self.server.seen.clear()
        self.check(["Assets were $5.0 billion."] * 2, claims, refresh=True)
        self.assertEqual(len(self.server.seen), 2)

    def test_cache_folder_option_and_variable(self):
        other = self.dir / "elsewhere"
        self.check(["Assets were $5.0 billion."], [{"id": "c1", "line": 1, "quote": "Assets were $5.0 billion.",
                   "number": "$5.0 billion", "kind": "lookup", "operands": {"of": edgar("2025-12-31")}}],
                   edgar_cache=str(other))
        self.assertTrue((other / "company_tickers.json").exists())
        self.assertFalse((self.dir / ".receipts").exists())
        os.environ["RECEIPTS_EDGAR_CACHE"] = str(self.dir / "by-variable")
        self.one("Assets were $5.0 billion.", "$5.0 billion", edgar("2025-12-31"))
        self.assertTrue((self.dir / "by-variable" / "company_tickers.json").exists())

    def test_gzip_answer(self):
        self.server.zipped = True
        row, _ = self.one("Assets were $5.0 billion.", "$5.0 billion", edgar("2025-12-31", edgar=CIK))
        self.assertEqual(row["verdict"], "verified")

    def test_no_contact_string_stops_without_a_request(self):
        del os.environ["RECEIPTS_SEC_USER_AGENT"]
        with self.assertRaises(R.StopError) as ctx:
            self.one("Assets were $5.0 billion.", "$5.0 billion", edgar("2025-12-31"))
        self.assertIn("RECEIPTS_SEC_USER_AGENT", str(ctx.exception))
        self.assertEqual(self.server.seen, [])
        code, _, err = self.main("check", str(self.dir / "claims.json"), "--quiet")
        self.assertEqual((code, self.server.seen), (2, []))
        self.assertFalse((self.dir / "receipts.json").exists())

    def test_unreachable_server_stops(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            dead = f"http://127.0.0.1:{s.getsockname()[1]}"
        os.environ.update({"RECEIPTS_EDGAR_DATA_URL": dead, "RECEIPTS_EDGAR_WWW_URL": dead})
        with self.assertRaises(R.StopError):
            self.one("Assets were $5.0 billion.", "$5.0 billion", edgar("2025-12-31", edgar=CIK))

    def test_http_errors_and_bad_answers_stop(self):
        for code in (403, 500):
            self.server.fail_with = code
            with self.assertRaises(R.StopError, msg=code):
                self.one("Assets were $5.0 billion.", "$5.0 billion", edgar("2025-12-31", edgar=CIK))
        self.assertEqual(len(self.server.seen), 3)            # 403 once, 500 tried twice
        self.server.fail_with = 0
        for raw in (b"<html>busy</html>", b'{"facts": 3}', b"[]"):
            self.server.raw = raw
            with self.assertRaises(R.StopError, msg=raw):
                self.one("Assets were $5.0 billion.", "$5.0 billion", edgar("2025-12-31", edgar=CIK))
        self.assertFalse((self.dir / ".receipts" / "edgar" / "companyfacts" / f"CIK{CIK:010d}.json").exists())

    def test_corrupt_cache_stops(self):
        path = self.dir / ".receipts" / "edgar" / "companyfacts" / f"CIK{CIK:010d}.json"
        path.parent.mkdir(parents=True)
        for raw in ('{"cik": 999001, "facts": {', '{"cik": 999001}'):
            path.write_text(raw, encoding="utf-8")
            with self.assertRaises(R.StopError, msg=raw):
                self.one("Assets were $5.0 billion.", "$5.0 billion", edgar("2025-12-31", edgar=CIK))
        self.assertEqual(self.server.seen, [])

    def test_receipt_shows_dates_and_address_but_never_the_contact_string(self):
        self.one("Assets were $5.0 billion.", "$5.0 billion", edgar("2025-12-31"))
        code, out, _ = self.main("check", str(self.dir / "claims.json"))
        self.assertEqual(code, 0)
        self.assertIn("### EDGAR data used (2)", out)
        self.assertIn(self.url, out)
        res = json.loads((self.dir / "receipts.json").read_text(encoding="utf-8"))
        self.assertRegex(res["edgar"]["files"][0]["fetched"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(res["edgar"]["addresses"], [self.url, self.url])
        for p in self.dir.rglob("*"):
            if p.is_file():
                self.assertNotIn(AGENT.encode(), p.read_bytes(), p)

    # ---- look-up commands

    def test_lookup_commands(self):
        cache = str(self.dir / "cache")
        code, out, _ = self.main("edgar", "company", "EXMP", "--edgar-cache", cache)
        self.assertEqual((code, json.loads(out)["cik"], json.loads(out)["name"]), (0, CIK, "Example Corp"))
        code, out, _ = self.main("edgar", "concepts", "EXMP", "--match", "income", "--edgar-cache", cache)
        got = json.loads(out)
        self.assertEqual([c["concept"] for c in got["concepts"]], ["us-gaap:NetIncomeLoss"])
        self.assertEqual((got["concepts"][0]["units"], got["concepts"][0]["latest_end"]), (["USD"], "2025-12-31"))
        code, out, _ = self.main("edgar", "concepts", "999001", "--limit", "2", "--edgar-cache", cache)
        self.assertEqual((json.loads(out)["matched"], len(json.loads(out)["concepts"])), (7, 2))
        code, out, _ = self.main("edgar", "facts", "EXMP", "us-gaap:Assets", "--until", "2024-12-31", "--edgar-cache", cache)
        rows = json.loads(out)["facts"]
        self.assertEqual([(r["period"], r["value"], r["filings_differ"]) for r in rows],
                         [("2024-12-31", "4200000000", True), ("2024-12-31", "4150000000", True)])
        code, out, _ = self.main("edgar", "facts", "EXMP", "us-gaap:Revenues", "--since", "2026-06-30", "--edgar-cache", cache)
        self.assertEqual([r["period"] for r in json.loads(out)["facts"]],
                         ["2026-01-01..2026-06-30", "2026-04-01..2026-06-30"])
        for argv in (("edgar", "facts", "EXMP"), ("edgar", "facts", "EXMP", "us-gaap:Nope"), ("edgar", "company", "NOPE")):
            code, out, err = self.main(*argv, "--edgar-cache", cache)
            self.assertEqual((code, out), (2, ""), argv)


if __name__ == "__main__":
    unittest.main()
