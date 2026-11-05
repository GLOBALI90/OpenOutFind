"""Discover fresh public company websites for diesel/gasoil buyer research.

Uses Bing's public RSS search endpoint (no API key). It excludes domains already
stored in Leads and domains persisted in the GitHub Actions cache, including sites
checked in prior runs that did not qualify. No social login, private data, or
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

STATE_FILE = Path(".cache/diesel_discovery_domains.txt")

USER_AGENT = "RozhanDieselBuyerResearch/1.0 (+public B2B company research)"
BLOCKED_HOSTS = {
    "bing.com", "www.bing.com", "microsoft.com", "www.microsoft.com",
    "linkedin.com", "www.linkedin.com", "facebook.com", "www.facebook.com",
    "instagram.com", "x.com", "twitter.com", "youtube.com", "www.youtube.com",
    "wikipedia.org", "www.wikipedia.org", "google.com", "www.google.com",
    "maps.google.com",
}

CHINA_QUERIES = [
    '"柴油" "矿业" 公司 官网',
    '"柴油采购" 物流 运输 企业 中国',
    '"柴油发电机" 电厂 企业 中国',
    '"燃料采购" 港口 航运 公司 中国',
    'China mining company diesel fuel procurement official website',
    'China logistics fleet diesel fuel transport company',
    'China cement plant diesel generator fuel procurement',
    'China port operator marine diesel fuel company',
    'China construction heavy equipment diesel fleet company',
    'China power generation company diesel generator operations',
    'China industrial agriculture machinery diesel fleet',
    'China gasoil buyer industrial company contact',
]

AFRICA_QUERIES = [
    'Africa mining company diesel fuel procurement official website',
    'Africa trucking logistics fleet diesel fuel buyer company',
    'Africa port shipping marine diesel fuel procurement company',
    'Africa cement plant diesel generator fuel procurement',
    'Africa power generation diesel generator operator company',
    'Africa construction heavy equipment diesel fleet company',
    'South Africa mining diesel procurement company',
    'Nigeria logistics fleet diesel fuel buyer company',
    'Egypt cement power plant diesel fuel procurement',
    'Kenya Tanzania mining transport diesel fuel company',
    'Ghana Zambia Angola mining diesel fuel procurement',
    'Morocco Algeria industrial diesel fuel buyer company',
    'entreprise minière Afrique achat carburant diesel',
    'société transport Afrique flotte camions achat gasoil',
    'شركة تعدين في أفريقيا شراء وقود الديزل',
    'empresa mineração África compra combustível diesel',
]

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
    # Leads prevents duplicate prospects; the Actions cache prevents re-crawling
    # domains checked in prior runs even when they did not qualify as buyers.
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

def save_discovery_history(urls: list[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    domains = set()
    if STATE_FILE.exists():
        domains.update(domain_key(line) for line in STATE_FILE.read_text(encoding="utf-8").splitlines())
    domains.update(domain_key(url) for url in urls)
    domains.discard("")
    STATE_FILE.write_text("\n".join(sorted(domains)) + ("\n" if domains else ""), encoding="utf-8")
    print(f"DISCOVERY_HISTORY_SAVED checked_domains={len(domains)} state_file={STATE_FILE}")

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
    parser.add_argument("--market", choices=("China", "Africa"), required=True)
    parser.add_argument("--output", default="discovered_seeds.txt")
    parser.add_argument("--limit", type=int, default=60)
    args = parser.parse_args()

    existing = read_existing_domains()
    queries = CHINA_QUERIES if args.market == "China" else AFRICA_QUERIES
    fresh: list[str] = []
    seen: set[str] = set()
    failures = 0
    for index, query in enumerate(queries, 1):
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
            print(f"SEARCH_QUERY market={args.market} index={index}/{len(queries)} results={len(urls)} fresh_domains={added}")
        except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError) as exc:
            failures += 1
            print(f"SEARCH_QUERY_FAILED market={args.market} index={index} error={type(exc).__name__}")
        if len(fresh) >= max(1, args.limit):
            break
        time.sleep(0.6)

    if failures == len(queries):
        raise RuntimeError("All public search queries failed; refusing to report a successful empty discovery run.")
    save_discovery_history(fresh)
    Path(args.output).write_text("\n".join(fresh) + ("\n" if fresh else ""), encoding="utf-8")
    print(f"DISCOVERY_COMPLETE market={args.market} queries={len(queries)} failed_queries={failures} fresh_domains={len(fresh)} output={args.output}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
