#!/usr/bin/env python3
"""Deterministic E1 benchmark generator for receipts (see docs/eval-plan.md, section E1).

Usage: python bench/generate.py [--seed 20261119] [--out bench/out]

Writes synthetic documents, their sources (CSV, JSON, Markdown table, XLSX), gold plans in the
format of skills/receipts/references/plan-format.md, coverage plans and abstention cases.
Standard library only. All truth arithmetic uses decimal.Decimal.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import sys
import zipfile
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal as D, getcontext
from xml.sax.saxutils import escape

getcontext().prec = 60

DEV_SEED, HELD_OUT_SEED = 20261119, 20261120
GEN_VERSION = "1"
YEARS = [2021, 2022, 2023, 2024, 2025]
N_DOCS, STMTS_PER_DOC = 64, 15
SOURCE_TYPES = ["csv", "json", "md", "xlsx"]
ABSTAIN_PER_KIND = 24
ERROR_TYPES = {1: "digit_typo", 2: "wrong_period", 3: "wrong_scale", 4: "direction_flip",
               5: "stale_value", 6: "rounding", 7: "wrong_denominator", 8: "wrong_count"}
MONEY = ("revenue", "cost")
SEGS = {"en": ["North", "South", "East", "West", "Central", "Coastal"],
        "ko": ["서울", "부산", "대구", "인천", "광주", "대전"]}
HDR = {"en": dict(year="year", seg="region", revenue="revenue", cost="cost", units="units",
                  price="price", staff="staff", margin="margin"),
       "ko": dict(year="연도", seg="지역", revenue="매출", cost="비용", units="판매량",
                  price="단가", staff="인원", margin="이익률")}
SALES_COLS = ["year", "seg", "revenue", "cost", "units", "price"]
STAFF_COLS = ["year", "seg", "staff", "margin"]
SHEET = {"en": {"data": "Data", "sales": "Sales", "staff": "Staff"},
         "ko": {"data": "자료", "sales": "매출", "staff": "인원"}}
# Direction lexicon copied from plan-format.md section 4 (used only to assert our sentences are clean).
LEX = {"up": "rose risen increased increase grew grown growth up higher gained climbed jumped surged "
             "expanded 증가 늘었 늘어 늘며 늘고 상승 올랐 올라 오른 성장 확대 높아 급등".split(),
       "down": "fell fallen decreased decrease declined decline dropped drop down lower shrank contracted "
               "slipped plunged reduced 감소 줄었 줄어 줄며 줄고 하락 내렸 내려 내린 떨어 축소 낮아 급락".split()}
VERBS = {"en": {"up": ["rose", "increased", "grew", "climbed", "jumped"],
                "down": ["fell", "decreased", "declined", "slipped", "dropped"]},
         "ko": {"up": ["증가했다", "늘었다", "상승했다", "올랐다"],
                "down": ["감소했다", "줄었다", "하락했다", "떨어졌다"]}}
HEDGES = {"en": ["about", "approximately", "roughly", "around"], "ko": ["약", "대략"]}
E3, E4, E6, E8, E9, E12 = (D(10) ** k for k in (3, 4, 6, 8, 9, 12))


# ---------------------------------------------------------------- numbers and formats
def rnd(v: D, q: D, mode: str) -> D:
    return (v / q).to_integral_value(rounding=mode) * q


def is_negative_tie(v: D, q: D) -> bool:
    frac = abs(v / q) % 1
    return v < 0 and frac == D("0.5")


def F(**kw) -> dict:
    f = dict(style="plain", dec=0, scale=D(1), stxt="", prefix="", suffix="", after="",
             hedge=False, signed=False, minus="-", big=None, small=None)
    f.update(kw)
    return f


def fmt_digits(x: D, dec: int) -> str:
    q = D(1).scaleb(-dec)
    assert x == x.quantize(q), (x, dec)
    return format(x.quantize(q), f",.{dec}f")


def q_of(f: dict, w: D) -> D:
    if f["hedge"]:
        s = str(int(abs(w)))
        return D(10) ** (len(s) - len(s.rstrip("0")))
    if f["style"] == "compound":
        return f["small"][1]
    return D(1).scaleb(-f["dec"]) * f["scale"]


def renderable(f: dict, w: D) -> bool:
    a = abs(w)
    if f["prefix"] and w < 0:
        return False
    if f["style"] == "compound":
        big = a // f["big"][1]
        small = (a - big * f["big"][1]) / f["small"][1]
        return big >= 1 and 0 < small < 10000 and small == small.to_integral_value()
    digits = a / f["scale"]
    if f["stxt"] and digits < 1:
        return False
    return digits == digits.quantize(D(1).scaleb(-f["dec"]))


def render(f: dict, w: D) -> str:
    a = abs(w)
    if f["style"] == "compound":
        (bt, bs), (st, ss) = f["big"], f["small"]
        big = a // bs
        body = f"{fmt_digits(big, 0)}{bt} {fmt_digits((a - big * bs) / ss, 0)}{st}"
    else:
        body = fmt_digits(a / f["scale"], f["dec"]) + f["stxt"]
    sign = f["minus"] if w < 0 else ("+" if f["signed"] else "")
    return sign + f["prefix"] + body + f["suffix"]


def parse_token(tok: str, f: dict) -> D:
    neg = tok[:1] in ("-", "−")
    if f["style"] == "compound":
        m = re.search(r"([\d,]+)\D+([\d,]+)", tok)
        a = D(m.group(1).replace(",", "")) * f["big"][1] + D(m.group(2).replace(",", "")) * f["small"][1]
    else:
        a = D(re.search(r"\d[\d,]*(?:\.\d+)?", tok).group().replace(",", "")) * f["scale"]
    return -a if neg else a


def fmt_label(f: dict) -> str:
    if f["hedge"]:
        return "hedged_int"
    if f["style"] == "compound":
        return "compound_" + f["big"][0] + f["small"][0]
    if f["suffix"] in ("%", " percent", "퍼센트"):
        return "pct_signed" if f["signed"] else "pct"
    if f["suffix"] in (" percentage points", "pp", "%p", "%포인트"):
        return "pp"
    if f["suffix"] in ("x", "배"):
        return "multiple"
    if f["stxt"]:
        return "scale_" + f["stxt"].strip()
    if f["prefix"]:
        return "money_plain"
    return "decimal" if f["dec"] else "integer"


def money_fmt(lang: str, v: D, rng: random.Random) -> dict:
    v = abs(v)
    if lang == "en":
        opts = []
        for sc, letter, word in ((E9, "B", " billion"), (E6, "M", " million"), (E3, "K", " thousand")):
            if v >= sc:
                opts.append(F(prefix="$", scale=sc, stxt=letter, dec=rng.choice([1, 2])))
                if sc >= E6:
                    opts.append(F(prefix="$", scale=sc, stxt=word, dec=rng.choice([1, 2])))
                break
        opts.append(F(prefix="$"))
        return rng.choice(opts)
    if v >= E12:
        return rng.choice([F(scale=E12, stxt="조", dec=rng.choice([1, 2]), after=" 원"),
                           F(style="compound", big=("조", E12), small=("억", E8), after=" 원"),
                           F(scale=E8, stxt="억", after=" 원")])
    if v >= E8:
        return rng.choice([F(scale=E8, stxt="억", after=" 원"),
                           F(style="compound", big=("억", E8), small=("만", E4), after=" 원")])
    if v >= E4:
        return F(scale=E4, stxt="만", after=" 원")
    return F(after="원")


def int_fmt(lang: str, v: D, rng: random.Random, after: str = "") -> dict:
    if lang == "en" and abs(v) >= E6 and rng.random() < 0.5:
        return F(scale=E6, stxt=" million", dec=rng.choice([1, 2]), after=after)
    if lang == "ko" and abs(v) >= D(100000) and rng.random() < 0.3:
        return F(scale=E4, stxt="만", after=after)
    return F(after=after)


def pct_fmt(lang, rng, dec=None, signed=False):
    suffix = "%" if rng.random() < 0.8 else (" percent" if lang == "en" else "퍼센트")
    return F(suffix=suffix, dec=dec if dec is not None else rng.choice([1, 2]), signed=signed,
             minus=rng.choice(["-", "-", "−"]))


def pp_fmt(lang, rng):
    return F(suffix=rng.choice([" percentage points", "pp"] if lang == "en" else ["%p", "%포인트"]), dec=1)


def mult_fmt(lang, rng):
    return F(suffix="x" if lang == "en" else "배", dec=rng.choice([1, 2]))


def cop(f: dict) -> str:  # Korean copula after the token and its trailing word
    return "이었다" if f["after"].strip() in ("원", "건", "명") or f["stxt"] in ("만", "억", "조") and not f["after"] \
        else "였다"


# ---------------------------------------------------------------- synthetic data
def walk(rng, start: D, n: int, quant: D, lo=-150, hi=250):
    out, v = [start], start
    for _ in range(n - 1):
        g = 0
        while abs(g) < 10:
            g = rng.randint(lo, hi)
        v = (v * (1 + D(g) / 1000)).quantize(quant)
        out.append(v)
    return out


class Doc:
    def __init__(self, rng: random.Random, doc_id: str, lang: str, stype: str, layout: str | None = None):
        self.id, self.lang, self.stype = doc_id, lang, stype
        self.layout = layout or rng.choice(["single", "split"])
        self.segs = rng.sample(SEGS[lang], rng.randint(4, 6))
        self.scaled = rng.random() < 0.5
        self.unit = (E3 if lang == "en" else E6) if self.scaled else D(1)
        series = {}
        for s in self.segs:
            if lang == "en":
                base = D(rng.randint(5000, 900000)) if self.scaled else D(rng.randint(5000000, 900000000))
                price = walk(rng, D(rng.randint(500, 25000)) / 100, 5, D("0.01"), -80, 120)
            else:
                base = D(rng.randint(10000, 2000000)) if self.scaled else D(rng.randint(10000, 2000000) * 1000000
                                                                           + rng.randint(0, 999999))
                price = walk(rng, D(rng.randint(1000, 90000)), 5, D(1), -80, 120)
            rev = walk(rng, base, 5, D(1))
            margins = []
            for _ in YEARS:
                m = D(rng.randint(-50, 250)) / 10
                while margins and m == margins[-1]:
                    m = D(rng.randint(-50, 250)) / 10
                margins.append(m)
            series[s] = dict(revenue=rev, cost=[(r * D(rng.randint(550, 1050)) / 1000).quantize(D(1)) for r in rev],
                             units=walk(rng, D(rng.randint(10000, 300000)), 5, D(1)),
                             staff=walk(rng, D(rng.randint(100, 9000)), 5, D(1)),
                             price=price, margin=margins)
        self.rows = []
        for k, y in enumerate(YEARS):
            for s in self.segs:
                r = dict(year=y, seg=s)
                r.update({c: series[s][c][k] for c in ("revenue", "cost", "units", "price", "staff", "margin")})
                self.rows.append(r)
        self._place(rng)

    # where things live
    def _place(self, rng):
        self.tables = ({"data": SALES_COLS + ["staff", "margin"]} if self.layout == "single"
                       else {"sales": SALES_COLS, "staff": STAFF_COLS})
        self.col_table = {c: t for t, cols in self.tables.items() for c in cols if c not in ("year", "seg")}
        self.tloc, self.files = {}, {}
        names = list(self.tables)
        if self.stype == "csv":
            for t in names:
                self.files[f"{t}.csv"] = [t]
                self.tloc[t] = {"file": f"{t}.csv"}
        elif self.stype == "json":
            if len(names) == 2 and rng.random() < 0.5:
                for t in names:
                    self.files[f"{t}.json"] = [t]
                    self.tloc[t] = {"file": f"{t}.json", "pointer": "/rows"}
            else:
                self.files["data.json"] = names
                for t in names:
                    self.tloc[t] = {"file": "data.json", "pointer": "/rows" if len(names) == 1 else f"/{t}"}
        elif self.stype == "md":
            self.glossary = rng.random() < 0.5
            self.files["tables.md"] = names
            for k, t in enumerate(names):
                self.tloc[t] = {"file": "tables.md", "table": k + 1 + int(self.glossary)}
        else:
            self.header_row = rng.choice([1, 2])
            self.files["book.xlsx"] = names
            for t in names:
                self.tloc[t] = {"file": "book.xlsx", "sheet": SHEET[self.lang][t]}
                if self.header_row != 1:
                    self.tloc[t]["header_row"] = self.header_row

    def hdr(self, col):
        h = HDR[self.lang][col]
        return h.capitalize() if self.lang == "en" and self.stype in ("md", "xlsx") else h

    def idx(self, y, s):
        return YEARS.index(y) * len(self.segs) + self.segs.index(s)

    def truth(self, i, col):
        return self.rows[i][col] * (self.unit if col in MONEY else 1)

    def mult(self, col):
        return col in MONEY and self.unit != 1

    def cell(self, rng, i, col, mult=True, form=None):
        t = self.col_table[col]
        base = self.tloc[t]
        form = form or ("pointer" if self.stype == "json" and rng.random() < 0.25 else
                        "a1" if self.stype == "xlsx" and rng.random() < 0.25 else "row")
        if form == "pointer":
            loc = {"file": base["file"], "pointer": f"{base['pointer']}/{i}/{self.hdr(col)}"}
        elif form == "a1":
            c = self.tables[t].index(col)
            loc = {"file": base["file"], "sheet": base["sheet"], "cell": f"{chr(65 + c)}{self.header_row + 1 + i}"}
        else:
            r = self.rows[i]
            loc = dict(base)
            loc["row"] = {self.hdr("year"): str(r["year"]), self.hdr("seg"): r["seg"]}
            loc["column"] = self.hdr(col)
        if mult and self.mult(col):
            loc["multiplier"] = int(self.unit)
        return loc

    def many(self, where, col):
        loc = dict(self.tloc[self.col_table[col]])
        loc["where"] = {self.hdr(k): str(v) for k, v in where.items()}
        loc["column"] = self.hdr(col)
        return loc

    def rowsloc(self, where, col, op, value):
        loc = dict(self.tloc[self.col_table[col]])
        loc["where"] = {self.hdr(k): str(v) for k, v in where.items()}
        loc["test"] = {"column": self.hdr(col), "op": op, "value": value}
        return loc

    def sel(self, where):
        return [i for i, r in enumerate(self.rows) if all(r[k] == v for k, v in where.items())]

    def agg_ops(self, rng, where, col):
        """operands for sum/mean/min/max over a filter; scaled money uses explicit items with multipliers."""
        if self.mult(col):
            return {"items": [self.cell(rng, i, col) for i in self.sel(where)]}
        return {"cells": self.many(where, col)}


# ---------------------------------------------------------------- statement builders
def nb_year(rng, y):
    return rng.choice([x for x in (y - 1, y + 1) if x in YEARS])


def B(formula, operands, cv, fmt, line, alts=None, direction=False, vi=0):
    return dict(formula=formula, operands=operands, cv=cv, fmt=fmt, line=line, alts=alts or {},
                direction=direction, vi=vi)


def b_value(doc, rng, which):
    en = doc.lang == "en"
    cols = {"money": ["revenue", "cost"], "int": ["units", "staff"], "pct": ["margin"], "dec": ["price"]}[which]
    col = rng.choice(cols)
    y, s = rng.choice(YEARS), rng.choice(doc.segs)
    i = doc.idx(y, s)
    cv = doc.truth(i, col)
    if which == "money":
        f = money_fmt(doc.lang, cv, rng)
    elif which == "int":
        f = F(after="" if en else ("건" if col == "units" else "명"))
    elif which == "pct":
        f = pct_fmt(doc.lang, rng, dec=1)
    else:
        f = F(prefix="$", dec=2) if en else F(after="원")
    alts = {2: doc.truth(doc.idx(nb_year(rng, y), s), col)}
    if i > 0:
        alts[5] = doc.truth(i - 1, col)
    yt = f"FY{y}" if en and rng.random() < 0.25 else str(y)

    def line(tok, d):
        if en:
            return {"revenue": f"{s} revenue was {tok} in {yt}.", "cost": f"{s} operating cost was {tok} in {yt}.",
                    "units": f"{s} sold {tok} units in {yt}.", "staff": f"{s} employed {tok} people in {yt}.",
                    "margin": f"The margin in {s} was {tok} in {yt}.",
                    "price": f"The selling price in {s} was {tok} in {yt}."}[col]
        noun = {"revenue": "매출은", "cost": "비용은", "units": "판매량은", "staff": "인원은", "margin": "이익률은",
                "price": "단가는"}[col]
        return f"{y}년 {s} 지역 {noun} {tok}{f['after']}{cop(f)}."
    return B("value", {"of": doc.cell(rng, i, col)}, cv, f, line, alts)


def change_pair(doc, rng, cols):
    col = rng.choice(cols)
    y, s = rng.choice(YEARS[1:]), rng.choice(doc.segs)
    return col, y, s, doc.idx(y, s), doc.idx(y - 1, s)


def b_diff(doc, rng, which):
    en = doc.lang == "en"
    col, y, s, inew, iold = change_pair(doc, rng, {"money": ["revenue", "cost"], "int": ["staff"],
                                                     "pp": ["margin"]}[which])
    cv = doc.truth(inew, col) - doc.truth(iold, col)
    if cv == 0:
        return None
    f = money_fmt(doc.lang, cv, rng) if which == "money" else (
        F(after="" if en else "명") if which == "int" else pp_fmt(doc.lang, rng))
    vi = rng.randrange(len(VERBS[doc.lang]["up"]))

    def line(tok, d):
        v = VERBS[doc.lang][d][vi]
        if en:
            noun = {"revenue": f"{s} revenue", "cost": f"{s} operating cost", "staff": f"{s} headcount",
                    "margin": f"The margin in {s}"}[col]
            by = "" if col == "margin" else "by "
            return f"{noun} {v} {by}{tok} in {y} vs {y - 1}."
        noun = {"revenue": "매출은", "cost": "비용은", "staff": "인원은", "margin": "이익률은"}[col]
        return f"{s} 지역 {noun} {y - 1}년 대비 {y}년에 {tok}{f['after']} {v}."
    ops = {"a": doc.cell(rng, inew, col), "b": doc.cell(rng, iold, col)}
    return B("difference", ops, cv, f, line, direction=True, vi=vi)


def b_pct(doc, rng, signed):
    en = doc.lang == "en"
    col, y, s, inew, iold = change_pair(doc, rng, ["revenue", "units", "staff"])
    a, b = doc.truth(inew, col), doc.truth(iold, col)
    if a == b:
        return None
    cv, alt = (a - b) / abs(b) * 100, (a - b) / abs(a) * 100
    f = pct_fmt(doc.lang, rng, signed=signed)
    vi = rng.randrange(len(VERBS[doc.lang]["up"]))
    noun_en = {"revenue": "revenue", "units": "unit sales", "staff": "headcount"}[col]
    noun_ko = {"revenue": "매출", "units": "판매량", "staff": "인원"}[col]

    def line(tok, d):
        if signed:
            return (f"The change in {s} {noun_en} in {y} vs {y - 1} was {tok}." if en else
                    f"{s} 지역 {noun_ko}의 {y - 1}년 대비 {y}년 변동률은 {tok}{cop(f)}.")
        v = VERBS[doc.lang][d][vi]
        return (f"{s} {noun_en} {v} {tok} in {y} vs {y - 1}." if en else
                f"{s} 지역 {noun_ko}은 {y - 1}년 대비 {y}년에 {tok} {v}.")
    ops = {"new": doc.cell(rng, inew, col), "old": doc.cell(rng, iold, col)}
    return B("pct_change", ops, cv, f, line, {7: alt}, direction=not signed, vi=vi)


def b_ratio(doc, rng, which):
    en = doc.lang == "en"
    y = rng.choice(YEARS)
    s1, s2 = rng.sample(doc.segs, 2)
    i1, i2 = doc.idx(y, s1), doc.idx(y, s2)
    if which == "mult":
        if doc.truth(i1, "revenue") < doc.truth(i2, "revenue") and rng.random() < 0.8:
            s1, s2, i1, i2 = s2, s1, i2, i1
        num, den, cv, f = (i1, "revenue"), (i2, "revenue"), doc.truth(i1, "revenue") / doc.truth(i2, "revenue"), \
            mult_fmt(doc.lang, rng)
    elif which in ("pct", "plain"):
        num, den = (i1, "cost"), (i1, "revenue")
        cv = doc.truth(i1, "cost") / doc.truth(i1, "revenue") * (100 if which == "pct" else 1)
        f = pct_fmt(doc.lang, rng, dec=1) if which == "pct" else F(dec=2)
    else:
        num, den = (i1, "revenue"), (i1, "staff")
        cv = doc.truth(i1, "revenue") / doc.truth(i1, "staff")
        f = money_fmt(doc.lang, cv, rng)

    def line(tok, d):
        if en:
            return {"mult": f"{s1} revenue was {tok} that of {s2} in {y}.",
                    "pct": f"{s1} operating cost was {tok} of revenue in {y}.",
                    "plain": f"The cost-to-revenue ratio in {s1} was {tok} in {y}.",
                    "money": f"Revenue per employee in {s1} was {tok} in {y}."}[which]
        return {"mult": f"{y}년 {s1} 지역 매출은 {s2} 지역 매출의 {tok}{cop(f)}.",
                "pct": f"{y}년 {s1} 지역 비용은 매출의 {tok}{cop(f)}.",
                "plain": f"{y}년 {s1} 지역의 매출 대비 비용 비율은 {tok}{cop(f)}.",
                "money": f"{y}년 {s1} 지역 인당 매출은 {tok}{f['after']}{cop(f)}."}[which]
    ops = {"num": doc.cell(rng, *num), "den": doc.cell(rng, *den)}
    return B("ratio", ops, cv, f, line)


def b_share(doc, rng):
    en = doc.lang == "en"
    col = rng.choice(["revenue", "revenue", "units"])
    y, s = rng.choice(YEARS), rng.choice(doc.segs)
    part = doc.truth(doc.idx(y, s), col)
    whole = sum(doc.truth(i, col) for i in doc.sel({"year": y}))
    cv, alt = part / whole * 100, part / (whole - part) * 100
    f = pct_fmt(doc.lang, rng)
    noun_en, noun_ko = ("revenue", "매출") if col == "revenue" else ("unit sales", "판매량")

    def line(tok, d):
        return (f"{s} accounted for {tok} of total {noun_en} in {y}." if en else
                f"{y}년 {s} 지역은 전체 {noun_ko}의 {tok}를 차지했다.")
    ops = {"part": doc.cell(rng, doc.idx(y, s), col, mult=False), "whole": doc.many({"year": y}, col)}
    return B("share", ops, cv, f, line, {7: alt})


def b_sum(doc, rng, which):
    en = doc.lang == "en"
    y = rng.choice(YEARS)
    col = "units" if which in ("int", "hedged") else rng.choice(["revenue", "cost"])
    if which == "items":
        segs = rng.sample(doc.segs, 2)
        where_rows = lambda yy: [doc.idx(yy, s) for s in segs]  # noqa: E731
        ops = {"items": [doc.cell(rng, i, col) for i in where_rows(y)]}
    elif which == "hedged":
        where_rows = lambda yy: list(range(len(doc.rows)))  # noqa: E731
        ops = {"cells": doc.many({}, col)}
    else:
        where_rows = lambda yy: doc.sel({"year": yy})  # noqa: E731
        ops = doc.agg_ops(rng, {"year": y}, col)
    calc = lambda yy: sum(doc.truth(i, col) for i in where_rows(yy))  # noqa: E731
    cv = calc(y)
    if which == "hedged":
        f = F(hedge=True, after="" if en else "건")
        hedge = rng.choice(HEDGES[doc.lang])
    elif col == "units":
        f = int_fmt(doc.lang, cv, rng, after="" if en else "건")
    else:
        f = money_fmt(doc.lang, cv, rng)
    noun_en = {"revenue": "revenue", "cost": "operating cost"}.get(col)
    noun_ko = {"revenue": "매출", "cost": "비용"}.get(col)

    def line(tok, d):
        if which == "hedged":
            return (f"Across all regions and years, shipments totalled {hedge} {tok} units." if en else
                    f"전 기간 전 지역 판매량은 {hedge} {tok}{f['after']}{cop(f)}.")
        if which == "int":
            return (f"All regions together sold {tok} units in {y}." if en else
                    f"{y}년 전 지역 판매량 합계는 {tok}{f['after']}{cop(f)}.")
        if which == "items":
            return (f"{segs[0]} and {segs[1]} {noun_en} combined was {tok} in {y}." if en else
                    f"{y}년 {segs[0]} 지역과 {segs[1]} 지역의 {noun_ko}을 합하면 {tok}{f['after']}{cop(f)}.")
        return (f"Total {noun_en} across all regions was {tok} in {y}." if en else
                f"{y}년 전 지역 {noun_ko} 합계는 {tok}{f['after']}{cop(f)}.")
    alts = {} if which == "hedged" else {2: calc(nb_year(rng, y))}
    return B("sum", ops, cv, f, line, alts)


def b_meanminmax(doc, rng, func):
    en = doc.lang == "en"
    col = rng.choice(["margin", "price", "units"] if func == "mean" else ["margin", "units", "revenue"])
    y = rng.choice(YEARS)
    py = {"mean": lambda xs: sum(xs) / len(xs), "min": min, "max": max}[func]
    calc = lambda yy: py([doc.truth(i, col) for i in doc.sel({"year": yy})])  # noqa: E731
    cv = calc(y)
    if col == "margin":
        f = pct_fmt(doc.lang, rng, dec=2 if func == "mean" else 1)
    elif col == "price":
        f = F(prefix="$", dec=2) if en else F(after="원")
    elif col == "units":
        f = F(after="" if en else "건")
    else:
        f = money_fmt(doc.lang, cv, rng)
    noun_en = {"margin": "margin", "price": "selling price", "units": "unit sales", "revenue": "revenue"}[col]
    noun_ko = {"margin": "이익률", "price": "단가", "units": "판매량", "revenue": "매출"}[col]
    units_en = " units" if col == "units" else ""

    def line(tok, d):
        if func == "mean":
            part = {"margin": "이익률은", "price": "단가는", "units": "판매량은"}[col]
            return (f"The average {noun_en} across regions was {tok}{units_en} in {y}." if en else
                    f"{y}년 지역 평균 {part} {tok}{f['after']}{cop(f)}.")
        word_en = "highest" if func == "max" else "lowest"
        word_ko = "최고치는" if func == "max" else "최저치는"
        return (f"The {word_en} regional {noun_en} was {tok}{units_en} in {y}." if en else
                f"{y}년 지역별 {noun_ko} {word_ko} {tok}{f['after']}{cop(f)}.")
    return B(func, doc.agg_ops(rng, {"year": y}, col), cv, f, line, {2: calc(nb_year(rng, y))})


def b_count(doc, rng):
    en = doc.lang == "en"
    variant = rng.choice(["year_dd", "year_neg", "seg_dd"])
    y, s = rng.choice(YEARS), rng.choice(doc.segs)
    where = {"seg": s} if variant == "seg_dd" else {"year": y}
    op, val = ("<", "0") if variant == "year_neg" else (">=", "10")
    test = (lambda m: m < 0) if op == "<" else (lambda m: m >= 10)
    cv = D(sum(1 for i in doc.sel(where) if test(doc.truth(i, "margin"))))
    if cv < 1:
        return None
    f = F()
    k = rng.choice([-2, -1, 1, 2])

    def line(tok, d):
        if en:
            return {"year_dd": f"Double-digit margins were posted by {tok} regions in {y}.",
                    "year_neg": f"Negative margins were posted by {tok} regions in {y}.",
                    "seg_dd": f"{s} posted double-digit margins in {tok} of the five years covered."}[variant]
        return {"year_dd": f"{y}년 이익률이 두 자릿수인 지역은 {tok}개였다.",
                "year_neg": f"{y}년 이익률이 마이너스인 지역은 {tok}개였다.",
                "seg_dd": f"{s} 지역은 다섯 해 가운데 {tok}개 연도에 두 자릿수 이익률을 기록했다."}[variant]
    alt = cv + k if cv + k >= 1 else cv + abs(k)
    return B("count", {"rows": doc.rowsloc(where, "margin", op, val)}, cv, f, line, {8: alt})


KINDS = {  # kind: (builder, compatible planted error types)
    "value_money": (lambda d, r: b_value(d, r, "money"), {1, 2, 3, 5, 6}),
    "value_int": (lambda d, r: b_value(d, r, "int"), {1, 2, 3, 5, 6}),
    "value_pct": (lambda d, r: b_value(d, r, "pct"), {1, 2, 3, 5, 6}),
    "value_dec": (lambda d, r: b_value(d, r, "dec"), {1, 2, 3, 5, 6}),
    "diff_money": (lambda d, r: b_diff(d, r, "money"), {1, 3, 4, 6}),
    "diff_int": (lambda d, r: b_diff(d, r, "int"), {1, 3, 4, 6}),
    "diff_pp": (lambda d, r: b_diff(d, r, "pp"), {1, 3, 4, 6}),
    "pct_dir": (lambda d, r: b_pct(d, r, False), {1, 3, 4, 6, 7}),
    "pct_signed": (lambda d, r: b_pct(d, r, True), {1, 3, 6, 7}),
    "ratio_mult": (lambda d, r: b_ratio(d, r, "mult"), {1, 3, 6}),
    "ratio_pct": (lambda d, r: b_ratio(d, r, "pct"), {1, 3, 6}),
    "ratio_plain": (lambda d, r: b_ratio(d, r, "plain"), {1, 3, 6}),
    "ratio_money": (lambda d, r: b_ratio(d, r, "money"), {1, 3, 6}),
    "share": (b_share, {1, 3, 6, 7}),
    "sum_money": (lambda d, r: b_sum(d, r, "money"), {1, 2, 3, 6}),
    "sum_items": (lambda d, r: b_sum(d, r, "items"), {1, 2, 3, 6}),
    "sum_int": (lambda d, r: b_sum(d, r, "int"), {1, 2, 3, 6}),
    "sum_hedged": (lambda d, r: b_sum(d, r, "hedged"), set()),
    "mean": (lambda d, r: b_meanminmax(d, r, "mean"), {1, 2, 3, 6}),
    "minmax": (lambda d, r: b_meanminmax(d, r, r.choice(["min", "max"])), {1, 2, 3, 6}),
    "count": (b_count, {8}),
}


# ---------------------------------------------------------------- judging (our own reading of the contract)
def judged_ok(w: D, q: D, cv: D, wdir: str | None) -> bool:
    if wdir:
        if (wdir == "up") != (cv > 0) or cv == 0:
            return False
        w, cv = abs(w), abs(cv)
    return w in (rnd(cv, q, ROUND_HALF_UP), rnd(cv, q, ROUND_HALF_EVEN), rnd(cv, q, ROUND_DOWN))


def dir_hits(text: str):
    low, sub, word = text.lower(), set(), set()
    for d, words in LEX.items():
        for x in words:
            if x in low:
                sub.add(d)
            if x.isascii() and re.search(r"(?<![a-z])" + re.escape(x) + r"(?![a-z])", low) or \
                    not x.isascii() and x in low:
                word.add(d)
    return sub, word


def typo(tok: str, rng) -> str:
    pos = [k for k, ch in enumerate(tok) if ch.isdigit()]
    p = rng.choice(pos)
    lead = (p == 0 or not (tok[p - 1].isdigit() or tok[p - 1] in ",.")) and p + 1 < len(tok) and \
        (tok[p + 1].isdigit() or tok[p + 1] == ",")
    choices = [c for c in "0123456789" if c != tok[p] and not (lead and c == "0")]
    return tok[:p] + rng.choice(choices) + tok[p + 1:]


LADDERS = [[("K", E3), ("M", E6), ("B", E9)], [(" thousand", E3), (" million", E6), (" billion", E9)],
           [("만", E4), ("억", E8), ("조", E12)]]


def scale_error(f: dict, w: D, rng):
    if f["style"] == "compound":
        pairs = [(("억", E8), ("만", E4)), (("조", E12), ("억", E8))]
        cur = pairs.index((f["big"], f["small"]))
        big = abs(w) // f["big"][1]
        small = (abs(w) - big * f["big"][1]) / f["small"][1]
        nb, ns = pairs[1 - cur]
        return dict(f, big=nb, small=ns), big * nb[1] + small * ns[1]
    for lad in LADDERS:
        names = [n for n, _ in lad]
        if f["stxt"] in names:
            k = names.index(f["stxt"])
            nk = rng.choice([x for x in (k - 1, k + 1) if 0 <= x < len(lad)])
            return dict(f, stxt=lad[nk][0], scale=lad[nk][1]), w / f["scale"] * lad[nk][1]
    if rng.random() < 0.5:
        return f, w * 10
    return dict(f, dec=f["dec"] + 1), w / 10


class State:
    def __init__(self):
        self.correct = self.trunc = 0


def realize(b: dict, etype: int | None, rng, state: State):
    f, cv = b["fmt"], b["cv"]
    tdir = (("up" if cv > 0 else "down") if b["direction"] else None)
    mag = abs(cv) if tdir else cv
    if f["hedge"]:
        n = len(str(int(mag)))
        if n < 4:
            return None
        w = rnd(mag, D(10) ** (n - 2), ROUND_HALF_UP)
        q = q_of(f, w)
    else:
        q = q_of(f, D(0))
        if is_negative_tie(mag, q):
            return None
        w = rnd(mag, q, ROUND_HALF_UP)
    if (tdir or f["signed"]) and abs(rnd(mag, q, ROUND_DOWN)) < q:
        return None
    if w == 0 or not renderable(f, w):
        return None
    trunc = False
    if etype is None:
        t = rnd(mag, q, ROUND_DOWN)
        if not f["hedge"] and t not in (w, rnd(mag, q, ROUND_HALF_EVEN)) and t != 0 and renderable(f, t) \
                and state.trunc < D("0.10") * (state.correct + 1):
            w, trunc = t, True
        tok, wdir, f2, w2 = render(f, w), tdir, f, w
        assert judged_ok(w2, q_of(f2, w2), cv, wdir), ("correct statement not accepted", tok, cv)
    else:
        wdir, f2 = tdir, f
        if etype == 1:
            tok = typo(render(f, w), rng)
            w2 = parse_token(tok, f)
            if not renderable(f, w2) or render(f, w2) != tok:
                return None
        elif etype in (2, 5, 7, 8):
            if etype not in b["alts"]:
                return None
            alt = b["alts"][etype]
            if tdir and (alt > 0) != (cv > 0):
                return None
            w2 = rnd(abs(alt) if tdir else alt, q, ROUND_HALF_UP)
            if w2 == 0 or not renderable(f, w2):
                return None
            tok = render(f, w2)
        elif etype == 3:
            f2, w2 = scale_error(f, w, rng)
            if not renderable(f2, w2):
                return None
            tok = render(f2, w2)
        elif etype == 4:
            if not tdir:
                return None
            wdir, w2 = ("down" if tdir == "up" else "up"), w
            tok = render(f, w)
        elif etype == 6:
            k = rng.choice([1, 2]) * q
            w2 = w + k if (w - k <= 0 and (tdir or w > 0)) or rng.random() < 0.5 else w - k
            if w2 == 0 or not renderable(f, w2):
                return None
            tok = render(f, w2)
        if judged_ok(w2, q_of(f2, w2), cv, wdir):  # must be outside tolerance: re-draw
            return None
        assert w2 not in (rnd(mag, q, m) for m in (ROUND_HALF_UP, ROUND_HALF_EVEN, ROUND_DOWN)) or etype == 4
    text = b["line"]("\x00", wdir)
    start = text.index("\x00")
    text = text.replace("\x00", tok)
    hits_sub, hits_word = dir_hits(text)
    want = {wdir} if wdir else set()
    assert hits_sub == hits_word == want, ("direction words", text, hits_sub, hits_word)
    if etype is None:
        state.correct += 1
        state.trunc += int(trunc)
    return dict(text=text, start=start, end=start + len(tok), token=tok, written=w2, q=q_of(f2, w2), cv=cv,
                wdir=wdir, tdir=tdir, truncated_ok=trunc, fmt_label=fmt_label(f2))


def build_statement(doc, rng, etype, state, used_text):
    kinds = [k for k, (_, compat) in KINDS.items() if etype is None or etype in compat]
    for _ in range(400):
        kind = rng.choice(kinds)
        b = KINDS[kind][0](doc, rng)
        if b is None:
            continue
        r = realize(b, etype, rng, state)
        if r is None or r["text"] in used_text:
            continue
        r.update(kind=kind, formula=b["formula"], operands=b["operands"], planted=etype)
        return r
    raise RuntimeError(f"could not build a statement for error type {etype} in {doc.id}")


def choose_quote(line: str, s: int, e: int, tok: str, want: set):
    for q in (line, line[s:], line[:e], line[s:e]):
        if q.count(tok) == 1 and dir_hits(q)[0] == want:
            return q
    raise AssertionError(("no unique quote", line, tok))


def make_claim(cid, lineno, line, st):
    s, e = st["start"], st["end"]
    want = {st["wdir"]} if st["wdir"] else set()
    c = {"id": cid, "line": lineno, "quote": choose_quote(line, s, e, st["token"], want), "number": st["token"]}
    if st["formula"] == "value":
        c["kind"] = "lookup"
    else:
        c["kind"], c["formula"] = "derived", st["formula"]
    c["operands"] = st["operands"]
    if st.get("plan_direction"):
        c["direction"] = st["plan_direction"]
    return c


# ---------------------------------------------------------------- report layout
COMPANIES = {"en": ["Northwind Traders", "Bluefin Logistics", "Harbor Foods", "Maple Retail", "Granite Tools",
                    "Silverline Media"],
             "ko": ["한빛상사", "가람유통", "누리식품", "다온전자", "솔빛물산", "해든건설"]}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def dist(text, frag, rule, at=0):
    s = text.index(frag, at)
    return {"start": s, "end": s + len(frag), "text": frag, "rule": rule}


def year_distractors(text, s, e):
    out = []
    for m in re.finditer(r"FY(?:19|20)\d\d|(?<![\d,.])(?:19|20)\d\d(?![\d,.])", text):
        if m.end() <= s or m.start() >= e:
            out.append({"start": m.start(), "end": m.end(), "text": m.group(),
                        "rule": "date" if m.group().startswith("FY") else "year"})
    return out


def compose_report(doc, stmts, rng, title_suffix=True):
    en = doc.lang == "en"
    lines = []  # (text, statement index or None, distractors)
    title = rng.choice(COMPANIES[doc.lang]) + (" regional performance review" if en else " 지역별 실적 점검")
    lines.append((f"# {title}", None, []))
    lines.append(("", None, []))
    if title_suffix:
        mo, day = rng.randint(1, 12), rng.randint(1, 28)
        if en:
            dt = rng.choice([f"2026-{mo:02d}-{day:02d}", f"{MONTHS[mo - 1]} {day}, 2026", f"{day} {MONTHS[mo - 1]} 2026"])
            t = f"Prepared on {dt}."
            dd = dist(t, dt[re.search(r"\d", dt).start():], "date")
        else:
            dt = rng.choice([f"2026-{mo:02d}-{day:02d}", f"2026년 {mo}월 {day}일"])
            t = f"{dt} 작성."
            dd = dist(t, dt, "date")
        lines.append((t, None, [dd]))
        ver = f"v{rng.randint(1, 3)}.{rng.randint(0, 9)}.{rng.randint(0, 9)}"
        if rng.random() < 0.5:
            k = rng.randint(3, 50)
            t = (f"Built with pipeline {ver} using `top_n = {k}`." if en else f"파이프라인 {ver}에서 `top_n = {k}` 설정으로 만들었다.")
            ds = [dist(t, ver, "version"), dist(t, str(k), "code", t.index("`"))]
        else:
            t = f"Built with pipeline {ver}." if en else f"파이프라인 {ver}에서 만들었다."
            ds = [dist(t, ver, "version")]
        lines.append((t, None, ds))
        url = f"https://data.example.org/{rng.choice([2024, 2025])}/table-{rng.randint(2, 99)}.csv"
        t = f"Raw data: {url}" if en else f"원자료: {url}"
        lines.append((t, None, [dist(t, url, "url")]))
        lines.append(("", None, []))
        h = "## 1. Summary" if en else "## 1. 요약"
        lines.append((h, None, [dist(h, "1", "heading_number")]))
        lines.append(("", None, []))
    groups = [stmts[:6], stmts[6:10], stmts[10:]] if title_suffix else [stmts]
    for gi, group in enumerate(groups):
        if gi == 1:
            lines.append(("", None, []))
            h = "## 2. Results" if en else "## 2. 세부 결과"
            lines.append((h, None, [dist(h, "2", "heading_number")]))
            lines.append(("", None, []))
        if gi == 2:
            lines.append(("", None, []))
        for k, st in enumerate(group):
            prefix = f"{k + 1}. " if gi == 1 else ""
            st["start"] += len(prefix)
            st["end"] += len(prefix)
            text = prefix + st["text"]
            ds = [dist(text, str(k + 1), "list_marker")] if prefix else []
            ds += year_distractors(text, st["start"], st["end"])
            lines.append((text, st, ds))
    if title_suffix:
        lines.append(("", None, []))
        qn = rng.randint(1, 4)
        t = f"Q{qn} figures are preliminary and may be revised." if en else f"{qn}분기 수치는 잠정치이며 바뀔 수 있다."
        lines.append((t, None, [dist(t, f"Q{qn}" if en else f"{qn}분기", "date")]))
        lines.append(("", None, []))
        lines.append(("```text", None, []))
        for name, val in (("min_rows", str(rng.randint(10, 500))), ("threshold", f"0.{rng.randint(1, 99):02d}")):
            t = f"{name} = {val}"
            lines.append((t, None, [dist(t, val, "code")]))
        lines.append(("```", None, []))
    return lines


# ---------------------------------------------------------------- source writers
def wtext(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def sval(v):
    return v if isinstance(v, str) else format(v, "f") if isinstance(v, D) else str(v)


def jval(v):
    if isinstance(v, D):
        if v == v.to_integral_value():
            return int(v)
        x = float(v)
        assert D(repr(x)) == v
        return x
    return v


def xlsx_bytes_write(path, sheets):
    strings, index = [], {}

    def sidx(s):
        if s not in index:
            index[s] = len(strings)
            strings.append(s)
        return index[s]
    sheet_xml = []
    for _, rows in sheets:
        out = []
        for r, row in enumerate(rows, start=1):
            cells = []
            for c, v in enumerate(row):
                ref = f"{chr(65 + c)}{r}"
                if v is None:
                    continue
                if isinstance(v, str):
                    cells.append(f'<c r="{ref}" t="s"><v>{sidx(v)}</v></c>')
                else:
                    cells.append(f'<c r="{ref}"><v>{sval(v)}</v></c>')
            out.append(f'<row r="{r}">{"".join(cells)}</row>')
        sheet_xml.append('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="http://schemas.'
                         'openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(out) +
                         "</sheetData></worksheet>")
    ns = "http://schemas.openxmlformats.org"
    ct = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="{ns}/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.'
          'spreadsheetml.sheet.main+xml"/>' +
          "".join(f'<Override PartName="/xl/worksheets/sheet{k + 1}.xml" ContentType="application/vnd.openxmlformats-'
                  f'officedocument.spreadsheetml.worksheet+xml"/>' for k in range(len(sheets))) +
          '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.'
          'spreadsheetml.sharedStrings+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.'
          'openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
    rel = f"{ns}/officeDocument/2006/relationships"
    rels = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{ns}/package/2006/'
            f'relationships"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>')
    wb = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<workbook xmlns="{ns}/spreadsheetml/2006/main" '
          f'xmlns:r="{rel}"><sheets>' +
          "".join(f'<sheet name="{escape(n)}" sheetId="{k + 1}" r:id="rId{k + 1}"/>' for k, (n, _) in enumerate(sheets))
          + "</sheets></workbook>")
    n = len(sheets)
    wbrels = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{ns}/package/2006/'
              'relationships">' +
              "".join(f'<Relationship Id="rId{k + 1}" Type="{rel}/worksheet" Target="worksheets/sheet{k + 1}.xml"/>'
                      for k in range(n)) +
              f'<Relationship Id="rId{n + 1}" Type="{rel}/sharedStrings" Target="sharedStrings.xml"/>'
              f'<Relationship Id="rId{n + 2}" Type="{rel}/styles" Target="styles.xml"/></Relationships>')
    sst = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<sst xmlns="{ns}/spreadsheetml/2006/main" '
           f'count="{len(strings)}" uniqueCount="{len(strings)}">' +
           "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>")
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<styleSheet xmlns="{ns}/spreadsheetml/2006/'
              'main"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill>'
              '<patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    parts = [("[Content_Types].xml", ct), ("_rels/.rels", rels), ("xl/workbook.xml", wb),
             ("xl/_rels/workbook.xml.rels", wbrels), ("xl/sharedStrings.xml", sst), ("xl/styles.xml", styles)]
    parts += [(f"xl/worksheets/sheet{k + 1}.xml", x) for k, x in enumerate(sheet_xml)]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts:
            zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data.encode("utf-8"))


def write_sources(doc: Doc, folder: str):
    def table_rows(t):
        cols = doc.tables[t]
        return [doc.hdr(c) for c in cols], [[r[c] for c in cols] for r in doc.rows]
    en = doc.lang == "en"
    note = ("Money columns are in thousands of US dollars." if en else "금액 단위는 백만 원이다.") if doc.scaled else \
        ("Money columns are in US dollars." if en else "금액 단위는 원이다.")
    for fname, tabs in doc.files.items():
        path = os.path.join(folder, fname)
        if doc.stype == "csv":
            h, rows = table_rows(tabs[0])
            wtext(path, "\n".join(",".join(sval(v) for v in row) for row in [h] + rows) + "\n")
        elif doc.stype == "json":
            obj = {"note": note}
            for t in tabs:
                h, rows = table_rows(t)
                key = "rows" if doc.tloc[t]["pointer"] == "/rows" else t
                obj[key] = [{hh: jval(v) for hh, v in zip(h, row)} for row in rows]
            wtext(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
        elif doc.stype == "md":
            out = ["# Source tables" if en else "# 원자료 표", "", note, ""]
            if doc.glossary:
                out += ["| Term | Meaning |" if en else "| 용어 | 뜻 |", "|---|---|"]
                out += [f"| {doc.hdr(c)} | {c} |" for c in ("seg", "revenue", "margin")] + [""]
            for t in tabs:
                h, rows = table_rows(t)
                out += ["| " + " | ".join(h) + " |", "|" + "---|" * len(h)]
                out += ["| " + " | ".join(sval(v) for v in row) + " |" for row in rows] + [""]
            wtext(path, "\n".join(out))
        else:
            sheets = []
            for t in tabs:
                h, rows = table_rows(t)
                body = [[v if isinstance(v, str) else D(v) for v in row] for row in rows]
                pre = [[note] + [None] * (len(h) - 1)] if doc.header_row == 2 else []
                sheets.append((SHEET[doc.lang][t], pre + [h] + body))
            xlsx_bytes_write(path, sheets)


# ---------------------------------------------------------------- documents
def write_doc(doc, stmts, rng, folder, n_remove=None):
    os.makedirs(folder, exist_ok=True)
    lines = compose_report(doc, stmts, rng, title_suffix=n_remove is not None)
    wtext(os.path.join(folder, "report.md"), "\n".join(t for t, _, _ in lines) + "\n")
    write_sources(doc, folder)
    claims, gold, distractors = [], [], []
    for ln, (text, st, ds) in enumerate(lines, start=1):
        for d in ds:
            distractors.append(dict(line=ln, **d))
        if st is None:
            continue
        cid = f"s{len(claims) + 1:02d}"
        st["id"], st["line"] = cid, ln
        if st["wdir"] and rng.random() < 0.5:
            st["plan_direction"] = st["wdir"]
        assert text[st["start"]:st["end"]] == st["token"]
        claims.append(make_claim(cid, ln, text, st))
        gold.append(dict(id=cid, line=ln, start=st["start"], end=st["end"], text=st["token"], lang=doc.lang,
                         formula=st["formula"], kind=st["kind"], source_type=doc.stype,
                         planted=ERROR_TYPES[st["planted"]] if st["planted"] else None,
                         truncated_ok=st["truncated_ok"], removed_in_coverage=False, format=st["fmt_label"],
                         truth=format(st["cv"], "f"), written=format(st["written"], "f"), q=format(st["q"], "f"),
                         direction_written=st["wdir"], direction_true=st["tdir"],
                         plan_direction=st.get("plan_direction")))
    plan = {"document": "report.md", "claims": claims}
    wtext(os.path.join(folder, "claims.gold.json"), json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    removed = []
    if n_remove:
        removed = sorted(rng.sample([c["id"] for c in claims], n_remove))
        for g in gold:
            g["removed_in_coverage"] = g["id"] in removed
        cov = {"document": "report.md", "claims": [c for c in claims if c["id"] not in removed]}
        wtext(os.path.join(folder, "claims.coverage.json"), json.dumps(cov, ensure_ascii=False, indent=2) + "\n")
    g = dict(doc_id=doc.id, lang=doc.lang, source_type=doc.stype, layout=doc.layout, scaled=doc.scaled,
             source_files=sorted(doc.files), statements=gold, distractors=distractors, removed_in_coverage=removed)
    wtext(os.path.join(folder, "gold.json"), json.dumps(g, ensure_ascii=False, indent=2) + "\n")
    return claims, gold


def break_locator(doc, claim, kind, variant, rng):
    """Replace the value claim's locator with one that matches zero or several cells."""
    st = claim["_st"]
    i, col = st["_i"], st["_col"]
    loc = doc.cell(rng, i, col, form="row")
    seg_h, year_h = doc.hdr("seg"), doc.hdr("year")
    if kind == "zero_match":
        if variant == "missing_value":
            loc["row"][seg_h] = "Atlantis" if doc.lang == "en" else "제주"
        elif variant == "missing_year":
            loc["row"][year_h] = "2031"
        elif variant == "missing_column":
            loc["column"] = loc["column"] + ("_total" if doc.lang == "en" else "액")
        else:  # missing container: sheet / table / pointer key; for csv a filter column that does not exist
            if doc.stype == "xlsx":
                loc["sheet"] = "Missing"
            elif doc.stype == "md":
                loc["table"] = 9
            elif doc.stype == "json":
                loc["pointer"] = "/missing"
            else:
                loc["row"]["quarter"] = "Q9"
    else:
        if variant == "drop_segment":
            del loc["row"][seg_h]
        else:
            del loc["row"][year_h]
    claim["operands"] = {"of": loc}


def value_statement(doc, rng, state, used):
    for _ in range(200):
        which = rng.choice(["money", "int", "pct"])
        b = b_value(doc, rng, which)
        loc = b["operands"]["of"]
        r = realize(b, None, rng, state)
        if r is None or r["text"] in used:
            continue
        # recover (row, column) for later locator surgery
        r.update(kind="value_" + which, formula="value", operands=b["operands"], planted=None)
        col = [c for c in HDR[doc.lang] if doc.hdr(c) == loc.get("column")] if "column" in loc else []
        r["_col"] = col[0] if col else None
        return r
    raise RuntimeError("value statement")


def abstain_case(seed_rng, case_id, kind, stype, lang, root, state):
    rng = random.Random(seed_rng.randrange(2 ** 32))
    doc = Doc(rng, case_id, lang, stype, layout="single" if kind in ("missing_source", "corrupt_source") else None)
    used, stmts = set(), []
    while len(stmts) < 3:
        st = value_statement(doc, rng, state, used)
        if kind in ("zero_match", "multi_match") and not stmts:
            if st["_col"] is None:
                continue
            # locate the row index from the statement's row filter
            loc = st["operands"]["of"]
            y, s = int(loc["row"][doc.hdr("year")]), loc["row"][doc.hdr("seg")]
            st["_i"] = doc.idx(y, s)
        used.add(st["text"])
        stmts.append(st)
    folder = os.path.join(root, case_id)
    claims, _ = write_doc(doc, stmts, rng, folder, n_remove=None)
    os.remove(os.path.join(folder, "gold.json"))
    expect = {"case_id": case_id, "kind": kind, "source_type": stype, "lang": lang}
    if kind in ("zero_match", "multi_match"):
        variants = (["missing_value", "missing_year", "missing_column", "missing_container"] if kind == "zero_match"
                    else ["drop_segment", "drop_year"])
        variant = rng.choice(variants)
        c = claims[0]
        c["_st"] = stmts[0]
        break_locator(doc, c, kind, variant, rng)
        del c["_st"]
        wtext(os.path.join(folder, "claims.json"),
              json.dumps({"document": "report.md", "claims": claims}, ensure_ascii=False, indent=2) + "\n")
        expect.update(expect="unverifiable", claim_id=c["id"], variant=variant,
                      expected_reason="locator_no_match" if kind == "zero_match" else "locator_multiple")
    else:
        os.replace(os.path.join(folder, "claims.gold.json"), os.path.join(folder, "claims.json"))
        src = os.path.join(folder, sorted(doc.files)[0])
        if kind == "missing_source":
            os.remove(src)
            variant = "deleted"
        else:
            data = open(src, "rb").read()
            if stype == "json":
                cut = data[: len(data) * 3 // 5]
                variant = "truncated_json"
                try:
                    json.loads(cut.decode("utf-8", "replace"))
                    raise AssertionError("truncated json still parses")
                except ValueError:
                    pass
                data = cut
            elif stype == "xlsx":
                variant = rng.choice(["truncated_zip", "not_a_zip"])
                data = data[: len(data) // 2] if variant == "truncated_zip" else b"this is not a workbook\n"
            else:
                variant = "invalid_utf8"
                mid = data.index(b"\n", len(data) // 2) + 1
                data = data[:mid] + b"\xff\xc3\x28\x80" + data[mid:]
                try:
                    data.decode("utf-8")
                    raise AssertionError("corrupt text still decodes")
                except UnicodeDecodeError:
                    pass
            with open(src, "wb") as fh:
                fh.write(data)
        expect.update(expect="exit2", variant=variant, file=os.path.basename(src))
    os.remove(os.path.join(folder, "claims.gold.json")) if os.path.exists(os.path.join(folder, "claims.gold.json")) \
        else None
    wtext(os.path.join(folder, "expect.json"), json.dumps(expect, ensure_ascii=False, indent=2) + "\n")
    return expect


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=DEV_SEED)
    ap.add_argument("--out", default=os.path.join("bench", "out"))
    ap.add_argument("--held-out", action="store_true", help="required to generate the held-out seed")
    a = ap.parse_args(argv)
    if a.seed == HELD_OUT_SEED and not a.held_out:
        sys.exit(f"refusing seed {HELD_OUT_SEED}: it is the held-out seed. Pass --held-out only for the release run.")
    root = os.path.join(a.out, str(a.seed))
    if os.path.exists(root):
        shutil.rmtree(root)
    os.makedirs(root)
    rng = random.Random(a.seed)
    total = N_DOCS * STMTS_PER_DOC
    n_planted = total * 3 // 10
    per_type = n_planted // 8
    slots = [t for t in range(1, 9) for _ in range(per_type)]
    slots += [None] * (total - len(slots))
    rng.shuffle(slots)
    state = State()
    manifest = {"seed": a.seed, "generator_version": GEN_VERSION, "docs": [], "abstain": []}
    all_gold = []
    for k in range(N_DOCS):
        stype, lang = SOURCE_TYPES[k % 4], ("en" if (k // 4) % 2 == 0 else "ko")
        doc = Doc(rng, f"d{k + 1:03d}", lang, stype)
        stmts, used = [], set()
        for j in range(STMTS_PER_DOC):
            st = build_statement(doc, rng, slots[k * STMTS_PER_DOC + j], state, used)
            used.add(st["text"])
            stmts.append(st)
        n_remove = max(1, int((D(STMTS_PER_DOC) / 10).to_integral_value(rounding=ROUND_HALF_UP)))
        _, gold = write_doc(doc, stmts, rng, os.path.join(root, "docs", doc.id), n_remove=n_remove)
        all_gold += gold
        manifest["docs"].append({"id": doc.id, "lang": lang, "source_type": stype, "layout": doc.layout,
                                 "path": f"docs/{doc.id}", "statements": len(gold)})
    kinds = ["zero_match", "multi_match", "missing_source", "corrupt_source"]
    for kind in kinds:
        for n in range(ABSTAIN_PER_KIND):
            cid = f"{kind}_{n + 1:02d}"
            ex = abstain_case(rng, cid, kind, SOURCE_TYPES[n % 4], "en" if (n // 4) % 2 == 0 else "ko",
                              os.path.join(root, "abstain"), State())
            manifest["abstain"].append(dict(ex, path=f"abstain/{cid}"))

    def tally(key, items):
        out = {}
        for x in items:
            out[str(key(x))] = out.get(str(key(x)), 0) + 1
        return dict(sorted(out.items()))
    counts = {
        "documents": len(manifest["docs"]), "statements": len(all_gold),
        "by_language": tally(lambda g: g["lang"], all_gold),
        "documents_by_source_type": tally(lambda d: d["source_type"], manifest["docs"]),
        "statements_by_source_type": tally(lambda g: g["source_type"], all_gold),
        "by_formula": tally(lambda g: g["formula"], all_gold),
        "by_error_type": tally(lambda g: g["planted"] or "correct", all_gold),
        "by_format": tally(lambda g: f'{g["lang"]}:{g["format"]}', all_gold),
        "truncated_ok": sum(g["truncated_ok"] for g in all_gold),
        "removed_in_coverage": sum(g["removed_in_coverage"] for g in all_gold),
        "abstain": tally(lambda e: e["kind"], manifest["abstain"]),
    }
    manifest["counts"] = counts
    wtext(os.path.join(root, "manifest.json"), json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    # the eval-plan minimums
    assert counts["documents"] >= 60 and counts["statements"] >= 900
    assert counts["by_language"]["en"] == counts["by_language"]["ko"]
    assert all(counts["documents_by_source_type"].get(t, 0) >= 10 for t in SOURCE_TYPES)
    assert set(counts["by_formula"]) == {"value", "difference", "pct_change", "ratio", "share", "sum", "mean", "min",
                                         "max", "count"}, counts["by_formula"]
    planted = sum(v for k, v in counts["by_error_type"].items() if k != "correct")
    assert planted * 10 == counts["statements"] * 3
    assert all(counts["by_error_type"][n] == per_type for n in ERROR_TYPES.values())
    assert all(v >= 20 for v in counts["abstain"].values()) and len(counts["abstain"]) == 4
    for lang in ("en", "ko"):
        fmts = {k.split(":", 1)[1] for k in counts["by_format"] if k.startswith(lang + ":")}
        need = {"pct", "pct_signed", "pp", "multiple", "integer", "decimal", "hedged_int"}
        assert need <= fmts, (lang, need - fmts)
    print(f"seed {a.seed}: wrote {root}")
    print(json.dumps({k: v for k, v in counts.items() if k != "by_format"}, indent=1, ensure_ascii=True))
    print("formats: " + json.dumps(counts["by_format"], ensure_ascii=True))


if __name__ == "__main__":
    main()
