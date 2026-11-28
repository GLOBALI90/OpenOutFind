"""Public search discovery for buyers of Tehran Oil Refinery product families.

Uses indexed public web results (Bing RSS; no API key). Social-network URLs are
captured as source evidence only; no login, private-profile access, or scraping
of authenticated pages. Company domains are emitted one URL per line.
"""
from __future__ import annotations

import argparse
import csv
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

UA = "RojanGlobalRefineryBuyerResearch/1.0 (public B2B company research)"
SOCIAL = ("linkedin.com", "facebook.com")
IGNORE = {
    "bing.com", "google.com", "wikipedia.org", "youtube.com", "instagram.com",
    "x.com", "twitter.com", "reddit.com", "pinterest.com", "amazon.com",
    "alibaba.com", "made-in-china.com", "globalsources.com", "tradeindia.com",
}
PRODUCTS = {
    "diesel_gasoil": ("diesel", "gasoil", "gas oil", "EN590", "Euro diesel", "گازوئیل", "diesel fuel"),
    "fuel_oil": ("fuel oil", "heavy fuel oil", "HFO", "mazut", "نفت کوره"),
    "LPG_propane_butane": ("LPG", "propane", "butane", "liquefied petroleum gas", "گاز مایع"),
    "naphtha": ("light naphtha", "heavy naphtha", "naphtha", "نفتا"),
    "petroleum_solvents": ("petroleum solvent", "hydrocarbon solvent", "industrial solvent", "حلال نفتی"),
    "sulfur": ("granular sulfur", "sulphur", "sulfur buyer", "گوگرد"),
    "vacuum_bottoms": ("vacuum bottom", "vacuum residue", "VB", "bitumen feedstock", "وکیوم باتوم"),
    "lubcut_base_oil_feed": ("lubcut", "lubricant base oil feedstock", "base oil refinery", "برش روغنی"),
    "kerosene_aviation": ("kerosene", "jet fuel buyer", "ATK aviation fuel", "JP4 fuel", "نفت سفید"),
    "refinery_cuts_recycle": ("isorecycle", "refinery recycle stream", "petroleum cut buyer", "برش نفتی"),
}
BUYER_TERMS = (
    "buyer", "purchasing", "procurement", "purchase tender", "RFQ", "importer",
    "bulk distributor", "industrial consumer", "feedstock", "supplier qualification",
    "خریدار", "خرید", "تأمین", "واردکننده", "مصرف کننده صنعتی", "مناقصه خرید",
)
SOCIAL_QUERY_SITES = ("site:linkedin.com/company", "site:facebook.com")
QUERIES: list[tuple[str, str]] = []
for product, terms in PRODUCTS.items():
    for term in terms[:4]:
        QUERIES.append((product, f'"{term}" (buyer OR importer OR procurement OR purchase OR RFQ OR distributor) company'))
        QUERIES.append((product, f'"{term}" industrial consumer purchasing company supplier'))
        for site in SOCIAL_QUERY_SITES:
            QUERIES.append((product, f'{site} "{term}" buyer importer procurement company'))

def rss(query: str) -> list[dict[str, str]]:
    url = "https://www.bing.com/search?" + urllib.parse.urlencode(
        {"q": query, "format": "rss", "count": "25", "setlang": "en-us"}
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml"})
    with urllib.request.urlopen(req, timeout=22) as response:
        root = ET.fromstring(response.read(1_500_000))
    rows = []
    for item in root.findall(".//item"):
        rows.append({
            "title": (item.findtext("title") or "").strip(),
            "url": (item.findtext("link") or "").strip(),
            "snippet": re.sub(r"\s+", " ", (item.findtext("description") or "")).strip(),
        })
    return rows

def domain(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.")

def is_social(host: str) -> bool:
    return any(host == x or host.endswith("." + x) for x in SOCIAL)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="discovered_seeds.txt")
    ap.add_argument("--social-output", default="social_buyer_evidence.csv")
    ap.add_argument("--limit", type=int, default=160)
    args = ap.parse_args()
    official: list[str] = []
    seen: set[str] = set()
    social_rows: list[dict[str, str]] = []
    failures = 0
    query_count = min(len(QUERIES), 60)  # bounded public search batch for Actions quota
    for i, (product, query) in enumerate(QUERIES[:query_count], 1):
        try:
            items = rss(query)
            social_added = 0
            company_added = 0
            for item in items:
                url = item["url"]
                parsed = urllib.parse.urlparse(url)
                host = (parsed.hostname or "").lower()
                d = domain(url)
                if parsed.scheme not in ("http", "https") or not d:
                    continue
                if is_social(host):
                    social_rows.append({"product_family": product, **item, "source_platform": "LinkedIn" if "linkedin" in host else "Facebook"})
                    social_added += 1
                    continue
                if host in IGNORE or any(host.endswith("." + bad) for bad in IGNORE):
                    continue
                if d in seen:
                    continue
                seen.add(d)
                official.append(parsed._replace(fragment="").geturl())
                company_added += 1
                if len(official) >= max(1, args.limit):
                    break
            print(f"REFINERY_QUERY {i}/{query_count} product={product} hits={len(items)} company_domains={company_added} social_evidence={social_added}")
        except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError) as exc:
            failures += 1
            print(f"REFINERY_QUERY_FAILED {i}/{query_count} product={product} error={type(exc).__name__}")
        if len(official) >= max(1, args.limit):
            break
        time.sleep(0.35)
    Path(args.output).write_text("\n".join(official) + ("\n" if official else ""), encoding="utf-8")
    with Path(args.social_output).open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["product_family", "source_platform", "title", "url", "snippet"])
        writer.writeheader()
        unique = {}
        for row in social_rows:
            unique[(row["url"], row["product_family"])] = row
        writer.writerows(unique.values())
    print(f"REFINERY_DISCOVERY_DONE queries={query_count} failures={failures} company_domains={len(official)} social_records={len(set((r['url'],r['product_family']) for r in social_rows))}")
    if failures == query_count:
        raise RuntimeError("All refinery buyer searches failed; not treating this as successful empty discovery.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
