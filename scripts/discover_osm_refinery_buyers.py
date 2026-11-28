"""Low-volume OSM company-site discovery for refinery-derived product buyer sectors.

Uses one Overpass request per manually selected country batch. It is optional,
non-blocking, and emits only public company website URLs plus OSM tag evidence.
The Global market intentionally skips OSM to avoid expensive worldwide queries.
"""
from __future__ import annotations

import argparse
import csv
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

COUNTRIES = {
    "Iran": "Iran",
    "Afghanistan": "Afghanistan",
    "Egypt": "Egypt",
    "Morocco": "Morocco",
    "Iraq": "Iraq",
}
ENDPOINT = "https://overpass-api.de/api/interpreter"
UA = "RojanGlobalRefineryBuyerResearch/1.0 (low-volume public OSM query; contact via rojanglobal.com)"
# OSM tags that plausibly indicate petroleum/refinery, gas, chemical or bulk fuel businesses.
TAG_FILTERS = (
    '["industrial"~"oil|petroleum|refinery|chemical|gas|fuel",i]',
    '["craft"~"oil|chemical|fuel",i]',
    '["shop"~"fuel|wholesale",i]',
    '["office"="company"]["name"~"petroleum|oil|gas|fuel|chemical|refinery|lubricant",i]',
)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True)
    ap.add_argument("--output", default="osm_seeds.txt")
    ap.add_argument("--evidence", default="osm_buyer_evidence.csv")
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args()
    market = args.market.strip()
    if market not in COUNTRIES:
        Path(args.output).write_text("", encoding="utf-8")
        with Path(args.evidence).open("w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerow(["market", "name", "website", "tags", "osm_type", "osm_id"])
        print(f"OSM_SKIPPED market={market!r}; choose a supported country for one bounded query")
        return 0

    filters = "".join(f"nwr(area.a){tag};" for tag in TAG_FILTERS)
    q = f'[out:json][timeout:45];area["name"="{COUNTRIES[market]}"]["boundary"="administrative"]["admin_level"="2"]->.a;({filters});out center tags {max(1,min(args.limit,500))};'
    data = urllib.parse.urlencode({"data": q}).encode()
    req = urllib.request.Request(ENDPOINT, data=data, headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
    urls, rows, seen = [], [], set()
    try:
        with urllib.request.urlopen(req, timeout=65) as response:
            payload = json.loads(response.read(8_000_000))
        for el in payload.get("elements", []):
            tags = el.get("tags", {})
            website = (tags.get("website") or tags.get("contact:website") or "").strip()
            if not website:
                continue
            if not website.startswith(("http://", "https://")):
                website = "https://" + website
            host = urllib.parse.urlparse(website).hostname
            if not host or host in seen:
                continue
            seen.add(host)
            urls.append(website)
            rows.append([market, tags.get("name", ""), website, json.dumps(tags, ensure_ascii=False, sort_keys=True), el.get("type", ""), el.get("id", "")])
        Path(args.output).write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")
        with Path(args.evidence).open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f); w.writerow(["market", "name", "website", "tags", "osm_type", "osm_id"]); w.writerows(rows)
        print(f"OSM_DISCOVERY_DONE market={market} elements={len(payload.get('elements', []))} website_domains={len(urls)}")
        return 0
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        # Optional source: report clearly but don't fail the main lead run.
        Path(args.output).write_text("", encoding="utf-8")
        with Path(args.evidence).open("w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerow(["market", "name", "website", "tags", "osm_type", "osm_id"])
        print(f"OSM_DISCOVERY_WARNING market={market} error={type(exc).__name__}; continuing without OSM")
        return 0

if __name__ == "__main__":
    raise SystemExit(main())
