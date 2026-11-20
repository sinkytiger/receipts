#!/usr/bin/env python3
"""receipts: every number in a document gets a receipt, or gets flagged.

The model writes a plan (claims.json) that says where each number comes from.
This script reads the sources, recomputes, and gives the verdict.
Contract: skills/receipts/references/plan-format.md. Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import http.client
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, getcontext

getcontext().prec = 60

VERSION = "0.1.1"


class StopError(Exception):
    """The run cannot continue (exit code 2). Nothing is passed."""


class LocatorError(Exception):
    """One claim cannot be checked. The verdict is `unverifiable` with this reason."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


# --------------------------------------------------------------------------- numbers in text

NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
K_SMALL = {"십": Decimal(10), "백": Decimal(100), "천": Decimal(1000)}
K_BIG = {"만": Decimal(10) ** 4, "억": Decimal(10) ** 8, "조": Decimal(10) ** 12}
KGROUP = rf"{NUM}\s?(?:[십백천]?[만억조]|[십백천])"
KOR = rf"{KGROUP}(?:\s?{KGROUP})*"
SUFFIX = (
    r"(?:\s?(?P<pp>%p|%\s?포인트|퍼센트\s?포인트|(?i:percentage\s+points?)|pp(?![A-Za-z]))"
    r"|\s?(?P<bp>bps?(?![A-Za-z]))"
    r"|\s?(?P<pct>%|(?i:percent)(?![A-Za-z])|퍼센트)"
    r"|(?P<scale>[KkMBT])(?![A-Za-z])"
    r"|\s?(?P<sword>(?i:thousand|million|billion|trillion|mn|bn))(?![A-Za-z])"
    r"|(?P<mult>x(?![A-Za-z])|배))"
)
TOKEN_RE = re.compile(
    r"(?P<sign>(?<![\w.,%$])[-−+])?(?:(?P<cur>US\$|\$|₩|€|£|¥)(?P<sign2>[-−+])?)?"
    rf"(?:(?P<kor>{KOR})|(?P<num>{NUM}){SUFFIX}?)"
)
KGROUP_RE = re.compile(rf"(?P<n>{NUM})\s?(?:(?P<s>[십백천])?(?P<b>[만억조])|(?P<s2>[십백천]))")
SCALE = {"K": 3, "k": 3, "M": 6, "B": 9, "T": 12,
         "thousand": 3, "million": 6, "mn": 6, "billion": 9, "bn": 9, "trillion": 12}
KNOWN_UNITS = {"kg", "g", "mg", "km", "m", "cm", "mm", "ms", "s", "h", "hr", "hrs", "min", "GB", "MB", "KB", "TB",
               "GW", "MW", "kW", "kWh", "MWh", "GWh", "Hz", "kHz", "MHz", "GHz", "px", "pt"}
HEDGE_RE = re.compile(r"(?i)(?:\b(?:about|approximately|roughly|around|nearly|almost)|~|약|대략|거의)\s*$")
UP_EN = r"rose|risen|increased|increase|grew|grown|growth|up|higher|gained|climbed|jumped|surged|expanded"
DOWN_EN = r"fell|fallen|decreased|decrease|declined|decline|dropped|drop|down|lower|shrank|contracted|slipped|plunged|reduced"
UP_RE = re.compile(rf"(?i)\b(?:{UP_EN})\b|증가|늘었|늘어|늘며|늘고|상승|올랐|올라|오른(?!쪽)|성장|확대|높아|급등")
DOWN_RE = re.compile(rf"(?i)\b(?:{DOWN_EN})\b|감소|줄었|줄어|줄며|줄고|하락|내렸|내려|내린|떨어|축소|낮아|급락")

MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?"
         r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?")
SKIP_RULES = [  # (rule, regex, group to mark). Order is priority: an earlier match wins.
    ("code", re.compile(r"`[^`]+`"), 0),
    ("url", re.compile(r"(?:https?://|www\.)\S+|\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), 0),
    ("date", re.compile(r"(?<![A-Za-z0-9_.])\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?![A-Za-z0-9_])\.?"), 0),
    ("date", re.compile(r"(?<![A-Za-z0-9_.])\d{4}\.\s\d{1,2}\.\s\d{1,2}\.?(?!\d)"), 0),
    ("date", re.compile(r"(?<![A-Za-z0-9_.])\d{1,2}/\d{1,2}/\d{4}(?![A-Za-z0-9_])"), 0),
    ("date", re.compile(rf"(?i:\b{MONTH})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?(?!\d)"), 0),
    ("date", re.compile(rf"(?<![A-Za-z0-9_.])\d{{1,2}}(?:st|nd|rd|th)?\s+(?i:{MONTH})(?:,?\s+\d{{4}})?(?!\d)"), 0),
    ("date", re.compile(rf"(?i:\b{MONTH}),?\s+\d{{4}}(?!\d)"), 0),
    ("date", re.compile(r"(?<![A-Za-z0-9_.])\d{4}\s?년\s?\d{1,2}\s?월(?:\s?\d{1,2}\s?일)?"), 0),
    ("date", re.compile(r"(?<![A-Za-z0-9_.,])\d{1,2}\s?월(?:\s?\d{1,2}\s?일)?"), 0),
    ("date", re.compile(r"(?<![A-Za-z0-9_.])(?:(?:19|20)\d{2}\s?)?(?:[1-4]Q|Q[1-4]|H[12])(?![A-Za-z0-9])"
                        r"(?:\s?(?:(?:19|20)\d{2}|['’]\d{2})(?!\d))?"), 0),
    ("date", re.compile(r"(?<![A-Za-z0-9_.])(?:FY|CY)\s?['’]?\d{2,4}(?!\d)"), 0),
    ("date", re.compile(r"(?<![A-Za-z0-9_.,])[1-4]\s?(?:/\s?4\s?)?분기"), 0),
    ("year", re.compile(r"(?<![A-Za-z0-9_.,])(?:19|20)\d{2}\s?년(?:도)?"), 0),
    ("time", re.compile(r"(?<![A-Za-z0-9_.:])\d{1,2}:\d{2}(?::\d{2})?(?![A-Za-z0-9_:])(?:\s?(?i:am|pm)(?![A-Za-z]))?"), 0),
    ("version", re.compile(r"(?<![A-Za-z0-9_])[vV]\d+(?:\.\d+)+(?![A-Za-z0-9_])"
                           r"|(?<![A-Za-z0-9_.])\d+\.\d+\.\d+(?:\.\d+)*(?![A-Za-z0-9_]|\.\d)"), 0),
    ("heading_number", re.compile(r"^(#{1,6}\s+)(\d+(?:\.\d+)*[.)]|\d+(?:\.\d+)+)(?=\s|$)"), 2),
    ("list_marker", re.compile(r"^(\s*)(\d+[.)]|\(\d+\))(?=\s)"), 2),
    ("reference", re.compile(r"\[\^?\d+\]"), 0),
    ("identifier", re.compile(r"(?<![A-Za-z0-9_-])\d{2,4}-\d{3,4}-\d{4}(?![A-Za-z0-9_-])"), 0),
    ("identifier", re.compile(r"(?<![A-Za-z0-9_.,$-])\d{1,3}-[A-Z]{1,2}(?:/A)?(?![A-Za-z0-9_-])"), 0),  # 10-K, 8-K, 20-F
]
PRE_YEAR_RE = re.compile(
    r"(?i)(?:\b(?:in|since|during|until|through|year|fiscal|versus|vs\.?|end of|as of|start of"
    r"|beginning of|half of|quarter of|months of)\s+|\b(?:FY|CY)\s?)$")
# "by" and "from" also come before amounts ("fell by 2000", "rose from 1950"), so they do not count
# when a direction word governs them directly. "up 12% from 2024" still reads 2024 as a year.
PRE_YEAR_WEAK_RE = re.compile(r"(?i)\b(?:by|from)\s+$")
WEAK_CANCEL_RE = re.compile(rf"(?i)\b(?:{UP_EN}|{DOWN_EN})\s+(?:[^\W\d]+\s+){{0,2}}(?:by|from)\s+$")
UNIT_AFTER_RE = re.compile(
    r"(?i)^\s?(?:units?|people|persons?|employees|customers|users|stores|shares|orders|items|cases|dollars|won"
    r"|명|건|개|원|달러|주|대|곳|가구|톤|kg|km|%)")
POST_YEAR_RE = re.compile(r"^(?:\s?년|\s+(?:fiscal|calendar)(?![A-Za-z])|\s?(?:Q[1-4]|H[12])(?![A-Za-z0-9]))")
YEAR_JOIN_AFTER_RE = re.compile(r"^\s*(?:and\b|to\b|or\b|[-–~/·]|과|와|및)\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


class Token:
    __slots__ = ("line", "start", "end", "text", "rule", "claim")

    def __init__(self, line, start, end, text, rule=None):
        self.line, self.start, self.end, self.text, self.rule = line, start, end, text, rule
        self.claim = None

    def as_dict(self, with_rule=False):
        d = {"line": self.line, "start": self.start, "end": self.end, "text": self.text}
        if with_rule:
            d["rule"] = self.rule
        return d


def _plain_year(m) -> bool:
    if m.group("kor") or m.group("sign") or m.group("sign2") or m.group("cur"):
        return False
    if any(m.group(g) for g in ("pp", "bp", "pct", "scale", "sword", "mult")):
        return False
    n = m.group("num")
    return bool(n) and len(n) == 4 and n.isdigit() and 1900 <= int(n) <= 2100


def scan_line(line: str, lineno: int):
    """Return (numbers, skipped) for one line that is not inside a code fence."""
    mask = [False] * len(line)
    skipped = []
    for rule, rx, grp in SKIP_RULES:
        for m in rx.finditer(line):
            a, b = m.span(grp)
            if a == b or any(mask[a:b]) or not any(ch.isdigit() for ch in line[a:b]):
                continue
            for i in range(a, b):
                mask[i] = True
            skipped.append(Token(lineno, a, b, line[a:b], rule))
    cands = []
    for m in TOKEN_RE.finditer(line):
        a, b = m.span()
        if any(mask[a:b]):
            continue
        prev = line[a - 1] if a > 0 else ""
        tail = re.match(r"[A-Za-z]+", line[b:])
        tail = tail.group(0) if tail else ""
        has_suffix = bool(m.group("kor")) or any(m.group(g) for g in ("pp", "bp", "pct", "scale", "sword", "mult"))
        rule = None
        prev2 = line[a - 2] if a > 1 else ""
        if (prev.isascii() and (prev.isalpha() or prev == "_")) and not m.group("cur"):
            rule = "identifier"
        elif prev == "-" and prev2.isascii() and prev2.isalpha() and not m.group("cur"):
            rule = "identifier"  # COVID-19, top-10
        elif line[:a].endswith("제") or line[b:].startswith("번째"):
            rule = "ordinal"
        elif tail and not has_suffix:
            if re.fullmatch(r"(?i)st|nd|rd|th", tail):
                rule = "ordinal"
            elif tail not in KNOWN_UNITS:
                rule = "identifier"
        cands.append([m, rule])
    # years: context before or after, then years joined to a known year
    year_spans = set()
    for item in cands:
        m, rule = item
        if rule is None and _plain_year(m):
            a, b = m.span()
            before, after = line[:a], line[b:]
            if UNIT_AFTER_RE.match(after):
                continue
            weak = PRE_YEAR_WEAK_RE.search(before)
            if weak and WEAK_CANCEL_RE.search(before):
                weak = None
            if (PRE_YEAR_RE.search(before) or weak or POST_YEAR_RE.match(after)
                    or re.match(r"^\s?[-–~]\s?(?:19|20)\d{2}(?!\d)", after)
                    or re.search(r"(?<!\d)(?:19|20)\d{2}\s?[-–~]\s?$", before)):
                item[1] = "year"
                year_spans.add((a, b))
    changed = True
    while changed:
        changed = False
        for idx, item in enumerate(cands):
            m, rule = item
            if rule is not None or not _plain_year(m):
                continue
            a, b = m.span()
            if UNIT_AFTER_RE.match(line[b:]):
                continue
            for (ya, yb) in list(year_spans):
                between = line[yb:a] if yb <= a else line[b:ya]
                if YEAR_JOIN_AFTER_RE.match(between):
                    item[1] = "year"
                    year_spans.add((a, b))
                    changed = True
                    break
    numbers = []
    for m, rule in cands:
        a, b = m.span()
        tok = Token(lineno, a, b, line[a:b], rule)
        (skipped if rule else numbers).append(tok)
    return numbers, skipped


def scan_lines(lines):
    numbers, skipped = [], []
    in_code = False
    for i, line in enumerate(lines, start=1):
        if FENCE_RE.match(line):
            in_code = not in_code
            continue
        if in_code:
            for m in TOKEN_RE.finditer(line):
                skipped.append(Token(i, m.start(), m.end(), m.group(0), "code"))
            continue
        n, s = scan_line(line, i)
        numbers.extend(n)
        skipped.extend(s)
    return numbers, skipped


class Written:
    """A written number: value w, quantum q (one unit of the last written digit), unit."""

    def __init__(self, text: str):
        m = TOKEN_RE.fullmatch(text.strip())
        if not m:
            m = TOKEN_RE.search(text)
            if not m:
                raise LocatorError("unsupported", f"cannot read the number in {text!r}")
        self.text = text
        neg = (m.group("sign") in ("-", "−")) or (m.group("sign2") in ("-", "−"))
        self.signed = bool(m.group("sign") or m.group("sign2"))
        self.unit = "plain"
        self.scale = Decimal(1)
        self.suffix = ""
        self.korean = bool(m.group("kor"))
        self.integer_digits = None
        if self.korean:
            total, q = Decimal(0), Decimal(1)
            for g in KGROUP_RE.finditer(m.group("kor")):
                n = g.group("n").replace(",", "")
                mult = K_SMALL.get(g.group("s") or g.group("s2") or "", Decimal(1)) * K_BIG.get(g.group("b") or "", Decimal(1))
                dec = len(n.split(".")[1]) if "." in n else 0
                total += Decimal(n) * mult
                q = Decimal(10) ** -dec * mult
            self.value, self.q, self.decimals = total, q, 0
        else:
            n = m.group("num").replace(",", "")
            self.decimals = len(n.split(".")[1]) if "." in n else 0
            if m.group("scale") or m.group("sword"):
                key = m.group("scale") or m.group("sword").lower()
                self.scale = Decimal(10) ** SCALE[key]
                self.suffix = m.group(0)[m.end("num") - m.start():]
            elif m.group("pp"):
                self.unit, self.suffix = "pp", "%p"
            elif m.group("bp"):
                self.unit, self.suffix = "bp", "bp"
            elif m.group("pct"):
                self.unit, self.suffix = "percent", "%"
            elif m.group("mult"):
                self.unit, self.suffix = "multiple", m.group("mult")
            else:
                if self.decimals == 0:
                    self.integer_digits = n
            self.value = Decimal(n) * self.scale
            self.q = Decimal(10) ** -self.decimals * self.scale
        if neg:
            self.value = -self.value

    def hedge(self):
        """An integer ending in zeros after a hedge word: the zeros are not significant."""
        if self.integer_digits:
            zeros = len(self.integer_digits) - len(self.integer_digits.rstrip("0"))
            if zeros and zeros < len(self.integer_digits):
                self.q = Decimal(10) ** zeros

    def show(self, v: Decimal) -> str:
        """Format a recomputed value the way the number was written."""
        r = _round(v, self.q, ROUND_HALF_UP)
        if self.korean or self.q > self.scale:
            return _fmt(r, 0)
        return _fmt(r / self.scale, self.decimals) + self.suffix


def _round(v: Decimal, q: Decimal, mode) -> Decimal:
    return (v / q).quantize(Decimal(1), rounding=mode) * q


def _fmt(d: Decimal, decimals: int) -> str:
    s = f"{d.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP):,f}"
    return s


def _plain(d: Decimal) -> str:
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


# --------------------------------------------------------------------------- sources

_CACHE = {}


def _read_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise StopError(f"cannot read {path}: {e.strerror or e}") from None


def _read_text(path: str) -> str:
    try:
        return _read_bytes(path).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise StopError(f"{path} is not valid UTF-8") from None


def _md_tables(text: str):
    tables, block = [], []

    def flush():
        if len(block) >= 2 and re.match(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$", block[1]):
            rows = [[c.strip() for c in re.split(r"(?<!\\)\|", ln.strip().strip("|"))] for ln in block]
            header = rows[0]
            body = [dict(zip(header, r + [""] * (len(header) - len(r)))) for r in rows[2:]]
            tables.append((header, body))
        block.clear()

    for ln in text.splitlines():
        if re.match(r"^\s*\|.*\|\s*$", ln):
            block.append(ln)
        else:
            flush()
    flush()
    return tables


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "p": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n


def _xlsx(path: str):
    """Return {sheet name: {(row, col): value}} with stored values. Formulas are not evaluated."""
    try:
        with zipfile.ZipFile(path) as z:
            wb = ET.fromstring(z.read("xl/workbook.xml"))
            rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            targets = {r.get("Id"): r.get("Target") for r in rels.findall("p:Relationship", _NS)}
            shared = []
            if "xl/sharedStrings.xml" in z.namelist():
                for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
                    parts = [t.text or "" for t in si.findall("m:t", _NS)]
                    parts += [t.text or "" for t in si.findall("m:r/m:t", _NS)]
                    shared.append("".join(parts))
            sheets = {}
            for sh in wb.findall("m:sheets/m:sheet", _NS):
                target = targets[sh.get(f"{{{_NS['r']}}}id")]
                target = target.lstrip("/") if target.startswith("/") else "xl/" + target
                grid = {}
                root = ET.fromstring(z.read(target))
                for ri, row in enumerate(root.findall("m:sheetData/m:row", _NS), start=1):
                    r = int(row.get("r") or ri)
                    for ci, c in enumerate(row.findall("m:c", _NS), start=1):
                        ref = c.get("r")
                        col = _col_index(re.match(r"[A-Za-z]+", ref).group(0)) if ref else ci
                        t, v = c.get("t"), c.find("m:v", _NS)
                        if t == "inlineStr":
                            val = "".join(x.text or "" for x in c.findall("m:is/m:t", _NS)
                                          + c.findall("m:is/m:r/m:t", _NS))
                        elif v is None or v.text is None:
                            continue
                        elif t == "s":
                            val = shared[int(v.text)]
                        elif t in ("str", "e"):
                            val = v.text
                        elif t == "b":
                            val = "TRUE" if v.text == "1" else "FALSE"
                        else:
                            val = Decimal(v.text)
                        grid[(r, col)] = val
                sheets[sh.get("name")] = grid
            return sheets
    except OSError as e:
        raise StopError(f"cannot read {path}: {e.strerror or e}") from None
    except (zipfile.BadZipFile, KeyError, ET.ParseError, InvalidOperation, ValueError, IndexError, AttributeError) as e:
        raise StopError(f"{path} is not a readable .xlsx file ({type(e).__name__})") from None


def load_source(path: str):
    """Return (kind, data). Raises StopError if the file is missing, unreadable or corrupt."""
    key = os.path.abspath(path)
    if key in _CACHE:
        return _CACHE[key]
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".tsv"):
        text = _read_text(path)
        try:
            rows = list(csv.reader(io.StringIO(text, newline=""), delimiter="\t" if ext == ".tsv" else ","))
        except csv.Error as e:
            raise StopError(f"{path} is not a readable {ext[1:].upper()} file ({e})") from None
        if not rows or "\x00" in text:
            raise StopError(f"{path} is empty or not a text table")
        header = [h.strip() for h in rows[0]]
        body = [dict(zip(header, r + [""] * (len(header) - len(r)))) for r in rows[1:] if any(c.strip() for c in r)]
        out = ("table", (header, body))
    elif ext == ".json":
        try:
            out = ("json", json.loads(_read_text(path), parse_float=Decimal, parse_int=Decimal))
        except ValueError as e:
            raise StopError(f"{path} is not valid JSON ({e})") from None
    elif ext == ".md":
        out = ("md", _md_tables(_read_text(path)))
    elif ext == ".xlsx":
        out = ("xlsx", _xlsx(path))
    else:
        out = ("text", _read_text(path).splitlines())
    _CACHE[key] = out
    return out


def _key_equal(cell, want) -> bool:
    a, b = _cell_str(cell), str(want).strip()
    if a == b:
        return True
    num = re.compile(r"^-?\d+(?:\.\d+)?$")
    if num.match(a) and num.match(b):
        return Decimal(a) == Decimal(b)
    return False


def _cell_str(cell) -> str:
    if cell is None:
        return ""
    if isinstance(cell, Decimal):
        return _plain(cell)
    return str(cell).strip()


def to_number(cell) -> Decimal:
    if isinstance(cell, bool):
        raise LocatorError("cell_not_numeric", "the cell is true/false")
    if isinstance(cell, Decimal):
        return cell
    s = _cell_str(cell).strip("*_` ")
    if not s:
        raise LocatorError("cell_not_numeric", "the cell is empty")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("−", "-").replace(",", "")
    s = re.sub(r"^(-?)(?:US\$|\$|₩|€|£|¥)", r"\1", s).rstrip("%").strip()
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", s):
        raise LocatorError("cell_not_numeric", f"the cell holds {_cell_str(cell)!r}")
    d = Decimal(s)
    return -d if neg else d


def _json_pointer(doc, pointer: str):
    if pointer in ("", "/"):
        return doc
    if not pointer.startswith("/"):
        raise LocatorError("locator_no_match", f"pointer {pointer!r} must start with /")
    node = doc
    for part in pointer[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            if not part.isdigit() or int(part) >= len(node):
                raise LocatorError("locator_no_match", f"pointer {pointer!r}: no item {part}")
            node = node[int(part)]
        elif isinstance(node, dict):
            if part not in node:
                raise LocatorError("locator_no_match", f"pointer {pointer!r}: no key {part!r}")
            node = node[part]
        else:
            raise LocatorError("locator_no_match", f"pointer {pointer!r} goes past a value")
    return node


def _table(loc: dict, base: str):
    """Return (header, rows, label) for a locator that addresses a table."""
    if isinstance(loc, dict) and "edgar" in loc:
        raise LocatorError("unsupported", "an EDGAR locator is a single cell: list the facts under items")
    if not isinstance(loc, dict) or not isinstance(loc.get("file"), str):
        raise LocatorError("locator_no_match", "the locator has no file")
    name = loc["file"]
    kind, data = load_source(os.path.join(base, name))
    if kind == "table":
        return data[0], data[1], name
    if kind == "json":
        node = _json_pointer(data, loc.get("pointer", ""))
        if not isinstance(node, list) or not all(isinstance(x, dict) for x in node):
            raise LocatorError("locator_no_match", "the pointer does not lead to a list of objects")
        header = []
        for obj in node:
            for k in obj:
                if k not in header:
                    header.append(k)
        return header, node, f"{name} {loc.get('pointer', '')}".strip()
    if kind == "md":
        idx = loc.get("table", 1)
        if not isinstance(idx, int) or isinstance(idx, bool) or not 1 <= idx <= len(data):
            raise LocatorError("locator_no_match", f"{name} has {len(data)} table(s), asked for {idx!r}")
        return data[idx - 1][0], data[idx - 1][1], f"{name} table {idx}"
    if kind == "xlsx":
        sheet = loc.get("sheet")
        if sheet is None and len(data) == 1:
            sheet = next(iter(data))
        if sheet not in data:
            raise LocatorError("locator_no_match", f"{name} has no sheet {sheet!r}")
        grid = data[sheet]
        hr = loc.get("header_row", 1)
        if not isinstance(hr, int) or isinstance(hr, bool) or hr < 1:
            raise LocatorError("locator_no_match", "header_row must be a positive integer")
        cols = {c: _cell_str(v) for (r, c), v in grid.items() if r == hr and _cell_str(v)}
        header = [cols[c] for c in sorted(cols)]
        last = max((r for r, _ in grid), default=0)
        rows = []
        for r in range(hr + 1, last + 1):
            row = {cols[c]: grid.get((r, c)) for c in cols}
            if any(v not in (None, "") for v in row.values()):
                rows.append(row)
        return header, rows, f"{name} [{sheet}]"
    raise LocatorError("locator_no_match", f"{name} is plain text: use contains / nth")


def _filter(header, rows, cond: dict):
    if not isinstance(cond, dict):
        raise LocatorError("locator_no_match", "row / where must be an object")
    for k in cond:
        if k not in header:
            raise LocatorError("locator_no_match", f"no column {k!r}")
    return [r for r in rows if all(_key_equal(r.get(k), v) for k, v in cond.items())]


def _cond_label(cond: dict) -> str:
    return "[" + ", ".join(f"{k}={v}" for k, v in cond.items()) + "]"


def _multiplier(loc: dict) -> Decimal:
    if "multiplier" not in loc:
        return Decimal(1)
    try:
        return Decimal(str(loc["multiplier"]))
    except InvalidOperation:
        raise LocatorError("unsupported", "multiplier is not a number") from None


def resolve_cell(loc: dict, base: str):
    """One cell. Returns (value, description)."""
    if isinstance(loc, dict) and "edgar" in loc:
        return resolve_edgar(loc, base)
    if not isinstance(loc, dict) or not isinstance(loc.get("file"), str):
        raise LocatorError("locator_no_match", "the locator has no file")
    name = loc["file"]
    kind, data = load_source(os.path.join(base, name))
    mult = _multiplier(loc)
    if kind == "text":
        want = loc.get("contains")
        if not isinstance(want, str) or not want:
            raise LocatorError("locator_no_match", "a text source needs contains")
        hits = [(i, ln) for i, ln in enumerate(data, start=1) if want in ln]
        if not hits:
            raise LocatorError("locator_no_match", f"no line of {name} contains {want!r}")
        if len(hits) > 1:
            raise LocatorError("locator_multiple", f"{len(hits)} lines of {name} contain {want!r}")
        nums, _ = scan_line(hits[0][1], hits[0][0])
        nth = loc.get("nth", 1)
        if not isinstance(nth, int) or not 1 <= nth <= len(nums):
            raise LocatorError("locator_no_match", f"line {hits[0][0]} of {name} has {len(nums)} number(s)")
        val = Written(nums[nth - 1].text).value
        label = f"{name} line {hits[0][0]} number {nth}"
    elif kind == "xlsx" and "cell" in loc:
        sheet = loc.get("sheet")
        if sheet is None and len(data) == 1:
            sheet = next(iter(data))
        if sheet not in data:
            raise LocatorError("locator_no_match", f"{name} has no sheet {sheet!r}")
        ref = re.fullmatch(r"\$?([A-Za-z]+)\$?(\d+)", str(loc["cell"]))
        if not ref or (int(ref.group(2)), _col_index(ref.group(1))) not in data[sheet]:
            raise LocatorError("locator_no_match", f"{name} [{sheet}] has no value at {loc['cell']!r}")
        val = to_number(data[sheet][(int(ref.group(2)), _col_index(ref.group(1)))])
        label = f"{name} [{sheet}] {str(loc['cell']).upper()}"
    elif kind == "json" and "row" not in loc and "column" not in loc:
        val = to_number(_json_pointer(data, loc.get("pointer", "")))
        label = f"{name} {loc.get('pointer', '')}"
    else:
        header, rows, where = _table(loc, base)
        col = loc.get("column")
        if col not in header:
            raise LocatorError("locator_no_match", f"{where} has no column {col!r}")
        cond = loc.get("row", {})
        hits = _filter(header, rows, cond)
        if not hits:
            raise LocatorError("locator_no_match", f"{where}: no row {_cond_label(cond)}")
        if len(hits) > 1:
            raise LocatorError("locator_multiple", f"{where}: {len(hits)} rows {_cond_label(cond)}")
        val = to_number(hits[0].get(col))
        label = f"{where} {_cond_label(cond)} {col}"
    desc = f"{label} = {_plain(val)}" + (f" x {_plain(mult)}" if mult != 1 else "")
    return val * mult, desc


def resolve_many(loc: dict, base: str):
    """Many cells of one column. Returns (values, description)."""
    header, rows, where = _table(loc, base)
    col = loc.get("column")
    if col not in header:
        raise LocatorError("locator_no_match", f"{where} has no column {col!r}")
    cond = loc.get("where", {})
    hits = _filter(header, rows, cond)
    mult = _multiplier(loc)
    vals = [to_number(r.get(col)) * mult for r in hits if _cell_str(r.get(col)) != ""]
    if not vals:
        raise LocatorError("locator_no_match", f"{where}: no rows {_cond_label(cond)} with a value in {col}")
    return vals, f"{where} {_cond_label(cond)} {col}: {len(vals)} cells"


def resolve_count(loc: dict, base: str):
    header, rows, where = _table(loc, base)
    cond = loc.get("where", {})
    hits = _filter(header, rows, cond)
    test = loc.get("test")
    label = f"{where} {_cond_label(cond)}"
    if test is not None:
        ops = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b, ">": lambda a, b: a > b,
               ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}
        if not isinstance(test, dict) or test.get("op") not in ops or test.get("column") not in header:
            raise LocatorError("locator_no_match", "test needs a known column and op")
        try:
            ref = Decimal(str(test.get("value")).replace(",", ""))
        except InvalidOperation:
            raise LocatorError("unsupported", "test value is not a number") from None
        kept = []
        for r in hits:
            if _cell_str(r.get(test["column"])) == "":
                continue
            if ops[test["op"]](to_number(r.get(test["column"])), ref):
                kept.append(r)
        hits = kept
        label += f" {test['column']} {test['op']} {test.get('value')}"
    return Decimal(len(hits)), f"{label}: {len(hits)} rows"


# --------------------------------------------------------------------------- SEC EDGAR (contract sections 2 and 8)

UA_ENV = "RECEIPTS_SEC_USER_AGENT"
EDGAR_GAP = 0.2  # seconds between requests: at most 5 a second, the SEC allows 10
_EDGAR = {"cache": None, "refresh": False, "last": 0.0, "files": {}, "fetched": set(), "missing": set()}
DATE = r"\d{4}-\d{2}-\d{2}"


class _NotFound(Exception):
    pass


class _SameHostRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect to another host is not followed, so the contact string goes nowhere but the address asked."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).netloc.lower() != urllib.parse.urlsplit(req.full_url).netloc.lower():
            return None  # urllib then reports the 3xx answer as an error, which stops the run
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SameHostRedirects)


def edgar_setup(cache=None, refresh=False):
    _EDGAR.update(cache=cache, refresh=bool(refresh), files={}, fetched=set(), missing=set())


def _edgar_hosts():
    data = os.environ.get("RECEIPTS_EDGAR_DATA_URL", "").strip().rstrip("/") or "https://data.sec.gov"
    www = os.environ.get("RECEIPTS_EDGAR_WWW_URL", "").strip().rstrip("/") or "https://www.sec.gov"
    return data, www


def _edgar_cache_dir(base: str) -> str:
    return _EDGAR["cache"] or os.environ.get("RECEIPTS_EDGAR_CACHE", "").strip() or os.path.join(base, ".receipts", "edgar")


def _edgar_get(url: str) -> bytes:
    """One request to the SEC. Raises StopError when EDGAR cannot be read, _NotFound on HTTP 404."""
    agent = os.environ.get(UA_ENV, "").strip()
    if not agent:
        raise StopError(f"EDGAR data is needed and {UA_ENV} is not set. The SEC asks automated clients to say who "
                        f"they are: set {UA_ENV} to your name and e-mail address. Nothing was requested.")
    if not url.startswith(("https://", "http://")):
        raise StopError(f"not an http(s) address: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": agent, "Accept-Encoding": "gzip",
                                               "Accept": "application/json"})
    for attempt in (1, 2):
        wait = EDGAR_GAP - (time.monotonic() - _EDGAR["last"])
        if wait > 0:
            time.sleep(wait)
        _EDGAR["last"] = time.monotonic()
        try:
            with _OPENER.open(req, timeout=30) as resp:
                body = resp.read()
                if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise _NotFound() from None
            if e.code in (429, 500, 502, 503, 504) and attempt == 1:
                time.sleep(2)
                continue
            hint = f" (the SEC refuses clients it cannot identify: check {UA_ENV})" if e.code == 403 else ""
            raise StopError(f"EDGAR answered HTTP {e.code} for {url}{hint}") from None
        except (OSError, EOFError, ValueError, http.client.HTTPException, zlib.error) as e:  # no connection, timeout, broken body
            if attempt == 1:
                time.sleep(2)
                continue
            raise StopError(f"cannot read EDGAR at {url} ({type(e).__name__}: {getattr(e, 'reason', e)})") from None
    raise StopError(f"cannot read EDGAR at {url}")


def _edgar_file(rel: str, url: str, base: str, check):
    """A cached EDGAR response as parsed JSON. Fetched when it is not in the cache, or once per run with --refresh."""
    path = os.path.join(_edgar_cache_dir(base), *rel.split("/"))
    key = os.path.abspath(path)
    if key in _EDGAR["missing"]:
        raise _NotFound()
    if (_EDGAR["refresh"] and key not in _EDGAR["fetched"]) or not os.path.exists(path):
        try:
            body = _edgar_get(url)
        except _NotFound:
            _EDGAR["missing"].add(key)
            raise
        try:
            doc = json.loads(body.decode("utf-8-sig"), parse_float=Decimal, parse_int=Decimal)
        except ValueError:
            raise StopError(f"EDGAR sent something that is not JSON for {url}") from None
        if not check(doc):
            raise StopError(f"EDGAR sent an unexpected answer for {url}")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".part", "wb") as f:
                f.write(body)
            os.replace(path + ".part", path)
        except OSError as e:
            raise StopError(f"cannot write the EDGAR cache {path}: {e.strerror or e}") from None
        _EDGAR["fetched"].add(key)
        _CACHE[key] = doc
        _edgar_dates(base, {rel: time.strftime("%Y-%m-%d", time.gmtime())})
    if key not in _CACHE:
        try:
            doc = json.loads(_read_text(path), parse_float=Decimal, parse_int=Decimal)
        except ValueError:
            raise StopError(f"the cached EDGAR file {path} is not valid JSON. Delete it or run with --refresh.") from None
        if not check(doc):
            raise StopError(f"the cached EDGAR file {path} is not what EDGAR sends. Delete it or run with --refresh.")
        _CACHE[key] = doc
    noted = _edgar_dates(base).get(rel)
    try:
        _EDGAR["files"][rel] = noted or time.strftime("%Y-%m-%d", time.gmtime(os.path.getmtime(path)))
    except OSError:
        _EDGAR["files"][rel] = ""
    return _CACHE[key]


def _edgar_dates(base: str, add: dict = None) -> dict:
    """Fetch dates by cached file, kept in <cache>/fetched.json. A file without an entry shows its modification date."""
    path = os.path.join(_edgar_cache_dir(base), "fetched.json")
    try:
        with open(path, encoding="utf-8") as f:
            dates = json.load(f)
        dates = {k: v for k, v in dates.items() if isinstance(v, str) and re.fullmatch(DATE, v)}
    except (OSError, ValueError, AttributeError):
        dates = {}
    if add:
        dates.update(add)
        try:
            with open(path + ".part", "w", encoding="utf-8", newline="\n") as f:
                json.dump(dates, f, indent=1, sort_keys=True)
            os.replace(path + ".part", path)
        except OSError:
            pass  # the modification date still tells when the file was fetched
    return dates


def _edgar_cik(ident, base: str):
    """A ticker or a CIK -> (cik, how the locator named it)."""
    if isinstance(ident, bool) or not isinstance(ident, (str, int)):
        raise LocatorError("locator_no_match", "edgar must be a ticker or a CIK")
    text = str(ident).strip()
    m = re.fullmatch(r"(?i)(?:CIK)?\s*(\d{1,10})", text)
    if m:
        return int(m.group(1)), ""
    if not text:
        raise LocatorError("locator_no_match", "edgar must be a ticker or a CIK")
    data, www = _edgar_hosts()
    try:
        listing = _edgar_file("company_tickers.json", f"{www}/files/company_tickers.json", base,
                              lambda d: isinstance(d, dict) and all(isinstance(v, dict) for v in d.values()))
    except _NotFound:
        raise StopError("the SEC's ticker list was not found (HTTP 404)") from None
    want = text.upper().replace(".", "-")
    for row in listing.values():
        if str(row.get("ticker", "")).upper() == want and row.get("cik_str") is not None:
            return int(row["cik_str"]), want
    raise LocatorError("locator_no_match", f"{text!r} is not in the SEC's ticker list")


def _edgar_facts(cik: int, base: str) -> dict:
    data, www = _edgar_hosts()
    rel = f"companyfacts/CIK{cik:010d}.json"
    try:
        return _edgar_file(rel, f"{data}/api/xbrl/companyfacts/CIK{cik:010d}.json", base,
                           lambda d: isinstance(d, dict) and isinstance(d.get("facts"), dict))
    except _NotFound:
        raise LocatorError("locator_no_match", f"EDGAR has no facts for CIK {cik}") from None


def _edgar_period(text):
    """'2026-09-26' -> (None, end); '2025-09-28..2026-09-26' -> (start, end)."""
    m = re.fullmatch(rf"({DATE})(?:\.\.({DATE}))?", text.strip()) if isinstance(text, str) else None
    if not m:
        raise LocatorError("locator_no_match", "period must be YYYY-MM-DD or YYYY-MM-DD..YYYY-MM-DD")
    return (m.group(1), m.group(2)) if m.group(2) else (None, m.group(1))


def _edgar_filing(r: dict) -> str:
    return f"{r.get('form', '?')} {r.get('accn', '?')}, filed {r.get('filed', '?')}"


def _edgar_concept(facts: dict, concept, unit):
    """Return (unit name, fact rows) of one concept."""
    if not isinstance(concept, str) or ":" not in concept:
        raise LocatorError("locator_no_match", "concept must be written taxonomy:Tag, for example us-gaap:Assets")
    tax, tag = concept.split(":", 1)
    group = facts["facts"].get(tax)
    node = group.get(tag) if isinstance(group, dict) else None
    units = node.get("units") if isinstance(node, dict) else None
    if not isinstance(units, dict) or not units:
        raise LocatorError("locator_no_match", f"the company has not reported {concept}")
    if unit is None:
        if len(units) > 1:
            raise LocatorError("locator_multiple", f"{concept} is reported in {', '.join(sorted(units))}: add unit")
        name = next(iter(units))
    else:
        hits = [u for u in units if u.lower() == str(unit).strip().lower()]
        if not hits:
            raise LocatorError("locator_no_match", f"{concept} is reported in {', '.join(sorted(units))}, not {unit}")
        name = hits[0]
    rows = units[name]
    return name, [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def resolve_edgar(loc: dict, base: str):
    """One fact a company reported to the SEC. Returns (value, description)."""
    if "where" in loc or "rows" in loc:
        raise LocatorError("unsupported", "an EDGAR locator is a single cell: list the facts under items")
    mult = _multiplier(loc)
    start, end = _edgar_period(loc.get("period"))
    accession = loc.get("accession")
    if accession is not None:
        accession = str(accession).replace("-", "").strip()
        if not re.fullmatch(r"\d{18}", accession):
            raise LocatorError("locator_no_match", "accession must look like 0000320193-26-000026")
    version = loc.get("version")
    if version not in (None, "first", "latest"):
        raise LocatorError("locator_no_match", "version must be first or latest")
    cik, ticker = _edgar_cik(loc.get("edgar"), base)
    facts = _edgar_facts(cik, base)
    name = str(facts.get("entityName") or "").strip()
    who = f"{ticker} ({name}, CIK {cik})" if ticker else f"CIK {cik}" + (f" ({name})" if name else "")
    concept = loc.get("concept")
    unit, rows = _edgar_concept(facts, concept, loc.get("unit"))
    period = f"{start}..{end}" if start else end
    if start is None or start == end:
        # One date, or a range of one day. The SEC's API stores a one-day period under a single date,
        # so both ways of writing it name the same fact.
        rows = [r for r in rows if r.get("end") == end and r.get("start") in (None, end)]
    else:
        rows = [r for r in rows if r.get("end") == end and r.get("start") == start]
    if not rows:
        raise LocatorError("locator_no_match", f"EDGAR has no {concept} in {unit} for {period} ({who})")
    if accession:
        rows = [r for r in rows if str(r.get("accn", "")).replace("-", "") == accession]
        if not rows:
            raise LocatorError("locator_no_match", f"that filing does not report {concept} for {period}")
    everywhere = len({to_number(r.get("val")) for r in rows})
    note = ""
    if everywhere > 1 and version and not accession:
        dates = sorted(str(r.get("filed", "")) for r in rows)
        pick = dates[0] if version == "first" else dates[-1]
        rows = [r for r in rows if str(r.get("filed", "")) == pick]
        note = f"; {version} report, other filings give another value"
    values = {}
    for r in rows:
        values.setdefault(to_number(r.get("val")), []).append(r)
    if len(values) > 1:
        shown = "; ".join(f"{_plain(v)} ({_edgar_filing(max(rs, key=lambda r: str(r.get('filed', ''))))})"
                          for v, rs in list(values.items())[:4])
        more = f"; and {len(values) - 4} more" if len(values) > 4 else ""
        raise LocatorError("edgar_versions_differ",
                           f"filings give {len(values)} values for {concept} {period}: {shown}{more}. "
                           "Pin one with accession, or choose version first or latest.")
    val, same = next(iter(values.items()))
    last = max(same, key=lambda r: str(r.get("filed", "")))
    if not note and len(same) > 1:
        note = f"; same in {len(same)} filings"
    desc = f"EDGAR {who} {concept} [{period}] {unit} = {_plain(val)}" + (f" x {_plain(mult)}" if mult != 1 else "")
    return val * mult, f"{desc} ({_edgar_filing(last)}{note})"


def edgar_lookup(what: str, ident: str, base: str, concept=None, match=None, unit=None, since=None, until=None,
                 limit=60) -> dict:
    """The three look-up commands: company, concepts, facts."""
    try:
        cik, ticker = _edgar_cik(ident, base)
        facts = _edgar_facts(cik, base)
        out = {"cik": cik, "name": facts.get("entityName"), "fetched": _EDGAR["files"].get(f"companyfacts/CIK{cik:010d}.json")}
        if ticker:
            out["ticker"] = ticker
        if what == "concepts":
            found = []
            for tax, group in sorted(facts["facts"].items()):
                for tag, node in sorted(group.items()) if isinstance(group, dict) else []:
                    if not isinstance(node, dict) or not isinstance(node.get("units"), dict):
                        continue
                    label = str(node.get("label") or "")
                    if match and match.lower() not in tag.lower() and match.lower() not in label.lower():
                        continue
                    units = node.get("units") or {}
                    rows = [r for rs in units.values() for r in rs]
                    found.append({"concept": f"{tax}:{tag}", "label": label, "units": sorted(units),
                                  "facts": len(rows), "latest_end": max((str(r.get("end", "")) for r in rows), default="")})
            out.update(matched=len(found), concepts=found[:limit] if limit else found)
        elif what == "facts":
            name, rows = _edgar_concept(facts, concept, unit)
            rows = [r for r in rows if (not since or str(r.get("end", "")) >= since)
                    and (not until or str(r.get("end", "")) <= until)]
            seen = {}
            for r in rows:
                seen.setdefault((r.get("start"), r.get("end")), set()).add(_cell_str(r.get("val")))
            out.update(concept=concept, unit=name, facts=[
                {"period": f"{r['start']}..{r.get('end')}" if r.get("start") else r.get("end"),
                 "value": _cell_str(r.get("val")), "form": r.get("form"), "accession": r.get("accn"),
                 "filed": r.get("filed"), "filings_differ": len(seen[(r.get("start"), r.get("end"))]) > 1}
                for r in sorted(rows, key=lambda r: (str(r.get("end", "")), str(r.get("start", "")), str(r.get("filed", ""))))])
        return out
    except LocatorError as e:
        raise StopError(e.detail or e.reason) from None


# --------------------------------------------------------------------------- formulas and verdicts

def compute(formula: str, ops, base: str):
    """Return (value, [source descriptions])."""
    if not isinstance(ops, dict):
        raise LocatorError("unsupported", "operands must be an object")

    def cell(name):
        if name not in ops:
            raise LocatorError("unsupported", f"{formula} needs operand {name!r}")
        loc = ops[name]
        if isinstance(loc, dict) and "where" in loc and "row" not in loc:  # many cells in a one-cell slot: summed
            vals, d = resolve_many(loc, base)
            return sum(vals, Decimal(0)), d + " (summed)"
        return resolve_cell(loc, base)

    def many():
        if "items" in ops:
            if not isinstance(ops["items"], list) or not ops["items"]:
                raise LocatorError("unsupported", "items must be a non-empty list")
            got = [resolve_cell(x, base) for x in ops["items"]]
            return [g[0] for g in got], [g[1] for g in got]
        if "cells" in ops:
            vals, d = resolve_many(ops["cells"], base)
            return vals, [d]
        raise LocatorError("unsupported", f"{formula} needs items or cells")

    if formula == "value":
        v, d = cell("of")
        return v, [d]
    if formula == "difference":
        (a, da), (b, db) = cell("a"), cell("b")
        return a - b, [da, db]
    if formula == "pct_change":
        (n, dn), (o, do) = cell("new"), cell("old")
        if o == 0:
            raise LocatorError("division_by_zero", "the old value is 0")
        return (n - o) / abs(o), [dn, do]
    if formula == "ratio":
        (n, dn), (d, dd) = cell("num"), cell("den")
        if d == 0:
            raise LocatorError("division_by_zero", "the denominator is 0")
        return n / d, [dn, dd]
    if formula == "share":
        (p, dp), (w, dw) = cell("part"), cell("whole")
        if w == 0:
            raise LocatorError("division_by_zero", "the whole is 0")
        return p / w, [dp, dw]
    if formula in ("sum", "mean", "min", "max"):
        vals, descs = many()
        if formula == "sum":
            return sum(vals, Decimal(0)), descs
        if formula == "mean":
            return sum(vals, Decimal(0)) / len(vals), descs
        return (min(vals) if formula == "min" else max(vals)), descs
    if formula == "count":
        if "rows" not in ops:
            raise LocatorError("unsupported", "count needs rows")
        v, d = resolve_count(ops["rows"], base)
        return v, [d]
    raise LocatorError("unsupported", f"unknown formula {formula!r}")


def in_written_unit(v: Decimal, unit: str, formula: str) -> Decimal:
    fractional = formula in ("pct_change", "ratio", "share")
    if unit == "percent":
        return v * 100 if fractional else v
    if unit in ("plain", "multiple"):
        return v
    if unit == "pp" and formula == "difference":
        return v
    if unit == "bp" and formula == "difference":
        return v * 100
    raise LocatorError("unsupported", f"a number written in {unit} cannot be checked with {formula}")


def direction_of(quote: str, planned):
    up, down = bool(UP_RE.search(quote)), bool(DOWN_RE.search(quote))
    seen = "up" if up and not down else "down" if down and not up else None
    if planned not in (None, "up", "down"):
        raise LocatorError("unsupported", "direction must be up or down")
    if seen and planned and seen != planned:
        raise LocatorError("direction_conflict", f"the sentence says {seen}, the plan says {planned}")
    return seen or planned


def compare(w: Decimal, q: Decimal, v: Decimal, mode: str):
    if w in (_round(v, q, ROUND_HALF_UP), _round(v, q, ROUND_HALF_EVEN)):
        return "match"
    if mode == "default" and w == _round(v, q, ROUND_DOWN):
        return "truncated"
    return None


def judge(claim: dict, tok: Token, line_text: str, base: str, mode: str) -> dict:
    out = {"verdict": "unverifiable", "reason": "", "recomputed": "", "recomputed_raw": "", "sources": [], "note": ""}
    try:
        kind = claim.get("kind")
        if kind == "no_source":
            out.update(reason="declared_no_source", note=str(claim.get("reason", "")))
            return out
        formula = "value" if kind == "lookup" else claim.get("formula")
        if kind not in ("lookup", "derived") or not isinstance(formula, str):
            raise LocatorError("unsupported", "kind must be lookup, derived or no_source")
        written = Written(tok.text)
        if HEDGE_RE.search(line_text[:tok.start]):
            written.hedge()
        v, out["sources"] = compute(formula, claim.get("operands"), base)
        v = in_written_unit(v, written.unit, formula)
        out["recomputed"], out["recomputed_raw"] = written.show(v), _plain(v)
        direction = None
        if formula in ("difference", "pct_change") and not written.signed:
            direction = direction_of(claim.get("quote", ""), claim.get("direction"))
        if direction:
            how = compare(abs(written.value), written.q, abs(v), mode)
            sign_ok = (v > 0) if direction == "up" else (v < 0)
            if how and sign_ok:
                out.update(verdict="verified", reason=how)
            else:
                out.update(verdict="wrong", reason="mismatch" if not how else "direction_mismatch")
                if how:
                    out["note"] = f"the sentence says {direction}, the recomputed change is {_plain(v)}"
        else:
            how = compare(written.value, written.q, v, mode)
            out.update(verdict="verified" if how else "wrong", reason=how or "mismatch")
    except LocatorError as e:
        out.update(verdict="unverifiable", reason=e.reason, note=e.detail)
    except StopError:
        raise
    except Exception as e:  # a malformed claim or a bug: abstain, never guess
        out.update(verdict="unverifiable", reason="internal_error", note=f"{type(e).__name__}: {e}")
    return out


# --------------------------------------------------------------------------- check

def _whole_number_places(quote: str, number: str):
    """Where `number` stands in `quote` as a whole number, not inside a longer one ("3" in "2023")."""
    out, at = [], quote.find(number)
    while at >= 0:
        end = at + len(number)
        before, before2 = quote[at - 1:at], quote[max(at - 2, 0):max(at - 1, 0)]
        after, after2 = quote[end:end + 1], quote[end + 1:end + 2]
        inside = (number[0].isdigit() and (before.isdigit() or (before in (".", ",") and before2.isdigit()))) or \
                 (number[-1].isdigit() and (after.isdigit() or (after in (".", ",") and after2.isdigit())))
        if not inside:
            out.append(at)
        at = quote.find(number, at + 1)
    return out


def _bind(claim: dict, lines, numbers, skipped):
    """Find the token a claim points at. Returns (token, from_skipped) or raises LocatorError."""
    line, quote, number = claim.get("line"), claim.get("quote"), claim.get("number")
    if (not isinstance(line, int) or isinstance(line, bool) or not isinstance(quote, str) or not quote
            or not isinstance(number, str) or not number or not isinstance(claim.get("id"), str)):
        raise LocatorError("invalid_claim", "id, line, quote and number are required")
    if not 1 <= line <= len(lines):
        raise LocatorError("quote_not_on_line", f"the document has no line {line}")
    pos = lines[line - 1].find(quote)
    if pos < 0:
        raise LocatorError("quote_not_on_line", f"line {line} does not contain the quote")
    occ = claim.get("occurrence", 1)
    if not isinstance(occ, int) or isinstance(occ, bool) or occ < 1:
        raise LocatorError("invalid_claim", "occurrence must be a positive integer")
    places = _whole_number_places(quote, number)
    if len(places) < occ:
        raise LocatorError("number_not_in_quote", "the quote does not contain the number")
    at = places[occ - 1]
    a, b = pos + at, pos + at + len(number)

    def best(pool):
        hits = [(min(b, t.end) - max(a, t.start), t) for t in pool if t.line == line and t.start < b and a < t.end]
        return max(hits, key=lambda h: h[0])[1] if hits else None

    tok = best(numbers)
    if tok:
        return tok, False
    tok = best(skipped)
    if tok:
        return tok, True
    raise LocatorError("no_number_at_position", "there is no number at that place")


def run_check(plan_path: str, mode: str = "default", doc_override: str = None, edgar_cache: str = None,
              refresh: bool = False) -> dict:
    edgar_setup(edgar_cache, refresh)
    try:
        plan = json.loads(_read_text(plan_path))
    except ValueError as e:
        raise StopError(f"{plan_path} is not valid JSON ({e})") from None
    if not isinstance(plan, dict) or not isinstance(plan.get("claims"), list):
        raise StopError("the plan needs a claims list")
    if not doc_override and not isinstance(plan.get("document"), str):
        raise StopError("the plan needs a document path")
    ids = [c.get("id") for c in plan["claims"] if isinstance(c, dict)]
    if len(ids) != len(plan["claims"]) or len(set(map(str, ids))) != len(ids):
        raise StopError("every claim must be an object with a unique id")
    base = os.path.dirname(os.path.abspath(plan_path))
    doc_path = doc_override or os.path.join(base, plan["document"])
    lines = _read_text(doc_path).splitlines()
    numbers, skipped = scan_lines(lines)
    claims_out, unbound, labels = [], [], []
    for claim in plan["claims"]:
        try:
            tok, was_skipped = _bind(claim, lines, numbers, skipped)
        except LocatorError as e:
            unbound.append({"id": str(claim.get("id")), "reason": e.reason, "note": e.detail})
            continue
        if claim.get("kind") == "label":
            # The plan may say "this is a year, not a quantity", but only for a token that looks like one.
            m = TOKEN_RE.fullmatch(tok.text)
            if tok.claim is not None or not (m and _plain_year(m)):
                unbound.append({"id": claim["id"], "reason": "label_not_allowed",
                                "note": "only a bare 4-digit year can be marked as a label"})
                continue
            if not was_skipped:
                numbers.remove(tok)
                tok.rule = "label"
                skipped.append(tok)
            labels.append({"id": claim["id"], "line": tok.line, "start": tok.start, "end": tok.end, "text": tok.text})
            continue
        if was_skipped:
            skipped.remove(tok)
            tok.rule = None
            numbers.append(tok)
        row = {"id": claim["id"], "line": tok.line, "start": tok.start, "end": tok.end, "text": tok.text,
               "quote": claim["quote"]}
        if tok.claim is not None:
            row.update(verdict="unverifiable", reason="duplicate_claim", recomputed="", recomputed_raw="",
                       sources=[], note=f"claim {tok.claim} already covers this number", counted=False)
        else:
            tok.claim = claim["id"]
            row.update(judge(claim, tok, lines[tok.line - 1], base, mode))
        claims_out.append(row)
    numbers.sort(key=lambda t: (t.line, t.start))
    counts = {"numbers": len(numbers), "verified": 0, "wrong": 0, "unverifiable": 0, "uncovered": 0}
    for row in claims_out:
        if row.get("counted", True):
            counts[row["verdict"]] += 1
    uncovered = [t.as_dict() for t in numbers if t.claim is None]
    counts["uncovered"] = len(uncovered)
    skip_counts = {}
    for t in skipped:
        skip_counts[t.rule] = skip_counts.get(t.rule, 0) + 1
    # Multipliers are the plan's own statement about source units. The script cannot check them, so it lists them.
    mults = {}

    def walk(node, claim_id):
        if isinstance(node, dict):
            src = node["file"] if isinstance(node.get("file"), str) else f"EDGAR {node['edgar']}" if "edgar" in node else None
            if "multiplier" in node and src:
                where = (node.get("column") or node.get("cell") or node.get("pointer") or node.get("contains")
                         or node.get("concept") or "")
                mults.setdefault((src, str(where), str(node["multiplier"])), set()).add(claim_id)
            for v in node.values():
                walk(v, claim_id)
        elif isinstance(node, list):
            for v in node:
                walk(v, claim_id)

    for claim in plan["claims"]:
        walk(claim.get("operands"), str(claim.get("id")))
    multipliers = [{"file": f, "column": c, "multiplier": m, "claims": len(ids)}
                   for (f, c, m), ids in sorted(mults.items())]
    truncated = sum(1 for r in claims_out if r["verdict"] == "verified" and r["reason"] == "truncated")
    res = {"document": plan.get("document") or doc_override, "mode": mode, "counts": counts,
           "truncated": truncated, "skipped": dict(sorted(skip_counts.items())), "claims": claims_out,
           "uncovered": uncovered, "unbound_claims": unbound, "labels": labels, "multipliers": multipliers}
    if _EDGAR["files"]:
        res["edgar"] = {"files": [{"file": f, "fetched": d} for f, d in sorted(_EDGAR["files"].items())]}
        hosts = _edgar_hosts()
        if hosts != ("https://data.sec.gov", "https://www.sec.gov"):
            res["edgar"]["addresses"] = list(hosts)
    return res


def _sig(raw: str, digits: int = 6) -> str:
    """A recomputed value to about six significant digits, for the wrong-number rows."""
    try:
        d = Decimal(raw)
    except InvalidOperation:
        return raw
    if d == 0:
        return "0"
    places = max(digits - 1 - d.adjusted(), 0)
    s = f"{d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP):,f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def render(res: dict) -> str:
    c = res["counts"]
    head = (f"{c['numbers']} numbers / {c['verified']} verified / {c['wrong']} wrong / "
            f"{c['unverifiable']} unverifiable / {c['uncovered']} uncovered")
    if res.get("skipped"):
        head += "   (skipped: " + ", ".join(f"{n} {r}" for r, n in res["skipped"].items()) + ")"
    out = [head]
    if res.get("truncated"):
        out.append(f"{res['truncated']} of the verified numbers match only when the source value is cut off, not rounded "
                   "(note: truncated).")
    out.append("")

    def esc(s):
        return str(s).replace("|", "\\|").replace("\n", " ")

    for verdict in ("wrong", "unverifiable", "verified"):
        rows = [r for r in res["claims"] if r["verdict"] == verdict]
        if not rows:
            continue
        out += [f"### {verdict} ({len(rows)})", "",
                "| line | sentence | written | source | recomputed | note |", "|---|---|---|---|---|---|"]
        for r in sorted(rows, key=lambda r: (r["line"], r["start"])):
            note = r["reason"] + (f": {r['note']}" if r.get("note") else "")
            recomputed = r["recomputed"]
            if verdict == "wrong" and r.get("recomputed_raw"):
                recomputed += f" (exact {_sig(r['recomputed_raw'])})"
            out.append(f"| {r['line']} | {esc(r['quote'])} | {esc(r['text'])} | {esc('; '.join(r['sources']))} "
                       f"| {esc(recomputed)} | {esc(note)} |")
        out.append("")
    if res.get("multipliers"):
        out += [f"### multipliers declared by the plan ({len(res['multipliers'])})", "",
                "The script cannot check these. Each must come from what the source says about its own units.", "",
                "| file | column | multiplier | claims |", "|---|---|---|---|"]
        out += [f"| {esc(m['file'])} | {esc(m['column'])} | {esc(m['multiplier'])} | {m['claims']} |"
                for m in res["multipliers"]]
        out.append("")
    if res.get("edgar"):
        out += [f"### EDGAR data used ({len(res['edgar']['files'])})", "",
                "Read from the SEC's company facts. A filing made after the fetch date is not in it "
                "(run with --refresh to fetch again).", "", "| file | fetched (UTC) |", "|---|---|"]
        out += [f"| {esc(f['file'])} | {esc(f['fetched'])} |" for f in res["edgar"]["files"]]
        if res["edgar"].get("addresses"):
            out += ["", "Not read from the SEC's own address but from: " + esc(", ".join(res["edgar"]["addresses"]))]
        out.append("")
    if res["uncovered"]:
        out += [f"### uncovered ({len(res['uncovered'])})", "", "| line | number |", "|---|---|"]
        out += [f"| {u['line']} | {esc(u['text'])} |" for u in res["uncovered"]]
        out.append("")
    if res.get("labels"):
        out += [f"### marked as year labels by the plan ({len(res['labels'])})", "", "| line | token |", "|---|---|"]
        out += [f"| {u['line']} | {esc(u['text'])} |" for u in res["labels"]]
        out.append("")
    if res["unbound_claims"]:
        out += [f"### claims that point at nothing ({len(res['unbound_claims'])})", "", "| claim | reason |", "|---|---|"]
        out += [f"| {esc(u['id'])} | {esc(u['reason'])}: {esc(u.get('note', ''))} |" for u in res["unbound_claims"]]
        out.append("")
    return "\n".join(out)


def exit_code(res: dict, strict: bool) -> int:
    c = res["counts"]
    if c["wrong"]:
        return 1
    if strict and (c["unverifiable"] or c["uncovered"] or res["unbound_claims"]):
        return 1
    return 0


# --------------------------------------------------------------------------- command line

def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="receipts", description="Every number gets a receipt, or gets flagged.")
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan", help="list every number in a document")
    p.add_argument("document")
    p = sub.add_parser("check", help="check a plan (claims.json) against its sources")
    p.add_argument("plan")
    p.add_argument("--doc", help="document path (default: the plan's document)")
    p.add_argument("--out", help="folder for receipts.json and receipts.md (default: the plan's folder)")
    p.add_argument("--mode", choices=["default", "strict"], default="default",
                   help="strict accepts rounding only, not truncation")
    p.add_argument("--strict", action="store_true", help="exit 1 also for unverifiable, uncovered or unbound")
    p.add_argument("--quiet", action="store_true", help="do not print the table")
    p.add_argument("--edgar-cache", help="folder for EDGAR responses (default: .receipts/edgar in the plan's folder)")
    p.add_argument("--refresh", action="store_true", help="fetch EDGAR data again instead of using the cache")
    p = sub.add_parser("report", help="print receipts.json as a table")
    p.add_argument("receipts")
    p = sub.add_parser("edgar", help="look up a company's concept names and period dates on SEC EDGAR")
    p.add_argument("what", choices=["company", "concepts", "facts"])
    p.add_argument("company", help="a ticker or a CIK")
    p.add_argument("concept", nargs="?", help="for facts: taxonomy:Tag, for example us-gaap:Assets")
    p.add_argument("--match", help="for concepts: text in the concept name or label")
    p.add_argument("--limit", type=int, default=60, help="for concepts: how many to print (0 = all)")
    p.add_argument("--unit", help="for facts: USD, shares, USD/shares, ...")
    p.add_argument("--since", help="for facts: period end on or after YYYY-MM-DD")
    p.add_argument("--until", help="for facts: period end on or before YYYY-MM-DD")
    p.add_argument("--edgar-cache", help="folder for EDGAR responses (default: .receipts/edgar in the current folder)")
    p.add_argument("--refresh", action="store_true", help="fetch EDGAR data again instead of using the cache")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "scan":
            numbers, skipped = scan_lines(_read_text(args.document).splitlines())
            counts = {}
            for t in skipped:
                counts[t.rule] = counts.get(t.rule, 0) + 1
            print(json.dumps({"document": args.document, "numbers": [t.as_dict() for t in numbers],
                              "skipped": dict(sorted(counts.items())),
                              "skipped_items": [t.as_dict(True) for t in skipped]}, ensure_ascii=False, indent=1))
            return 0
        if args.cmd == "report":
            try:
                print(render(json.loads(_read_text(args.receipts))))
            except (ValueError, KeyError, TypeError) as e:
                raise StopError(f"{args.receipts} is not a receipts file ({e})") from None
            return 0
        if args.cmd == "edgar":
            if args.what == "facts" and not args.concept:
                raise StopError("edgar facts needs a concept, for example us-gaap:Assets")
            edgar_setup(args.edgar_cache, args.refresh)
            print(json.dumps(edgar_lookup(args.what, args.company, os.getcwd(), concept=args.concept, match=args.match,
                                          unit=args.unit, since=args.since, until=args.until, limit=args.limit),
                             ensure_ascii=False, indent=1))
            return 0
        res = run_check(args.plan, args.mode, args.doc, args.edgar_cache, args.refresh)
        out_dir = args.out or os.path.dirname(os.path.abspath(args.plan))
        os.makedirs(out_dir, exist_ok=True)
        text = render(res)
        with open(os.path.join(out_dir, "receipts.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        with open(os.path.join(out_dir, "receipts.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")
        if not args.quiet:
            print(text)
        return exit_code(res, args.strict)
    except StopError as e:
        print(f"receipts: stopped: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # never let a crash look like a verdict
        print(f"receipts: stopped: internal error ({type(e).__name__}: {e})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
