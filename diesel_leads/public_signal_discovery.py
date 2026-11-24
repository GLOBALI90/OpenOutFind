"""Discover recent public procurement/buyer signals from RSS search feeds.

This stage produces a reviewable research artifact only; it does not contact leads
or write unverified news items into the customer Leads sheet.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

QUERIES = {
    "diesel_rfq": '"diesel" OR "gasoil" OR "diesel fuel" (RFQ OR tender OR procurement OR "request for quotation")',
    "fuel_supply": '"fuel supply" OR "diesel supply" (tender OR bid OR procurement)',
    "mining_fuel": '(mine OR mining OR "haul truck") diesel (procurement OR tender OR supplier)',
    "power_fuel": '("power plant" OR generator) diesel fuel (tender OR procurement OR supply)',
    "petrochemicals": '(methanol OR urea OR ammonia OR "base oil") (RFQ OR tender OR procurement)',
    "industrial_chemicals": '("caustic soda" OR sulfur OR sulphur OR "sulfuric acid") (RFQ OR tender OR procurement)',
    "instrumentation": '("control valve" OR SCADA OR flowmeter OR actuator) (RFQ OR tender OR procurement)',
    "africa_buyers": '(Africa OR Kenya OR Tanzania OR Uganda OR Ghana OR Nigeria OR South Africa) diesel procurement tender',
    "asia_buyers": '(China OR India OR Pakistan OR Bangladesh) diesel fuel procurement tender',
    "middle_east_buyers": '(UAE OR Oman OR Iraq OR Saudi Arabia) diesel fuel procurement tender',
}

BUYING = ("rfq", "request for quotation", "request for quote", "tender", "procurement",
           "invitation to bid", "supply contract", "supplier", "purchase", "award")
PRODUCTS = ("diesel", "gasoil", "fuel", "methanol", "urea", "ammonia", "sulfur",
            "sulphur", "caustic soda", "control valve", "scada", "flowmeter", "actuator")


def feed_url(query: str, days: int) -> str:
    q = f"({query}) when:{days}d"
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"
    })


def read_feed(query_name: str, query: str, days: int) -> list[dict]:
    url = feed_url(query, days)
    req = urllib.request.Request(url, headers={
        "User-Agent": "RojanGlobalPublicSignalResearch/1.0 (RSS client)",
        "Accept": "application/rss+xml, application/xml, text/xml",
    })
    with urllib.request.urlopen(req, timeout=25) as response:
        data = response.read(2_000_000)
    root = ET.fromstring(data)
    rows = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pubdate = (item.findtext("pubDate") or "").strip()
        publisher = ""
        source = item.find("source")
        if source is not None and source.text:
            publisher = source.text.strip()
        text = (title + " " + publisher).lower()
        buying_hits = sorted(t for t in BUYING if t in text)
        product_hits = sorted(t for t in PRODUCTS if t in text)
        if not title or not link:
            continue
        rows.append({
            "signal_type": "public_news_rss_procurement_discovery",
            "query_id": query_name,
            "query": query,
            "title": title,
            "publisher": publisher,
            "published": pubdate,
            "discovery_url": link,
            "buying_signal_terms": buying_hits,
            "product_terms": product_hits,
            "signal_score": min(100, 20 * bool(product_hits) + 15 * min(3, len(buying_hits))),
            "review_status": "needs_source_and_buyer_verification",
            "discovered_at_utc": datetime.now(timezone.utc).isoformat(),
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", default="Africa")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output", default="public_buyer_signals.jsonl")
    args = parser.parse_args()
    market = args.market.lower()
    selected = dict(QUERIES)
    if market == "africa":
        selected = {k: v for k, v in selected.items() if k not in {"asia_buyers", "middle_east_buyers"}}
    elif market == "china":
        selected = {k: v for k, v in selected.items() if k in {"diesel_rfq", "fuel_supply", "petrochemicals", "instrumentation", "asia_buyers"}}
    elif market == "asia":
        selected = {k: v for k, v in selected.items() if k != "africa_buyers"}
    elif market == "middle east":
        selected = {k: v for k, v in selected.items() if k not in {"africa_buyers", "asia_buyers"}}
    out = Path(args.output)
    count = 0
    seen = set()
    errors = []
    with out.open("w", encoding="utf-8") as handle:
        for name, query in selected.items():
            try:
                found = read_feed(name, query, max(1, min(args.days, 90)))
                for row in found:
                    key = row["discovery_url"]
                    if key in seen:
                        continue
                    seen.add(key)
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                    count += 1
                print(f"DISCOVERY query={name} items={len(found)} unique_written={count}")
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                print(f"DISCOVERY_ERROR query={name} error={type(exc).__name__}: {exc}")
            time.sleep(1.2)
    print(f"DISCOVERY_SUMMARY market={args.market} queries={len(selected)} unique_signals={count} errors={len(errors)} output={out}")
    return 0 if count or not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
