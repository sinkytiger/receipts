#!/usr/bin/env python3
"""E5 generator: short Korean notes about one report of one company, written from the XBRL gold of E4's sets.

The rules are in docs/eval-plan.md, section E5. This file reads neither the script nor DART. Its input is
bench/dart/out/<dev|heldout>/ as E4's builder left it: every amount there was read from the company's XBRL file,
with its period as dates. A sentence describes its period from those dates.

  python bench/e5/generate.py               # the 10 development companies -> bench/e5/out/dev/
  python bench/e5/generate.py --held-out    # the 31 held-out companies    -> bench/e5/out/heldout/ (release run)

Written per company: docs/<corp>/document.md, gold.json (what each statement means and whether its number is
right) and gold-plan.json (the plan the sentences mean). manifest.json lists the documents and the companies left out.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from decimal import Decimal, ROUND_HALF_UP

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
E4_OUT = os.path.join(ROOT, "bench", "dart", "out")
VERSION = "e5-generator-1"
SEED = 20261123
TURN = ("q3", "half", "annual")
STATEMENTS = 12

INCOME_IDS = ["ifrs-full_Revenue", "dart_OperatingIncomeLoss", "ifrs-full_ProfitLoss", "ifrs-full_ProfitLossBeforeTax",
              "ifrs-full_GrossProfit", "ifrs-full_CostOfSales", "dart_TotalSellingGeneralAdministrativeExpenses",
              "ifrs-full_FinanceIncome", "ifrs-full_FinanceCosts", "ifrs-full_IncomeTaxExpenseContinuingOperations"]
BALANCE_IDS = ["ifrs-full_Assets", "ifrs-full_Liabilities", "ifrs-full_Equity", "ifrs-full_CashAndCashEquivalents",
               "ifrs-full_PropertyPlantAndEquipment", "ifrs-full_RetainedEarnings", "ifrs-full_IssuedCapital"]
CASH_IDS = ["ifrs-full_CashFlowsFromUsedInOperatingActivities", "ifrs-full_CashFlowsFromUsedInInvestingActivities",
            "ifrs-full_CashFlowsFromUsedInFinancingActivities"]
MILLION, EOK, JO = 10 ** 6, 10 ** 8, 10 ** 12
QUANTUM = {"won": 1, "million": MILLION, "eok": EOK, "jo_eok": EOK}
REPORT_TITLE = {"q3": "3분기보고서", "half": "반기보고서", "annual": "사업보고서"}
BASIS = {"CFS": "연결", "OFS": "별도"}

# the names E4's builder gives the periods it read from the XBRL file
INTERIM = {"cur": "three_months", "cum": "cumulative", "pcur": "prior_three_months", "pcum": "prior_cumulative",
           "end": "end", "pend": "prev_year_end"}
ANNUAL = {"cur": "year0", "prev": "year1", "prev2": "year2", "end": "end0", "pend": "end1", "pend2": "end2"}
# which period is shown in place of which, for the planted kinds a and b
SWAP_A = {"three_months": "cumulative", "cumulative": "three_months",
          "prior_three_months": "prior_cumulative", "prior_cumulative": "prior_three_months"}
SWAP_B = {"three_months": "prior_three_months", "prior_three_months": "three_months",
          "cumulative": "prior_cumulative", "prior_cumulative": "cumulative",
          "end": "prev_year_end", "prev_year_end": "end",
          "year0": "year1", "year1": "year0", "year2": "year1", "end0": "end1", "end1": "end0", "end2": "end1"}


class Stop(Exception):
    pass


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_json(path, data):
    write_text(path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")


# ---- one of E4's sets: a company's report in one kind of statements

class Set:
    def __init__(self, gold):
        s = gold["set"]
        self.corp, self.report, self.year, self.fs = s["corp"], s["report"], int(s["year"]), s["fs"]
        self.periods = {name: list(dates) for name, dates in gold["periods"].items()}
        spans = [tuple(d) for d in self.periods.values()]
        self.usable = len(set(spans)) == len(spans)          # two periods with the same dates cannot be told apart
        self.cells = {}
        for f in gold["facts"]:
            if f["unit"] != "KRW":
                continue
            role = next((name for name, dates in self.periods.items() if dates == list(f["period"])), None)
            if role is None:
                continue
            key = (f["statement"], f["account"], role)
            if key in self.cells:
                self.usable = False
            self.cells[key] = {"fs": self.fs, "statement": f["statement"], "account": f["account"], "name": f["name"],
                               "role": role, "column": f["column"], "period": list(f["period"]),
                               "value": int(f["value"])}
        self.income = "IS" if any(k[0] == "IS" for k in self.cells) else "CIS"

    def get(self, statement, account, role):
        return self.cells.get((self.income if statement == "income" else statement, account, role))


# ---- Korean wording

def _last_hangul(word):
    for ch in reversed(word):
        if "가" <= ch <= "힣":
            return ord(ch) - 0xAC00
    return None


def topic(word):                      # 은 / 는
    code = _last_hangul(word)
    return "은" if code is None or code % 28 else "는"


def obj(word):                        # 을 / 를
    code = _last_hangul(word)
    return "을" if code is None or code % 28 else "를"


def by(word):                         # 으로 / 로
    code = _last_hangul(word)
    return "으로" if code is None or code % 28 not in (0, 8) else "로"


def ymd(text):
    y, m, d = text.split("-")
    return int(y), int(m), int(d)


def period_words(dates):
    """The period in words, from its dates. Returns (text, instant)."""
    if len(dates) == 1:
        y, m, d = ymd(dates[0])
        return f"{y}년 {m}월 말", True
    (y1, m1, d1), (y2, m2, d2) = ymd(dates[0]), ymd(dates[1])
    if d1 != 1:
        raise Stop(f"a period that does not start on the first of a month: {dates}")
    if y1 == y2:
        return f"{y1}년 {m1}월부터 {m2}월까지", False
    return f"{y1}년 {m1}월부터 {y2}년 {m2}월까지", False


# ---- written numbers

def ways_for(value, rng):
    mag = abs(value)
    options = []
    if mag >= JO:
        options += ["jo_eok"] * 5 + ["eok"] * 2
    elif mag >= 10 * EOK:
        options += ["eok"] * 6
    if mag >= MILLION and mag % MILLION == 0:
        options += ["million"] * 2
    options += ["won"]
    return rng.choice(options)


def can_write(value, way):
    mag = abs(value)
    if way == "jo_eok":
        return mag >= JO
    if way == "eok":
        return mag >= 10 * EOK
    if way == "million":
        return mag >= MILLION and mag % MILLION == 0
    return True


def write_amount(value, way):
    """(text as written in the document, the number without its last unit word, the value the text stands for)."""
    sign = "-" if value < 0 else ""
    mag = abs(value)
    if way == "won":
        body = f"{mag:,}"
        return f"{sign}{body}원", f"{sign}{body}", value
    if way == "million":
        body = f"{mag // MILLION:,}백만"
        return f"{sign}{body}원", f"{sign}{body}", value
    eok = int((Decimal(mag) / EOK).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if way == "jo_eok" and eok % 10000:
        body = f"{eok // 10000:,}조 {eok % 10000:,}억"
    else:
        body = f"{eok:,}억"
    return f"{sign}{body}원", f"{sign}{body}", (-1 if value < 0 else 1) * eok * EOK


def one_decimal(x):
    return Decimal(x).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def pct_change(new, old):
    return (Decimal(new) - Decimal(old)) / abs(Decimal(old)) * 100


def percent_of(num, den):
    return Decimal(num) / Decimal(den) * 100


# ---- statements

def amount(cell, slot):
    return {"slot": slot, "kind": "amount", "fs": cell["fs"], "cells": [cell], "truth": cell["value"]}


def build_statements(main, other, interim, rng):
    """The truthful statements of a document, or None when the set cannot fill it. Also the slots filled otherwise."""
    R = INTERIM if interim else ANNUAL
    cur = R["cur"]
    inc = [a for a in INCOME_IDS if main.get("income", a, cur)]
    bal = [a for a in BALANCE_IDS if main.get("BS", a, R["end"])]
    cash = [a for a in CASH_IDS if main.get("CF", a, R["cum"] if interim else cur)]
    out, fills, used = [], [], set()

    def take(slot, st, account, role, source=main):
        cell = source.get(st, account, role) if account else None
        key = (source.fs, st, account, role)
        if cell is None or key in used:
            return False
        used.add(key)
        out.append(amount(cell, slot))
        return True

    def pick(ids, i):
        return ids[i] if i < len(ids) else None

    wanted = []
    if interim:
        wanted += [("income_three_months", "income", pick(inc, i), R["cur"]) for i in range(3)]
        wanted += [("income_cumulative", "income", pick(inc, i), R["cum"]) for i in range(2)]
        wanted += [("income_year_earlier", "income", pick(inc, 0), rng.choice([R["pcur"], R["pcum"]]))]
        wanted += [("balance_end", "BS", pick(bal, 0), R["end"]), ("balance_previous_end", "BS", pick(bal, 1), R["pend"]),
                   ("cash_flow", "CF", pick(cash, 0), R["cum"])]
    else:
        wanted += [("income_year", "income", pick(inc, i), R["cur"]) for i in range(4)]
        wanted += [("income_previous_year", "income", pick(inc, 0), R["prev"]),
                   ("income_year_before", "income", pick(inc, 1), R["prev2"]),
                   ("balance_end", "BS", pick(bal, 0), R["end"]), ("balance_previous_end", "BS", pick(bal, 1), R["pend"]),
                   ("cash_flow", "CF", pick(cash, 0), R["cur"])]
    missing = [slot for slot, st, account, role in wanted if not take(slot, st, account, role)]

    # one amount from the company's other statements
    if other is not None:
        other_inc = [a for a in INCOME_IDS if other.get("income", a, cur)]
        other_bal = [a for a in BALANCE_IDS if other.get("BS", a, R["end"])]
        if not (take("other_statements", "income", pick(other_inc, 0), cur, other)
                or take("other_statements", "BS", pick(other_bal, 2), R["end"], other)):
            missing.append("other_statements")
    else:
        missing.append("other_statements")

    # two derived figures
    def change():
        pairs = [(R["cur"], R["pcur"] if interim else R["prev"])] + ([(R["cum"], R["pcum"])] if interim else [])
        for account in inc[:3]:
            for new_role, old_role in pairs:
                new, old = main.get("income", account, new_role), main.get("income", account, old_role)
                if new and old and new["value"] > 0 and old["value"] > 0:
                    truth = pct_change(new["value"], old["value"])
                    if abs(one_decimal(truth)) >= Decimal("0.2"):
                        return {"slot": "percent_change", "kind": "pct_change", "fs": main.fs, "cells": [new, old],
                                "truth": truth}
        return None

    def margin():
        for role in ([R["cur"], R["cum"]] if interim else [R["cur"]]):
            num = main.get("income", "dart_OperatingIncomeLoss", role)
            den = main.get("income", "ifrs-full_Revenue", role)
            if num and den and 0 < num["value"] < den["value"]:
                return {"slot": "margin", "kind": "margin", "fs": main.fs, "cells": [num, den],
                        "truth": percent_of(num["value"], den["value"])}
        num, den = main.get("BS", "ifrs-full_Liabilities", R["end"]), main.get("BS", "ifrs-full_Equity", R["end"])
        if num and den and num["value"] > 0 and den["value"] > 0:
            return {"slot": "debt_ratio", "kind": "debt_ratio", "fs": main.fs, "cells": [num, den],
                    "truth": percent_of(num["value"], den["value"])}
        return None

    for name, make in (("percent_change", change), ("margin_or_debt_ratio", margin)):
        st = make()
        if st is None:
            missing.append(name)
        else:
            out.append(st)

    # a slot the set cannot fill is filled from another kind: more plain amounts, in a fixed order
    spare = []
    ends = [R["end"], R["pend"]]
    spans = [R["cum"], R["pcum"], R["pcur"]] if interim else [R["cur"], R["prev"], R["prev2"]]
    for i in range(max(len(bal), len(cash), len(inc))):
        for role in ends:
            spare.append(("BS", pick(bal, i), role))
        for role in (spans[:2] if interim else spans):
            spare.append(("CF", pick(cash, i), role))
        for role in spans:
            spare.append(("income", pick(inc, i), role))
    for slot in missing:
        while spare:
            st, account, role = spare.pop(0)
            if take("fill:" + slot, st, account, role):
                fills.append(slot)
                break
    if len(out) != STATEMENTS:
        return None, fills
    return out, fills


def plant(statements, main, other, interim, rng):
    """Put a wrong number into four statements. Returns the kinds planted and the kinds that had to be replaced."""
    kinds = ["a", "b", "c", rng.choice(["d", "e"])] if interim else ["b", "c", "d", "e"]
    sets = {main.fs: main}
    if other is not None:
        sets[other.fs] = other
    free = list(range(len(statements)))
    rng.shuffle(free)
    planted, replaced = [], []

    def shown_amount(st, kind):
        cell = st["cells"][0]
        source = sets[cell["fs"]]
        if kind == "a":
            role = SWAP_A.get(cell["role"]) if cell["statement"] in ("IS", "CIS") else None
            return source.cells.get((cell["statement"], cell["account"], role)) if role else None
        if kind == "b":
            role = SWAP_B.get(cell["role"])
            return source.cells.get((cell["statement"], cell["account"], role)) if role else None
        if kind == "c":
            twin = sets.get("OFS" if cell["fs"] == "CFS" else "CFS")
            if twin is None:
                return None
            st_name = "income" if cell["statement"] in ("IS", "CIS") else cell["statement"]
            return twin.get(st_name, cell["account"], cell["role"])
        return None

    def try_amount(i, kind):
        st = statements[i]
        if st["kind"] != "amount":
            return False
        truth = st["truth"]
        if kind == "d":
            for _ in range(20):
                factor = rng.choice([rng.uniform(1.03, 1.30), rng.uniform(0.70, 0.97)])
                value = int(round(truth * factor))
                if value % MILLION and truth % MILLION == 0:
                    value -= value % MILLION                     # keep the look of the true number
                if _far_enough(st, value):
                    st["planted"] = {"kind": "d", "factor": round(factor, 4), "shown_value": value}
                    return True
            return False
        cell = shown_amount(st, kind)
        if cell is None or not _far_enough(st, cell["value"]):
            return False
        st["planted"] = {"kind": kind, "shown_value": cell["value"],
                         "shown_cell": {k: cell[k] for k in ("fs", "statement", "account", "role", "column")}}
        return True

    def try_derived(i):
        st = statements[i]
        if st["kind"] == "amount":
            return False
        options = []
        if st["kind"] == "pct_change":
            options.append(("sign", None))
        new_or_num, old_or_den = st["cells"]
        for how, swap in (("a", SWAP_A), ("b", SWAP_B)):
            role = swap.get(new_or_num["role"])
            if how == "a" and new_or_num["statement"] not in ("IS", "CIS"):
                role = None
            cell = sets[new_or_num["fs"]].cells.get((new_or_num["statement"], new_or_num["account"], role)) if role else None
            if cell is not None and cell is not old_or_den and cell["value"] > 0:
                options.append((how, cell))
        twin = sets.get("OFS" if new_or_num["fs"] == "CFS" else "CFS")
        if twin is not None:
            st_name = "income" if new_or_num["statement"] in ("IS", "CIS") else new_or_num["statement"]
            cell = twin.get(st_name, new_or_num["account"], new_or_num["role"])
            if cell is not None and cell["value"] > 0:
                options.append(("c", cell))
        rng.shuffle(options)
        for how, cell in options:
            if how == "sign":
                st["planted"] = {"kind": "e", "how": "sign"}
                return True
            if st["kind"] == "pct_change":
                if cell["value"] == old_or_den["value"]:
                    continue
                shown = pct_change(cell["value"], old_or_den["value"])
            else:
                shown = percent_of(cell["value"], old_or_den["value"])
            if abs(one_decimal(shown) - one_decimal(st["truth"])) >= Decimal("0.3") and abs(one_decimal(shown)) >= Decimal("0.2") \
                    and abs(one_decimal(shown)) < Decimal("100000"):
                st["planted"] = {"kind": "e", "how": "input_" + how, "shown_percent": str(shown),
                                 "shown_cell": {k: cell[k] for k in ("fs", "statement", "account", "role", "column")}}
                return True
        return False

    for kind in kinds:
        done = False
        for i in list(free):
            if (try_derived(i) if kind == "e" else try_amount(i, kind)):
                free.remove(i)
                planted.append(kind)
                done = True
                break
        if not done:                                             # a kind the company cannot have is replaced by d
            for i in list(free):
                if try_amount(i, "d"):
                    free.remove(i)
                    planted.append("d")
                    replaced.append(kind)
                    done = True
                    break
        if not done:
            return None, replaced
    return planted, replaced


def _far_enough(st, value):
    """A planted amount is kept only if no way of writing lets the true amount pass for it: the true and the shown
    amount differ by at least two units of the coarsest way either could be written in, and by at least 1%."""
    truth = st["truth"]
    if value == truth:
        return False
    coarsest = EOK if max(abs(value), abs(truth)) >= 10 * EOK else MILLION if max(abs(value), abs(truth)) >= MILLION else 1
    return abs(value - truth) >= 2 * coarsest and abs(value - truth) * 100 >= abs(truth)


# ---- the document

def sentence(st, rng):
    """Write the statement's line. Fills st['text'], st['number'] (as the scan shows it) and st['shown']."""
    basis = BASIS[st["fs"]]
    planted = st.get("planted")
    if st["kind"] == "amount":
        cell = st["cells"][0]
        value = planted["shown_value"] if planted else st["truth"]
        way = ways_for(value, rng)
        text, number, stands_for = write_amount(value, way)
        period, instant = period_words(cell["period"])
        name = cell["name"]
        form = rng.choice([1, 2, 3])
        if form == 1:
            line = f"{basis} 기준 {period}의 {name}{topic(name)} {text}이다."
        elif form == 2:
            line = f"{basis} 기준 {name}{topic(name)} {period}{'에' if instant else ''} {text}이었다."
        else:
            line = f"{period}의 {basis} 기준 {name}{topic(name)} {text}으로 집계됐다."
        st.update(text=line, number=number, written=text, way=way, shown=str(stands_for))
        return
    a, b = st["cells"]
    period, _ = period_words(a["period"])
    shown = Decimal(planted["shown_percent"]) if planted and "shown_percent" in planted else st["truth"]
    if st["kind"] == "pct_change":
        up = shown > 0
        if planted and planted.get("how") == "sign":
            up = not up
        size = abs(one_decimal(shown))
        old_period, _ = period_words(b["period"])
        name = a["name"]
        line = (f"{basis} 기준 {period}의 {name}{topic(name)} 한 해 전 같은 기간({old_period})보다 "
                f"{size}% {'늘었다' if up else '줄었다'}.")
        st.update(text=line, number=f"{size}%", written=f"{size}%", way="percent", shown=str(size if up else -size))
        return
    label = "영업이익률" if st["kind"] == "margin" else "부채비율"
    size = one_decimal(shown)
    line = (f"{basis} 기준 {period}의 {label}({a['name']}{obj(a['name'])} {b['name']}{by(b['name'])} 나눈 비율)은 "
            f"{size}%이다.")
    st.update(text=line, number=f"{size}%", written=f"{size}%", way="percent", shown=str(size))


def locator(corp, year, report, cell):
    return {"dart": corp, "year": year, "report": report, "fs": cell["fs"], "statement": cell["statement"],
            "account": cell["account"], "column": cell["column"]}


def claim(n, line, st, corp, year, report):
    body = {"id": f"s{n:02d}", "line": line, "quote": st["text"], "number": st["number"]}
    cells = [locator(corp, year, report, c) for c in st["cells"]]
    if st["kind"] == "amount":
        body.update(kind="lookup", operands={"of": cells[0]})
    elif st["kind"] == "pct_change":
        body.update(kind="derived", formula="pct_change", operands={"new": cells[0], "old": cells[1]})
    elif st["kind"] == "margin":
        body.update(kind="derived", formula="share", operands={"part": cells[0], "whole": cells[1]})
    else:
        body.update(kind="derived", formula="ratio", operands={"num": cells[0], "den": cells[1]})
    return body


def make_document(company, sets, position, rng):
    """One document for a company, or the reason there is none."""
    reasons = []
    for step in range(len(TURN)):
        report = TURN[(position + step) % len(TURN)]
        fs = "CFS" if (report, "CFS") in sets else "OFS" if (report, "OFS") in sets else None
        if fs is None:
            reasons.append(f"{report}: no set")
            continue
        main = sets[(report, fs)]
        other = sets.get((report, "OFS" if fs == "CFS" else "CFS"))
        if not main.usable or (other is not None and not other.usable):
            reasons.append(f"{report}: two periods of a set have the same dates")
            continue
        interim = report != "annual"
        statements, fills = build_statements(main, other, interim, rng)
        if statements is None:
            reasons.append(f"{report}: fewer than {STATEMENTS} statements can be written")
            continue
        planted, replaced = plant(statements, main, other, interim, rng)
        if planted is None:
            reasons.append(f"{report}: four errors cannot be planted")
            continue
        rng.shuffle(statements)
        for st in statements:
            sentence(st, rng)
        title = f"{main.year}년 {REPORT_TITLE[report]}"
        head = [f"# {company['name']} {title} 메모", "",
                f"{company['name']}의 {title}에 실린 재무제표 수치를 정리했다. 금액은 원화다.", ""]
        lines = head + ["- " + st["text"] for st in statements]
        gold, claims = [], []
        for n, st in enumerate(statements, start=1):
            line = len(head) + n
            claims.append(claim(n, line, st, main.corp, main.year, report))
            p = st.get("planted")
            gold.append({"id": f"s{n:02d}", "line": line, "slot": st["slot"], "kind": st["kind"], "fs": st["fs"],
                         "number": st["number"], "written": st["written"], "way": st["way"],
                         "truth": str(st["truth"]), "shown": st["shown"],
                         "planted": p["kind"] if p else None, "planted_detail": p,
                         "cells": [dict(locator(main.corp, main.year, report, c), role=c["role"], period=c["period"],
                                        name=c["name"], value=str(c["value"])) for c in st["cells"]]})
        info = {"corp_code": main.corp, "company": company["name"], "group": company["group"], "report": report,
                "year": main.year, "fs": fs, "other_statements": other is not None, "statements": len(statements),
                "planted": planted, "replaced_by_d": replaced, "slots_filled_from_another_kind": fills,
                "passed_over": reasons}
        return {"info": info, "document": "\n".join(lines) + "\n", "gold": gold,
                "plan": {"document": "document.md", "claims": claims}}, reasons
    return None, reasons


def main(argv=None):
    ap = argparse.ArgumentParser(description="E5 generator (docs/eval-plan.md, section E5)")
    ap.add_argument("--held-out", action="store_true", help="the held-out companies (release run only)")
    ap.add_argument("--e4-out", default=E4_OUT, help="E4's built sets (default bench/dart/out)")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    args = ap.parse_args(argv)
    name = "heldout" if args.held_out else "dev"
    source = os.path.join(args.e4_out, name)
    try:
        manifest = read_json(os.path.join(source, "manifest.json"))
    except OSError as e:
        print(f"error: E4's sets are not there: {e}", file=sys.stderr)
        return 2
    drawn = {k["corp_code"] for k in ((manifest.get("draw") or {}).get("kept") or [])}
    companies, by_corp = [], {}
    for s in manifest["sets"]:
        if s["corp_code"] not in by_corp:
            group = "development" if name == "dev" else "drawn" if s["corp_code"] in drawn else "named"
            by_corp[s["corp_code"]] = {"corp_code": s["corp_code"], "name": s["company"], "group": group, "sets": {}}
            companies.append(by_corp[s["corp_code"]])
        gold = read_json(os.path.join(source, "sets", s["folder"], "gold.json"))
        by_corp[s["corp_code"]]["sets"][(s["report"], s["fs"])] = Set(gold)
    out_dir = os.path.join(args.out, name)
    docs, left_out = [], []
    try:
        for position, company in enumerate(companies):
            rng = random.Random(f"{SEED}:{company['corp_code']}")
            made, reasons = make_document(company, company["sets"], position, rng)
            if made is None:
                left_out.append({"corp_code": company["corp_code"], "company": company["name"], "reasons": reasons})
                continue
            folder = os.path.join(out_dir, "docs", company["corp_code"])
            write_text(os.path.join(folder, "document.md"), made["document"])
            write_json(os.path.join(folder, "gold.json"), {"document": made["info"], "statements": made["gold"]})
            write_json(os.path.join(folder, "gold-plan.json"), made["plan"])
            docs.append(made["info"])
    except Stop as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    kinds = {}
    for d in docs:
        for k in d["planted"]:
            kinds[k] = kinds.get(k, 0) + 1
    totals = {"companies": len(companies), "documents": len(docs), "left_out": len(left_out),
              "statements": sum(d["statements"] for d in docs), "planted": sum(len(d["planted"]) for d in docs),
              "planted_by_kind": dict(sorted(kinds.items())),
              "by_report": {r: sum(1 for d in docs if d["report"] == r) for r in TURN}}
    write_json(os.path.join(out_dir, "manifest.json"),
               {"generator": VERSION, "seed": SEED, "set": name, "source": os.path.relpath(source, ROOT).replace("\\", "/"),
                "e4_builder": manifest.get("builder_version"), "documents": docs, "left_out": left_out, "totals": totals})
    print(json.dumps(totals, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
