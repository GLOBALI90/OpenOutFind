"""Discover fresh public company websites for diesel/gasoil buyer research.

Uses Bing's public RSS search endpoint (no API key). It excludes domains already
stored in Leads and domains successfully fetched by the crawler in prior runs. No social login, private data, or
outbound messaging is used.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

STATE_FILE = Path(".cache/diesel_checked_domains_v2.txt")

USER_AGENT = "RozhanDieselBuyerResearch/1.0 (+public B2B company research)"
BLOCKED_HOSTS = {
    "bing.com", "www.bing.com", "microsoft.com", "www.microsoft.com",
    "linkedin.com", "www.linkedin.com", "facebook.com", "www.facebook.com",
    "instagram.com", "x.com", "twitter.com", "youtube.com", "www.youtube.com",
    "wikipedia.org", "www.wikipedia.org", "google.com", "www.google.com",
    "maps.google.com", "meinestadt.de", "www.meinestadt.de",
    "yelp.com", "www.yelp.com", "yellowpages.com", "www.yellowpages.com",
    "tripadvisor.com", "www.tripadvisor.com", "amazon.com", "www.amazon.com",
    "ebay.com", "www.ebay.com", "etsy.com", "www.etsy.com",
}

EGYPT_QUERIES = [
    '"Egypt diesel gasoil importer procurement company official website"',
    '"Egypt petroleum products importers diesel suppliers company"',
    '"Egypt oil marketing companies gasoil diesel distribution procurement"',
    '"Egypt licensed fuel importers diesel bulk supply companies"',
    '"Egypt diesel fuel procurement industrial consumers mining cement logistics"',
    '"Egypt gasoil import tenders EN590 procurement EGPC suppliers"',
    '"مصر مستوردين السولار والديزل شركات استيراد وتوزيع الوقود"',
    '"مصر شركات تسويق المنتجات البترولية استيراد السولار"',
    '"مصر مناقصات توريد سولار ديزل شركات بترول"',
    '"مصر موزعي الوقود بالجملة شركات توزيع المنتجات البترولية"',
    '"Egypt fuel storage terminal diesel importer petroleum trading company"',
    '"Egypt commercial fleet diesel procurement transport logistics company"',
    '"Egypt cement mining industrial diesel generator fuel procurement"',
    '"Egypt marine bunker gasoil suppliers port fuel distributors"',
    '"Egypt petroleum products wholesale distributors gasoil company"',
    '"Egypt diesel EN590 importers fuel distributors procurement"',
]

MOROCCO_QUERIES = [
    '"Morocco diesel gasoil importer procurement company official website"',
    '"Morocco petroleum products importers gasoil suppliers company"',
    '"Morocco licensed liquid petroleum product importers diesel"',
    '"Morocco fuel distributors wholesale gasoil diesel companies"',
    '"Morocco oil marketing companies diesel procurement distribution"',
    '"Morocco gasoil import tenders EN590 fuel procurement"',
    '"importateur gasoil Maroc société produits pétroliers"',
    '"importation gasoil Maroc distributeur carburants professionnels"',
    '"société de distribution carburants Maroc gasoil approvisionnement"',
    '"grossiste carburant gasoil Maroc importateur produits pétroliers"',
    '"Maroc autorisation importation produits pétroliers liquides gasoil"',
    '"Maroc terminal stockage carburants importateur gasoil société"',
    '"Maroc transport logistique flotte camions achat gasoil entreprise"',
    '"Maroc cimenterie industrie achat gasoil diesel fournisseur"',
    '"Maroc bunker marine gasoil port fournisseur carburant"',
    '"Morocco EN590 diesel importers fuel distributors procurement"',
]

AFGHANISTAN_QUERIES = [
    '"Afghanistan industrial natural gas LPG CNG LNG diesel fuel consumers companies"',
    '"Afghanistan cement factories fuel gas diesel procurement official website"',
    '"Afghanistan mining companies fuel consumption diesel generators operations"',
    '"Afghanistan manufacturing factories LPG natural gas fuel procurement"',
    '"Afghanistan power plants fuel oil diesel natural gas operators"',
    '"Afghanistan food processing textile factories LPG diesel boilers"',
    '"Afghanistan fuel importers petroleum products LPG diesel gasoline companies"',
    '"Afghanistan petroleum products importers distributors official company"',
    '"Afghanistan industrial fuel procurement natural gas LPG propane butane"',
    '"Afghanistan heavy transport logistics fleet diesel fuel consumers"',
    '"افغانستان شرکت های صنعتی مصرف کننده گاز طبیعی گاز مایع دیزل سوخت"',
    '"افغانستان کارخانه سیمان معدن کارخانه تولیدی مصرف گازوئیل گاز مایع"',
    '"افغانستان واردکنندگان و توزیع کنندگان گاز مایع نفت و مواد نفتی"',
    '"افغانستان شرکت های تولید برق مصرف کننده دیزل نفت کوره گاز"',
    '"افغانستان شرکت های حمل و نقل ماشین آلات سنگین مصرف کننده گازوئیل"',
    '"افغانستان د طبیعي ګازو مایع ګاز ډیزل مصرفوونکي فابریکې شرکتونه"',
    '"افغانستان د تېلو او نفتو محصولاتو واردوونکي او وېشونکي شرکتونه"',
]
AFGHANISTAN_SOURCE_QUERIES = [
    ("industrial_consumers", "Afghanistan cement mining manufacturing food processing industrial energy fuel consumers"),
    ("fuel_importers", "Afghanistan petroleum products LPG diesel gasoline importers distributors official companies"),
    ("gas_consumers", "Afghanistan natural gas LPG CNG LNG industrial consumers companies"),
    ("power_transport", "Afghanistan power generation heavy transport logistics diesel fuel procurement"),
]

SOURCE_QUERIES = {
    "Afghanistan": AFGHANISTAN_SOURCE_QUERIES,
    "Egypt": [
        ("official_petroleum_sector", 'Egypt official petroleum companies fuel distribution diesel importer EGPC supplier'),
        ("official_tender_discovery", 'Egypt diesel gasoil procurement tender EN590 petroleum products import'),
        ("industrial_company_discovery", 'Egypt mining cement logistics industrial diesel fuel procurement company website'),
        ("fuel_distribution_discovery", 'Egypt fuel marketing company petroleum products distribution gasoil official'),
    ],
    "Morocco": [
        ("official_petroleum_sector", 'Morocco Ministry energy approved liquid petroleum product importers diesel gasoil'),
        ("official_tender_discovery", 'Morocco diesel gasoil procurement tender EN590 petroleum products import'),
        ("industrial_company_discovery", 'Morocco logistics cement mining industrial diesel fuel procurement company website'),
        ("fuel_distribution_discovery", 'Morocco fuel marketing company petroleum products distributor gasoil official'),
    ],
}

def domain_key(value: str) -> str:
    parsed = urlparse(value if "://" in value else "//" + value)
    return (parsed.hostname or "").lower().removeprefix("www.")

def _read_tab(endpoint: str, token: str, tab: str) -> list[dict]:
    payload = json.dumps({"action": "read", "tab": tab, "token": token}).encode()
    request = urllib.request.Request(
        endpoint, data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict) or not result.get("ok") or not isinstance(result.get("rows"), list):
        raise RuntimeError(f"Google Sheets did not return a valid {tab} table; refusing discovery without durable deduplication.")
    return [row for row in result["rows"] if isinstance(row, dict)]

def read_existing_domains() -> set[str]:
    endpoint = os.environ.get("GOOGLE_SHEETS_API_URL", "").strip()
    token = os.environ.get("GOOGLE_SHEETS_API_TOKEN", "").strip()
    if not endpoint or not token:
        raise RuntimeError("Google Sheets bridge secrets are required for deduplication.")
    domains = set()
    # Leads prevents duplicate prospects; only successfully crawled domains are cached.
    # New candidates are not marked checked until the crawler fetches their pages.
    for row in _read_tab(endpoint, token, "Leads"):
        for field in ("website", "discovered_url"):
            domain = domain_key(str(row.get(field) or ""))
            if domain:
                domains.add(domain)
                break
    if STATE_FILE.exists():
        for line in STATE_FILE.read_text(encoding="utf-8").splitlines():
            domain = domain_key(line)
            if domain:
                domains.add(domain)
    print(f"DISCOVERY_DEDUPE_LOADED leads_and_cached_domains={len(domains)}")
    return domains

def search_rss(query: str) -> list[str]:
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({
        "q": query, "format": "rss", "count": "30", "setlang": "en-us",
    })
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml",
    })
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read(1_500_000)
    root = ET.fromstring(payload)
    found = []
    for item in root.findall(".//item"):
        link = (item.findtext("link") or "").strip()
        if link.startswith(("http://", "https://")):
            found.append(link)
    return found

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=("Afghanistan", "Egypt", "Morocco"), required=True)
    parser.add_argument("--output", default="discovered_seeds.txt")
    parser.add_argument("--limit", type=int, default=120)
    args = parser.parse_args()

    existing = read_existing_domains()
    base_queries = {"Afghanistan": AFGHANISTAN_QUERIES, "Egypt": EGYPT_QUERIES, "Morocco": MOROCCO_QUERIES}[args.market]
    source_queries = SOURCE_QUERIES[args.market]
    query_items = [("general_public_web", q) for q in base_queries] + source_queries
    print(f"SOURCE_QUERY_GROUPS market={args.market} general={len(base_queries)} curated={len(source_queries)}")
    fresh: list[str] = []
    seen: set[str] = set()
    failures = 0
    for index, (source_name, query) in enumerate(query_items, 1):
        try:
            urls = search_rss(query)
            added = 0
            for url in urls:
                parsed = urlparse(url)
                host = (parsed.hostname or "").lower()
                domain = domain_key(url)
                if parsed.scheme not in {"http", "https"} or not domain:
                    continue
                if host in BLOCKED_HOSTS or any(host.endswith("." + blocked) for blocked in BLOCKED_HOSTS):
                    continue
                if domain in existing or domain in seen:
                    continue
                # Strip tracking fragments; retain the result path for relevant contact pages.
                clean = parsed._replace(fragment="").geturl()
                seen.add(domain)
                fresh.append(clean)
                added += 1
                if len(fresh) >= max(1, args.limit):
                    break
            print(f"SEARCH_QUERY market={args.market} source={source_name} index={index}/{len(query_items)} results={len(urls)} fresh_domains={added}")
        except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError) as exc:
            failures += 1
            print(f"SEARCH_QUERY_FAILED market={args.market} source={source_name} index={index} error={type(exc).__name__}")
        if len(fresh) >= max(1, args.limit):
            break
        time.sleep(0.6)

    if failures == len(query_items):
        raise RuntimeError("All public search queries failed; refusing to report a successful empty discovery run.")
    Path(args.output).write_text("\n".join(fresh) + ("\n" if fresh else ""), encoding="utf-8")
    print(f"DISCOVERY_COMPLETE market={args.market} queries={len(query_items)} failed_queries={failures} fresh_domains={len(fresh)} output={args.output}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
