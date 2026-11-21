#!/usr/bin/env python3
"""E4 builder for receipts (see docs/eval-plan.md, section "E4: DART connection").

Usage:
  python bench/dart/build.py --fetch-only            # development companies -> bench/dart/cache/ (needs the key)
  python bench/dart/build.py --offline               # build bench/dart/out/dev/ from the cache, no request
  python bench/dart/build.py --fetch-only --held-out # held-out companies and the draw (the release run only)
  python bench/dart/build.py --offline --held-out    # build bench/dart/out/heldout/ from the cache
  python bench/dart/build.py --self-test             # offline checks on made-up data

Fetching is a step of its own. It reads four things from OpenDART: the statements of each set
(fnlttSinglAcntAll.json), the XBRL file of each receipt number (fnlttXbrl.xml), each company's list of periodic
reports (list.json) and the company code file (corpCode.xml). The key comes from RECEIPTS_DART_KEY (no default).
It is sent as the crtfc_key parameter only and is never printed or written: messages name the endpoint, never the
address of a request. At most 5 requests a second (this builder makes at most 4). Every answer is cached under
bench/dart/cache/ and never fetched again.

The gold comes from the XBRL instance only. From the statements answer the builder takes the list of rows
(statement, account id, account name) and the receipt number. It reads no amount from it.

Exit 0 on success, 1 when a set could not be built, 2 when it has to stop.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BUILDER_VERSION = "e4-builder-2"

# ---------------------------------------------------------------- the registered lists (docs/eval-plan.md, E4)

DEV = [("00126380", "삼성전자"), ("00120030", "GS건설"), ("00367844", "자이에스앤디"), ("00118008", "동원모빌리티"),
       ("00164742", "현대자동차"), ("00164779", "SK하이닉스"), ("00266961", "NAVER"), ("00190321", "케이티"),
       ("00126256", "삼성생명"), ("00688996", "KB금융")]
HELD_OUT_NAMED = [("00106641", "기아"), ("00164788", "현대모비스"), ("00155319", "POSCO홀딩스"), ("00356361", "LG화학"),
                  ("00126362", "삼성SDI"), ("00877059", "삼성바이오로직스"), ("00159023", "SK텔레콤"),
                  ("00149655", "삼성물산"), ("01515323", "LG에너지솔루션"), ("00258801", "카카오"),
                  ("00401731", "LG전자")]
DRAW_COUNT = 20
DRAW_TEXT = "receipts-E4:"

KEY_ENV = "RECEIPTS_DART_KEY"
API_BASE = "https://opendart.fss.or.kr"
LIST_FROM = "20250101"          # the filing list is asked from this day to the day of the fetch

REPORTS = (("annual", 2025, "11011"), ("half", 2026, "11012"), ("q3", 2026, "11014"))
FS_DIVS = ("CFS", "OFS")
STATEMENTS = ("BS", "IS", "CIS", "CF")
NO_STANDARD_ID = "-표준계정코드 미사용-"
ID_PREFIXES = ("ifrs-full_", "dart_")
ALL_COLUMNS = ("thstrm_amount", "thstrm_add_amount", "frmtrm_amount", "frmtrm_q_amount", "frmtrm_add_amount",
               "bfefrmtrm_amount")

# Contract, section 2, "What each column holds". Values are period keys, see set_periods().
# In an interim cash flow statement the contract calls frmtrm_q_amount "not usable" (a locator that names it gives
# `unsupported`), so it is no column of a fact there. It is the subject of the abstention kind "cfq".
ANNUAL_COLUMNS = {
    "BS": (("thstrm_amount", "end0"), ("frmtrm_amount", "end1"), ("bfefrmtrm_amount", "end2")),
    "FLOW": (("thstrm_amount", "year0"), ("frmtrm_amount", "year1"), ("bfefrmtrm_amount", "year2")),
}
INTERIM_COLUMNS = {
    "BS": (("thstrm_amount", "end"), ("frmtrm_amount", "prev_year_end")),
    "INCOME": (("thstrm_amount", "three_months"), ("thstrm_add_amount", "cumulative"),
               ("frmtrm_q_amount", "prior_three_months"), ("frmtrm_add_amount", "prior_cumulative")),
    "CF": (("thstrm_amount", "cumulative"), ("frmtrm_amount", "prior_cumulative")),
}
ABSTAIN_KINDS = ("account", "column", "year", "fs", "sce", "receipt", "statement", "cfq")
UNUSABLE_CF_COLUMN = "frmtrm_q_amount"
ABSTAIN_EACH = 5
AMENDED_EACH = 5
WAYS = ("won", "million", "eok", "jo_eok")
EOK = Decimal(100000000)
JO = Decimal(1000000000000)
MILLION = Decimal(1000000)


def columns_for(report, statement):
    """The columns of the contract's table for a report type and a statement, with the key of their period."""
    if report == "annual":
        return ANNUAL_COLUMNS["BS" if statement == "BS" else "FLOW"]
    if statement == "BS":
        return INTERIM_COLUMNS["BS"]
    return INTERIM_COLUMNS["CF" if statement == "CF" else "INCOME"]


class Stop(Exception):
    """The run has to stop with exit code 2."""


def scrub(text):
    """Defence in depth: a message never shows the key, even if a library put it into an error text."""
    key = os.environ.get(KEY_ENV, "").strip()
    text = str(text)
    if key:
        text = text.replace(key, "<key>")
    return re.sub(r"crtfc_key=[^&\s'\"]*", "crtfc_key=<key>", text)


def kst_today():
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d")


def utc_today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def write_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def write_rows_json(path, data, row_keys):
    """JSON with one line per item of the lists named in row_keys (plans and gold files stay small and greppable)."""
    parts = []
    for key, value in data.items():
        if key in row_keys and isinstance(value, list) and value:
            rows = ",\n".join(json.dumps(v, ensure_ascii=False) for v in value)
            parts.append(f"{json.dumps(key)}: [\n{rows}\n]")
        else:
            parts.append(f"{json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("{" + ",\n".join(parts) + "}\n")
    os.replace(tmp, path)


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- fetching

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None          # a redirect is not followed: the address carries the key


def _default_opener():
    return urllib.request.build_opener(_NoRedirect).open


class Fetcher:
    """Cached, paced GET of the four OpenDART endpoints. A cached answer is never requested again.

    Cache layout (under the cache folder):
      corpCode.zip | corpCode-heldout.zip        the company code file (the held-out run fetches its own, on its day)
      fnlttSinglAcntAll/<corp>_<year>_<reprt>_<fs>.json   the answer as DART sent it (status 000 or 013)
      fnlttXbrl/<rcept_no>_<reprt>.zip            the XBRL file
      fnlttXbrl/<rcept_no>_<reprt>.status.json    DART's answer when it has no such file (status 013 or 014)
      list/<corp>_A_<from>_p<page>.json           the company's periodic reports, earlier versions included
      fetched.json                                for each file the UTC date it was fetched (and the list's end day)
    """

    MIN_GAP, TIMEOUT, TIMEOUT_ZIP, RETRY_WAIT = 0.25, 30, 180, 2.0
    NO_DATA = ("013",)
    NO_FILE = ("013", "014")

    def __init__(self, cache, offline=False, base=API_BASE, opener=None, sleep=None, clock=None, log=None):
        self.cache, self.offline, self.base = cache, offline, base.rstrip("/")
        self.opener = opener or _default_opener()
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        self.log = log or (lambda msg: print(msg, flush=True))
        self.last = None
        self.requests = 0
        self.by_endpoint = Counter()
        self._fetched = None

    # -- bookkeeping
    def _fetched_map(self):
        if self._fetched is None:
            path = os.path.join(self.cache, "fetched.json")
            try:
                self._fetched = load_json(path)
            except (OSError, ValueError):
                self._fetched = {}
        return self._fetched

    def fetched_date(self, rel):
        got = self._fetched_map().get(rel)
        if isinstance(got, dict):
            return got.get("date")
        if got:
            return got
        path = os.path.join(self.cache, *rel.split("/"))
        if os.path.isfile(path):
            return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).strftime("%Y-%m-%d")
        return None

    def fetched_info(self, rel):
        got = self._fetched_map().get(rel)
        return got if isinstance(got, dict) else {"date": self.fetched_date(rel)}

    def _store(self, rel, body, **extra):
        path = os.path.join(self.cache, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(body)
        os.replace(tmp, path)
        info = {"date": utc_today()}
        info.update(extra)
        self._fetched_map()[rel] = info
        write_json(os.path.join(self.cache, "fetched.json"), dict(sorted(self._fetched_map().items())))

    def _read(self, rel):
        path = os.path.join(self.cache, *rel.split("/"))
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                return fh.read()
        return None

    def has(self, rel):
        return os.path.isfile(os.path.join(self.cache, *rel.split("/")))

    # -- the request
    def _pace(self):
        if self.last is not None:
            gap = self.clock() - self.last
            if gap < self.MIN_GAP:
                self.sleep(self.MIN_GAP - gap)
        self.last = self.clock()

    def _request(self, endpoint, params, timeout):
        """One GET with the key. Returns the body. Messages name the endpoint only, never the address."""
        if self.offline:
            raise Stop(f"offline: an answer of {endpoint} is not in the cache ({self.cache}). "
                       "Run build.py --fetch-only first.")
        key = os.environ.get(KEY_ENV, "").strip()
        if not key:
            raise Stop(f"{KEY_ENV} is empty. Set it to your own OpenDART key. No request was made.")
        query = urllib.parse.urlencode([("crtfc_key", key)] + list(params))
        url = f"{self.base}/api/{endpoint}?{query}"
        problem = None
        for attempt in (1, 2):
            self._pace()
            self.requests += 1
            self.by_endpoint[endpoint] += 1
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "receipts-bench-e4"})
                with self.opener(req, timeout=timeout) as r:
                    status = getattr(r, "status", 200) or 200
                    body = r.read()
                if 300 <= status < 400:
                    raise Stop(f"{endpoint}: a redirect (HTTP {status}) is not followed")
                return body
            except urllib.error.HTTPError as e:
                if 300 <= e.code < 400:
                    raise Stop(f"{endpoint}: a redirect (HTTP {e.code}) is not followed") from None
                if e.code == 429 or 500 <= e.code < 600:
                    problem = f"HTTP {e.code}"
                else:
                    raise Stop(f"{endpoint}: HTTP {e.code}") from None
            except urllib.error.URLError as e:
                if isinstance(e.reason, (socket.timeout, TimeoutError)):
                    problem = "timeout"
                else:
                    problem = f"no connection ({type(e.reason).__name__})"
            except (socket.timeout, TimeoutError):
                problem = "timeout"
            except Stop:
                raise
            except (OSError, EOFError, ValueError) as e:
                problem = f"request failed ({type(e).__name__})"
            if attempt == 1:
                self.sleep(self.RETRY_WAIT)
        raise Stop(f"{endpoint}: {problem}, also after one retry")

    @staticmethod
    def _status_of(body):
        """DART's status and message from a JSON or XML answer, or (None, None)."""
        head = body[:4096]
        try:
            text = head.decode("utf-8")
        except UnicodeDecodeError:
            text = head.decode("utf-8", "replace")
        if text.lstrip().startswith("{"):
            try:
                data = json.loads(body.decode("utf-8"))
                if isinstance(data, dict) and "status" in data:
                    return str(data.get("status")), str(data.get("message", ""))
            except (ValueError, UnicodeDecodeError):
                return None, None
        m = re.search(r"<status>\s*(\d{3})\s*</status>", text)
        if m:
            mm = re.search(r"<message>\s*(.*?)\s*</message>", text, re.S)
            return m.group(1), (mm.group(1) if mm else "")
        return None, None

    def _stop_status(self, endpoint, status, message):
        raise Stop(f"{endpoint}: DART answered with status {status} ({scrub(message)[:120]}). Not cached.")

    # -- the four endpoints
    def statements(self, corp, year, reprt, fs):
        """The statements answer (a dict with status 000 or 013). Returns (data, was_fetched)."""
        if fs not in FS_DIVS:
            raise Stop(f"fnlttSinglAcntAll: fs must be CFS or OFS, not {fs!r}")
        rel = f"fnlttSinglAcntAll/{corp}_{year}_{reprt}_{fs}.json"
        body = self._read(rel)
        fetched = False
        if body is None:
            body = self._request("fnlttSinglAcntAll.json", [("corp_code", corp), ("bsns_year", str(year)),
                                                            ("reprt_code", reprt), ("fs_div", fs)], self.TIMEOUT)
            status, message = self._status_of(body)
            if status is None:
                raise Stop("fnlttSinglAcntAll.json: the answer is not the expected JSON. Not cached.")
            if status != "000" and status not in self.NO_DATA:
                self._stop_status("fnlttSinglAcntAll.json", status, message)
            self._store(rel, body)
            fetched = True
        try:
            data = json.loads(body.decode("utf-8"))
            if not isinstance(data, dict) or "status" not in data:
                raise ValueError("no status")
            if data["status"] == "000" and not isinstance(data.get("list"), list):
                raise ValueError("no list")
        except (ValueError, UnicodeDecodeError) as e:
            raise Stop(f"cached file is not the expected JSON: {rel} ({e})") from None
        return data, fetched

    def xbrl(self, rcept_no, reprt):
        """The XBRL zip of a receipt number. Returns (zip bytes or None when DART has no file, status, fetched)."""
        if not re.fullmatch(r"\d{14}", str(rcept_no)):
            raise Stop("fnlttXbrl: a receipt number has 14 digits")
        rel_zip = f"fnlttXbrl/{rcept_no}_{reprt}.zip"
        rel_no = f"fnlttXbrl/{rcept_no}_{reprt}.status.json"
        body = self._read(rel_zip)
        if body is not None:
            return body, "000", False
        no = self._read(rel_no)
        if no is not None:
            try:
                return None, str(json.loads(no.decode("utf-8")).get("status")), False
            except (ValueError, UnicodeDecodeError, AttributeError):
                raise Stop(f"cached file is not the expected JSON: {rel_no}") from None
        body = self._request("fnlttXbrl.xml", [("rcept_no", rcept_no), ("reprt_code", reprt)], self.TIMEOUT_ZIP)
        if body[:2] == b"PK":
            try:
                with zipfile.ZipFile(io.BytesIO(body)) as z:
                    z.infolist()
            except (zipfile.BadZipFile, OSError, ValueError):
                raise Stop("fnlttXbrl.xml: the answer is not a readable zip. Not cached.") from None
            self._store(rel_zip, body)
            return body, "000", True
        status, message = self._status_of(body)
        if status is None:
            raise Stop("fnlttXbrl.xml: the answer is neither a zip nor a status. Not cached.")
        if status not in self.NO_FILE:
            self._stop_status("fnlttXbrl.xml", status, message)
        self._store(rel_no, json.dumps({"status": status, "message": message}, ensure_ascii=False).encode("utf-8"))
        return None, status, True

    def filing_list(self, corp):
        """All pages of the company's periodic reports from LIST_FROM on. Returns (rows, info, requests made)."""
        rows, page, made, info = [], 1, 0, {}
        while True:
            rel = f"list/{corp}_A_{LIST_FROM}_p{page}.json"
            body = self._read(rel)
            if body is None:
                end = kst_today()
                body = self._request("list.json", [("corp_code", corp), ("bgn_de", LIST_FROM), ("end_de", end),
                                                   ("pblntf_ty", "A"), ("last_reprt_at", "N"),
                                                   ("page_count", "100"), ("page_no", str(page))], self.TIMEOUT)
                status, message = self._status_of(body)
                if status is None:
                    raise Stop("list.json: the answer is not the expected JSON. Not cached.")
                if status != "000" and status not in self.NO_DATA:
                    self._stop_status("list.json", status, message)
                self._store(rel, body, list_from=LIST_FROM, list_to=end)
                made += 1
            try:
                data = json.loads(body.decode("utf-8"))
                if not isinstance(data, dict) or "status" not in data:
                    raise ValueError("no status")
            except (ValueError, UnicodeDecodeError) as e:
                raise Stop(f"cached file is not the expected JSON: {rel} ({e})") from None
            if page == 1:
                info = dict(self.fetched_info(rel))
            if data.get("status") != "000":
                break
            rows += [r for r in data.get("list") or [] if isinstance(r, dict)]
            try:
                total = int(data.get("total_page") or 1)
            except (TypeError, ValueError):
                total = 1
            if page >= total or page >= 50:
                break
            page += 1
        return rows, info, made

    def corp_codes(self, heldout=False):
        """The company code file as a list of dicts (corp_code, corp_name, stock_code). Returns (rows, fetched)."""
        rel = "corpCode-heldout.zip" if heldout else "corpCode.zip"
        body = self._read(rel)
        fetched = False
        if body is None:
            body = self._request("corpCode.xml", [], self.TIMEOUT_ZIP)
            if body[:2] != b"PK":
                status, message = self._status_of(body)
                if status is None:
                    raise Stop("corpCode.xml: the answer is neither a zip nor a status. Not cached.")
                self._stop_status("corpCode.xml", status, message)
            self._store(rel, body)
            fetched = True
        try:
            return parse_corp_codes(body), fetched
        except (zipfile.BadZipFile, ET.ParseError, OSError, ValueError, KeyError) as e:
            raise Stop(f"cached file is not the expected zip: {rel} ({type(e).__name__})") from None


MAX_MEMBER = 600 * 1024 * 1024


def _refuse_doctype(data):
    if b"<!DOCTYPE" in data[:65536] or b"<!ENTITY" in data[:65536]:
        raise ValueError("the XML has a DOCTYPE or an entity declaration, not read")


def parse_corp_codes(body):
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = [i for i in z.infolist() if i.filename.lower().endswith(".xml")]
        if len(names) != 1 or names[0].file_size > MAX_MEMBER:
            raise ValueError("expected one XML file in the company code zip")
        data = z.read(names[0])
    _refuse_doctype(data)
    out = []
    for _, el in ET.iterparse(io.BytesIO(data), events=("end",)):
        if el.tag == "list":
            row = {c.tag: (c.text or "").strip() for c in el}
            if row.get("corp_code"):
                out.append({"corp_code": row["corp_code"], "corp_name": row.get("corp_name", ""),
                            "stock_code": row.get("stock_code", "")})
            el.clear()
    if not out:
        raise ValueError("no company in the company code file")
    return out


# ---------------------------------------------------------------- the XBRL instance

XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
AXIS_LOCAL = "ConsolidatedAndSeparateFinancialStatementsAxis"
MEMBER_LOCAL = {"ConsolidatedMember": "CFS", "SeparateMember": "OFS"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NUM_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")


def instance_from_zip(body):
    """The bytes of the one instance document (.xbrl) in DART's zip."""
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        inst = [i for i in z.infolist() if i.filename.lower().endswith(".xbrl")]
        if len(inst) != 1:
            raise ValueError(f"expected one .xbrl file in the zip, found {len(inst)}")
        if inst[0].file_size > MAX_MEMBER:
            raise ValueError("the instance is too large")
        return z.read(inst[0]), inst[0].filename


class Instance:
    """What the builder needs from an XBRL instance: periods, and the facts of the statements' own contexts."""

    def __init__(self, data):
        _refuse_doctype(data)
        self.ns = {}                # prefix -> uri, as declared in this file
        self.ns_conflict = set()    # prefixes declared with two addresses
        self.contexts = {}          # id -> {"period": tuple, "dims": [((uri, local), (uri, local))], "other": n}
        self.units = {}             # id -> text such as KRW, KRW/shares, shares, pure
        self.facts = {}             # (tag uri, local, fs, period) -> [(value text, unit, nil)]
        self.gcd_periods = {}       # member local name -> {"start": date, "end": date}
        self.fact_count = 0
        self._by_element = None
        raw_ctx, raw_units, raw_facts = {}, {}, []
        depth = 0
        for event, x in ET.iterparse(io.BytesIO(data), events=("start-ns", "start", "end")):
            if event == "start-ns":
                prefix, uri = x
                if prefix in self.ns and self.ns[prefix] != uri:
                    self.ns_conflict.add(prefix)
                self.ns.setdefault(prefix, uri)
            elif event == "start":
                depth += 1
            else:
                depth -= 1
                if depth != 1:
                    continue
                if x.tag == f"{{{XBRLI}}}context":
                    raw_ctx[x.get("id")] = self._context(x)
                elif x.tag == f"{{{XBRLI}}}unit":
                    raw_units[x.get("id")] = x
                    continue
                elif x.get("contextRef") is not None:
                    raw_facts.append((x.tag, x.get("contextRef"), x.get("unitRef"), (x.text or "").strip(),
                                      x.get(f"{{{XSI}}}nil") in ("true", "1")))
                x.clear()
        for uid, el in raw_units.items():
            self.units[uid] = self._unit(el)
        for cid, c in raw_ctx.items():
            c["dims"] = [(self.qname(d), self.qname(m)) for d, m in c.pop("raw_dims")]
            self.contexts[cid] = c
        full = self.ns.get("ifrs-full") if "ifrs-full" not in self.ns_conflict else None
        gcd = self.ns.get("dart-gcd") if "dart-gcd" not in self.ns_conflict else None
        for tag, cref, uref, text, nil in raw_facts:
            self.fact_count += 1
            c = self.contexts.get(cref)
            if c is None or not tag.startswith("{"):
                continue
            uri, local = tag[1:].split("}", 1)
            if c["other"] == 0 and len(c["dims"]) == 1:
                (duri, dlocal), (muri, mlocal) = c["dims"][0]
                if full and duri == full and dlocal == AXIS_LOCAL and muri == full and mlocal in MEMBER_LOCAL:
                    if c["period"] is not None:
                        key = (uri, local, MEMBER_LOCAL[mlocal], c["period"])
                        self.facts.setdefault(key, []).append((text, self.units.get(uref, uref), nil))
                elif gcd and uri == gcd and duri == gcd and dlocal == "PeriodAxis" and muri == gcd \
                        and local in ("DocumentPeriodStartDate", "DocumentPeriodEndDate") and DATE_RE.match(text):
                    slot = self.gcd_periods.setdefault(mlocal, {})
                    which = "start" if local.endswith("StartDate") else "end"
                    if slot.get(which, text) != text:
                        slot["conflict"] = True
                    slot[which] = text

    @staticmethod
    def _context(x):
        period, raw_dims, other = None, [], 0
        per = x.find(f"{{{XBRLI}}}period")
        if per is not None:
            inst = per.find(f"{{{XBRLI}}}instant")
            start, end = per.find(f"{{{XBRLI}}}startDate"), per.find(f"{{{XBRLI}}}endDate")
            if inst is not None and DATE_RE.match((inst.text or "").strip()[:10]):
                period = ((inst.text or "").strip()[:10],)
            elif start is not None and end is not None:
                s, e = (start.text or "").strip()[:10], (end.text or "").strip()[:10]
                if DATE_RE.match(s) and DATE_RE.match(e):
                    period = (s, e)
        for holder in list(x.iter(f"{{{XBRLI}}}segment")) + list(x.iter(f"{{{XBRLI}}}scenario")):
            for child in holder:
                if child.tag == f"{{{XBRLDI}}}explicitMember":
                    raw_dims.append((child.get("dimension") or "", (child.text or "").strip()))
                else:
                    other += 1
        return {"period": period, "raw_dims": raw_dims, "other": other}

    def qname(self, text):
        """(address, local name) of a prefixed name, by the prefixes this file declares."""
        prefix, _, local = text.strip().rpartition(":")
        if prefix in self.ns_conflict:
            return (None, local)
        return (self.ns.get(prefix), local)

    def _unit(self, el):
        def measures(node):
            out = []
            for m in node.findall(f"{{{XBRLI}}}measure"):
                uri, local = self.qname(m.text or "")
                out.append(local)
            return "*".join(out)
        div = el.find(f"{{{XBRLI}}}divide")
        if div is not None:
            num, den = div.find(f"{{{XBRLI}}}unitNumerator"), div.find(f"{{{XBRLI}}}unitDenominator")
            if num is None or den is None:
                return "?"
            return f"{measures(num)}/{measures(den)}"
        return measures(el)

    def element(self, account_id):
        """(address, local name) of the element an account id names: prefix, underscore, local name."""
        prefix, _, local = account_id.partition("_")
        if not local or prefix in self.ns_conflict or prefix not in self.ns:
            return None
        return (self.ns[prefix], local)

    def statement_periods(self, fs):
        """The periods of this member's own contexts (one dimension member): instants and durations."""
        instants, durations = set(), set()
        for (_, _, f, period) in self.facts:
            if f == fs:
                (instants if len(period) == 1 else durations).add(period)
        return instants, durations

    def periods_of(self, element, fs):
        """The periods under which this member has a fact of the element."""
        if self._by_element is None:
            self._by_element = {}
            for (uri, local, f, period) in self.facts:
                self._by_element.setdefault((uri, local, f), set()).add(period)
        return self._by_element.get((element[0], element[1], fs), set())


def _d(text):
    return date(int(text[0:4]), int(text[5:7]), int(text[8:10]))


def _gcd_slot(inst, *needles, exclude=()):
    hits = [(name, p) for name, p in inst.gcd_periods.items()
            if all(n in name for n in needles) and not any(e in name for e in exclude)]
    if len(hits) != 1:
        return None
    p = hits[0][1]
    if p.get("conflict") or "start" not in p or "end" not in p or _d(p["start"]) > _d(p["end"]):
        return None
    return (p["start"], p["end"])


def set_periods(inst, report, fs):
    """The period of each column kind, with the dates taken from the instance. A missing key means: not known.

    The instance states its periods as facts (dart-gcd:DocumentPeriodStartDate / EndDate, one pair per period
    shown). The three months of an interim report are not stated that way: they are the one period of about three
    months that ends on the period end among the contexts of the statements themselves.
    """
    out, notes = {}, []
    cur = _gcd_slot(inst, "CurrentFiscalYear")
    last = _gcd_slot(inst, "LastFiscalYear", exclude=("YearBeforeLast", "Quarter", "HalfYear"))
    two = _gcd_slot(inst, "YearBeforeLast")
    prior = _gcd_slot(inst, "Quarter", "LastFiscalYear", exclude=("YearBeforeLast", "CurrentFiscalYear"))
    if cur is None:
        return out, ["the instance does not state the period of the report"]
    one = timedelta(days=1)
    if report == "annual":
        out["year0"], out["end0"] = cur, (cur[1],)
        if last is not None and _d(last[1]) + one == _d(cur[0]):
            out["year1"], out["end1"] = last, (last[1],)
            if two is not None and _d(two[1]) + one == _d(last[0]):
                out["year2"], out["end2"] = two, (two[1],)
            else:
                notes.append("the year two years back is not stated or does not join the previous year")
        else:
            notes.append("the previous year is not stated or does not join the year of the report")
        return out, notes
    out["cumulative"], out["end"] = cur, (cur[1],)
    if last is not None and _d(last[1]) + one == _d(cur[0]):
        out["prev_year_end"] = (last[1],)
    else:
        notes.append("the previous business year is not stated or does not join the period of the report")
    if prior is not None and (last is None or prior[0] == last[0]) and _d(prior[1]) < _d(cur[0]):
        out["prior_cumulative"] = prior
    else:
        notes.append("the same period a year earlier is not stated")
    _, durations = inst.statement_periods(fs)

    def three_months(cumulative):
        if report == "q1":
            return cumulative
        hits = sorted(p for p in durations if p[1] == cumulative[1] and _d(p[0]) > _d(cumulative[0])
                      and 80 <= (_d(p[1]) - _d(p[0])).days + 1 <= 100)
        return hits[0] if len(hits) == 1 else None
    q = three_months(cur)
    if q is not None:
        out["three_months"] = q
    else:
        notes.append("no single three-month period ends on the period end")
    if "prior_cumulative" in out:
        pq = three_months(out["prior_cumulative"])
        if pq is not None:
            out["prior_three_months"] = pq
        else:
            notes.append("no single three-month period ends on the period end a year earlier")
    return out, notes


def gold_of(inst, account_id, fs, period):
    """(value as Decimal, unit, None) or (None, None, reason)."""
    el = inst.element(account_id)
    if el is None:
        return None, None, "prefix_not_declared"
    if period is None:
        return None, None, "period_not_known"
    got = inst.facts.get((el[0], el[1], fs, period))
    if not got:
        have = inst.periods_of(el, fs)
        if not have:
            return None, None, "no_fact"
        if len(period) == 2 and all(len(p) == 1 for p in have):
            return None, None, "only_instant_facts"     # a balance shown in a flow statement, for example
        if len(period) == 1 and all(len(p) == 2 for p in have):
            return None, None, "only_duration_facts"
        return None, None, "no_fact_in_period"
    values = set()
    for text, unit, nil in got:
        if nil or not NUM_RE.match(text):
            return None, None, "not_a_number"
        try:
            values.add((Decimal(text), unit))
        except InvalidOperation:
            return None, None, "not_a_number"
    if len(values) != 1:
        return None, None, "values_differ"
    value, unit = next(iter(values))
    return value, unit, None


# ---------------------------------------------------------------- companies, the draw, fetching

def registry(path=None):
    """The registered lists. A file replaces them in the self-test only (made-up companies)."""
    if path:
        data = load_json(path)
        return {"dev": [tuple(x) for x in data["dev"]], "heldout": [tuple(x) for x in data["heldout"]],
                "draw": int(data.get("draw", 0)), "made_up": True}
    return {"dev": list(DEV), "heldout": list(HELD_OUT_NAMED), "draw": DRAW_COUNT, "made_up": False}


def choose_companies(reg, held_out, only=None):
    """The named companies of the run. A held-out company is refused without --held-out, before any request."""
    held = {c for c, _ in HELD_OUT_NAMED} | {c for c, _ in reg["heldout"]}
    if held_out:
        if only:
            raise Stop("--companies cannot be combined with --held-out: the held-out run takes every company.")
        return list(reg["heldout"])
    names = dict(reg["dev"])
    chosen = only or [c for c, _ in reg["dev"]]
    for c in chosen:
        if c in held:
            raise Stop(f"refusing the held-out company {c}: pass --held-out only for the release run. "
                       "No request was made.")
        if c not in names:
            raise Stop(f"{c} is not a development company of the plan. Companies outside the registered lists "
                       "are fetched only by the held-out draw. No request was made.")
    return [(c, names[c]) for c in chosen]


def receipt_numbers(data):
    """The receipt numbers of a statements answer, in order of first appearance."""
    seen = OrderedDict()
    for row in data.get("list") or []:
        if isinstance(row, dict) and row.get("rcept_no"):
            seen[str(row["rcept_no"])] = True
    return list(seen)


def draw_order(corp_rows, excluded):
    """Companies with a stock code that are not named in the plan, by the SHA-256 digest of DRAW_TEXT + code."""
    seen, cands = set(), []
    for r in corp_rows:
        code = r["corp_code"]
        if r.get("stock_code", "").strip() and code not in excluded and code not in seen:
            seen.add(code)
            cands.append(r)
    return sorted(cands, key=lambda r: hashlib.sha256((DRAW_TEXT + r["corp_code"]).encode("utf-8")).hexdigest())


def walk_draw(fetcher, reg, corp_rows, say=None):
    """Walk the draw order until reg['draw'] companies are kept. Every answer is cached, so the walk can be
    repeated offline and gives the same result."""
    excluded = {c for c, _ in DEV} | {c for c, _ in HELD_OUT_NAMED} | {c for c, _ in reg["dev"]} \
        | {c for c, _ in reg["heldout"]}
    order = draw_order(corp_rows, excluded)
    kept, passed = [], []
    annual = next(r for r in REPORTS if r[0] == "annual")
    for pos, r in enumerate(order, 1):
        if len(kept) >= reg["draw"]:
            break
        corp, reason, rcept = r["corp_code"], None, None
        for fs in FS_DIVS:
            data, _ = fetcher.statements(corp, annual[1], annual[2], fs)
            numbers = receipt_numbers(data) if data.get("status") == "000" else []
            if numbers:
                rcept = numbers[0]
                break
        if rcept is None:
            reason = f"no statements for the annual report {annual[1]}, consolidated or separate"
        else:
            body, status, _ = fetcher.xbrl(rcept, annual[2])
            if body is None:
                reason = f"no XBRL file for receipt {rcept} (status {status})"
            else:
                try:
                    instance_from_zip(body)
                except (zipfile.BadZipFile, ValueError, OSError) as e:
                    reason = f"the XBRL zip of receipt {rcept} holds no single instance document ({e})"
        entry = {"position": pos, "corp_code": corp, "corp_name": r.get("corp_name", ""),
                 "stock_code": r.get("stock_code", "")}
        if reason:
            passed.append(dict(entry, reason=reason))
        else:
            kept.append(entry)
        if say:
            say(f"draw {pos}: {corp} {'kept' if not reason else 'passed over: ' + reason}")
    return {"candidates": len(order), "wanted": reg["draw"], "kept": kept, "passed_over": passed,
            "complete": len(kept) >= reg["draw"]}


def fetch_company(fetcher, corp, say):
    """Put into the cache what a build reads for one company. Returns the number of sets DART has data for."""
    found = 0
    for report, year, reprt in REPORTS:
        numbers = OrderedDict()
        for fs in FS_DIVS:
            data, fetched = fetcher.statements(corp, year, reprt, fs)
            status = data.get("status")
            got = receipt_numbers(data) if status == "000" else []
            for n in got:
                numbers[n] = True
            found += 1 if got else 0
            say(f"  {'fetched' if fetched else 'cached already'}  statements {corp} {year} {report} {fs}: "
                + (f"receipt {', '.join(got)}" if got else f"no data (status {status})"))
        for n in numbers:
            body, status, fetched = fetcher.xbrl(n, reprt)
            say(f"  {'fetched' if fetched else 'cached already'}  XBRL file {n} ({report}): "
                + (f"{len(body):,} bytes" if body is not None else f"no file (status {status})"))
    rows, info, made = fetcher.filing_list(corp)
    say(f"  {'fetched' if made else 'cached already'}  filing list {corp}: {len(rows)} periodic reports "
        f"from {LIST_FROM} to {info.get('list_to', '?')}")
    return found


def fetch_only(reg, held_out, only, fetcher, say):
    companies = choose_companies(reg, held_out, only)
    rows, fetched = fetcher.corp_codes(heldout=held_out)
    say(f"{'fetched' if fetched else 'cached already'}  company code file: {len(rows):,} companies")
    if held_out and reg["draw"]:
        draw = walk_draw(fetcher, reg, rows, say)
        companies += [(k["corp_code"], k["corp_name"]) for k in draw["kept"]]
        say(f"draw: {len(draw['kept'])} kept, {len(draw['passed_over'])} passed over, "
            f"{draw['candidates']:,} candidates")
        if not draw["complete"]:
            say("draw: fewer companies kept than the plan asks for")
    for corp, name in companies:
        say(f"{corp} {name}")
        fetch_company(fetcher, corp, say)
    say(f"requests made: {fetcher.requests} ("
        + ", ".join(f"{k} {v}" for k, v in sorted(fetcher.by_endpoint.items())) + ")")
    return 0


# ---------------------------------------------------------------- writing a value the Korean way

def unit_word(unit):
    """The word after the number. Won for KRW and for won per share; other units by their own name."""
    if unit in ("KRW", "KRW/shares"):
        return "원"
    if unit == "shares":
        return "주"
    if unit in ("pure", "", None):
        return ""
    return " " + str(unit).split("/")[0]


def write_value(value, want, unit):
    """Write a value in the way `want` (one of WAYS). A way that does not apply falls back to whole won.

    won      107,440,482,000,000원        every digit
    million  107,440,482백만원            only when that is exact
    eok      1,074,405억원                rounded to whole 억, only from 1억 up
    jo_eok   107조 4,405억원              rounded to whole 억, only from 1조 up
    Returns the text with its unit word, the number alone (sign, digits, scale), the value as written, the value of
    one unit in the last written digit, and the way used.
    """
    with localcontext() as ctx:
        ctx.prec = 80
        neg = value < 0
        mag = -value if neg else value
        way = want
        if way == "million" and (mag == 0 or mag % MILLION != 0):
            way = "won"
        if way == "eok" and mag < EOK:
            way = "won"
        if way == "jo_eok" and mag < JO:
            way = "won"
        if way == "won":
            whole, _, frac = format(mag, "f").partition(".")
            frac = frac.rstrip("0")
            digits = f"{int(whole):,}" + ("." + frac if frac else "")
            quantum = Decimal(1).scaleb(-len(frac)) if frac else Decimal(1)
            written = mag
        elif way == "million":
            digits, quantum, written = f"{int(mag / MILLION):,}백만", MILLION, mag
        else:
            eoks = int((mag / EOK).quantize(Decimal(1), rounding=ROUND_HALF_UP))
            written = eoks * EOK
            if way == "eok":
                digits, quantum = f"{eoks:,}억", EOK
            else:
                jo, rest = divmod(eoks, 10000)
                digits = f"{jo:,}조" + (f" {rest:,}억" if rest else "")
                quantum = EOK if rest else JO
        number = ("-" if neg else "") + digits
        return {"text": number + unit_word(unit), "number": number, "written": -written if neg else written,
                "quantum": quantum, "way": way}


def matches(written, quantum, value):
    """The contract's default tolerance (section 4): the written value equals the value rounded at the last
    written digit, half-up or half-even, or cut off toward zero."""
    with localcontext() as ctx:
        ctx.prec = 80
        x = Decimal(value) / quantum
        return any(x.quantize(Decimal(1), rounding=mode) * quantum == written
                   for mode in (ROUND_HALF_UP, ROUND_HALF_EVEN, ROUND_DOWN))


# ---------------------------------------------------------------- rows, facts, cases

PLAN_FILES = ("by-id.json", "by-name.json", "pinned.json", "swap.json") \
    + tuple(f"abstain-{k}.json" for k in ABSTAIN_KINDS) + ("amended.json",)
SET_FILES = PLAN_FILES + ("document.md", "swap.md", "gold.json")
MARK_RE = re.compile(r"^(?:\s*\[[^\]]*\])+")


def read_rows(data):
    """All the builder takes from a statements answer: statement, account id and account name of each row.
    No amount is read."""
    return [(str(r.get("sj_div") or ""), str(r.get("account_id") or ""), str(r.get("account_nm") or ""))
            for r in data.get("list") or [] if isinstance(r, dict)]


def eligible_rows(rows):
    """Rows of the four statements with a standard account id that occurs once in its statement."""
    ids = {s: Counter() for s in STATEMENTS}
    names = {s: Counter() for s in STATEMENTS}
    for st, aid, nm in rows:
        if st in ids:
            ids[st][aid] += 1
            names[st][nm.strip()] += 1
    out, left = [], Counter()
    for st, aid, nm in rows:
        if st == "SCE":
            left["statement_of_changes_in_equity"] += 1
        elif st not in ids:
            left["other_statement"] += 1
        elif aid == NO_STANDARD_ID:
            left["no_standard_account_id"] += 1
        elif not aid.startswith(ID_PREFIXES):
            left["other_id_prefix"] += 1
        elif ids[st][aid] != 1:
            left["account_id_repeats"] += 1
        else:
            out.append({"statement": st, "account": aid, "name": nm,
                        "name_once": bool(nm.strip()) and names[st][nm.strip()] == 1})
    return out, left, ids


def report_name(text):
    """A report name without its leading marks in square brackets, such as [기재정정]."""
    return re.sub(r"\s+", " ", MARK_RE.sub("", str(text or ""))).strip()


def amended_info(list_rows, rcept_no):
    """Whether the filing list shows an earlier filing with the same report name. Returns a dict."""
    mine = [r for r in list_rows if str(r.get("rcept_no")) == rcept_no]
    if not mine:
        return {"in_filing_list": False, "amended": False}
    name = report_name(mine[0].get("report_nm"))
    me = (str(mine[0].get("rcept_dt") or ""), rcept_no)
    earlier = sorted((str(r.get("rcept_dt") or ""), str(r.get("rcept_no")), str(r.get("report_nm") or ""))
                     for r in list_rows
                     if str(r.get("rcept_no")) != rcept_no and report_name(r.get("report_nm")) == name
                     and re.fullmatch(r"\d{14}", str(r.get("rcept_no") or ""))
                     and (str(r.get("rcept_dt") or ""), str(r.get("rcept_no"))) < me)
    out = {"in_filing_list": True, "report_name": str(mine[0].get("report_nm") or ""), "amended": bool(earlier)}
    if earlier:
        out["earliest_rcept_no"] = earlier[0][1]
        out["earlier"] = [{"rcept_dt": d, "rcept_no": n, "report_nm": nm} for d, n, nm in earlier]
    return out


def neighbours(report, statement, column):
    """Swap kinds (a) and (b) of the plan: (kind, the column whose value is shown instead)."""
    if report != "annual" and statement in ("IS", "CIS"):
        a = {"thstrm_amount": "thstrm_add_amount", "thstrm_add_amount": "thstrm_amount",
             "frmtrm_q_amount": "frmtrm_add_amount", "frmtrm_add_amount": "frmtrm_q_amount"}
        b = {"thstrm_amount": "frmtrm_q_amount", "frmtrm_q_amount": "thstrm_amount",
             "thstrm_add_amount": "frmtrm_add_amount", "frmtrm_add_amount": "thstrm_add_amount"}
        return [("a", a[column]), ("b", b[column])] if column in a else []
    b = {"thstrm_amount": "frmtrm_amount", "frmtrm_amount": "thstrm_amount"}
    return [("b", b[column])] if column in b else []


def _label(text):
    return re.sub(r"\s+", " ", str(text).replace("`", "'")).strip()


def _line(label, text):
    """One document line. Everything but the number sits in inline code, which `scan` skips."""
    quote = f"값 {text}"
    line = f"- `{_label(label)}` {quote}"
    if line.count("`") != 2 or line.count(quote) != 1:
        raise ValueError("a document line would not be read the way it is meant")
    return line, quote


def _claim(cid, line_no, quote, number, locator):
    if number not in quote:
        raise ValueError("the number is not in its quote")
    return {"id": cid, "line": line_no, "quote": quote, "number": number, "kind": "lookup",
            "operands": {"of": locator}}


class Builder:
    def __init__(self, fetcher):
        self.fetcher = fetcher
        self.instances = {}

    def instance(self, rcept, reprt):
        key = (rcept, reprt)
        if key not in self.instances:
            body, status, _ = self.fetcher.xbrl(rcept, reprt)
            if body is None:
                self.instances[key] = (None, "no_xbrl_file", None)
            else:
                try:
                    raw, name = instance_from_zip(body)
                    self.instances[key] = (Instance(raw), None, name)
                except (zipfile.BadZipFile, ET.ParseError, ValueError, OSError, KeyError) as e:
                    self.instances[key] = (None, "xbrl_not_readable", f"{type(e).__name__}: {e}")
        return self.instances[key]

    def load_set(self, corp, report, year, reprt, fs):
        """Rows, periods and facts with a gold of one set. A set DART has no data for comes back as missing."""
        data, _ = self.fetcher.statements(corp, year, reprt, fs)
        rel = f"fnlttSinglAcntAll/{corp}_{year}_{reprt}_{fs}.json"
        if data.get("status") != "000":
            return {"missing": f"DART has no data (status {data.get('status')})"}
        numbers = receipt_numbers(data)
        if len(numbers) != 1 or not re.fullmatch(r"\d{14}", numbers[0]):
            return {"missing": f"the answer names {len(numbers)} receipt numbers"}
        rcept = numbers[0]
        rows = read_rows(data)
        elig, left, ids = eligible_rows(rows)
        inst, why, detail = self.instance(rcept, reprt)
        periods, notes = set_periods(inst, report, fs) if inst is not None else ({}, [str(detail or why)])
        facts, no_gold, possible = [], Counter(), 0
        for row in elig:
            for column, pk in columns_for(report, row["statement"]):
                possible += 1
                if inst is None:
                    no_gold[why] += 1
                    continue
                value, unit, reason = gold_of(inst, row["account"], fs, periods.get(pk))
                if reason:
                    no_gold[reason] += 1
                    continue
                facts.append(dict(row, column=column, period_key=pk, period=list(periods[pk]), value=value,
                                  unit=unit))
        return {"corp": corp, "report": report, "year": year, "reprt": reprt, "fs": fs, "rcept_no": rcept,
                "rows": len(rows), "rows_by_statement": dict(Counter(st for st, _, _ in rows)),
                "eligible_rows": len(elig), "left_out": dict(left), "ids": ids, "facts": facts,
                "possible": possible, "no_gold": dict(no_gold),
                "periods": {k: list(v) for k, v in periods.items()}, "period_notes": notes,
                "statements_file": rel, "statements_fetched": self.fetcher.fetched_date(rel),
                "xbrl_file": f"fnlttXbrl/{rcept}_{reprt}.zip" if inst is not None else None,
                "xbrl_fetched": self.fetcher.fetched_date(f"fnlttXbrl/{rcept}_{reprt}.zip"),
                "instance": detail if inst is not None else None}


def number_facts(s):
    """Give each fact its id, its line and its written form: the four ways are taken in turn."""
    for i, f in enumerate(s["facts"]):
        w = write_value(f["value"], WAYS[i % len(WAYS)], f["unit"])
        label = f"{f['statement']} · {f['account']} · {f['name']} · {f['column']}"
        line, quote = _line(label, w["text"])
        f.update(id=f"f{i + 1:04d}", line=i + 1, way=w["way"], text=w["text"], number=w["number"],
                 doc_line=line, quote=quote)


def build_swaps(s, other):
    """Swap cases: the locator names the fact, the line shows a neighbour's gold."""
    index = {(f["statement"], f["account"], f["column"]): f for f in s["facts"]}
    oindex = {(f["statement"], f["account"], f["column"]): f for f in other["facts"]} if other else {}
    swaps, skipped = [], Counter()
    for f in s["facts"]:
        cands = [(kind, index.get((f["statement"], f["account"], col)), col)
                 for kind, col in neighbours(s["report"], f["statement"], f["column"])]
        if other:
            cands.append(("c", oindex.get((f["statement"], f["account"], f["column"])),
                          f"{other['fs']} {f['column']}"))
        for kind, n, shown in cands:
            if n is None:
                continue
            w = write_value(n["value"], WAYS[len(swaps) % len(WAYS)], n["unit"])
            if matches(w["written"], w["quantum"], f["value"]):
                skipped[kind] += 1          # the neighbour as written would pass for the fact itself
                continue
            label = f"{f['statement']} · {f['account']} · {f['name']} · {f['column']} · shown: {shown}"
            line, quote = _line(label, w["text"])
            swaps.append({"id": f"s{len(swaps) + 1:04d}", "line": len(swaps) + 1, "fact": f["id"], "kind": kind,
                          "shown": shown, "shown_value": str(n["value"]), "way": w["way"], "text": w["text"],
                          "number": w["number"], "doc_line": line, "quote": quote})
    return swaps, skipped


def build_cases(s, other, company_ref, amended):
    """The documents, the plans and the gold of one set. Returns {file name: text or data}."""
    number_facts(s)
    facts = s["facts"]

    def locator(f, **changes):
        loc = OrderedDict([("dart", company_ref), ("year", s["year"]), ("report", s["report"]), ("fs", s["fs"]),
                           ("statement", f["statement"]), ("account", f["account"]), ("column", f["column"])])
        for k, v in changes.items():
            if v is None:
                loc.pop(k, None)
            else:
                loc[k] = v
        return dict(loc)

    def plan(document, claims):
        return {"document": document, "claims": claims}

    def fact_claim(f, **changes):
        return _claim(f["id"], f["line"], f["quote"], f["number"], locator(f, **changes))

    files = {"document.md": "\n".join(f["doc_line"] for f in facts) + "\n"}
    files["by-id.json"] = plan("document.md", [fact_claim(f) for f in facts])
    named = [f for f in facts if f["name_once"]]
    if named:
        files["by-name.json"] = plan("document.md", [fact_claim(f, account=None, name=f["name"]) for f in named])
    files["pinned.json"] = plan("document.md", [fact_claim(f, rcept_no=s["rcept_no"]) for f in facts])

    swaps, skipped = build_swaps(s, other)
    if swaps:
        files["swap.md"] = "\n".join(x["doc_line"] for x in swaps) + "\n"
        by_id = {f["id"]: f for f in facts}
        files["swap.json"] = plan("swap.md", [_claim(x["id"], x["line"], x["quote"], x["number"],
                                                     locator(by_id[x["fact"]])) for x in swaps])

    first = facts[:ABSTAIN_EACH]
    last = s["rcept_no"][-1]
    wrong_receipt = s["rcept_no"][:-1] + str((int(last) + 1) % 10)
    absent_column = "frmtrm_q_amount" if s["report"] == "annual" else "bfefrmtrm_amount"
    abstain, abstain_skipped = {}, Counter()
    cash_flow = [f for f in facts if f["statement"] == "CF"][:ABSTAIN_EACH] if s["report"] != "annual" else []
    for kind in ABSTAIN_KINDS:
        claims, changed = [], []
        for f in (cash_flow if kind == "cfq" else first):
            if kind == "cfq":
                ch = {"column": UNUSABLE_CF_COLUMN}     # an interim cash flow fact asked under the unusable column
            elif kind == "account":
                ch = {"account": f["account"] + "Zz"}
            elif kind == "column":
                ch = {"column": absent_column}
            elif kind == "year":
                ch = {"year": 2031}
            elif kind == "fs":
                ch = {"fs": "XXX"}
            elif kind == "sce":
                ch = {"statement": "SCE"}
            elif kind == "receipt":
                ch = {"rcept_no": wrong_receipt}
            else:
                others = [st for st in STATEMENTS if st != f["statement"] and s["ids"][st][f["account"]] == 0]
                if not others:
                    abstain_skipped[kind] += 1
                    continue
                ch = {"statement": others[0]}
            claims.append(fact_claim(f, **ch))
            changed.append({"fact": f["id"], "changed": ch})
        if claims:
            files[f"abstain-{kind}.json"] = plan("document.md", claims)
        abstain[kind] = changed

    amended_facts = []
    if amended.get("amended"):
        amended_facts = [f["id"] for f in facts[:AMENDED_EACH]]
        files["amended.json"] = plan("document.md", [fact_claim(f, rcept_no=amended["earliest_rcept_no"])
                                                     for f in facts[:AMENDED_EACH]])

    counts = {"rows": s["rows"], "eligible_rows": s["eligible_rows"], "facts_possible": s["possible"],
              "facts": len(facts), "by_id": len(facts), "by_name": len(named), "pinned": len(facts),
              "swap": len(swaps), "swap_by_kind": dict(Counter(x["kind"] for x in swaps)),
              "swap_not_built_same_as_written": dict(skipped),
              "abstain": sum(len(v) for v in abstain.values()),
              "abstain_by_kind": {k: len(v) for k, v in abstain.items()},
              "abstain_not_built": dict(abstain_skipped), "amended": len(amended_facts),
              "ways": dict(Counter(f["way"] for f in facts))}
    files["gold.json"] = {
        "set": {k: s[k] for k in ("corp", "report", "year", "reprt", "fs", "rcept_no")},
        "company_in_plans": company_ref, "periods": s["periods"], "period_notes": s["period_notes"],
        "counts": counts, "no_gold": s["no_gold"], "rows_left_out": s["left_out"],
        "rows_by_statement": s["rows_by_statement"],
        "facts": [{"id": f["id"], "line": f["line"], "statement": f["statement"], "account": f["account"],
                   "name": f["name"], "by_name": f["name_once"], "column": f["column"], "period": f["period"],
                   "value": str(f["value"]), "unit": f["unit"], "way": f["way"], "written": f["text"]}
                  for f in facts],
        "swap": [{k: x[k] for k in ("id", "line", "fact", "kind", "shown", "shown_value", "way", "text")}
                 for x in swaps],
        "abstain": abstain,
        "amended": {"facts": amended_facts, "earliest_rcept_no": amended.get("earliest_rcept_no"),
                    "served_rcept_no": s["rcept_no"]} if amended_facts else None,
    }
    return files, counts


def write_set(folder, files):
    os.makedirs(folder, exist_ok=True)
    for name in SET_FILES:
        path = os.path.join(folder, name)
        if name not in files and os.path.isfile(path):
            os.remove(path)                 # a file of an earlier build that this build does not write
    for name in os.listdir(folder):
        if name.startswith("receipts-") and name.endswith(".json"):
            os.remove(os.path.join(folder, name))   # the tool's answers to an earlier build
    for name, content in files.items():
        path = os.path.join(folder, name)
        if isinstance(content, str):
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
        else:
            write_rows_json(path, content, ("claims", "facts", "swap"))


def _add(total, part):
    for k, v in part.items():
        if isinstance(v, dict):
            _add(total.setdefault(k, {}), v)
        elif isinstance(v, (int, float)):
            total[k] = total.get(k, 0) + v


def build(reg, held_out, only, fetcher, out_root, set_name=None):
    """Build the cases of a company set from the cache. No request is made."""
    if not fetcher.offline:
        raise Stop("the build runs offline")
    companies = choose_companies(reg, held_out, only)
    corp_rows, _ = fetcher.corp_codes(heldout=held_out)
    draw = None
    if held_out and reg["draw"]:
        draw = walk_draw(fetcher, reg, corp_rows)
        companies += [(k["corp_code"], k["corp_name"]) for k in draw["kept"]]
    name = set_name or ("heldout" if held_out else "dev")
    root = os.path.join(out_root, name)
    stock_of = {r["corp_code"]: r.get("stock_code", "").strip() for r in corp_rows}
    stock_count = Counter(r.get("stock_code", "").strip().upper() for r in corp_rows
                          if r.get("stock_code", "").strip())
    b = Builder(fetcher)
    sets, missing, empty, failed, company_rows = [], [], [], [], []
    totals, per_company = {}, OrderedDict()
    for corp, cname in companies:
        stock = stock_of.get(corp, "")
        stock_ok = bool(stock) and stock_count[stock.upper()] == 1
        list_rows, list_info, _ = fetcher.filing_list(corp)
        pc = per_company.setdefault(corp, {"name": cname, "sets_found": 0, "sets_missing": 0, "counts": {},
                                           "no_gold": {}, "rows_left_out": {}})
        company_rows.append({"corp_code": corp, "name": cname, "stock_code": stock or None,
                             "stock_code_names_one_company": stock_ok,
                             "filing_list_fetched": list_info.get("date"), "filing_list_to": list_info.get("list_to"),
                             "periodic_reports_listed": len(list_rows)})
        for report, year, reprt in REPORTS:
            pair = {}
            for fs in FS_DIVS:
                tag = {"corp_code": corp, "company": cname, "report": report, "year": year, "fs": fs}
                try:
                    s = b.load_set(corp, report, year, reprt, fs)
                except Stop:
                    raise
                except Exception as e:
                    failed.append(dict(tag, reason=f"{type(e).__name__}: {scrub(e)}"))
                    continue
                if "missing" in s:
                    missing.append(dict(tag, reason=s["missing"]))
                    pc["sets_missing"] += 1
                    continue
                pair[fs] = s
            for fs in FS_DIVS:
                if fs not in pair:
                    continue
                s = pair[fs]
                other = pair.get("OFS" if fs == "CFS" else "CFS")
                tag = {"corp_code": corp, "company": cname, "report": report, "year": year, "fs": fs,
                       "rcept_no": s["rcept_no"]}
                pc["sets_found"] += 1
                _add(pc["no_gold"], s["no_gold"])
                _add(pc["rows_left_out"], s["left_out"])
                _add(totals.setdefault("no_gold", {}), s["no_gold"])
                _add(totals.setdefault("rows_left_out", {}), s["left_out"])
                if not s["facts"]:
                    empty.append(dict(tag, reason="no fact with a gold", no_gold=s["no_gold"],
                                      period_notes=s["period_notes"]))
                    _add(pc["counts"], {"facts_possible": s["possible"], "rows": s["rows"],
                                        "eligible_rows": s["eligible_rows"]})
                    continue
                company_ref = stock if (report == "annual" and stock_ok) else corp
                amended = amended_info(list_rows, s["rcept_no"])
                folder = f"{corp}-{report}-{fs}"
                try:
                    files, counts = build_cases(s, other if other and other["facts"] else None, company_ref,
                                                amended)
                    write_set(os.path.join(root, "sets", folder), files)
                except Stop:
                    raise
                except Exception as e:
                    failed.append(dict(tag, reason=f"{type(e).__name__}: {scrub(e)}"))
                    continue
                _add(pc["counts"], counts)
                _add(totals.setdefault("counts", {}), counts)
                sets.append(dict(tag, folder=folder, reprt_code=reprt, company_in_plans=company_ref,
                                 plans=[n for n in PLAN_FILES if n in files], counts=counts,
                                 no_gold=s["no_gold"], rows_left_out=s["left_out"], amended=amended,
                                 periods=s["periods"], period_notes=s["period_notes"],
                                 statements_file=s["statements_file"], statements_fetched=s["statements_fetched"],
                                 xbrl_file=s["xbrl_file"], xbrl_fetched=s["xbrl_fetched"], instance=s["instance"]))
    manifest = {"builder_version": BUILDER_VERSION, "set": name, "held_out": bool(held_out),
                "made_up": bool(reg.get("made_up")),
                "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "fetch_dates": sorted({d for s in sets for d in (s["statements_fetched"], s["xbrl_fetched"]) if d}),
                "company_code_file_fetched": fetcher.fetched_date("corpCode-heldout.zip" if held_out
                                                                  else "corpCode.zip"),
                "companies": company_rows, "sets": sets, "missing_sets": missing, "sets_without_gold": empty,
                "failed_sets": failed, "totals": totals, "per_company": per_company, "draw": draw}
    write_json(os.path.join(root, "manifest.json"), manifest)
    print_counts(manifest)
    print(f"written: {os.path.join(root, 'manifest.json')}")
    return 1 if failed else 0


def print_counts(m):
    """The counts the plan asks the builder to print."""
    c = m["totals"].get("counts", {})
    print(f"set {m['set']}: {len(m['companies'])} companies, {len(m['sets'])} sets with cases, "
          f"{len(m['missing_sets'])} sets DART has no data for, {len(m['sets_without_gold'])} sets without a gold, "
          f"{len(m['failed_sets'])} sets that could not be built")
    for x in m["missing_sets"]:
        print(f"  missing: {x['corp_code']} {x['company']} {x['report']} {x['year']} {x['fs']}: {x['reason']}")
    for x in m["sets_without_gold"]:
        print(f"  no gold: {x['corp_code']} {x['company']} {x['report']} {x['year']} {x['fs']}: {x['no_gold']}")
    for x in m["failed_sets"]:
        print(f"  not built: {x['corp_code']} {x['company']} {x['report']} {x['year']} {x['fs']}: {x['reason']}")
    if m.get("draw"):
        d = m["draw"]
        print(f"draw: {len(d['kept'])} kept of {d['wanted']} wanted, {len(d['passed_over'])} passed over, "
              f"{d['candidates']:,} candidates")
        for x in d["passed_over"]:
            print(f"  passed over: {x['corp_code']} {x['corp_name']}: {x['reason']}")
    no_gold = m["totals"].get("no_gold", {})
    left = m["totals"].get("rows_left_out", {})
    print(f"rows {c.get('rows', 0):,}; eligible rows {c.get('eligible_rows', 0):,}; rows left out: "
          + (", ".join(f"{k} {v:,}" for k, v in sorted(left.items())) or "none"))
    print(f"facts possible {c.get('facts_possible', 0):,}; facts with a gold {c.get('facts', 0):,}; without a gold "
          f"{sum(no_gold.values()):,}: " + (", ".join(f"{k} {v:,}" for k, v in sorted(no_gold.items())) or "none"))
    print("cases: by id {by_id:,}; by name {by_name:,}; pinned {pinned:,}; swap {swap:,} ({kinds}); "
          "abstention {abstain:,} ({akinds}); amended {amended:,}".format(
              by_id=c.get("by_id", 0), by_name=c.get("by_name", 0), pinned=c.get("pinned", 0), swap=c.get("swap", 0),
              kinds=", ".join(f"{k} {v:,}" for k, v in sorted(c.get("swap_by_kind", {}).items())) or "none",
              abstain=c.get("abstain", 0),
              akinds=", ".join(f"{k} {v:,}" for k, v in c.get("abstain_by_kind", {}).items()) or "none",
              amended=c.get("amended", 0)))
    print("swap cases not built because the neighbour as written passes for the fact: "
          + (", ".join(f"{k} {v:,}" for k, v in sorted(c.get("swap_not_built_same_as_written", {}).items()))
             or "none"))
    print("ways of writing: " + (", ".join(f"{k} {v:,}" for k, v in sorted(c.get("ways", {}).items())) or "none"))
    print("per company (sets found/missing; facts with a gold; without a gold by reason; cases):")
    for corp, pc in m["per_company"].items():
        cc = pc["counts"]
        print(f"  {corp} {pc['name']}: sets {pc['sets_found']}/{pc['sets_missing']}; gold {cc.get('facts', 0):,}; "
              f"no gold {sum(pc['no_gold'].values()):,} "
              f"({', '.join(f'{k} {v}' for k, v in sorted(pc['no_gold'].items())) or 'none'}); "
              f"by name {cc.get('by_name', 0):,}; swap {cc.get('swap', 0):,}; abstention {cc.get('abstain', 0):,}; "
              f"amended {cc.get('amended', 0):,}")


def self_test():
    sys.path.insert(0, os.path.join(HERE, "testdata"))
    import selftest_build
    return selftest_build.run(sys.modules[__name__])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fetch-only", action="store_true", help="fetch into the cache and stop (needs the key)")
    ap.add_argument("--offline", action="store_true", help="build the cases from the cache, no request")
    ap.add_argument("--held-out", action="store_true", help="the held-out companies and the draw (release run only)")
    ap.add_argument("--self-test", action="store_true", help="offline checks on made-up data")
    ap.add_argument("--companies", help="comma-separated company codes of the development list")
    ap.add_argument("--cache", default=os.path.join(HERE, "cache"))
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--set-name", help=argparse.SUPPRESS)       # self-test: another folder name under --out
    ap.add_argument("--registry", help=argparse.SUPPRESS)       # self-test: made-up company lists
    ap.add_argument("--api-base", help=argparse.SUPPRESS)       # self-test: a local stand-in for DART
    a = ap.parse_args(argv)
    try:
        if a.self_test:
            if a.fetch_only or a.offline or a.held_out:
                raise Stop("--self-test stands alone.")
            return self_test()
        if a.fetch_only == a.offline:
            raise Stop("choose one: --fetch-only (needs the key) or --offline (builds from the cache).")
        if a.api_base and not a.registry:
            raise Stop("--api-base is for the self-test and needs --registry.")
        reg = registry(a.registry)
        only = [c.strip() for c in a.companies.split(",") if c.strip()] if a.companies else None
        fetcher = Fetcher(a.cache, offline=a.offline, base=a.api_base or API_BASE)
        if a.fetch_only:
            return fetch_only(reg, a.held_out, only, fetcher, lambda m: print(scrub(m), flush=True))
        return build(reg, a.held_out, only, fetcher, a.out, a.set_name)
    except Stop as e:
        print("stopped: " + scrub(e), file=sys.stderr)
        return 2
    except Exception as e:      # no traceback: an error text of a library may hold the address of a request
        print(f"stopped: unexpected {type(e).__name__}: {scrub(e)}", file=sys.stderr)
        if os.environ.get("RECEIPTS_BENCH_DEBUG") == "1":
            import traceback
            print(scrub(traceback.format_exc()), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
