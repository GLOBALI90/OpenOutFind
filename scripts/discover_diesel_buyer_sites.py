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

CHINA_QUERIES = [
    '"diesel fuel procurement" mining company China official website',
    '"柴油采购" 矿山 采矿 企业 官网',
    '"柴油" 露天矿 矿卡 车队 运营商',
    '"柴油" 采石场 骨料 矿业 公司',
    '"柴油" 水泥厂 熟料 工厂 采购',
    '"柴油" 钢铁厂 焦化厂 冶金 工业集团',
    '"柴油" 发电厂 备用发电机 电力运营商',
    '"柴油发电机组" 租赁 服务 公司 中国',
    '"柴油" 物流运输 重卡 车队 企业',
    '"柴油" 港口 集装箱码头 运营商',
    '"柴油" 船舶 船队 航运 公司',
    '"柴油" 工程机械 土方施工 总承包商',
    '"柴油" 油田 钻井 固井 压裂 服务商',
    '"柴油" 农场 农业机械 大型农垦 企业',
    '"柴油" 铁路 机车 维修 运营 公司',
    '"柴油" 机场 地勤 特种车辆 运营商',
    '"柴油" 环卫 垃圾运输 压缩车 运营公司',
    '"柴油" 工业园区 自备电厂 制造企业',
    '"柴油" 数据中心 备用发电机 运维 公司',
    '"柴油" 通信基站 发电机 运维 服务商',
    'China mine operator haul truck fleet fuel procurement company',
    'China cement steel plant industrial diesel generator operations',
    'China heavy haulage fleet logistics company diesel fuel',
    'China port terminal marine shipping fleet operator diesel',
    'China oilfield drilling contractor diesel equipment fleet',
]

AFRICA_QUERIES = [
    'Africa mining operator haul truck fleet diesel fuel procurement official company',
    'South Africa platinum gold coal mine contractor diesel fleet',
    'Zambia DRC copper mine operator mining contractor fuel diesel',
    'Ghana Burkina Faso gold mine operator heavy equipment fleet',
    'Namibia Botswana Zimbabwe quarry mining haulage diesel',
    'Nigeria Ghana Kenya Tanzania trucking haulage fleet diesel company',
    'South Africa logistics transport heavy truck fleet fuel procurement',
    'Africa cement clinker manufacturing plant diesel generator company',
    'Africa steel manufacturing industrial plant backup diesel generator',
    'Africa independent power producer diesel power plant generator operator',
    'Africa industrial generator rental power solutions diesel company',
    'Africa port terminal container handling equipment operator fleet',
    'Africa shipping marine vessel fleet diesel fuel operator',
    'Africa oilfield drilling services mining construction contractor fleet',
    'Africa commercial agriculture plantation farm machinery diesel fleet',
    'Africa railway locomotive freight operator diesel fleet company',
    'Africa airport ground handling diesel vehicle fleet operator',
    'Africa telecom tower power generator operations diesel service provider',
    'Africa waste management municipal truck fleet diesel operator',
    'Africa data centre backup generator diesel facilities operator',
    'entreprise minière Afrique flotte camions diesel achat gasoil',
    'société transport Afrique flotte poids lourds achat carburant diesel',
    'cimenterie Afrique centrale électrique groupe électrogène diesel',
    'opérateur portuaire Afrique flotte engins carburant gasoil',
    'شركة تعدين في أفريقيا أسطول معدات ثقيلة وقود الديزل',
    'شركة نقل في أفريقيا أسطول شاحنات شراء وقود الديزل',
    'empresa mineração África frota caminhões diesel combustível',
    'empresa logística África frota pesada compra gasóleo',
]


# Curated discovery queries anchored to authoritative industry directories and registries.
# These sources are used as public search indexes, not scraped behind interactive/login controls.
SOURCE_QUERIES = {
    "China": [
        ("CCCME_industrial_directory", 'site:cccme.cn/suppliers China mining construction machinery power generation cement steel company'),
        ("CCCME_industrial_directory", 'site:cccme.cn/suppliers China dump truck mine truck quarry aggregate heavy equipment fleet'),
        ("FIATA_logistics_directory", 'site:fiata.org/directory China freight forwarder trucking logistics fleet company'),
        ("industrial_company_discovery", 'China official company website diesel generator rental industrial power solutions'),
        ("industrial_company_discovery", 'China port terminal operator mining contractor cement plant official company website'),
    ],
    "Africa": [
        ("official_mining_registry", 'site:mmmd.gov.zm mining licence holder company Zambia mining operator'),
        ("official_mining_registry", 'site:dmpr.gov.za mining permit company South Africa mine operator'),
        ("official_mining_registry", 'Africa mining cadastre licence holder mine operator company website'),
        ("FIATA_logistics_directory", 'site:fiata.org/directory Africa freight forwarder logistics member company website'),
        ("industrial_company_discovery", 'Africa official company website diesel generator rental power solutions mining logistics'),
        ("industrial_company_discovery", 'Africa cement plant port terminal trucking fleet operator company website'),
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
    parser.add_argument("--market", choices=("China", "Africa"), required=True)
    parser.add_argument("--output", default="discovered_seeds.txt")
    parser.add_argument("--limit", type=int, default=120)
    args = parser.parse_args()

    existing = read_existing_domains()
    base_queries = CHINA_QUERIES if args.market == "China" else AFRICA_QUERIES
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
