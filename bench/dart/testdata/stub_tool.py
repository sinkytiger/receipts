#!/usr/bin/env python3
"""A stand-in for the receipts tool, for the E4 scorer self-test only. It is not the tool and shares no code with it.

It was written from the contract (skills/receipts/references/plan-format.md, sections 2, 4, 7 and 9) and answers
`check PLAN --out DIR --dart-cache DIR [--refresh]` for plans that hold DART lookups written by bench/dart/build.py.
It talks only to a local stand-in: without RECEIPTS_DART_URL on 127.0.0.1 it refuses to make any request.

`mode` turns on one fault, so that the scorer can be shown to catch it:
  column_ignored     every claim reads thstrm_amount
  fs_unchecked       `fs` is passed to DART as it is written
  receipt_ignored    `rcept_no` is not looked at
  status_as_no_data  every DART status other than 000 counts as "no data"
  name_substring     a name matches when the row's name contains it
  statement_ignored  the row is looked for in all four statements, whatever the plan says
  cfq_allowed        frmtrm_q_amount of an interim cash flow statement is read like any other column
  first_of_several   when several rows match a name or an account id, the first one is taken
  key_leak           the address of each request, key included, is printed
  key_in_cache       the address of each request, key included, is written to fetched.json
  always_verified    every claim is verified
  no_stop            nothing stops the run: what cannot be read counts as "no data"
"""
from __future__ import annotations

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
from datetime import datetime, timezone
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

REPORT_CODES = {"q1": "11013", "half": "11012", "q3": "11014", "annual": "11011"}
COLUMNS = ("thstrm_amount", "thstrm_add_amount", "frmtrm_amount", "frmtrm_q_amount", "frmtrm_add_amount",
           "bfefrmtrm_amount")
NOSTD = "-표준계정코드 미사용-"
NUM_RE = re.compile(r"(?P<sign>[-−+]?)(?P<d1>\d{1,3}(?:,\d{3})+|\d+)(?P<frac>\.\d+)?(?P<scale>백만|억|조)?"
                    r"(?: (?P<d2>\d{1,3}(?:,\d{3})+|\d+)억)?")
SCALE = {None: Decimal(1), "백만": Decimal(10) ** 6, "억": Decimal(10) ** 8, "조": Decimal(10) ** 12}


class Stopped(Exception):
    """Exit code 2."""


class NoMatch(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def read_written(line, quote, number):
    """(value, quantum) of the number the claim points at, read from the document line."""
    at = line.find(quote)
    if at < 0:
        return None
    inner = quote.find(number)
    if inner < 0:
        return None
    m = NUM_RE.match(line, at + inner)
    if not m:
        return None
    frac = m.group("frac") or ""
    scale = SCALE[m.group("scale")]
    value = Decimal(m.group("d1").replace(",", "") + frac) * scale
    quantum = scale * (Decimal(1).scaleb(-(len(frac) - 1)) if frac else Decimal(1))
    if m.group("scale") == "조" and m.group("d2"):
        value += Decimal(m.group("d2").replace(",", "")) * SCALE["억"]
        quantum = SCALE["억"]
    if m.group("sign") in ("-", "−"):
        value = -value
    return value, quantum


def compare(written, quantum, value):
    with localcontext() as ctx:
        ctx.prec = 80
        x = value / quantum
        if x.quantize(Decimal(1), rounding=ROUND_HALF_UP) * quantum == written \
                or x.quantize(Decimal(1), rounding=ROUND_HALF_EVEN) * quantum == written:
            return "verified", "match"
        if x.quantize(Decimal(1), rounding=ROUND_DOWN) * quantum == written:
            return "verified", "truncated"
    return "wrong", "mismatch"


class Dart:
    def __init__(self, cache, mode, refresh=False):
        self.cache, self.mode, self.refresh = cache, mode, refresh
        self.base = os.environ.get("RECEIPTS_DART_URL", "").rstrip("/")
        self.files = {}
        self.codes = None
        self.memo = {}
        self.failed = {}

    def _get(self, endpoint, params):
        key = os.environ.get("RECEIPTS_DART_KEY", "").strip()
        if not key:
            raise Stopped("RECEIPTS_DART_KEY is empty: no request was made")
        if not self.base.startswith("http://127.0.0.1:"):
            raise Stopped("this stub only talks to a local stand-in (set RECEIPTS_DART_URL): no request was made")
        url = f"{self.base}/api/{endpoint}?" + urllib.parse.urlencode([("crtfc_key", key)] + params)
        if self.mode == "key_leak":
            print(f"GET {url}", file=sys.stderr)
        if self.mode == "key_in_cache":
            self._note_fetched("last-request", url)
        time.sleep(0.01)
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return r.read()
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise Stopped(f"{endpoint}: the request failed ({type(e).__name__})") from None

    def _note_fetched(self, rel, value=None):
        path = os.path.join(self.cache, "fetched.json")
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        data[rel] = value or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        os.makedirs(self.cache, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)

    def statements(self, corp, year, reprt, fs):
        rel = f"fnlttSinglAcntAll/{corp}_{year}_{reprt}_{fs}.json"
        if rel in self.memo:
            return self.memo[rel]
        if rel in self.failed:
            raise Stopped(self.failed[rel])
        try:
            return self._statements(rel, corp, year, reprt, fs)
        except Stopped as e:
            self.failed[rel] = str(e)
            raise

    def _statements(self, rel, corp, year, reprt, fs):
        path = os.path.join(self.cache, *rel.split("/"))
        if os.path.isfile(path) and not self.refresh:
            with open(path, "rb") as fh:
                body = fh.read()
        else:
            body = self._get("fnlttSinglAcntAll.json", [("corp_code", corp), ("bsns_year", str(year)),
                                                        ("reprt_code", reprt), ("fs_div", fs)])
            try:
                status = str(json.loads(body.decode("utf-8")).get("status"))
            except (ValueError, UnicodeDecodeError, AttributeError):
                raise Stopped("fnlttSinglAcntAll.json: the answer is not the expected JSON") from None
            if status not in ("000", "013"):
                if self.mode != "status_as_no_data":
                    raise Stopped(f"fnlttSinglAcntAll.json: DART answered with status {status}")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(body)
            self._note_fetched(rel)
        try:
            data = json.loads(body.decode("utf-8"))
            if not isinstance(data, dict) or "status" not in data:
                raise ValueError("no status")
            if data["status"] == "000" and not isinstance(data.get("list"), list):
                raise ValueError("no list")
        except (ValueError, UnicodeDecodeError):
            raise Stopped(f"the cached file {rel} is not the expected JSON") from None
        entry = {"file": rel}
        if data["status"] == "000" and data["list"]:
            entry["rcept_no"] = str(data["list"][0].get("rcept_no"))
        self.files[rel] = entry
        self.memo[rel] = data
        return data

    def company(self, text):
        text = str(text).strip()
        if re.fullmatch(r"\d{8}", text):
            return text
        if len(text) != 6:
            raise NoMatch("locator_no_match")
        if "corpCode" in self.failed:
            raise Stopped(self.failed["corpCode"])
        try:
            return self._by_stock_code(text)
        except Stopped as e:
            self.failed["corpCode"] = str(e)
            raise

    def _by_stock_code(self, text):
        if self.codes is None:
            path = os.path.join(self.cache, "corpCode.zip")
            if os.path.isfile(path) and not self.refresh:
                with open(path, "rb") as fh:
                    body = fh.read()
            else:
                body = self._get("corpCode.xml", [])
                if body[:2] != b"PK":
                    raise Stopped("corpCode.xml: the answer is not the expected zip")
                os.makedirs(self.cache, exist_ok=True)
                with open(path, "wb") as fh:
                    fh.write(body)
                self._note_fetched("corpCode.zip")
            try:
                with zipfile.ZipFile(io.BytesIO(body)) as z:
                    xml = z.read(z.namelist()[0]).decode("utf-8")
            except (zipfile.BadZipFile, OSError, IndexError, UnicodeDecodeError):
                raise Stopped("the cached company code file is not the expected zip") from None
            self.codes = re.findall(r"<corp_code>\s*(\d{8})\s*</corp_code>.*?<stock_code>\s*([^<]*?)\s*</stock_code>",
                                    xml, re.S)
        hits = [corp for corp, stock in self.codes if stock.upper() == text.upper()]
        if not hits:
            raise NoMatch("locator_no_match")
        if len(hits) > 1:
            raise NoMatch("locator_multiple")
        return hits[0]


def lookup(dart, loc, mode):
    """The amount a DART locator names, as a Decimal. Raises NoMatch with the contract's reason otherwise."""
    if not isinstance(loc, dict) or "dart" not in loc:
        raise NoMatch("unsupported")
    year = str(loc.get("year", "")).strip()
    if not re.fullmatch(r"\d{4}", year) or int(year) < 2015:
        raise NoMatch("locator_no_match")
    report = str(loc.get("report", "")).lower()
    fs = str(loc.get("fs", ""))
    statement = str(loc.get("statement", "")).upper()
    column = "thstrm_amount" if mode == "column_ignored" else loc.get("column")
    if report not in REPORT_CODES or column not in COLUMNS:
        raise NoMatch("locator_no_match")
    if mode != "fs_unchecked":
        fs = fs.upper()
        if fs not in ("CFS", "OFS"):
            raise NoMatch("locator_no_match")
    if mode != "statement_ignored":
        if statement == "SCE":
            raise NoMatch("unsupported")
        if statement not in ("BS", "IS", "CIS", "CF"):
            raise NoMatch("locator_no_match")
    refuses_cfq = mode not in ("cfq_allowed", "statement_ignored")
    if refuses_cfq and report != "annual" and statement == "CF" and column == "frmtrm_q_amount":
        raise NoMatch("unsupported")        # contract, section 2: not usable in an interim cash flow statement
    rcept = loc.get("rcept_no")
    if rcept is not None and not re.fullmatch(r"\d{14}", str(rcept)):
        raise NoMatch("locator_no_match")
    account, name = loc.get("account"), loc.get("name")
    if account is None and name is None:
        raise NoMatch("locator_no_match")
    corp = dart.company(loc["dart"])
    data = dart.statements(corp, year, REPORT_CODES[report], fs)
    if data.get("status") != "000":
        raise NoMatch("locator_no_match")
    rows = [r for r in data["list"] if isinstance(r, dict)]
    served = str(rows[0].get("rcept_no")) if rows else None
    if rcept is not None and mode != "receipt_ignored" and str(rcept) != served:
        raise NoMatch("dart_receipt_differs")
    wanted = ("BS", "IS", "CIS", "CF") if mode == "statement_ignored" else (statement,)
    hits = []
    for r in rows:
        if r.get("sj_div") not in wanted:
            continue
        if account is not None and (r.get("account_id") != account or account == NOSTD):
            continue
        if name is not None:
            nm = str(r.get("account_nm", "")).strip()
            if (str(name).strip() not in nm) if mode == "name_substring" else (nm != str(name).strip()):
                continue
        hits.append(r)
    if not hits:
        raise NoMatch("locator_no_match")
    if len(hits) > 1 and mode != "first_of_several":
        raise NoMatch("locator_multiple")
    cell = hits[0].get(column)
    if cell is None or str(cell).strip() == "":
        raise NoMatch("locator_no_match")
    try:
        return Decimal(str(cell).replace(",", "").strip()), hits[0]
    except InvalidOperation:
        raise NoMatch("cell_not_numeric") from None


def main(argv, mode="ok"):
    args = list(argv)
    if not args or args[0] != "check":
        print("stub tool: only `check` is answered", file=sys.stderr)
        return 2
    plan_path, out, cache, refresh = None, None, None, False
    i = 1
    while i < len(args):
        a = args[i]
        if a in ("--out", "--dart-cache", "--edgar-cache", "--mode"):
            if a == "--out":
                out = args[i + 1]
            elif a == "--dart-cache":
                cache = args[i + 1]
            i += 2
            continue
        if a == "--refresh":
            refresh = True
        elif not a.startswith("--") and plan_path is None:
            plan_path = a
        i += 1
    try:
        with open(plan_path, encoding="utf-8") as fh:
            plan = json.load(fh)
        folder = os.path.dirname(os.path.abspath(plan_path))
        with open(os.path.join(folder, plan["document"]), encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        claims = plan["claims"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"stopped: the plan or its document cannot be read ({type(e).__name__})", file=sys.stderr)
        return 2
    out = out or folder
    cache = cache or os.environ.get("RECEIPTS_DART_CACHE") or os.path.join(folder, ".receipts", "dart")
    dart = Dart(cache, mode, refresh)
    results, unbound = [], []
    try:
        for c in claims:
            line = lines[c["line"] - 1] if 0 < c.get("line", 0) <= len(lines) else ""
            got = read_written(line, c.get("quote", ""), c.get("number", ""))
            if got is None:
                unbound.append({"id": c.get("id"), "reason": "quote_not_on_line"})
                continue
            written, quantum = got
            row = {"id": c["id"], "line": c["line"], "text": c["number"], "quote": c["quote"], "sources": [],
                   "note": ""}
            try:
                try:
                    value, source = lookup(dart, c.get("operands", {}).get("of"), mode)
                except Stopped:
                    if mode != "no_stop":
                        raise
                    raise NoMatch("locator_no_match") from None
                verdict, reason = compare(written, quantum, value)
                if mode == "always_verified":
                    verdict, reason = "verified", "match"
                row.update(verdict=verdict, reason=reason, recomputed_raw=str(value),
                           sources=[f"DART {source.get('rcept_no')} {source.get('sj_div')} "
                                    f"{source.get('account_id')} = {value}"])
            except NoMatch as e:
                if mode == "always_verified":
                    row.update(verdict="verified", reason="match")
                else:
                    row.update(verdict="unverifiable", reason=e.reason)
            results.append(row)
    except Stopped as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 2
    counts = {"numbers": len(lines) - 1}
    for v in ("verified", "wrong", "unverifiable"):
        counts[v] = sum(1 for r in results if r["verdict"] == v)
    counts["uncovered"] = max(0, counts["numbers"] - len(results))
    receipt = {"document": plan["document"], "mode": "default", "counts": counts, "skipped": {}, "claims": results,
               "uncovered": [], "unbound_claims": unbound, "labels": [], "truncated": 0, "multipliers": [],
               "dart": {"files": [dict(f, fetched="made-up") for f in dart.files.values()]}}
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "receipts.json"), "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, ensure_ascii=False)
    md = f"# receipt (stub tool)\n\n{counts}\n"
    with open(os.path.join(out, "receipts.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    print(md)
    return 1 if counts["wrong"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
