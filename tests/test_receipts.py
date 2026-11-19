"""Unit tests for skills/receipts/scripts/receipts.py. Run: python -m unittest discover -s tests"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import tempfile
import unittest
import zipfile
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "receipts" / "scripts" / "receipts.py"
_spec = importlib.util.spec_from_file_location("receipts", SCRIPT)
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

SALES = "year,region,revenue,orders\n2024,East,972000,410\n2024,West,655000,298\n2025,East,1088000,455\n2025,West,612000,301\n"


def scan(text):
    nums, skipped = R.scan_lines(text.splitlines())
    return [t.text for t in nums], {(t.text, t.rule) for t in skipped}


def cell(year, region, column="revenue", **extra):
    loc = {"file": "sales.csv", "row": {"year": str(year), "region": region}, "column": column}
    loc.update(extra)
    return loc


class WrittenTest(unittest.TestCase):
    def test_values_quanta_units(self):
        cases = [
            ("12.4%", "12.4", "0.1", "percent"), ("12 percent", "12", "1", "percent"),
            ("$1.2M", "1200000", "100000", "plain"), ("1.2 million", "1200000", "100000", "plain"),
            ("$3.4B", "3400000000", "100000000", "plain"), ("2.5 billion", "2500000000", "100000000", "plain"),
            ("$5K", "5000", "1000", "plain"), ("1,234,567", "1234567", "1", "plain"),
            ("-3.2%", "-3.2", "0.1", "percent"), ("−3.2%", "-3.2", "0.1", "percent"),
            ("3조 2,000억", "3200000000000", "100000000", "plain"), ("2천억", "200000000000", "100000000000", "plain"),
            ("1,234만", "12340000", "10000", "plain"), ("3.4조", "3400000000000", "100000000000", "plain"),
            ("1.8x", "1.8", "0.1", "multiple"), ("1.8배", "1.8", "0.1", "multiple"),
            ("45 bp", "45", "1", "bp"), ("1.5%p", "1.5", "0.1", "pp"), ("2.0 percentage points", "2.0", "0.1", "pp"),
        ]
        for text, value, q, unit in cases:
            w = R.Written(text)
            self.assertEqual((w.value, w.q, w.unit), (Decimal(value), Decimal(q), unit), text)

    def test_signed_flag(self):
        self.assertTrue(R.Written("-3.2%").signed)
        self.assertTrue(R.Written("+1.5%p").signed)
        self.assertFalse(R.Written("3.2%").signed)

    def test_hedge_only_for_integers_ending_in_zeros(self):
        w = R.Written("1,200,000"); w.hedge(); self.assertEqual(w.q, Decimal(100000))
        w = R.Written("1,234,567"); w.hedge(); self.assertEqual(w.q, Decimal(1))
        w = R.Written("$1.2M"); w.hedge(); self.assertEqual(w.q, Decimal(100000))
        w = R.Written("12.0%"); w.hedge(); self.assertEqual(w.q, Decimal("0.1"))

    def test_compare(self):
        d = Decimal
        self.assertEqual(R.compare(d("11.9"), d("0.1"), d("11.934"), "default"), "match")
        self.assertEqual(R.compare(d("11.9"), d("0.1"), d("11.96"), "default"), "truncated")
        self.assertIsNone(R.compare(d("11.9"), d("0.1"), d("11.96"), "strict"))
        self.assertEqual(R.compare(d("0.12"), d("0.01"), d("0.125"), "strict"), "match")  # half-even
        self.assertEqual(R.compare(d("0.13"), d("0.01"), d("0.125"), "strict"), "match")  # half-up
        self.assertEqual(R.compare(d("-3.2"), d("0.1"), d("-3.27"), "default"), "truncated")  # toward zero
        self.assertIsNone(R.compare(d("12.4"), d("0.1"), d("11.934"), "default"))


class ScanTest(unittest.TestCase):
    def test_years_and_amounts(self):
        nums, skipped = scan("Revenue was $1.2M in 2025, up 12.4% from 2024.")
        self.assertEqual(nums, ["$1.2M", "12.4%"])
        self.assertEqual(skipped, {("2025", "year"), ("2024", "year")})
        self.assertEqual(scan("It rose from 2024 to 2025 and in 2023-2024.")[0], ["2024", "2025"])  # amounts after "rose from"
        self.assertEqual(scan("Between 2023-2024 and from 2019 to 2021 nothing changed.")[0], [])
        self.assertEqual(scan("Costs fell by 2000 last month.")[0], ["2000"])
        self.assertEqual(scan("We sold 2000 units in 2025.")[0], ["2000"])
        self.assertEqual(scan("The 2025 plan lists 1950 as a base.")[0], ["2025", "1950"])  # no year context: counted
        self.assertEqual(scan("2025년 매출은 3조 2,000억 원, 직원은 2000명이다.")[0], ["3조 2,000억", "2000"])

    def test_dates_quarters_times_versions(self):
        text = ("Written 2026-11-19 at 14:30. In Q3 2025 we opened 12 stores; FY2025 and 3분기 look alike.\n"
                "2026. 11. 19. 기준, 2025년 3월 5일과 11월 실적. Released v1.2.3 (was 1.2.2) on March 3, 2026.")
        nums, skipped = scan(text)
        self.assertEqual(nums, ["12"])
        rules = {r for _, r in skipped}
        self.assertEqual(rules, {"date", "time", "version"})

    def test_identifiers_ordinals_markers_code_urls(self):
        text = ("## 2. Results\n"
                "1. CO2 fell 3% and COVID-19 cases rose 5%; see sales_2025.csv and [3].\n"
                "(2) The 1st and 2nd plants, 제3공장 and the 3번째 line use `x = 42` and https://example.com/2025/q3?id=123.\n"
                "```\nrows = 500\n```\n"
                "A 5G phone weighs 190g and costs $5K.")
        nums, skipped = scan(text)
        self.assertEqual(nums, ["3%", "5%", "190", "$5K"])
        rules = {r for _, r in skipped}
        self.assertEqual(rules, {"heading_number", "list_marker", "identifier", "reference", "ordinal", "code", "url"})

    def test_token_shapes(self):
        self.assertEqual(scan("Margins of 12.4 % (−3.2%) in a 10-15% band, about $2,250, 약 1,200만 명, 1.8배.")[0],
                         ["12.4 %", "−3.2%", "10", "15%", "$2,250", "1,200만", "1.8배"])

    def test_korean_particles_do_not_break_skip_rules(self):
        text = ("2026-11-19에 작성했고 11월에는 v3.0.9에서 14:30에 시작했다. 3분기에도, 2025년에는 FY2025와 같다.\n"
                "이익률은 0.86였다. 직원은 3명이고 매출은 12,345건이었다. 버전은 1.2.3.")
        nums, skipped = scan(text)
        self.assertEqual(nums, ["0.86", "3", "12,345"])
        self.assertEqual({r for _, r in skipped}, {"date", "version", "time", "year"})

    def test_offsets(self):
        nums, _ = R.scan_lines(["Total: 1,204 units"])
        self.assertEqual((nums[0].line, nums[0].start, nums[0].end, nums[0].text), (1, 7, 12, "1,204"))


class CheckCase(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="receipts-test-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        R._CACHE.clear()

    def write(self, name, content):
        p = self.dir / name
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8", newline="\n")
        return p

    def run_check(self, doc, claims, sources=None, mode="default"):
        self.write("report.md", doc)
        for name, content in (sources or {"sales.csv": SALES}).items():
            self.write(name, content)
        plan = self.write("claims.json", json.dumps({"document": "report.md", "claims": claims}, ensure_ascii=False))
        return R.run_check(str(plan), mode)

    def one(self, sentence, claim, sources=None, mode="default"):
        claim = dict({"id": "c1", "line": 1, "quote": sentence}, **claim)
        res = self.run_check(sentence + "\n", [claim], sources, mode)
        self.assertEqual(sum(res["counts"][k] for k in ("verified", "wrong", "unverifiable", "uncovered")),
                         res["counts"]["numbers"])
        return res["claims"][0] if res["claims"] else res


class VerdictTest(CheckCase):
    def test_lookup_and_formulas(self):
        pc = {"new": cell(2025, "East"), "old": cell(2024, "East")}
        self.assertEqual(self.one("East grew 11.9%.", {"number": "11.9%", "kind": "derived", "formula": "pct_change", "operands": pc})["verdict"], "verified")
        r = self.one("East grew 12.4%.", {"number": "12.4%", "kind": "derived", "formula": "pct_change", "operands": pc})
        self.assertEqual((r["verdict"], r["reason"], r["recomputed"]), ("wrong", "mismatch", "11.9%"))
        self.assertEqual(self.one("East revenue was $1.1M.", {"number": "$1.1M", "kind": "lookup", "operands": {"of": cell(2025, "East")}})["verdict"], "verified")
        self.assertEqual(self.one("The gap was 476,000.", {"number": "476,000", "kind": "derived", "formula": "difference",
                                                           "operands": {"a": cell(2025, "East"), "b": cell(2025, "West")}})["verdict"], "verified")
        self.assertEqual(self.one("East is 1.78x West.", {"number": "1.78x", "kind": "derived", "formula": "ratio",
                                                          "operands": {"num": cell(2025, "East"), "den": cell(2025, "West")}})["verdict"], "verified")
        many = {"file": "sales.csv", "where": {"year": "2025"}, "column": "revenue"}
        self.assertEqual(self.one("East made 64.0% of it.", {"number": "64.0%", "kind": "derived", "formula": "share",
                                                             "operands": {"part": cell(2025, "East"), "whole": many}})["verdict"], "verified")
        for formula, text in (("sum", "1,700,000"), ("mean", "850,000"), ("min", "612,000"), ("max", "1,088,000")):
            self.assertEqual(self.one(f"It was {text}.", {"number": text, "kind": "derived", "formula": formula, "operands": {"cells": many}})["verdict"], "verified", formula)
        self.assertEqual(self.one("It was 1,700,000.", {"number": "1,700,000", "kind": "derived", "formula": "sum",
                                                        "operands": {"items": [cell(2025, "East"), cell(2025, "West")]}})["verdict"], "verified")
        rows = {"file": "sales.csv", "where": {"region": "East"}, "test": {"column": "orders", "op": ">=", "value": "450"}}
        self.assertEqual(self.one("There was 1 such year.", {"number": "1", "kind": "derived", "formula": "count", "operands": {"rows": rows}})["verdict"], "verified")

    def test_summed_slots(self):
        tot = lambda y: {"file": "sales.csv", "where": {"year": str(y)}, "column": "revenue"}
        r = self.one("Total revenue grew 4.5%.", {"number": "4.5%", "kind": "derived", "formula": "pct_change",
                                                  "operands": {"new": tot(2025), "old": tot(2024)}})
        self.assertEqual(r["verdict"], "verified")
        self.assertIn("summed", r["sources"][0])

    def test_truncation_and_strict_mode(self):
        pc = {"new": cell(2025, "West"), "old": cell(2024, "West")}  # -6.5649%
        c = {"number": "-6.5%", "kind": "derived", "formula": "pct_change", "operands": pc}
        r = self.one("West changed by -6.5%.", c)
        self.assertEqual((r["verdict"], r["reason"]), ("verified", "truncated"))
        self.assertEqual(self.one("West changed by -6.5%.", c, mode="strict")["verdict"], "wrong")

    def test_direction(self):
        pc = {"new": cell(2025, "West"), "old": cell(2024, "West")}
        base = {"number": "6.6%", "kind": "derived", "formula": "pct_change", "operands": pc}
        self.assertEqual(self.one("West fell 6.6%.", base)["verdict"], "verified")
        r = self.one("West rose 6.6%.", base)
        self.assertEqual((r["verdict"], r["reason"]), ("wrong", "direction_mismatch"))
        self.assertEqual(self.one("West moved 6.6%.", dict(base, direction="down"))["verdict"], "verified")
        self.assertEqual(self.one("West moved 6.6%.", base)["verdict"], "wrong")  # unsigned, no direction: signed compare
        self.assertEqual(self.one("West fell 6.6%.", dict(base, direction="up"))["reason"], "direction_conflict")
        self.assertEqual(self.one("서부 매출은 6.6% 줄었다.", base)["verdict"], "verified")
        signed = dict(base, number="-6.6%")
        self.assertEqual(self.one("West growth was -6.6%.", signed)["verdict"], "verified")  # sign wins over "growth"
        self.assertEqual(self.one("오늘 서부 매출 증감률은 -6.6%다.", signed)["verdict"], "verified")

    def test_units(self):
        src = {"m.csv": "year,margin\n2024,23.1\n2025,24.4\n"}
        a, b = ({"file": "m.csv", "row": {"year": y}, "column": "margin"} for y in ("2025", "2024"))
        self.assertEqual(self.one("Margin was 24.4%.", {"number": "24.4%", "kind": "lookup", "operands": {"of": a}}, src)["verdict"], "verified")
        diff = {"kind": "derived", "formula": "difference", "operands": {"a": a, "b": b}}
        self.assertEqual(self.one("Margin widened 1.3%p.", dict(diff, number="1.3%p"), src)["verdict"], "verified")
        self.assertEqual(self.one("Margin widened 130 bp.", dict(diff, number="130 bp"), src)["verdict"], "verified")
        r = self.one("Margin ratio was 1.3%p.", {"number": "1.3%p", "kind": "derived", "formula": "ratio", "operands": {"num": a, "den": b}}, src)
        self.assertEqual((r["verdict"], r["reason"]), ("unverifiable", "unsupported"))
        thousands = {"t.csv": "item,usd_thousands\nrevenue,1234.5\n"}
        loc = {"file": "t.csv", "row": {"item": "revenue"}, "column": "usd_thousands", "multiplier": 1000}
        r = self.one("Revenue was $1.2M.", {"number": "$1.2M", "kind": "lookup", "operands": {"of": loc}}, thousands)
        self.assertEqual(r["verdict"], "verified")
        self.assertIn("x 1000", r["sources"][0])
        self.assertEqual(self.one("Revenue was $1.2K.", {"number": "$1.2K", "kind": "lookup", "operands": {"of": loc}}, thousands)["verdict"], "wrong")
        won = {"k.csv": "항목,금액\n매출,3214500000000\n"}
        kloc = {"file": "k.csv", "row": {"항목": "매출"}, "column": "금액"}
        self.assertEqual(self.one("매출은 3조 2,145억 원이다.", {"number": "3조 2,145억", "kind": "lookup", "operands": {"of": kloc}}, won)["verdict"], "verified")
        self.assertEqual(self.one("매출은 3조 2,415억 원이다.", {"number": "3조 2,415억", "kind": "lookup", "operands": {"of": kloc}}, won)["verdict"], "wrong")

    def test_hedged_integer(self):
        c = {"kind": "lookup", "operands": {"of": cell(2025, "East")}}
        self.assertEqual(self.one("East made about 1,100,000.", dict(c, number="1,100,000"))["verdict"], "verified")
        self.assertEqual(self.one("East made 1,100,000.", dict(c, number="1,100,000"))["verdict"], "wrong")

    def test_abstentions(self):
        look = lambda loc: {"number": "5", "kind": "lookup", "operands": {"of": loc}}
        cases = [
            (cell(2030, "East"), "locator_no_match"),
            ({"file": "sales.csv", "row": {"year": "2025"}, "column": "revenue"}, "locator_multiple"),
            (cell(2025, "East", column="profit"), "locator_no_match"),
            (cell(2025, "East", column="region"), "cell_not_numeric"),
            ({"file": "sales.csv", "row": {"nope": "1"}, "column": "revenue"}, "locator_no_match"),
        ]
        for loc, reason in cases:
            r = self.one("It was 5.", look(loc))
            self.assertEqual((r["verdict"], r["reason"]), ("unverifiable", reason))
        zero = {"z.csv": "k,v\na,0\nb,7\n"}
        r = self.one("It was 5.", {"number": "5", "kind": "derived", "formula": "ratio",
                                   "operands": {"num": {"file": "z.csv", "row": {"k": "b"}, "column": "v"},
                                                "den": {"file": "z.csv", "row": {"k": "a"}, "column": "v"}}}, zero)
        self.assertEqual(r["reason"], "division_by_zero")
        r = self.one("We expect 5.", {"number": "5", "kind": "no_source", "reason": "forecast"})
        self.assertEqual((r["verdict"], r["reason"], r["note"]), ("unverifiable", "declared_no_source", "forecast"))
        self.assertEqual(self.one("It was 5.", {"number": "5", "kind": "derived", "formula": "median", "operands": {}})["reason"], "unsupported")

    def test_binding_coverage_and_labels(self):
        doc = "East took 455 orders in 2025, 45 more.\nThe 2025 plan assumes 500 orders and a 2100 baseline.\n"
        ok = {"kind": "lookup", "operands": {"of": cell(2025, "East", column="orders")}}
        claims = [
            dict(ok, id="a", line=1, quote="East took 455 orders", number="455"),
            dict(ok, id="dup", line=1, quote="East took 455 orders", number="455"),
            dict(ok, id="ghost", line=1, quote="West took 455 orders", number="455"),
            dict(ok, id="nonum", line=1, quote="East took 455 orders", number="456"),
            {"id": "y", "line": 2, "quote": "The 2025 plan", "number": "2025", "kind": "label"},
            {"id": "bad", "line": 2, "quote": "assumes 500 orders", "number": "500", "kind": "label"},
            dict(ok, id="yr", line=1, quote="orders in 2025", number="2025"),
        ]
        res = self.run_check(doc, claims)
        c = res["counts"]
        self.assertEqual(c["verified"] + c["wrong"] + c["unverifiable"] + c["uncovered"], c["numbers"])
        verdicts = {r["id"]: (r["verdict"], r["reason"]) for r in res["claims"]}
        self.assertEqual(verdicts["a"], ("verified", "match"))
        self.assertEqual(verdicts["dup"], ("unverifiable", "duplicate_claim"))
        self.assertEqual(verdicts["yr"][0], "wrong")  # a skipped year is promoted when a claim points at it
        self.assertEqual({u["id"]: u["reason"] for u in res["unbound_claims"]},
                         {"ghost": "quote_not_on_line", "nonum": "number_not_in_quote", "bad": "label_not_allowed"})
        self.assertEqual([u["text"] for u in res["uncovered"]], ["45", "500", "2100"])
        self.assertEqual([u["text"] for u in res["labels"]], ["2025"])
        self.assertEqual(res["skipped"].get("label"), 1)
        self.assertEqual(c, {"numbers": 5, "verified": 1, "wrong": 1, "unverifiable": 0, "uncovered": 3})


class ReceiptTest(CheckCase):
    def test_multipliers_and_truncation_are_listed(self):
        src = {"t.csv": "item,usd_thousands\nrevenue,1234.5\ncost,987.6\n"}
        loc = lambda item: {"file": "t.csv", "row": {"item": item}, "column": "usd_thousands", "multiplier": 1000}
        doc = "Revenue was $1.2M and cost was $0.9M.\nProfit was $250K.\n"
        claims = [
            {"id": "a", "line": 1, "quote": "Revenue was $1.2M", "number": "$1.2M", "kind": "lookup", "operands": {"of": loc("revenue")}},
            {"id": "b", "line": 1, "quote": "cost was $0.9M", "number": "$0.9M", "kind": "lookup", "operands": {"of": loc("cost")}},
            {"id": "c", "line": 2, "quote": "Profit was $250K", "number": "$250K", "kind": "derived", "formula": "difference",
             "operands": {"a": loc("revenue"), "b": loc("cost")}},
        ]
        res = self.run_check(doc, claims, src)
        self.assertEqual(res["multipliers"], [{"file": "t.csv", "column": "usd_thousands", "multiplier": "1000", "claims": 3}])
        self.assertEqual(res["truncated"], 1)                      # 987,600 written as $0.9M
        self.assertEqual(res["counts"], {"numbers": 3, "verified": 2, "wrong": 1, "unverifiable": 0, "uncovered": 0})
        text = R.render(res)
        self.assertIn("1 of the verified numbers match only when the source value is cut off", text)
        self.assertIn("multipliers declared by the plan (1)", text)
        self.assertIn("247K (exact 246,900)", text)

    def test_sig(self):
        self.assertEqual(R._sig("11.93415637860082304526748971"), "11.9342")
        self.assertEqual(R._sig("1875683000"), "1,875,683,000")
        self.assertEqual(R._sig("0.000123456789"), "0.000123457")
        self.assertEqual(R._sig("-6.56488549"), "-6.56489")
        self.assertEqual(R._sig("0"), "0")


class OccurrenceTest(CheckCase):
    def test_number_inside_a_longer_number_is_not_a_place(self):
        self.assertEqual(R._whole_number_places("In 2023, 3 stores and 3.5% of 1,003", "3"), [9])
        self.assertEqual(R._whole_number_places("It was 455.", "455"), [7])
        self.assertEqual(R._whole_number_places("5.5% and 5%", "5"), [9])
        doc = "In 2023 the region took 3 orders."
        src = {"o.csv": "k,v\na,3\n"}
        res = self.run_check(doc + "\n", [{"id": "c", "line": 1, "quote": doc, "number": "3", "kind": "lookup",
                                         "operands": {"of": {"file": "o.csv", "row": {"k": "a"}, "column": "v"}}}], src)
        self.assertEqual(res["counts"], {"numbers": 1, "verified": 1, "wrong": 0, "unverifiable": 0, "uncovered": 0})


class SourceTest(CheckCase):
    def look(self, loc, text, sources):
        return self.one(f"It was {text}.", {"number": text, "kind": "lookup", "operands": {"of": loc}}, sources)["verdict"]

    def test_json_markdown_text_tsv(self):
        data = {"data.json": json.dumps({"companies": [{"name": "Acme", "revenue": 1234.5}, {"name": "Bolt", "revenue": "2,000"}],
                                         "meta": {"count": 2}})}
        self.assertEqual(self.look({"file": "data.json", "pointer": "/companies/0/revenue"}, "1,234.5", data), "verified")
        self.assertEqual(self.look({"file": "data.json", "pointer": "/companies", "row": {"name": "Bolt"}, "column": "revenue"}, "2,000", data), "verified")
        self.assertEqual(self.look({"file": "data.json", "pointer": "/meta/count"}, "2", data), "verified")
        self.assertEqual(self.look({"file": "data.json", "pointer": "/meta/missing"}, "2", data), "unverifiable")
        md = {"t.md": "# T\n\n| Quarter | Revenue |\n|---|---:|\n| Q2 | 1,100 |\n| Q3 | **1,250** |\n\ntext\n\n| A | B |\n|---|---|\n| x | 7 |\n"}
        self.assertEqual(self.look({"file": "t.md", "table": 1, "row": {"Quarter": "Q3"}, "column": "Revenue"}, "1,250", md), "verified")
        self.assertEqual(self.look({"file": "t.md", "table": 2, "row": {"A": "x"}, "column": "B"}, "7", md), "verified")
        self.assertEqual(self.look({"file": "t.md", "table": 3, "row": {"A": "x"}, "column": "B"}, "7", md), "unverifiable")
        txt = {"notes.txt": "Total revenue: $1.2M across 14 stores\nTotal cost: 900\nOther line\n"}
        self.assertEqual(self.look({"file": "notes.txt", "contains": "Total revenue", "nth": 1}, "1,200,000", txt), "verified")
        self.assertEqual(self.look({"file": "notes.txt", "contains": "Total revenue", "nth": 2}, "14", txt), "verified")
        self.assertEqual(self.look({"file": "notes.txt", "contains": "Total"}, "14", txt), "unverifiable")
        tsv = {"s.tsv": "k\tv\na\t(1,500)\n"}
        self.assertEqual(self.look({"file": "s.tsv", "row": {"k": "a"}, "column": "v"}, "-1,500", tsv), "verified")

    def test_xlsx_from_a_spreadsheet_library(self):
        book = {"book.xlsx": (ROOT / "tests" / "fixtures" / "book.xlsx").read_bytes()}
        self.assertEqual(self.look({"file": "book.xlsx", "sheet": "Sales", "row": {"Year": "2025", "Region": "East"}, "column": "Revenue"}, "1,088,000", book), "verified")
        self.assertEqual(self.look({"file": "book.xlsx", "sheet": "Sales", "cell": "C4"}, "1,088,000", book), "verified")
        self.assertEqual(self.look({"file": "book.xlsx", "sheet": "Sales", "row": {"Year": "2025", "Region": "East"}, "column": "Margin", "multiplier": 100}, "24.4%", book), "verified")
        self.assertEqual(self.look({"file": "book.xlsx", "sheet": "Notes", "header_row": 2, "row": {"Item": "Staff"}, "column": "Count"}, "340", book), "verified")
        self.assertEqual(self.look({"file": "book.xlsx", "sheet": "Sales", "cell": "F2"}, "3,327,000", book), "unverifiable")  # formula without a stored value
        self.assertEqual(self.look({"file": "book.xlsx", "sheet": "Nope", "cell": "A1"}, "1", book), "unverifiable")

    def test_xlsx_inline_strings_without_cell_refs(self):
        ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
        p = self.dir / "mini.xlsx"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("xl/workbook.xml", f'<workbook {ns} {rel}><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>')
            z.writestr("xl/_rels/workbook.xml.rels",
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr("xl/worksheets/sheet1.xml",
                       f'<worksheet {ns}><sheetData><row><c t="inlineStr"><is><t>k</t></is></c><c t="inlineStr"><is><t>v</t></is></c></row>'
                       '<row><c t="inlineStr"><is><t>a</t></is></c><c><v>41.5</v></c></row></sheetData></worksheet>')
        self.assertEqual(self.look({"file": "mini.xlsx", "row": {"k": "a"}, "column": "v"}, "41.5", {"mini.xlsx": p.read_bytes()}), "verified")


class ExitCodeTest(CheckCase):
    def main(self, claims, sources=None, extra=(), doc="It was 455.\n"):
        self.write("report.md", doc)
        for name, content in (sources if sources is not None else {"sales.csv": SALES}).items():
            self.write(name, content)
        plan = self.write("claims.json", claims if isinstance(claims, str) else json.dumps({"document": "report.md", "claims": claims}))
        return R.main(["check", str(plan), "--quiet", *extra])

    def claim(self, loc, number="455"):
        return [{"id": "c1", "line": 1, "quote": "It was 455.", "number": number, "kind": "lookup", "operands": {"of": loc}}]

    def test_zero_one_and_strict(self):
        good = self.claim(cell(2025, "East", column="orders"))
        self.assertEqual(self.main(good), 0)
        self.assertTrue((self.dir / "receipts.json").exists() and (self.dir / "receipts.md").exists())
        self.assertEqual(self.main(self.claim(cell(2024, "East", column="orders"))), 1)
        self.assertEqual(self.main([]), 0)                       # uncovered only
        self.assertEqual(self.main([], extra=["--strict"]), 1)
        self.assertEqual(self.main(self.claim(cell(2030, "East", column="orders")), extra=["--strict"]), 1)

    def test_stops_with_code_two(self):
        ok = cell(2025, "East", column="orders")
        self.assertEqual(self.main(self.claim(ok), sources={}), 2)                                   # missing source
        self.assertEqual(self.main(self.claim(ok), sources={"sales.csv": b"year,region\n\xff\xfe\x00bad"}), 2)  # not UTF-8
        self.assertEqual(self.main(self.claim({"file": "d.json", "pointer": "/a"}), sources={"d.json": '{"a": 455'}), 2)
        self.assertEqual(self.main(self.claim({"file": "b.xlsx", "cell": "A1"}), sources={"b.xlsx": b"PK\x03\x04 truncated"}), 2)
        self.assertEqual(self.main("{not json"), 2)
        self.assertEqual(self.main(json.dumps({"document": "report.md", "claims": [{"id": "a"}, {"id": "a"}]})), 2)
        self.assertEqual(self.main(json.dumps({"document": "missing.md", "claims": []})), 2)


class ExampleTest(unittest.TestCase):
    def test_quickstart(self):
        R._CACHE.clear()
        res = R.run_check(str(ROOT / "examples" / "quickstart" / "claims.json"))
        self.assertEqual(res["counts"], {"numbers": 9, "verified": 6, "wrong": 1, "unverifiable": 1, "uncovered": 1})
        self.assertEqual([r["id"] for r in res["claims"] if r["verdict"] == "wrong"], ["c1"])


if __name__ == "__main__":
    unittest.main()
