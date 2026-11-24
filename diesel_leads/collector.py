"""Permission-respecting first-stage diesel buyer collector.

This pilot starts from explicitly supplied public company/directory URLs. It does
not scrape search engines, Google Maps, LinkedIn, or Facebook. It obeys robots.txt,
stays on each seed host, and never sends messages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import deque
from html.parser import HTMLParser
from pathlib import Path

USER_AGENT = "RozhanDieselBuyerResearch/0.1 (+public-company-research; contact: admin@example.invalid)"
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
PRODUCT_TERMS = {
    "oil_gas_fuels": ("diesel", "gasoil", "gas oil", "diesel fuel", "fuel procurement", "jet fuel", "lpg", "lng", "fuel oil", "base oil", "bitumen", "naphtha", "crude oil"),
    "petrochemicals": ("methanol", "urea", "ammonia", "polyethylene", "polypropylene", "pvc resin", "ethylene", "propylene", "petrochemical"),
    "industrial_chemicals": ("sulfuric acid", "caustic soda", "sodium hydroxide", "soda ash", "hydrochloric acid", "industrial chemicals", "solvents", "chemical reagents"),
    "energy": ("solar energy", "solar panels", "power generation", "energy storage", "power plant", "renewable energy"),
    "automation_instrumentation": ("plc", "dcs", "scada", "instrumentation", "pressure transmitter", "flow meter", "control valve", "actuator", "industrial sensor", "automation"),
    "metals_minerals": ("sulphur", "sulfur", "iron ore", "copper cathode", "steel", "bauxite", "zinc", "lead", "coal"),
    "industrial_materials": ("cement", "construction materials", "industrial materials"),
}
BUYING_SIGNALS = ("request for quotation", "request for quote", "rfq", "request for proposal", "rfi", "invitation to tender", "invitation to bid", "open tender", "tender notice", "procurement notice", "supplier registration", "bid submission", "buy lead", "supply requirement")
DIESEL_TERMS = PRODUCT_TERMS["oil_gas_fuels"]
EXISTING_COMPANY_DOMAINS: set[str] = set()

SECTOR_TERMS = {
    "mining": ("mining", "mine operator", "mineral processing"),
    "transport_logistics": ("trucking", "haulage", "logistics fleet", "transport fleet"),
    "shipping_ports": ("shipping", "marine fleet", "port operator", "vessel"),
    "construction": ("construction equipment", "earthmoving", "heavy equipment"),
    "power_generation": ("power plant", "backup generator", "power generation"),
    "cement_manufacturing": ("cement plant", "cement manufacturing", "industrial manufacturing"),
    "agriculture": ("agricultural machinery", "farm equipment", "agriculture"),
}
CONTACT_PATH_HINTS = ("contact", "about", "company", "location", "procurement", "fuel")


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False
        self.site_name = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag == "title":
            self.in_title = True
        if tag == "meta" and attrs.get("property") == "og:site_name":
            self.site_name = attrs.get("content", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(data.split())
        if cleaned:
            self.parts.append(cleaned)
            if self.in_title:
                self.title_parts.append(cleaned)


def _public_http_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password


def _domain_key(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        return ""
    parsed = urllib.parse.urlparse(candidate if "://" in candidate else "//" + candidate)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return host


def read_existing_company_domains() -> set[str]:
    """Read the durable Leads tab before collection so known companies are recognised."""
    endpoint = os.environ.get("GOOGLE_SHEETS_API_URL", "").strip()
    token = os.environ.get("GOOGLE_SHEETS_API_TOKEN", "").strip()
    if not endpoint or not token:
        raise RuntimeError("Google Sheets bridge secrets are required before lead collection.")
    payload = json.dumps({"action": "read", "tab": "Leads", "token": token}).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Could not read existing Leads before collection: {type(exc).__name__}: {exc}") from exc
    if not isinstance(result, dict) or not result.get("ok") or not isinstance(result.get("rows"), list):
        raise RuntimeError("Google Sheets did not return a valid existing Leads table; refusing collection.")
    domains: set[str] = set()
    for row in result["rows"]:
        if not isinstance(row, dict):
            continue
        for field in ("website", "discovered_url"):
            domain = _domain_key(str(row.get(field) or ""))
            if domain:
                domains.add(domain)
                break
    return domains


def _robots_parser(base_url: str) -> urllib.robotparser.RobotFileParser:
    parsed = urllib.parse.urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    try:
        request = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=8) as response:
            parser.parse(response.read(256_000).decode("utf-8", "replace").splitlines())
    except Exception:
        # If robots.txt cannot be read, fail closed for this pilot.
        parser.parse(["User-agent: *", "Disallow: /"])
    return parser


def _fetch(url: str) -> tuple[str, PageParser] | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                return None
            body = response.read(1_500_000).decode(response.headers.get_content_charset() or "utf-8", "replace")
            parser = PageParser()
            parser.feed(body)
            return response.geturl(), parser
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def _normalise_phone(raw: str) -> str:
    value = re.sub(r"[^\d+]", "", raw)
    if value.startswith("00"):
        value = "+" + value[2:]
    return value if len(re.sub(r"\D", "", value)) >= 8 else ""


def _contact_data(text: str, links: list[str]) -> tuple[list[str], list[str], list[str], str]:
    emails = sorted({e.lower() for e in EMAIL_RE.findall(text) if not e.lower().endswith((".png", ".jpg", ".webp"))})
    phones = sorted({p for raw in PHONE_RE.findall(text) if (p := _normalise_phone(raw))})
    wa_numbers: set[str] = set()
    wa_evidence: list[str] = []
    for link in links:
        absolute = urllib.parse.urljoin("https://placeholder.invalid", link)
        parsed = urllib.parse.urlparse(absolute)
        host = (parsed.hostname or "").lower()
        query = urllib.parse.parse_qs(parsed.query)
        raw = ""
        if host == "wa.me":
            raw = parsed.path.strip("/")
        elif host == "api.whatsapp.com" and parsed.path.startswith("/send"):
            raw = (query.get("phone") or [""])[0]
        if raw:
            # WhatsApp click-to-chat paths contain international digits without a plus.
            # Preserve E.164-style display for these explicitly verified numbers.
            normalised_raw = raw if raw.startswith(("+", "00")) else "+" + raw
            number = _normalise_phone(normalised_raw)
            if number:
                wa_numbers.add(number)
                wa_evidence.append(link)
    # A phone is not labelled WhatsApp merely because a company has WhatsApp text.
    # Only explicit WhatsApp click-to-chat links are verified in this pilot.
    status = "explicit_link" if wa_numbers else "not_verified"
    return emails, phones, sorted(wa_numbers), status


def collect_company(seed_url: str, market: str, max_pages: int = 5, priority: str = "fast_buying_signals") -> dict | None:
    seed_url = seed_url.strip()
    if not _public_http_url(seed_url):
        return None
    parsed_seed = urllib.parse.urlparse(seed_url)
    host = (parsed_seed.hostname or "").lower()
    robots = _robots_parser(seed_url)
    queue = deque([seed_url])
    visited: set[str] = set()
    all_text: list[str] = []
    all_links: list[str] = []
    source_urls: list[str] = []
    company_name = host
    while queue and len(visited) < max_pages:
        url = queue.popleft()
        url, _frag = urllib.parse.urldefrag(url)
        if url in visited or not _public_http_url(url):
            continue
        parsed = urllib.parse.urlparse(url)
        if (parsed.hostname or "").lower() != host:
            continue
        if not robots.can_fetch(USER_AGENT, url):
            continue
        visited.add(url)
        result = _fetch(url)
        if result is None:
            continue
        final_url, page = result
        if (urllib.parse.urlparse(final_url).hostname or "").lower() != host:
            continue
        text = " ".join(page.parts)
        all_text.append(text)
        all_links.extend(page.links)
        source_urls.append(final_url)
        if page.site_name or page.title_parts:
            company_name = page.site_name or " ".join(page.title_parts)[:160]
        for href in page.links:
            absolute = urllib.parse.urljoin(final_url, href)
            link = urllib.parse.urlparse(absolute)
            if (link.hostname or "").lower() == host and any(h in link.path.lower() for h in CONTACT_PATH_HINTS):
                if absolute not in visited and absolute not in queue:
                    queue.append(absolute)
        time.sleep(1.0)

    combined = " ".join(all_text).lower()
    product_hits = {category: sorted({term for term in terms if term in combined})
                    for category, terms in PRODUCT_TERMS.items()}
    product_hits = {k: v for k, v in product_hits.items() if v}
    diesel_hits = sorted({term for term in DIESEL_TERMS if term in combined})
    buying_hits = sorted({term for term in BUYING_SIGNALS if term in combined})
    sector_hits = {
        sector: sorted({term for term in terms if term in combined})
        for sector, terms in SECTOR_TERMS.items()
    }
    sector_hits = {k: v for k, v in sector_hits.items() if v}
    if not product_hits and not sector_hits:
        return None

    emails, phones, whatsapp, wa_status = _contact_data(" ".join(all_text), all_links)
    direct = bool(diesel_hits)
    signal = bool(buying_hits)
    priority_categories = {
        "fast_buying_signals": {"oil_gas_fuels", "industrial_chemicals", "automation_instrumentation"},
        "fuel_buyers": {"oil_gas_fuels"},
        "chemicals": {"petrochemicals", "industrial_chemicals"},
        "automation_instrumentation": {"automation_instrumentation", "energy"},
        "all_catalog": set(PRODUCT_TERMS),
    }.get(priority, set(PRODUCT_TERMS))
    priority_hits = sorted(set(product_hits) & priority_categories)
    score = min(100, (40 if direct else 20 if product_hits else 0) + min(20, 5 * len(product_hits)) + (20 if signal else 0) + min(10, 5 * len(sector_hits)) + (5 if emails else 0) + (5 if phones else 0) + (10 if priority_hits else 0))
    domain = host.removeprefix("www.")
    key = hashlib.sha256(domain.encode("utf-8")).hexdigest()[:24]
    return {
        "lead_key": key,
        "lead_id": key,
        "company_name": company_name,
        "website": f"{parsed_seed.scheme}://{parsed_seed.netloc}",
        "website_domain": domain,
        "market_search_bucket": market,\n        "campaign_priority": priority,\n        "priority_product_matches": priority_hits,
        "target_market": market,
        "source": f"Public company website: {parsed_seed.scheme}://{parsed_seed.netloc}",
        "source_provider": "public_company_website",
        "lead_segment": "Rojan Global industrial buyer research",
        "product_interest": "; ".join(sorted(product_hits)),
        "lead_source": "public_company_website_research",
        "industry": ", ".join(sorted(sector_hits)),
        "email": "; ".join(emails),
        "phone": "; ".join(phones),
        "whatsapp": "; ".join(whatsapp),
        "evidence": "Products: " + ", ".join(sorted(product_hits)) + "; buying signals: " + ", ".join(buying_hits) + "; sectors: " + ", ".join(sorted(sector_hits)),
        "lead_score": score,
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "country": "",
        "city": "",
        "buyer_type": "public_buying_signal_text" if signal else "product_or_sector_candidate_needs_confirmation",
        "purchase_status": "public_rfq_or_tender_terms_found_not_verified" if signal else "no_explicit_rfq_signal_found",
        "product_evidence": product_hits,
        "buying_signal_terms": buying_hits,
        "diesel_evidence_terms": diesel_hits,
        "sector_evidence": sector_hits,
        "commercial_emails": emails,
        "business_phones": phones,
        "whatsapp_numbers": whatsapp,
        "whatsapp_status": wa_status,
        "contact_validation": "format_only_not_mailbox_verified" if emails else "not_found",
        "qualification_score": score,
        "source_urls": sorted(set(source_urls)),
        "evidence_note": "Public buying/procurement wording found on the website." if signal else "Product or sector relevance found; an active purchase is not confirmed.",
        "collected_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", required=True, help="Text file containing one public company/directory URL per line")
    parser.add_argument("--market", default="China", choices=("Worldwide", "Africa", "Asia", "Middle East", "China"))
    parser.add_argument("--max-pages", type=int, default=3)\n    parser.add_argument("--priority", default="fast_buying_signals", choices=("fast_buying_signals", "fuel_buyers", "chemicals", "automation_instrumentation", "all_catalog"))
    parser.add_argument("--output", default="diesel_leads.jsonl")
    args = parser.parse_args()
    global EXISTING_COMPANY_DOMAINS
    EXISTING_COMPANY_DOMAINS = read_existing_company_domains()
    print(f"EXISTING_LEADS_READ count={len(EXISTING_COMPANY_DOMAINS)} tab=Leads")
    rows: dict[str, dict] = {}
    for line in Path(args.seeds).read_text(encoding="utf-8").splitlines():
        seed = line.strip()
        if not seed or seed.startswith("#"):
            continue
        row = collect_company(seed, args.market, max(1, min(args.max_pages, 10)), args.priority)
        if row:
            row["record_status"] = (
                "existing_company_refresh"
                if row["website_domain"] in EXISTING_COMPANY_DOMAINS
                else "new_company_candidate"
            )
            row["previously_in_leads"] = row["website_domain"] in EXISTING_COMPANY_DOMAINS
            rows[row["lead_key"]] = row
    with Path(args.output).open("w", encoding="utf-8") as handle:
        for row in rows.values():
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    existing_count = sum(1 for row in rows.values() if row.get("previously_in_leads"))
    print(
        f"Collected {len(rows)} unique company record(s): "
        f"{len(rows) - existing_count} new candidate(s), {existing_count} existing company refresh(es)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
