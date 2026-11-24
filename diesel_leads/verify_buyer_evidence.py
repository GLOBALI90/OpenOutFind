"""Verify candidate evidence against the originating public company/procurement pages.

This is a bounded, robots-aware second stage. It never sends outreach and does not
write verification output into Leads; results are kept as an auditable artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

UA = "RojanGlobalBuyerEvidenceAudit/1.0 (+public-source-review)"
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
PRODUCT = ("diesel", "gasoil", "gas oil", "fuel supply", "fuel oil", "base oil",
           "jet fuel", "lpg", "lng", "methanol", "urea", "ammonia", "sulfur",
           "sulphur", "caustic soda", "sodium hydroxide", "control valve",
           "flow meter", "flowmeter", "scada", "actuator", "instrumentation")
BUYING = ("request for quotation", "request for quote", "rfq", "request for proposal",
          "invitation to tender", "invitation to bid", "tender notice", "procurement notice",
          "supply requirement", "fuel supply contract", "purchase order", "bid submission",
          "expression of interest", "supplier registration", "seeking suppliers")
ACTIVE = ("closing date", "deadline", "due date", "open tender", "currently open",
          "submission date", "tender number", "rfq number", "reference number")


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.links = []
        self.title = []
        self.in_title = False
        self.meta = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag == "title":
            self.in_title = True
        if tag == "meta" and a.get("content"):
            if a.get("name", "").lower() in {"description", "keywords"} or a.get("property", "").lower() in {"og:description", "og:title"}:
                self.meta.append(a["content"])

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        s = " ".join(data.split())
        if s:
            self.text.append(s)
            if self.in_title:
                self.title.append(s)


def host(url):
    return (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.")


def get_page(url):
    if urllib.parse.urlparse(url).scheme not in ("http", "https") or not host(url):
        return None, "unsafe_url"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
    try:
        with urllib.request.urlopen(req, timeout=18) as res:
            final = res.geturl()
            if host(final) != host(url):
                return None, "cross_host_redirect"
            if res.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
                return None, "not_html"
            raw = res.read(1_200_000).decode(res.headers.get_content_charset() or "utf-8", "replace")
        parser = TextParser()
        parser.feed(raw)
        return {"url": final, "text": " ".join(parser.text + parser.meta),
                "title": " ".join(parser.title)[:180], "links": parser.links}, ""
    except urllib.error.HTTPError as e:
        return None, f"http_{e.code}"
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return None, type(e).__name__


def allowed(url):
    p = urllib.parse.urlparse(url)
    robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        req = urllib.request.Request(robots_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as res:
            rp.parse(res.read(200_000).decode("utf-8", "replace").splitlines())
        return rp.can_fetch(UA, url)
    except Exception:
        # Fail closed if robots cannot be fetched in this verifier.
        return False


def extract_contacts(text, links):
    emails = set(EMAIL_RE.findall(text))
    phones = set()
    for raw in PHONE_RE.findall(text):
        number = re.sub(r"[^0-9+]", "", raw)
        if number.startswith("00"):
            number = "+" + number[2:]
        if len(re.sub(r"\D", "", number)) >= 8:
            phones.add(number)
    for href in links:
        parsed = urllib.parse.urlparse(urllib.parse.urljoin("https://invalid.example", href))
        if parsed.scheme == "mailto":
            email = parsed.path.strip().lower()
            if EMAIL_RE.fullmatch(email):
                emails.add(email)
        if parsed.scheme == "tel":
            number = re.sub(r"[^0-9+]", "", parsed.path)
            if len(re.sub(r"\D", "", number)) >= 8:
                phones.add(number)
    return sorted(e.lower() for e in emails), sorted(phones)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="diesel_leads.jsonl")
    ap.add_argument("--output", default="buyer_evidence_verification.jsonl")
    ap.add_argument("--max-sources", type=int, default=3)
    args = ap.parse_args()
    output = Path(args.output)
    records = []
    for line in Path(args.input).read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    records.append(row)
            except json.JSONDecodeError:
                print("VERIFY_INPUT_ERROR malformed JSONL row; skipping")
    total = verified = blocked = 0
    with output.open("w", encoding="utf-8") as f:
        for lead in records:
            urls = list(dict.fromkeys(lead.get("source_urls") or [lead.get("website", "")]))
            evidence_pages, failures = [], []
            for url in urls[:max(1, min(args.max_sources, 5))]:
                if not url:
                    continue
                if not allowed(url):
                    failures.append({"url": url, "reason": "robots_disallow_or_unavailable"})
                    blocked += 1
                    continue
                page, err = get_page(url)
                if err:
                    failures.append({"url": url, "reason": err})
                elif page:
                    text = page["text"].lower()
                    p_hits = sorted(t for t in PRODUCT if t in text)
                    b_hits = sorted(t for t in BUYING if t in text)
                    a_hits = sorted(t for t in ACTIVE if t in text)
                    if p_hits or b_hits:
                        emails, phones = extract_contacts(page["text"], page["links"])
                        evidence_pages.append({
                            "source_url": page["url"], "page_title": page["title"],
                            "product_terms": p_hits, "procurement_terms": b_hits,
                            "active_tender_clues": a_hits,
                            "public_emails": emails, "public_phones": phones,
                            "evidence_excerpt": page["text"][:900],
                        })
                time.sleep(1.0)
            all_products = sorted({x for p in evidence_pages for x in p["product_terms"]})
            all_buying = sorted({x for p in evidence_pages for x in p["procurement_terms"]})
            active = sorted({x for p in evidence_pages for x in p["active_tender_clues"]})
            verified += bool(evidence_pages)
            total += 1
            status = ("source_page_has_product_and_procurement_terms" if all_products and all_buying
                      else "product_or_procurement_evidence_only" if evidence_pages
                      else "source_not_verified")
            item = {
                "lead_key": lead.get("lead_key") or hashlib.sha256(str(lead.get("website", "")).encode()).hexdigest()[:24],
                "company_name_candidate": lead.get("company_name", ""),
                "website": lead.get("website", ""),
                "market": lead.get("target_market", ""),
                "verification_status": status,
                "verified_product_terms": all_products,
                "verified_procurement_terms": all_buying,
                "active_tender_clues": active,
                "public_contact_emails": sorted({e for p in evidence_pages for e in p["public_emails"]}),
                "public_contact_phones": sorted({n for p in evidence_pages for n in p["public_phones"]}),
                "verified_source_pages": evidence_pages,
                "fetch_failures": failures,
                "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "next_review": "human_check_source_dates_scope_supplier_eligibility_and_compliance",
                "outreach_status": "not_sent_review_required",
            }
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            print(f"VERIFY company={item['company_name_candidate'][:70]} status={status} pages={len(evidence_pages)}")
    print(f"VERIFY_SUMMARY input_records={total} source_evidence_records={verified} robots_or_fetch_blocks={blocked} output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
