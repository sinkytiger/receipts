"""A made-up DART for the E4 self-tests.

Everything here is invented: the companies (가상전자 and so on), their codes (9900xxxx, Zxxxxx), the receipt numbers
(year 2099) and every amount. The key is made up too. Nothing comes from a real filing and no real key is needed.

`World` builds the answers of the four endpoints the builder reads and the two the tool reads, in DART's formats:
  fnlttSinglAcntAll.json   rows with sj_div, account_id, account_nm and the amount columns
  fnlttXbrl.xml            a zip with one XBRL instance that holds the same amounts as facts
  list.json                the periodic reports, earlier versions included
  corpCode.xml             a zip with CORPCODE.xml
`FakeDart` serves them on 127.0.0.1.

The amounts of the statements answer and of the instance come from one function, so they agree, except where a row
says otherwise on purpose (see the row options).
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FAKE_KEY = "selftest-made-up-key-not-a-real-dart-key"       # 40 characters, like a real key, and plainly not one
NOSTD = "-표준계정코드 미사용-"
REPORTS = {"annual": (2025, "11011"), "half": (2026, "11012"), "q3": (2026, "11014")}
SJ_NM = {"BS": "재무상태표", "IS": "손익계산서", "CIS": "포괄손익계산서", "CF": "현금흐름표", "SCE": "자본변동표"}

# Business years ending in December and in March.
CAL = {
    "dec": {
        "annual": {"year0": ("2025-01-01", "2025-12-31"), "year1": ("2024-01-01", "2024-12-31"),
                   "year2": ("2023-01-01", "2023-12-31")},
        "half": {"cum": ("2026-01-01", "2026-06-30"), "three": ("2026-04-01", "2026-06-30"),
                 "pcum": ("2025-01-01", "2025-06-30"), "pthree": ("2025-04-01", "2025-06-30"),
                 "prev": ("2025-01-01", "2025-12-31"), "two": ("2024-01-01", "2024-12-31")},
        "q3": {"cum": ("2026-01-01", "2026-09-30"), "three": ("2026-07-01", "2026-09-30"),
               "pcum": ("2025-01-01", "2025-09-30"), "pthree": ("2025-07-01", "2025-09-30"),
               "prev": ("2025-01-01", "2025-12-31"), "two": ("2024-01-01", "2024-12-31")},
    },
    "mar": {
        "annual": {"year0": ("2024-04-01", "2025-03-31"), "year1": ("2023-04-01", "2024-03-31"),
                   "year2": ("2022-04-01", "2023-03-31")},
        "half": {"cum": ("2026-04-01", "2026-09-30"), "three": ("2026-07-01", "2026-09-30"),
                 "pcum": ("2025-04-01", "2025-09-30"), "pthree": ("2025-07-01", "2025-09-30"),
                 "prev": ("2025-04-01", "2026-03-31"), "two": ("2024-04-01", "2025-03-31")},
        "q3": {"cum": ("2026-04-01", "2026-12-31"), "three": ("2026-10-01", "2026-12-31"),
               "pcum": ("2025-04-01", "2025-12-31"), "pthree": ("2025-10-01", "2025-12-31"),
               "prev": ("2025-04-01", "2026-03-31"), "two": ("2024-04-01", "2025-03-31")},
    },
}

# A row: (statement, account id, name, kind, size, options).
#   kind "i" is a balance, "d" a flow, "cash" a balance shown in the cash flow statement (DART tags it at the end
#   of the period, so the builder finds no fact for the period itself).
#   options: neg (negative), eps (won per share), const (the same amount in every period and set),
#   cfs (only in the consolidated set), current (a fact for the current period only, the API cells of the other
#   periods are empty), differ (the previous period is tagged twice with different amounts),
#   api_empty (the instance has the amount, the API cell of this period in the annual report is empty),
#   twice (the row is listed twice under the same id).
FULL_ROWS = [
    ("BS", "ifrs-full_CurrentAssets", "유동자산", "i", 4 * 10**14, ""),
    ("BS", "ifrs-full_CashAndCashEquivalents", "현금및현금성자산", "i", 6 * 10**13, ""),
    ("BS", "ifrs-full_OtherCurrentAssets", "기타유동자산", "i", 3 * 10**12, "twice"),
    ("BS", "ifrs-full_NoncurrentAssets", "비유동자산", "i", 5 * 10**14, ""),
    ("BS", "ifrs-full_IntangibleAssetsOtherThanGoodwill", "무형자산", "i", 4 * 10**7, ""),
    ("BS", "ifrs-full_Assets", "자산총계", "i", 9 * 10**14, ""),
    ("BS", NOSTD, "기타자산(미분류)", "i", 10**12, ""),
    ("BS", "ifrs-full_Liabilities", "부채총계", "i", 3 * 10**14, ""),
    ("BS", "ifrs-full_IssuedCapital", "자본금", "i", 8 * 10**11, "const"),
    ("BS", "ifrs-full_NoncontrollingInterests", "비지배지분", "i", 2 * 10**13, "cfs current"),
    ("BS", "ifrs-full_Equity", "자본총계", "i", 6 * 10**14, ""),
    ("BS", "ifrs-full_EquityAndLiabilities", "부채와자본총계", "i", 9 * 10**14, ""),
    ("BS", "dart_OtherPayables", "기타", "i", 2 * 10**12, ""),
    ("BS", "dart_OtherProvisions", "기타", "i", 10**12, ""),
    ("IS", "ifrs-full_Revenue", "매출액", "d", 3 * 10**14, ""),
    ("IS", "ifrs-full_CostOfSales", "매출원가", "d", 2 * 10**14, ""),
    ("IS", "dart_OperatingIncomeLoss", "영업이익", "d", 4 * 10**13, ""),
    ("IS", "ifrs-full_FinanceCosts", "금융원가", "d", 5 * 10**11, "differ"),
    ("IS", "dart_OtherGains", "기타이익", "d", 10**11, "api_empty"),
    ("IS", "ifrs-full_ProfitLoss", "당기순이익", "d", 3 * 10**13, ""),
    ("IS", "ifrs-full_BasicEarningsLossPerShare", "기본주당이익", "d", 5000, "eps"),
    ("CIS", "ifrs-full_ProfitLoss", "당기순이익", "d", 3 * 10**13, ""),
    ("CIS", "ifrs-full_OtherComprehensiveIncome", "기타포괄손익", "d", 2 * 10**12, "neg"),
    ("CIS", "ifrs-full_ComprehensiveIncome", "총포괄손익", "d", 3 * 10**13, ""),
    ("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", "d", 5 * 10**13, ""),
    ("CF", "ifrs-full_ProfitLoss", "당기순이익", "d", 3 * 10**13, ""),
    ("CF", "ifrs-full_CashFlowsFromUsedInInvestingActivities", "투자활동현금흐름", "d", 4 * 10**13, "neg"),
    ("CF", "ifrs-full_CashFlowsFromUsedInFinancingActivities", "재무활동현금흐름", "d", 0, ""),
    ("CF", "dart_CashAndCashEquivalentsAtBeginningOfPeriodCf", "기초현금및현금성자산", "cash", 6 * 10**13, ""),
    ("CF", "dart_CashAndCashEquivalentsAtEndOfPeriodCf", "기말현금및현금성자산", "cash", 6 * 10**13, ""),
    ("SCE", "ifrs-full_Equity", "기말자본", "d", 6 * 10**14, "twice"),
]
SMALL_ROWS = [
    ("BS", "ifrs-full_Assets", "자산총계", "i", 7 * 10**13, ""),
    ("BS", "ifrs-full_Liabilities", "부채총계", "i", 5 * 10**13, ""),
    ("BS", "ifrs-full_IssuedCapital", "자본금", "i", 3 * 10**10, "const"),
    ("BS", "ifrs-full_Equity", "자본총계", "i", 2 * 10**13, ""),
    ("BS", "ifrs-full_EquityAndLiabilities", "부채와자본총계", "i", 7 * 10**13, ""),
    ("CIS", "ifrs-full_Revenue", "영업수익", "d", 9 * 10**12, ""),
    ("CIS", "dart_OperatingIncomeLoss", "영업이익", "d", 2 * 10**12, ""),
    ("CIS", "ifrs-full_ProfitLoss", "당기순이익", "d", 10**12, ""),
    ("CIS", "ifrs-full_ComprehensiveIncome", "총포괄손익", "d", 10**12, "neg"),
    ("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", "d", 3 * 10**12, ""),
    ("CF", "ifrs-full_ProfitLoss", "당기순이익", "d", 10**12, ""),
    ("CF", "dart_CashAndCashEquivalentsAtEndOfPeriodCf", "기말현금및현금성자산", "cash", 4 * 10**12, ""),
]

# code -> name, stock code, calendar, rows, step of the amounts, sets it has, reports it has, special behaviour
COMPANIES = {
    "99000001": dict(name="가상전자", stock="Z00001", cal="dec", rows=FULL_ROWS, step=10**6, fs=("CFS", "OFS"),
                     reports=("annual", "half", "q3"), amended=("annual",)),
    "99000002": dict(name="가상금융", stock="Z00002", cal="mar", rows=SMALL_ROWS, step=1, fs=("OFS",),
                     reports=("annual", "half", "q3")),
    "99000003": dict(name="가상화학", stock="Z00003", cal="dec", rows=SMALL_ROWS, step=1000, fs=("CFS", "OFS"),
                     reports=("annual", "half", "q3")),
    "99000004": dict(name="가상건설", stock="Z00004", cal="dec", rows=SMALL_ROWS, step=1, fs=(), reports=()),
    "99000005": dict(name="가상제약", stock="Z00005", cal="dec", rows=SMALL_ROWS, step=1, fs=("OFS",),
                     reports=("annual", "half")),
    "99000006": dict(name="가상통신", stock="Z00006", cal="dec", rows=SMALL_ROWS, step=1, fs=("CFS", "OFS"),
                     reports=("annual",), no_xbrl=True),
    "99000007": dict(name="가상식품", stock="Z00007", cal="dec", rows=SMALL_ROWS, step=1, fs=("CFS", "OFS"),
                     reports=("annual", "half")),
    "99000008": dict(name="가상해운", stock="Z00008", cal="mar", rows=SMALL_ROWS, step=1, fs=("CFS", "OFS"),
                     reports=("annual",)),
    "99000009": dict(name="가상보험", stock="Z00009", cal="dec", rows=SMALL_ROWS, step=1, fs=("CFS",),
                     reports=("annual", "q3")),
    "99000101": dict(name="가상비상장하나", stock="", cal="dec", rows=SMALL_ROWS, step=1, fs=("OFS",),
                     reports=("annual",)),
    "99000102": dict(name="가상비상장둘", stock="", cal="dec", rows=SMALL_ROWS, step=1, fs=(), reports=()),
}
REGISTRY = {"dev": [["99000001", "가상전자"], ["99000002", "가상금융"]], "heldout": [["99000003", "가상화학"]],
            "draw": 2}


def column_periods(cal, report, statement):
    """Contract, section 2, "What each column holds", written out again for the made-up data.
    Returns [(column, period)], a period being (date,) or (start, end). In an interim cash flow statement the
    contract has two usable columns: frmtrm_q_amount is "not usable" there."""
    c = CAL[cal][report]
    if report == "annual":
        years = (("thstrm_amount", c["year0"]), ("frmtrm_amount", c["year1"]), ("bfefrmtrm_amount", c["year2"]))
        if statement == "BS":
            return [(col, (p[1],)) for col, p in years]
        return list(years)
    if statement == "BS":
        return [("thstrm_amount", (c["cum"][1],)), ("frmtrm_amount", (c["prev"][1],))]
    if statement == "CF":
        return [("thstrm_amount", c["cum"]), ("frmtrm_amount", c["pcum"])]
    return [("thstrm_amount", c["three"]), ("thstrm_add_amount", c["cum"]), ("frmtrm_q_amount", c["pthree"]),
            ("frmtrm_add_amount", c["pcum"])]


def api_columns(cal, report, statement):
    """The columns the made-up API fills, as DART does: the contract's columns, and in an interim cash flow
    statement frmtrm_q_amount too (see World.statements for what it holds)."""
    cols = column_periods(cal, report, statement)
    if report != "annual" and statement == "CF":
        cols = cols + [("frmtrm_q_amount", CAL[cal][report]["pcum"])]
    return cols


def amount(corp, fs, account, period, size, step, opts):
    if size == 0:
        return 0
    seed = f"{corp}|{account}" if "const" in opts else f"{corp}|{fs}|{account}|{'|'.join(period)}"
    h = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12], 16)
    v = size + h % size
    if "eps" not in opts:
        v = v // step * step
    return -v if "neg" in opts else v


class World:
    def __init__(self, api_differs=False, companies=None):
        """The made-up API copies a habit of the real one: in an interim cash flow statement, frmtrm_q_amount of the
        profit row (a row that also has a three-month amount) holds those three months, and the same period a year
        earlier in every other row. That is why the contract calls the column not usable there.

        api_differs=True makes the API disagree with the XBRL file on purpose: thstrm_amount of the operating
        income row is twice as large in the statements answer as in the instance."""
        self.api_differs = api_differs
        self.companies = companies or COMPANIES
        self.receipts = {}
        for corp, c in self.companies.items():
            seq = int(corp[-4:])
            for report in c["reports"]:
                day = {"annual": "0310", "half": "0814", "q3": "1114"}[report]
                served = f"2099{'0401' if report in c.get('amended', ()) else day}{seq:06d}"
                self.receipts[served] = (corp, report)

    # -- receipts
    def receipt(self, corp, report):
        for number, (c, r) in self.receipts.items():
            if c == corp and r == report:
                return number
        return None

    def earlier_receipt(self, corp, report):
        if report in self.companies[corp].get("amended", ()):
            return f"20990310{int(corp[-4:]):06d}"
        return None

    # -- one cell
    def cell(self, corp, fs, row, period, current):
        """The amount of a row for a period as text, or None when the company reports nothing there."""
        st, account, name, kind, size, opts = row
        c = self.companies[corp]
        if "current" in opts and not current:
            return None
        return str(amount(corp, fs, account, period, size, c["step"], opts))

    def fact_period(self, row, period):
        """Where the instance holds the amount: a balance shown in the cash flow statement sits at the period end."""
        return (period[-1],) if row[3] == "cash" else period

    # -- fnlttSinglAcntAll.json
    def statements(self, corp, year, reprt, fs):
        no_data = {"status": "013", "message": "조회된 데이타가 없습니다."}
        c = self.companies.get(corp)
        report = next((r for r, (y, code) in REPORTS.items() if str(y) == str(year) and code == reprt), None)
        if c is None or report is None or report not in c["reports"]:
            return no_data
        sets = [f for f in c["fs"]] if fs not in ("CFS", "OFS") else ([fs] if fs in c["fs"] else [])
        if not sets:
            return no_data
        rows, rcept = [], self.receipt(corp, report)
        for one in sets:            # a statement type DART does not know returns both sets mixed together
            ord_no = 0
            for row in c["rows"]:
                st, account, name, kind, size, opts = row
                if "cfs" in opts and one != "CFS":
                    continue
                for _ in range(2 if "twice" in opts else 1):
                    ord_no += 1
                    out = {"rcept_no": rcept, "reprt_code": reprt, "bsns_year": str(year), "corp_code": corp,
                           "sj_div": st, "sj_nm": SJ_NM[st], "account_id": account, "account_nm": name,
                           "account_detail": "-", "thstrm_nm": "제 9 기"}
                    cols = api_columns(c["cal"], report, "CF" if st == "SCE" else st)
                    for i, (col, period) in enumerate(cols):
                        value = self.cell(corp, one, row, self.fact_period(row, period), current=(i == 0))
                        if "api_empty" in opts and report == "annual" and i == 0:
                            value = ""
                        if report != "annual" and st == "CF" and col == "frmtrm_q_amount" \
                                and account == "ifrs-full_ProfitLoss":
                            value = self.cell(corp, one, row, CAL[c["cal"]][report]["pthree"], current=False)
                        if self.api_differs and account == "dart_OperatingIncomeLoss" and i == 0 and value:
                            value = str(int(value) * 2)
                        if value is None:
                            if col == "frmtrm_q_amount" and st == "CF":
                                continue                    # DART leaves the key out
                            value = ""
                        out[col] = value
                    if report == "annual" and st in ("IS", "CIS"):
                        out["thstrm_add_amount"] = ""
                    out["ord"] = str(ord_no)
                    out["currency"] = "KRW"
                    rows.append(out)
        return {"status": "000", "message": "정상", "list": rows}

    # -- fnlttXbrl.xml
    def instance(self, corp, report):
        c = self.companies[corp]
        tag = {"annual": "2024-06-30", "half": "2026-01-31", "q3": "2026-01-31"}[report]
        ns = {"xbrli": "http://www.xbrl.org/2003/instance", "xbrldi": "http://xbrl.org/2006/xbrldi",
              "iso4217": "http://www.xbrl.org/2003/iso4217",
              "ifrs-full": f"http://made-up.example/taxonomy/{tag}/ifrs-full",
              "dart": f"http://made-up.example/taxonomy/{tag}/dart",
              "dart-gcd": f"http://made-up.example/taxonomy/{tag}/dart-gcd",
              f"entity{corp}": f"http://made-up.example/taxonomy/entity{corp}"}
        contexts, order, facts = {}, [], []
        axis = "ifrs-full:ConsolidatedAndSeparateFinancialStatementsAxis"
        member = {"CFS": "ifrs-full:ConsolidatedMember", "OFS": "ifrs-full:SeparateMember"}

        def ctx(period, dims=(), typed=False):
            key = (tuple(period), tuple(dims), typed)
            if key not in contexts:
                contexts[key] = f"c{len(contexts) + 1}"
                order.append(key)
            return contexts[key]

        def fact(qname, cid, text, unit="KRW", decimals="0"):
            attrs = f' unitRef="{unit}" decimals="{decimals}"' if unit else ' xml:lang="ko"'
            facts.append(f'  <{qname} contextRef="{cid}"{attrs}>{text}</{qname}>')

        seen = set()
        cal = CAL[c["cal"]][report]
        for fs in c["fs"]:
            dims = ((axis, member[fs]),)
            for row in c["rows"]:
                st, account, name, kind, size, opts = row
                if st == "SCE" or account == NOSTD or ("cfs" in opts and fs != "CFS"):
                    continue
                qname = account.replace("_", ":", 1)
                unit = "KRWEPS" if "eps" in opts else "KRW"
                for i, (col, period) in enumerate(column_periods(c["cal"], report, st)):
                    where = self.fact_period(row, period)
                    value = self.cell(corp, fs, row, where, current=(i == 0))
                    if value is None or (qname, fs, where) in seen:
                        continue
                    seen.add((qname, fs, where))
                    fact(qname, ctx(where, dims), value, unit)
                    if "differ" in opts and i == 1:
                        fact(qname, ctx(where, dims), str(int(value) + c["step"]), unit)
            # the same elements on a breakdown axis and in a typed context: other amounts, never the gold
            for period in sorted({p for st in ("IS", "CIS", "CF") for _, p in column_periods(c["cal"], report, st)}):
                seg = dims + (("ifrs-full:SegmentsAxis", f"entity{corp}:MadeUpSegmentMember"),)
                fact("ifrs-full:Revenue", ctx(period, seg), "111000000")
                fact("ifrs-full:ProfitLoss", ctx(period, dims, typed=True), "222000000")
        # facts with no dimension at all, and the document information
        whole = cal["year0"] if report == "annual" else cal["cum"]
        fact("ifrs-full:Revenue", ctx(whole), "333000000")
        fact("dart-gcd:EntityRegistrantName", ctx(whole), c["name"], unit=None)
        gcd = [("PeriodCoveredbytheReportofCurrentFiscalYearorQuarterorHalfYearMember", whole)]
        if report == "annual":
            gcd += [("PeriodCoveredbyLastFiscalYearMember", cal["year1"]),
                    ("PeriodCoveredbyTheYearBeforeLastFiscalYearMember", cal["year2"])]
        else:
            gcd += [("PeriodCoveredbyLastFiscalYearMember", cal["prev"]),
                    ("PeriodCoveredbyQuarterlyorHalfYearReportofLastFiscalYearMember", cal["pcum"]),
                    ("PeriodCoveredbyTheYearBeforeLastFiscalYearMember", cal["two"])]
        for name, (start, end) in gcd:
            cid = ctx(whole, (("dart-gcd:PeriodAxis", f"dart-gcd:{name}"),))
            fact("dart-gcd:DocumentPeriodStartDate", cid, start, unit=None)
            fact("dart-gcd:DocumentPeriodEndDate", cid, end, unit=None)
        out = ['<?xml version="1.0" encoding="utf-8"?>', "<!--made-up instance for the receipts E4 self-test-->",
               "<xbrli:xbrl " + " ".join(f'xmlns:{p}="{u}"' for p, u in ns.items()) + ">"]
        for period, dims, typed in order:
            seg = "".join(f'<xbrldi:explicitMember dimension="{d}">{m}</xbrldi:explicitMember>' for d, m in dims)
            if typed:
                seg += ('<xbrldi:typedMember dimension="dart:MadeUpTypedAxis"><dart:MadeUpDomain>x'
                        '</dart:MadeUpDomain></xbrldi:typedMember>')
            seg = f"<xbrli:segment>{seg}</xbrli:segment>" if seg else ""
            if len(period) == 1:
                per = f"<xbrli:instant>{period[0]}</xbrli:instant>"
            else:
                per = f"<xbrli:startDate>{period[0]}</xbrli:startDate><xbrli:endDate>{period[1]}</xbrli:endDate>"
            out.append(f'  <xbrli:context id="{contexts[(period, dims, typed)]}"><xbrli:entity><xbrli:identifier '
                       f'scheme="http://made-up.example/CIK">{corp}</xbrli:identifier>{seg}</xbrli:entity>'
                       f"<xbrli:period>{per}</xbrli:period></xbrli:context>")
        out += facts
        out += ['  <xbrli:unit id="KRW"><xbrli:measure>iso4217:KRW</xbrli:measure></xbrli:unit>',
                '  <xbrli:unit id="KRWEPS"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:KRW'
                "</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares"
                "</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>", "</xbrli:xbrl>"]
        return "\n".join(out).encode("utf-8"), f"entity{corp}_{whole[1]}.xbrl"

    def xbrl_zip(self, rcept_no, reprt):
        got = self.receipts.get(rcept_no)
        if got is None or REPORTS[got[1]][1] != reprt or self.companies[got[0]].get("no_xbrl"):
            return None
        data, name = self.instance(*got)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(name, data)
            z.writestr(name[:-5] + ".xsd", "<!-- made-up schema, not read -->")
        return buf.getvalue()

    # -- list.json
    def filings(self, corp):
        c = self.companies.get(corp)
        if c is None:
            return []
        label = {"annual": "사업보고서 (2025.12)", "half": "반기보고서 (2026.06)", "q3": "분기보고서 (2026.09)"}
        rows = []
        for report in c["reports"]:
            served, earlier = self.receipt(corp, report), self.earlier_receipt(corp, report)
            name = ("[기재정정]" if earlier else "") + label[report]
            rows.append({"corp_code": corp, "corp_name": c["name"], "stock_code": c["stock"], "corp_cls": "Y",
                         "report_nm": name, "rcept_no": served, "flr_nm": c["name"], "rcept_dt": served[:8],
                         "rm": ""})
            if earlier:
                rows.append({"corp_code": corp, "corp_name": c["name"], "stock_code": c["stock"], "corp_cls": "Y",
                             "report_nm": label[report], "rcept_no": earlier, "flr_nm": c["name"],
                             "rcept_dt": earlier[:8], "rm": "정"})
        rows.append({"corp_code": corp, "corp_name": c["name"], "stock_code": c["stock"], "corp_cls": "Y",
                     "report_nm": "사업보고서 (2024.12)", "rcept_no": f"20980310{int(corp[-4:]):06d}",
                     "flr_nm": c["name"], "rcept_dt": "20980310", "rm": ""})
        return sorted(rows, key=lambda r: r["rcept_no"], reverse=True)

    # -- corpCode.xml
    def corp_code_zip(self):
        rows = "".join(f"<list><corp_code>{corp}</corp_code><corp_name>{c['name']}</corp_name>"
                       f"<corp_eng_name>Made-up {corp}</corp_eng_name><stock_code>{c['stock'] or ' '}</stock_code>"
                       f"<modify_date>20990101</modify_date></list>" for corp, c in self.companies.items())
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("CORPCODE.xml", f'<?xml version="1.0" encoding="UTF-8"?><result>{rows}</result>')
        return buf.getvalue()


def xml_status(status, message):
    return (f'<?xml version="1.0" encoding="UTF-8"?><result><status>{status}</status><message>{message}</message>'
            "</result>").encode("utf-8")


PAGE = 2        # filings per page of list.json, so that the builder has to turn pages


def answer(w, endpoint, q):
    """The body the stand-in sends for an endpoint and its parameters (the key already checked), or None."""
    if endpoint == "fnlttSinglAcntAll.json":
        data = w.statements(q.get("corp_code", ""), q.get("bsns_year", ""), q.get("reprt_code", ""),
                            q.get("fs_div", ""))
        return json.dumps(data, ensure_ascii=False).encode("utf-8")
    if endpoint == "fnlttXbrl.xml":
        body = w.xbrl_zip(q.get("rcept_no", ""), q.get("reprt_code", ""))
        return body if body is not None else xml_status("014", "파일이 존재하지 않습니다.")
    if endpoint == "list.json":
        rows = w.filings(q.get("corp_code", ""))
        if not rows:
            return json.dumps({"status": "013", "message": "조회된 데이타가 없습니다."},
                              ensure_ascii=False).encode("utf-8")
        page = max(1, int(q.get("page_no", "1") or 1))
        total = (len(rows) + PAGE - 1) // PAGE
        chunk = rows[(page - 1) * PAGE: page * PAGE]
        return json.dumps({"status": "000", "message": "정상", "page_no": page, "page_count": PAGE,
                           "total_count": len(rows), "total_page": total, "list": chunk},
                          ensure_ascii=False).encode("utf-8")
    if endpoint == "corpCode.xml":
        return w.corp_code_zip()
    return None


def respond(w, key, target):
    """(endpoint, parameters without the key, body or None) for the path and query of a request."""
    parts = urllib.parse.urlsplit(target)
    q = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query, keep_blank_values=True).items()}
    endpoint = parts.path.rsplit("/", 1)[-1]
    given = q.pop("crtfc_key", "")
    if given != key:
        bad = "등록되지 않은 키입니다."
        body = (json.dumps({"status": "010", "message": bad}, ensure_ascii=False).encode("utf-8")
                if endpoint.endswith(".json") else xml_status("010", bad))
        return endpoint, q, body
    return endpoint, q, answer(w, endpoint, q)


class _Answer:
    """What urlopen returns, for an opener that asks the made-up world without a socket."""

    def __init__(self, body, status=200):
        self.body, self.status = body, status

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def in_process_opener(w, key, log):
    """An opener for the builder's Fetcher: no socket, every request is written to `log` without its key."""
    def open_(req, timeout=None):
        endpoint, q, body = respond(w, key, req.full_url)
        log.append((endpoint, q))
        return _Answer(body if body is not None else b"", 200 if body is not None else 404)
    return open_


class FakeDart:
    """A local stand-in for opendart.fss.or.kr that serves a World. It counts requests and never logs a key."""

    def __init__(self, world, key=FAKE_KEY):
        self.world, self.key = world, key
        self.requests = []          # (endpoint, parameters without the key)
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                endpoint, q, body = respond(outer.world, outer.key, self.path)
                with outer.lock:
                    outer.requests.append((endpoint, dict(q)))
                self.send_response(200 if body is not None else 404)
                kind = "application/json" if endpoint.endswith(".json") else "application/octet-stream"
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                self.wfile.write(body or b"")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self.url

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def count(self, endpoint=None):
        with self.lock:
            return sum(1 for e, _ in self.requests if endpoint is None or e == endpoint)

    def asked_companies(self):
        with self.lock:
            return {q.get("corp_code") for _, q in self.requests if q.get("corp_code")}
