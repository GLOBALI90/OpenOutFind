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
    "maps.google.com",
}

CHINA_QUERIES = [
    '"柴油" "矿业" 公司 官网',
    '"柴油采购" 物流 运输 企业 中国',
    '"柴油发电机" 电厂 企业 中国',
    '"燃料采购" 港口 航运 公司 中国',
    '"柴油" 水泥厂 采购 公司',
    '"柴油" 工程机械 施工 企业',
    '"柴油" 矿山 露天矿 运营商',
    '"柴油" 工业园区 工厂 物流车队',
    '"柴油" 港口 集装箱 运输 公司',
    '"柴油" 发电厂 备用发电机 企业',
    '"柴油" 农业机械 农场 企业',
    '"柴油" 船舶 船队 航运 企业',
    '"柴油" 钢铁厂 冶金 企业',
    '"柴油" 化工厂 石化 企业',
    '"柴油" 机场 地勤 车辆 运营',
    '"柴油" 采石场 矿业 公司',
    'China mining operator fleet fuel diesel official website',
    'China quarry aggregate company heavy equipment fleet',
    'China construction contractor earthmoving equipment fleet',
    'China trucking company long haul fleet diesel',
    'China logistics warehouse distribution fleet vehicles',
    'China cement manufacturer plant operations company',
    'China steel iron manufacturing plant industrial group',
    'China power utility thermal plant backup generators',
    'China port terminal operator container handling equipment',
    'China shipping company marine vessel fleet operations',
    'China industrial park manufacturing company fleet',
    'China oilfield services drilling contractor equipment fleet',
    'China agricultural enterprise farm machinery fleet',
    'China public transport bus operator diesel fleet',
]

AFRICA_QUERIES = [
    'Africa mining company diesel fuel procurement official website',
    'Africa mine operator haul truck fleet company',
    'Africa quarry aggregates cement heavy equipment operator',
    'Africa trucking haulage logistics fleet company',
    'Africa port terminal container handling operator',
    'Africa shipping marine vessel fleet company',
    'Africa cement manufacturer industrial plant operator',
    'Africa steel manufacturing industrial company',
    'Africa power generation diesel backup generator operator',
    'Africa construction contractor earthmoving equipment fleet',
    'Africa oilfield drilling services contractor fleet',
    'Africa industrial agriculture commercial farm machinery',
    'South Africa mining group fleet operations website',
    'Nigeria mining quarry logistics diesel fleet company',
    'Kenya Tanzania transport logistics fleet operator',
    'Ghana Zambia copper gold mine operator fleet',
    'Angola Mozambique mining port industrial company',
    'Egypt Morocco cement steel power plant company',
    'Namibia Botswana mining heavy equipment contractor',
    'Senegal Ivory Coast industrial port logistics company',
    'entreprise minière Afrique flotte engins carburant diesel',
    'société transport Afrique flotte camions achat gasoil',
    'entreprise cimenterie Afrique centrale électrique diesel',
    'opérateur portuaire Afrique flotte équipements diesel',
    'شركة تعدين في أفريقيا أسطول معدات وقود الديزل',
    'شركة نقل ولوجستيات في أفريقيا أسطول شاحنات',
    'empresa mineração África frota máquinas diesel',
    'empresa transporte África frota caminhões combustível',
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
    parser.add_argument("--market", choices=("China", "Africa"), required=True)
    parser.add_argument("--output", default="discovered_seeds.txt")
    parser.add_argument("--limit", type=int, default=120)
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
    Path(args.output).write_text("\n".join(fresh) + ("\n" if fresh else ""), encoding="utf-8")
    print(f"DISCOVERY_COMPLETE market={args.market} queries={len(queries)} failed_queries={failures} fresh_domains={len(fresh)} output={args.output}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
