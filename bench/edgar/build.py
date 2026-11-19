#!/usr/bin/env python3
"""E3 builder for receipts (see docs/eval-plan.md, section E3).

Usage:
  python bench/edgar/build.py                      # development companies -> bench/edgar/out/dev/
  python bench/edgar/build.py --held-out           # held-out companies -> bench/edgar/out/heldout/ (run once)
  python bench/edgar/build.py --offline            # build only from files already in the cache, no request
  python bench/edgar/build.py --self-test          # offline checks on bench/edgar/testdata/

For each company it reads the SEC's ticker list, the company's filing list and the primary inline-XBRL document of
the most recent 10-K and 10-Q. The gold comes from those documents only: the builder never reads companyfacts.
Requests send the User-Agent from RECEIPTS_SEC_USER_AGENT (no default), at most 5 per second, and every response is
cached under bench/edgar/cache/ and never fetched again. Exit 0 on success, 2 when it has to stop.
"""
from __future__ import annotations

import argparse
import gzip
import html
import json
import os
import random
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BUILDER_VERSION = "e3-builder-1"

DEV_TICKERS = ["AAPL", "MSFT", "JPM", "WMT", "XOM"]
HELD_OUT_TICKERS = ["JNJ", "KO", "NVDA", "COST", "PG", "HD", "BAC", "UNH", "PFE", "CVX", "CSCO", "ORCL", "NKE",
                    "DIS", "MCD", "CAT", "T", "AMZN", "GS", "BA"]

UA_ENV = "RECEIPTS_SEC_USER_AGENT"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

FORMS = ("10-K", "10-Q")
SAMPLE_FACTS, SAMPLE_PAIRS, ABSTAIN_EACH = 25, 5, 5
SCALE_WORDS = {0: "", 3: "thousand", 6: "million", 9: "billion"}
UNITS_OK = ("USD", "shares", "USD/shares")
PLAIN_FORMATS = {"", "num-dot-decimal", "numdotdecimal"}
DASHES = set("-‐‑‒–—―−﹘﹣－")
PLAIN_RE = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PLAN_FILES = ("pinned.json", "open.json", "abstain-concept.json", "abstain-period.json", "abstain-instant.json",
              "abstain-unit.json")


class Stop(Exception):
    """The run has to stop with exit code 2."""


# ---------------------------------------------------------------- fetching

class Fetcher:
    """Cached, paced HTTP GET. A cached URL is never requested again."""

    MIN_GAP, TIMEOUT, RETRY_WAIT = 0.25, 30, 2.0

    def __init__(self, cache, offline=False, opener=None, sleep=None, clock=None):
        self.cache, self.offline = cache, offline
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        self.last = None
        self.requests = 0

    def path(self, url):
        p = urllib.parse.urlsplit(url)
        parts = [p.netloc] + [x for x in p.path.split("/") if x]
        for x in parts:
            if x in (".", "..") or not re.fullmatch(r"[A-Za-z0-9._-]+", x):
                raise Stop(f"unexpected URL, not cached: {url}")
        return os.path.join(self.cache, *parts)

    def fetched_date(self, url):
        path = self.path(url)
        if not os.path.isfile(path):
            return None
        return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).strftime("%Y-%m-%d")

    def get(self, url):
        path = self.path(url)
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                return fh.read()
        if self.offline:
            raise Stop(f"offline mode: {url} is not in the cache ({self.cache})")
        ua = os.environ.get(UA_ENV, "").strip()
        if not ua:
            raise Stop(f"{UA_ENV} is empty. Set it to the contact string the SEC asks automated clients for. "
                       "No request was made.")
        body = self._request(url, ua)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(body)
        os.replace(tmp, path)
        return body

    def _pace(self):
        if self.last is not None:
            gap = self.clock() - self.last
            if gap < self.MIN_GAP:
                self.sleep(self.MIN_GAP - gap)
        self.last = self.clock()
        self.requests += 1

    def _request(self, url, ua):
        problem = None
        for attempt in (1, 2):
            self._pace()
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip"})
            try:
                with self.opener(req, timeout=self.TIMEOUT) as r:
                    body = r.read()
                    enc = (r.headers.get("Content-Encoding") or "").lower() if r.headers is not None else ""
                if "gzip" in enc or body[:2] == b"\x1f\x8b":
                    body = gzip.decompress(body)
                return body
            except urllib.error.HTTPError as e:
                if e.code == 429 or 500 <= e.code < 600:
                    problem = f"HTTP {e.code}"
                else:
                    raise Stop(f"HTTP {e.code} for {url}")
            except urllib.error.URLError as e:
                if isinstance(e.reason, (socket.timeout, TimeoutError)):
                    problem = "timeout"
                else:
                    raise Stop(f"request failed for {url}: {e.reason}")
            except (socket.timeout, TimeoutError):
                problem = "timeout"
            except (OSError, EOFError, ValueError) as e:
                raise Stop(f"request failed for {url}: {type(e).__name__}: {e}")
            if attempt == 1:
                self.sleep(self.RETRY_WAIT)
        raise Stop(f"{problem} for {url}, also after one retry")

    def get_json(self, url):
        try:
            return json.loads(self.get(url).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise Stop(f"not the expected JSON: {url} ({e})")


# ---------------------------------------------------------------- inline XBRL

TAG_RE = re.compile(r"<!--.*?-->|<!\[CDATA\[(.*?)\]\]>|<[!?][^>]*>"
                    r"|<(/?)\s*([A-Za-z_][\w.\-]*(?::[A-Za-z_][\w.\-]*)?)((?:[^>\"']|\"[^\"]*\"|'[^']*')*?)(/?)\s*>",
                    re.S)
ATTR_RE = re.compile(r"([A-Za-z_][\w.\-]*(?::[A-Za-z_][\w.\-]*)?)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")
XMLNS_RE = re.compile(r"xmlns:([\w.\-]+)\s*=\s*[\"']([^\"']+)[\"']", re.I)
_P = r"(?:[\w.\-]+:)?"
_A = r"((?:[^>\"']|\"[^\"]*\"|'[^']*')*)"
CTX_RE = re.compile(r"<" + _P + r"context\b" + _A + r">(.*?)</" + _P + r"context\s*>", re.S | re.I)
UNIT_RE = re.compile(r"<" + _P + r"unit\b" + _A + r">(.*?)</" + _P + r"unit\s*>", re.S | re.I)
DATE_TAG_RE = re.compile(r"<" + _P + r"(startDate|endDate|instant)\s*>\s*([^<]*?)\s*<", re.I)
DIM_RE = re.compile(r"<" + _P + r"(segment|scenario)\b", re.I)
MEASURE_RE = re.compile(r"<" + _P + r"measure\s*>\s*([^<]*?)\s*<", re.I)
NUM_RE = re.compile(r"<" + _P + r"unitNumerator\s*>(.*?)</" + _P + r"unitNumerator\s*>", re.S | re.I)
DEN_RE = re.compile(r"<" + _P + r"unitDenominator\s*>(.*?)</" + _P + r"unitDenominator\s*>", re.S | re.I)
DIVIDE_RE = re.compile(r"<" + _P + r"divide\b", re.I)


def local(name):
    return name.rsplit(":", 1)[-1].lower()


def parse_attrs(s):
    out = {}
    for m in ATTR_RE.finditer(s or ""):
        val = m.group(2) if m.group(2) is not None else m.group(3)
        out[local(m.group(1))] = html.unescape(val)
    return out


def dstr(d):
    if d == 0:
        return "0"
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def parse_contexts(text):
    ctx = {}
    for m in CTX_RE.finditer(text):
        cid = parse_attrs(m.group(1)).get("id")
        if not cid:
            continue
        body = m.group(2)
        dates = {}
        for d in DATE_TAG_RE.finditer(body):
            dates.setdefault(d.group(1).lower(), d.group(2).strip()[:10])
        start, end = dates.get("startdate"), dates.get("enddate")
        if "instant" in dates and DATE_RE.match(dates["instant"]):
            period = (None, dates["instant"])
        elif start and end and DATE_RE.match(start) and DATE_RE.match(end):
            period = (start, end)
        else:
            period = None
        ctx[cid] = {"dimension": bool(DIM_RE.search(body)), "period": period}
    return ctx


def norm_measure(m, xmlns):
    prefix, loc = m.split(":", 1) if ":" in m else ("", m)
    ns = (xmlns.get(prefix.lower()) or "").lower()
    if loc.upper() == "USD" and ("iso4217" in ns or prefix.lower() == "iso4217"):
        return "USD"
    if loc.lower() == "shares" and (ns.rstrip("/").endswith("xbrl.org/2003/instance") or prefix.lower() == "xbrli"):
        return "shares"
    return m


def parse_units(text, xmlns):
    units = {}
    for m in UNIT_RE.finditer(text):
        uid = parse_attrs(m.group(1)).get("id")
        if not uid:
            continue
        body = m.group(2)
        if DIVIDE_RE.search(body):
            num = [norm_measure(x, xmlns) for x in MEASURE_RE.findall((NUM_RE.search(body) or [None, ""])[1] or "")]
            den = [norm_measure(x, xmlns) for x in MEASURE_RE.findall((DEN_RE.search(body) or [None, ""])[1] or "")]
            name = "*".join(num) + "/" + "*".join(den)
        else:
            name = "*".join(norm_measure(x, xmlns) for x in MEASURE_RE.findall(body))
        units[uid] = name
    return units


def scan_nonfraction(text):
    """All ix:nonFraction tags in document order with their attributes and displayed text (tags removed)."""
    tags, stack, pos = [], [], 0

    def add_text(s):
        for r in stack:
            r["parts"].append(s)

    for m in TAG_RE.finditer(text):
        if m.start() > pos and stack:
            add_text(html.unescape(text[pos:m.start()]))
        pos = m.end()
        if m.group(1) is not None:
            add_text(m.group(1))
            continue
        name = m.group(3)
        if not name or local(name) != "nonfraction":
            continue
        if m.group(2):
            if stack:
                stack.pop()
            continue
        rec = {"attrs": parse_attrs(m.group(4)), "parts": [], "order": len(tags)}
        tags.append(rec)
        if not m.group(5):
            stack.append(rec)
    if pos < len(text) and stack:
        add_text(html.unescape(text[pos:]))
    for r in tags:
        r["text"] = "".join(r.pop("parts")).strip()
    return tags


def parse_ixbrl(text):
    """Eligible facts (plan, 'Filings and facts') and skipped tags by reason."""
    xmlns = {}
    for m in XMLNS_RE.finditer(text):
        xmlns.setdefault(m.group(1).lower(), m.group(2))
    contexts = parse_contexts(text)
    units = parse_units(text, xmlns)
    tags = scan_nonfraction(text)
    skipped, skipped_formats, skipped_units = Counter(), Counter(), Counter()
    facts = OrderedDict()
    for t in tags:
        a = t["attrs"]
        if a.get("nil", "").strip().lower() in ("true", "1"):
            skipped["nil"] += 1
            continue
        name = a.get("name", "").strip()
        if ":" not in name:
            skipped["no_concept"] += 1
            continue
        prefix, loc = name.split(":", 1)
        ns = xmlns.get(prefix.lower())
        if not ((ns and "fasb.org/us-gaap" in ns.lower()) or (ns is None and prefix.lower() == "us-gaap")):
            skipped["not_us_gaap"] += 1
            continue
        ctx = contexts.get(a.get("contextref", "").strip())
        if ctx is None:
            skipped["context_missing"] += 1
            continue
        if ctx["dimension"]:
            skipped["dimension"] += 1
            continue
        if ctx["period"] is None:
            skipped["no_period"] += 1
            continue
        unit = units.get(a.get("unitref", "").strip())
        if unit not in UNITS_OK:
            skipped["unit"] += 1
            skipped_units[str(unit)] += 1
            continue
        fmt = a.get("format", "").strip()
        floc = local(fmt) if fmt else ""
        txt = t["text"]
        if floc in PLAIN_FORMATS:
            if not PLAIN_RE.match(txt):
                skipped["not_plain_number"] += 1
                continue
            zero, digits = False, txt.replace(",", "")
        elif floc in ("fixed-zero", "fixedzero") or "zerodash" in floc.replace("-", ""):
            if not txt or any(ch not in DASHES for ch in txt):
                skipped["zero_format_not_dash"] += 1
                continue
            zero, digits = True, "0"
        else:
            skipped["format"] += 1
            skipped_formats[fmt] += 1
            continue
        try:
            scale = int(a.get("scale", "0").strip() or "0")
        except ValueError:
            skipped["bad_scale"] += 1
            continue
        sign = "-" if a.get("sign", "").strip() == "-" else ""
        try:
            value = Decimal(digits).scaleb(scale)
        except InvalidOperation:
            skipped["not_plain_number"] += 1
            continue
        if sign:
            value = -value
        if value == 0:
            value = Decimal(0)
        places = len(digits.split(".", 1)[1]) if "." in digits else 0
        start, end = ctx["period"]
        concept = "us-gaap:" + loc
        period = end if start is None else f"{start}..{end}"
        key = (concept, unit, period)
        disp = {"text": txt, "scale": scale, "sign": sign, "value": value, "exp": scale - places, "zero": zero,
                "format": fmt, "id": a.get("id", ""), "context": a.get("contextref", ""), "order": t["order"]}
        f = facts.get(key)
        if f is None:
            f = facts[key] = {"concept": concept, "unit": unit, "start": start, "end": end, "period": period,
                              "displays": [], "first": t["order"]}
        f["displays"].append(disp)
    out = []
    for f in facts.values():
        f["gold"] = min(f["displays"], key=lambda d: (d["exp"], d["order"]))
        f["displays_differ"] = len({d["value"] for d in f["displays"]}) > 1
        out.append(f)
    return {"numeric_tags": len(tags), "contexts": len(contexts),
            "contexts_with_dimension": sum(1 for c in contexts.values() if c["dimension"]), "units": len(units),
            "facts": out, "skipped": dict(sorted(skipped.items())),
            "skipped_formats": dict(sorted(skipped_formats.items())),
            "skipped_units": dict(sorted(skipped_units.items()))}


# ---------------------------------------------------------------- sample and cases

def same_end_pairs(facts):
    groups = OrderedDict()
    for f in facts:
        if f["start"] is not None:
            groups.setdefault((f["concept"], f["unit"], f["end"]), []).append(f)
    return [(g[0], g[1]) for g in groups.values() if len(g) >= 2]


def draw_sample(facts, accession):
    """25 facts, then up to 5 same-end pairs, with one generator seeded with the accession number."""
    pairs = same_end_pairs(facts)
    rng = random.Random(accession)
    picked = rng.sample(facts, min(SAMPLE_FACTS, len(facts)))
    chosen = rng.sample(pairs, min(SAMPLE_PAIRS, len(pairs)))
    order = list(picked)
    seen = {id(f) for f in order}
    pair_of = {}
    for i, (a, b) in enumerate(chosen, 1):
        for m in (a, b):
            pair_of[id(m)] = f"p{i}"
            if id(m) not in seen:
                order.append(m)
                seen.add(id(m))
    return order, chosen, pair_of, pairs


def number_text(d):
    if d["zero"] or d["value"] == 0:
        return "0"
    s = d["text"]
    if SCALE_WORDS[d["scale"]]:
        s += " " + SCALE_WORDS[d["scale"]]
    return ("-" + s) if d["sign"] else s


def fact_line(f, num):
    if f["start"] is None:
        return f"- `{f['concept']}` at {f['end']}: {num}"
    return f"- `{f['concept']}` from {f['start']} to {f['end']}: {num}"


def shift(day, n=3):
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def build_cases(parsed, accession, company):
    """Document lines, plans and gold for one filing."""
    eligible = parsed["facts"]
    writable = [f for f in eligible if f["gold"]["scale"] in SCALE_WORDS]
    sample, chosen, pair_of, all_pairs = draw_sample(writable, accession)
    lines, gold_facts, claims_pinned, claims_open = [], [], [], []
    for i, f in enumerate(sample, 1):
        g = f["gold"]
        num = number_text(g)
        line = fact_line(f, num)
        lines.append(line)
        fid = f"f{i:02d}"
        f["_id"], f["_line"], f["_num"], f["_quote"] = fid, i, num, line[2:]
        loc = {"edgar": company, "concept": f["concept"], "unit": f["unit"], "period": f["period"]}
        base = {"id": fid, "line": i, "quote": line[2:], "number": num, "kind": "lookup"}
        claims_pinned.append(dict(base, operands={"of": dict(loc, accession=accession)}))
        claims_open.append(dict(base, operands={"of": dict(loc)}))
        gold_facts.append({"id": fid, "line": i, "concept": f["concept"], "unit": f["unit"], "period": f["period"],
                           "start": f["start"], "end": f["end"], "text": g["text"], "written": num,
                           "scale": g["scale"], "sign": g["sign"], "zero": g["zero"], "format": g["format"],
                           "value": dstr(g["value"]), "displays_differ": f["displays_differ"],
                           "displays": [{"text": d["text"], "scale": d["scale"], "sign": d["sign"],
                                         "value": dstr(d["value"]), "format": d["format"], "context": d["context"]}
                                        for d in f["displays"]],
                           "pair": pair_of.get(id(f))})

    def claim(f, **changes):
        loc = {"edgar": company, "concept": f["concept"], "unit": f["unit"], "period": f["period"],
               "accession": accession}
        loc.update(changes)
        return {"id": f["_id"], "line": f["_line"], "quote": f["_quote"], "number": f["_num"], "kind": "lookup",
                "operands": {"of": loc}}

    def moved(f):
        return shift(f["end"]) if f["start"] is None else f"{f['start']}..{shift(f['end'])}"

    durations = [f for f in sample if f["start"] is not None]
    usd_shares = [f for f in sample if f["unit"] in ("USD", "shares")]
    abstain = {
        "abstain-concept.json": [claim(f, concept=f["concept"] + "Zz") for f in sample[:ABSTAIN_EACH]],
        "abstain-period.json": [claim(f, period=moved(f)) for f in sample[:ABSTAIN_EACH]],
        "abstain-instant.json": [claim(f, period=f["end"]) for f in durations[:ABSTAIN_EACH]],
        "abstain-unit.json": [claim(f, unit="shares" if f["unit"] == "USD" else "USD")
                              for f in usd_shares[:ABSTAIN_EACH]],
    }
    plans = {"pinned.json": claims_pinned, "open.json": claims_open}
    plans.update(abstain)
    pairs = [{"id": f"p{i}", "concept": a["concept"], "unit": a["unit"], "end": a["end"],
              "members": [a["_id"], b["_id"]], "periods": [a["period"], b["period"]]}
             for i, (a, b) in enumerate(chosen, 1)]
    counts = {"numeric_tags": parsed["numeric_tags"], "contexts": parsed["contexts"],
              "contexts_with_dimension": parsed["contexts_with_dimension"],
              "eligible_facts": len(eligible),
              "eligible_displays_differ": sum(1 for f in eligible if f["displays_differ"]),
              "excluded_scale": len(eligible) - len(writable), "writable_facts": len(writable),
              "same_end_pairs": len(all_pairs), "sampled_facts": len(sample), "sampled_pairs": len(chosen),
              "sampled_displays_differ": sum(1 for f in sample if f["displays_differ"]),
              "abstain": {k: len(v) for k, v in abstain.items()}}
    excluded = Counter(str(f["gold"]["scale"]) for f in eligible if f["gold"]["scale"] not in SCALE_WORDS)
    gold = {"accession": accession, "company": company, "counts": counts, "skipped": parsed["skipped"],
            "skipped_formats": parsed["skipped_formats"], "skipped_units": parsed["skipped_units"],
            "excluded_scale_by_scale": dict(sorted(excluded.items())), "facts": gold_facts, "pairs": pairs,
            "abstain": {k: [{"id": c["id"], "line": c["line"], "locator": c["operands"]["of"]} for c in v]
                        for k, v in abstain.items()}}
    return lines, plans, gold


def write_json(path, data):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def pick_filing(recent, form):
    """The most recent entry with this exact form that is inline XBRL (amendments have another form)."""
    n = len(recent.get("accessionNumber", []))
    best = None
    for i in range(n):
        try:
            if recent["form"][i] != form or int(recent["isInlineXBRL"][i] or 0) != 1:
                continue
            row = {k: recent[k][i] for k in ("accessionNumber", "form", "filingDate", "reportDate",
                                             "primaryDocument")}
        except (KeyError, IndexError, TypeError, ValueError):
            raise Stop("the filing list does not have the expected parallel arrays")
        if best is None or row["filingDate"] > best["filingDate"]:
            best = row
    return best


def build(tickers, set_name, fetcher, out_root):
    out = os.path.join(out_root, set_name)
    os.makedirs(out, exist_ok=True)
    listing = fetcher.get_json(TICKERS_URL)
    try:
        by_ticker = {str(v["ticker"]).upper(): (int(v["cik_str"]), str(v["title"])) for v in listing.values()}
    except (AttributeError, KeyError, TypeError, ValueError):
        raise Stop("the ticker list is not in the expected shape")
    manifest = {"set": set_name, "builder_version": BUILDER_VERSION,
                "fetch_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "offline": fetcher.offline,
                "ticker_list_fetched": fetcher.fetched_date(TICKERS_URL), "companies": [], "filings": [],
                "missing": []}
    totals = Counter()
    for t in tickers:
        if t not in by_ticker:
            raise Stop(f"ticker {t} is not in the SEC's ticker list")
        cik, title = by_ticker[t]
        sub_url = SUBMISSIONS_URL.format(cik=cik)
        subs = fetcher.get_json(sub_url)
        try:
            recent = subs["filings"]["recent"]
        except (KeyError, TypeError):
            raise Stop(f"the filing list of {t} has no filings.recent")
        comp = {"ticker": t, "cik": cik, "name": subs.get("name") or title, "submissions_fetched":
                fetcher.fetched_date(sub_url), "filings": []}
        manifest["companies"].append(comp)
        for form in FORMS:
            row = pick_filing(recent, form)
            if row is None:
                manifest["missing"].append({"ticker": t, "form": form, "reason": "no inline-XBRL filing in "
                                                                                  "filings.recent"})
                print(f"{t} {form}: no inline-XBRL filing in the recent list, skipped", flush=True)
                continue
            acc = row["accessionNumber"]
            url = ARCHIVE_URL.format(cik=cik, acc=acc.replace("-", ""), doc=row["primaryDocument"])
            text = fetcher.get(url).decode("utf-8", "replace")
            parsed = parse_ixbrl(text)
            company = t if form == "10-K" else str(cik)
            lines, plans, gold = build_cases(parsed, acc, company)
            folder = f"{t}-{form}"
            fdir = os.path.join(out, folder)
            os.makedirs(fdir, exist_ok=True)
            with open(os.path.join(fdir, "document.md"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(lines) + "\n")
            for name, claims in plans.items():
                write_json(os.path.join(fdir, name), {"document": "document.md", "claims": claims})
            gold.update({"ticker": t, "cik": cik, "form": form, "filing_date": row["filingDate"],
                         "report_date": row["reportDate"], "document_url": url})
            write_json(os.path.join(fdir, "gold.json"), gold)
            c = gold["counts"]
            entry = {"ticker": t, "cik": cik, "form": form, "accession": acc, "filing_date": row["filingDate"],
                     "report_date": row["reportDate"], "document_url": url, "folder": folder,
                     "company_in_plans": company, "document_fetched": fetcher.fetched_date(url),
                     "counts": {k: v for k, v in c.items() if k != "abstain"}, "abstain": c["abstain"],
                     "skipped": gold["skipped"]}
            comp["filings"].append({"form": form, "accession": acc, "folder": folder})
            manifest["filings"].append(entry)
            for k in ("numeric_tags", "eligible_facts", "writable_facts", "sampled_facts", "sampled_pairs",
                      "same_end_pairs", "eligible_displays_differ", "sampled_displays_differ", "excluded_scale"):
                totals[k] += c[k]
            for k, v in c["abstain"].items():
                totals["abstain:" + k] += v
            for k, v in gold["skipped"].items():
                totals["skipped:" + k] += v
            print(f"{folder} {acc}: {c['numeric_tags']} numeric tags, {c['eligible_facts']} eligible facts "
                  f"({c['excluded_scale']} with an unwritable scale), {c['same_end_pairs']} same-end pairs; "
                  f"sampled {c['sampled_facts']} facts, {c['sampled_pairs']} pairs; skipped {gold['skipped']}",
                  flush=True)
    totals["filings"] = len(manifest["filings"])
    manifest["counts"] = dict(sorted(totals.items()))
    write_json(os.path.join(out, "manifest.json"), manifest)
    print(f"{set_name}: {totals['filings']} filings, {totals['sampled_facts']} sampled facts, "
          f"{totals['sampled_pairs']} pairs; {fetcher.requests} requests made. Written to {out}", flush=True)
    return manifest


# ---------------------------------------------------------------- self-test

def self_test():
    td = os.path.join(HERE, "testdata")
    with open(os.path.join(td, "synthetic-10q.htm"), encoding="utf-8") as fh:
        p = parse_ixbrl(fh.read())
    facts = {(f["concept"].split(":", 1)[1], f["period"]): f for f in p["facts"]}

    def val(concept, period):
        return facts[(concept, period)]["gold"]["value"]

    q3, ytd, h1 = "2026-03-29..2026-06-27", "2025-09-28..2026-06-27", "2025-12-28..2026-06-27"
    pq3, pytd = "2025-03-30..2025-06-28", "2024-09-29..2025-06-28"
    checks = []

    def ok(cond, what):
        checks.append(what)
        assert cond, what

    ok(p["numeric_tags"] == 40, f"40 numeric tags, got {p['numeric_tags']}")
    ok(p["contexts"] == 10 and p["contexts_with_dimension"] == 2, "10 contexts, 2 with a dimension")
    ok(len(p["facts"]) == 24, f"24 eligible facts, got {len(p['facts'])}")
    ok(p["skipped"] == {"context_missing": 1, "dimension": 2, "format": 2, "nil": 1, "not_plain_number": 1,
                        "not_us_gaap": 2, "unit": 3, "zero_format_not_dash": 1}, f"skips {p['skipped']}")
    ok(p["skipped_formats"] == {"ixt-sec:numwordsen": 1, "ixt:num-comma-decimal": 1}, "skipped formats")
    ok(p["skipped_units"] == {"syn:store": 1, "xbrli:pure": 1, "iso4217:EUR": 1}, f"units {p['skipped_units']}")
    ok(val("RevenueFromContractWithCustomerExcludingAssessedTax", q3) == Decimal("94036000000"), "scale 6")
    ok(facts[("CostOfRevenue", q3)]["gold"]["value"] == Decimal("51000000000"), "upper-case tag, single quotes")
    ok(val("OtherNonoperatingIncomeExpense", q3) == Decimal("-171000000"), "sign minus")
    ok(val("EarningsPerShareBasic", q3) == Decimal("1.57") and facts[("EarningsPerShareBasic", q3)]["unit"]
       == "USD/shares", "no format, no scale, USD per share")
    ok(val("WeightedAverageNumberOfSharesOutstandingBasic", q3) == Decimal("14948500000"), "scale 3 shares")
    ok(val("LongTermDebtNoncurrent", "2026-06-27") == Decimal("98200000000"), "scale 9 with a decimal")
    ok(val("CommonStockParOrStatedValuePerShare", "2026-06-27") == Decimal("0.00001"), "scale -2")
    ok(val("ShortTermBorrowings", "2026-06-27") == 0 and facts[("ShortTermBorrowings", "2026-06-27")]["gold"][
        "zero"], "fixed-zero dash")
    ok(val("CommercialPaper", "2025-09-27") == 0, "zerodash")
    ok(val("CommonStockSharesIssued", "2026-06-27") == Decimal("15000000000")
       and val("CommonStockSharesOutstanding", "2025-09-27") == Decimal("15000000000"), "nested tags")
    ok(facts[("CommonStockSharesIssued", "2026-06-27")]["first"]
       < facts[("CommonStockSharesOutstanding", "2025-09-27")]["first"], "outer tag first in document order")
    ok(val("LiabilitiesCurrent", "2026-06-27") == Decimal("144430000000"), "inner markup removed")
    a = facts[("Assets", "2026-06-27")]
    ok(len(a["displays"]) == 2 and not a["displays_differ"], "same fact in two contexts with the same period")
    oi = facts[("OperatingIncomeLoss", q3)]
    ok(oi["gold"]["scale"] == 3 and not oi["displays_differ"], "finer display chosen, same value")
    cash = facts[("CashAndCashEquivalentsAtCarryingValue", "2026-06-27")]
    ok(cash["displays_differ"] and cash["gold"]["text"] == "36,269" and cash["gold"]["scale"] == 6,
       "displays differ, gold is the finest")
    ok(sum(1 for f in p["facts"] if f["displays_differ"]) == 1, "one fact whose displays differ")
    ok(not any(k[0] in ("EffectiveIncomeTaxRateContinuingOperations", "NumberOfStores") for k in facts),
       "pure and custom units skipped")
    writable = [f for f in p["facts"] if f["gold"]["scale"] in SCALE_WORDS]
    ok(len(writable) == 23, "scale -2 excluded from the sample")
    pairs = same_end_pairs(writable)
    got = sorted((x["concept"].split(":")[1], x["period"], y["period"]) for x, y in pairs)
    ok(got == sorted([("RevenueFromContractWithCustomerExcludingAssessedTax", q3, ytd),
                      ("RevenueFromContractWithCustomerExcludingAssessedTax", pq3, pytd),
                      ("NetIncomeLoss", q3, ytd), ("ComprehensiveIncomeNetOfTax", q3, h1)]),
       f"four same-end pairs, the two earliest-appearing for three starts: {got}")
    # sample and cases
    lines, plans, gold = build_cases(p, "0009990001-26-000020", "AAPL")
    ok(len(lines) == 23 and gold["counts"]["sampled_pairs"] == 4, "fewer than 25: all taken, all 4 pairs")
    lines2, _, _ = build_cases(parse_ixbrl(open(os.path.join(td, "synthetic-10q.htm"), encoding="utf-8").read()),
                               "0009990001-26-000020", "AAPL")
    ok(lines == lines2, "sample is deterministic for one accession")
    by_text = {g["concept"] + "@" + g["period"]: g for g in gold["facts"]}
    for g in gold["facts"]:
        ln = lines[g["line"] - 1]
        ok(g["written"] in ln and ln.endswith(g["written"]), "written number ends the line")
    ok(by_text["us-gaap:OtherNonoperatingIncomeExpense@" + q3]["written"] == "-171 million", "negative line")
    ok(by_text["us-gaap:EarningsPerShareBasic@" + q3]["written"] == "1.57", "scale 0 line")
    ok(by_text["us-gaap:WeightedAverageNumberOfSharesOutstandingBasic@" + q3]["written"] == "14,948,500 thousand",
       "thousand")
    ok(by_text["us-gaap:LongTermDebtNoncurrent@2026-06-27"]["written"] == "98.2 billion", "billion")
    ok(by_text["us-gaap:ShortTermBorrowings@2026-06-27"]["written"] == "0", "zero written as 0")
    ok(by_text["us-gaap:OperatingIncomeLoss@" + q3]["written"] == "28,202,000 thousand", "finest display written")
    for name, claims in plans.items():
        ok(len({c["line"] for c in claims}) == len(claims), f"{name}: at most one claim per number")
        for c in claims:
            ok(c["quote"] in lines[c["line"] - 1] and c["number"] in c["quote"], f"{name}: quote and number bind")
    ok(all("accession" in c["operands"]["of"] for c in plans["pinned.json"]), "pinned has accession")
    ok(not any("accession" in c["operands"]["of"] for c in plans["open.json"]), "open has none")
    ok(all(len(plans[k]) == 5 for k in PLAN_FILES[2:]), "5 cases of each abstention kind")
    ok(all(".." not in c["operands"]["of"]["period"] for c in plans["abstain-instant.json"]), "instant asks")
    ok(all(c["operands"]["of"]["concept"].endswith("Zz") for c in plans["abstain-concept.json"]), "concept Zz")
    # 25 + 5 from a larger set, with no duplicates
    fake = []
    for i in range(40):  # 30 durations (10 concepts x 3 starts, same end) and 10 instants
        start = f"2026-0{1 + i // 10}-01" if i < 30 else None
        fake.append({"concept": f"us-gaap:C{i % 10}" if i < 30 else f"us-gaap:I{i}", "unit": "USD",
                     "start": start, "end": "2026-06-30", "period": f"{start}..2026-06-30" if start else "2026-06-30"})
    s1, ch1, _, pr = draw_sample(fake, "0000000001-26-000001")
    s2, _, _, _ = draw_sample(fake, "0000000001-26-000001")
    s3, _, _, _ = draw_sample(fake, "0000000001-26-000002")
    ok(len(pr) >= 5 and len(ch1) == 5, "five pairs drawn")
    ok(len({id(f) for f in s1}) == len(s1) and 25 <= len(s1) <= 35, f"25 plus pair members, no duplicates: {len(s1)}")
    ok([f["period"] for f in s1] == [f["period"] for f in s2] and s1 != s3, "seeded by the accession")
    # fetching: no contact string -> stop with no request; retry once on 503; gzip; pacing
    calls, sleeps = [], []

    class Resp:
        def __init__(self, body, headers):
            self.body, self.headers = body, headers

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener_503_then_gzip(req, timeout):
        calls.append((req.full_url, req.get_header("User-agent"), req.get_header("Accept-encoding"), timeout))
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)
        return Resp(gzip.compress(b'{"ok": 1}'), {"Content-Encoding": "gzip"})

    def opener_fail(req, timeout):
        calls.append(req.full_url)
        raise AssertionError("no request may be made")

    saved = os.environ.get(UA_ENV)
    with tempfile.TemporaryDirectory(prefix="receipts-e3-selftest-") as tmp:
        try:
            os.environ.pop(UA_ENV, None)
            fx = Fetcher(tmp, opener=opener_fail, sleep=sleeps.append)
            try:
                fx.get(TICKERS_URL)
                ok(False, "stop without a contact string")
            except Stop:
                ok(calls == [], "no contact string: stop, no request")
            os.environ[UA_ENV] = "selftest-placeholder"
            fx = Fetcher(tmp, opener=opener_503_then_gzip, sleep=sleeps.append)
            ok(fx.get_json(TICKERS_URL) == {"ok": 1}, "503 retried once, gzip decompressed")
            ok(len(calls) == 2 and calls[0][1] == "selftest-placeholder" and calls[0][2] == "gzip"
               and calls[0][3] == 30, "headers and timeout")
            ok(Fetcher.RETRY_WAIT in sleeps and any(0 < s <= 0.25 for s in sleeps), "2 s retry wait and pacing")
            ok(fx.get_json(TICKERS_URL) == {"ok": 1} and len(calls) == 2, "cached URL not fetched again")
            with open(fx.path(TICKERS_URL), "rb") as fh:
                ok(b"selftest-placeholder" not in fh.read(), "contact string not cached")
            fo = Fetcher(tmp, offline=True, opener=opener_fail)
            try:
                fo.get(SUBMISSIONS_URL.format(cik=1))
                ok(False, "offline stop")
            except Stop:
                ok(len(calls) == 2, "offline: missing file stops, no request")

            def opener_404(req, timeout):
                calls.append(req.full_url)
                raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
            n = len(calls)
            try:
                Fetcher(tmp, opener=opener_404, sleep=sleeps.append).get(SUBMISSIONS_URL.format(cik=2))
                ok(False, "404 stops")
            except Stop:
                ok(len(calls) == n + 1, "404: no retry, stop")
        finally:
            if saved is None:
                os.environ.pop(UA_ENV, None)
            else:
                os.environ[UA_ENV] = saved
    ok(main(["--tickers", "JNJ", "--cache", os.path.join(tempfile.gettempdir(), "receipts-e3-never")]) == 2,
       "held-out ticker refused without --held-out")
    print(f"self-test passed: {len(checks)} checks")
    return 0


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tickers", help="comma-separated development tickers (default: all five)")
    ap.add_argument("--held-out", action="store_true", help="build the held-out companies (release run only)")
    ap.add_argument("--cache", default=os.path.join(HERE, "cache"))
    ap.add_argument("--out", default=os.path.join(HERE, "out"), help="writes <out>/dev or <out>/heldout")
    ap.add_argument("--offline", action="store_true", help="use only cached files; stop if one is missing")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.held_out:
        if a.tickers:
            print("error: the held-out set is built whole; --tickers is not allowed with --held-out", file=sys.stderr)
            return 2
        tickers, set_name = list(HELD_OUT_TICKERS), "heldout"
    else:
        tickers = [t.strip().upper() for t in (a.tickers or ",".join(DEV_TICKERS)).split(",") if t.strip()]
        held = [t for t in tickers if t in HELD_OUT_TICKERS]
        if held:
            print(f"error: {', '.join(held)} is a held-out ticker. It is fetched only in the held-out run "
                  "(--held-out). No request was made.", file=sys.stderr)
            return 2
        other = [t for t in tickers if t not in DEV_TICKERS]
        if other:
            print(f"error: {', '.join(other)} is not a registered development ticker ({', '.join(DEV_TICKERS)}).",
                  file=sys.stderr)
            return 2
        set_name = "dev"
    try:
        build(tickers, set_name, Fetcher(a.cache, offline=a.offline), a.out)
    except Stop as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
