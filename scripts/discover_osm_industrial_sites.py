"""Discover industrial company websites from free Geofabrik OpenStreetMap extracts.

Only static regional extracts are downloaded; this avoids automated bulk queries to
shared Overpass servers. OSM data is ODbL and attribution is included in the output.
Generated URL lists are cached for seven days to avoid repeat large downloads.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

INDEX_URL = "https://download.geofabrik.de/index-v1.json"
REGIONS = {
    "China": [
        "asia/china/guangdong", "asia/china/jiangsu", "asia/china/shandong",
        "asia/china/zhejiang", "asia/china/hebei", "asia/china/inner-mongolia",
    ],
    "Africa": [
        "africa/nigeria", "africa/south-africa", "africa/kenya",
        "africa/ghana", "africa/zambia", "africa/tanzania",
    ],
}
USER_AGENT = "RozhanGlobalLeadResearch/1.0 (OSM extract processing)"


def website_url(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return parsed._replace(fragment="").geturl().rstrip("/")


def relevant(tags) -> bool:
    pairs = {str(tag.k).lower(): str(tag.v).lower() for tag in tags}
    combined = " ".join(f"{k}={v}" for k, v in pairs.items())
    keys = (
        "industrial=", "landuse=industrial", "man_made=works", "man_made=mine",
        "landuse=quarry", "power=plant", "power=generator", "amenity=port",
        "office=logistics", "building=industrial", "industrial=oil",
        "industrial=chemical", "industrial=cement", "industrial=warehouse",
    )
    words = (
        "mine", "quarry", "port", "cement", "steel", "factory", "industrial",
        "logistics", "warehouse", "power", "oil", "chemical", "manufacturing",
    )
    return any(key in combined for key in keys) or any(word in combined for word in words)


def extract_region(index: dict, region_id: str, out_dir: Path, handler) -> int:
    feature = next(
        (item for item in index.get("features", [])
         if item.get("properties", {}).get("id") == region_id),
        None,
    )
    if not feature:
        print(f"OSM_REGION_MISSING id={region_id}")
        return 0
    pbf_url = feature.get("properties", {}).get("urls", {}).get("pbf")
    if not pbf_url:
        print(f"OSM_PBF_URL_MISSING id={region_id}")
        return 0
    local = out_dir / (region_id.rsplit("/", 1)[-1] + ".osm.pbf")
    request = urllib.request.Request(pbf_url, headers={"User-Agent": USER_AGENT})
    print(f"OSM_DOWNLOAD_START region={region_id}")
    with urllib.request.urlopen(request, timeout=90) as response, local.open("wb") as target:
        shutil.copyfileobj(response, target, length=1024 * 1024)
    size_mb = local.stat().st_size / (1024 * 1024)
    print(f"OSM_DOWNLOAD_DONE region={region_id} size_mb={size_mb:.1f}")
    before = len(handler.urls)
    handler.apply_file(str(local), locations=False)
    local.unlink(missing_ok=True)
    count = len(handler.urls) - before
    print(f"OSM_REGION_PARSED region={region_id} new_websites={count}")
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=("China", "Africa"), required=True)
    parser.add_argument("--output", default="osm_seeds.txt")
    parser.add_argument("--cache-file", required=True)
    parser.add_argument("--max-age-days", type=int, default=7)
    args = parser.parse_args()
    output = Path(args.output)
    cache_file = Path(args.cache_file)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < args.max_age_days * 86400:
        shutil.copyfile(cache_file, output)
        print(f"OSM_CACHE_HIT market={args.market} path={cache_file}")
        return 0

    try:
        import osmium
    except ImportError as exc:
        raise SystemExit("Missing pyosmium; install the 'osmium' package before OSM extraction.") from exc

    class WebsiteHandler(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.urls: dict[str, str] = {}

        def accept(self, obj):
            tags = obj.tags
            name = tags.get("name") or tags.get("brand") or ""
            if not name or not relevant(tags):
                return
            raw = tags.get("website") or tags.get("contact:website") or tags.get("url") or ""
            url = website_url(raw)
            if url:
                self.urls.setdefault(url, name)

        def node(self, obj):
            self.accept(obj)

        def way(self, obj):
            self.accept(obj)

        def relation(self, obj):
            self.accept(obj)

    handler = WebsiteHandler()
    work = Path(".cache/osm_pbf")
    work.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(INDEX_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        index = json.loads(response.read().decode("utf-8"))
    successful_regions = 0
    for region_id in REGIONS[args.market]:
        try:
            extract_region(index, region_id, work, handler)
            successful_regions += 1
        except Exception as exc:
            print(f"OSM_REGION_FAILED id={region_id} error={type(exc).__name__}")
    if successful_regions == 0:
        raise RuntimeError("No Geofabrik regions could be processed; refusing to cache an empty result.")
    urls = sorted(handler.urls)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# © OpenStreetMap contributors, ODbL; regional extracts supplied by Geofabrik",
        *urls,
    ]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    shutil.copyfile(output, cache_file)
    print(f"OSM_DISCOVERY_COMPLETE market={args.market} regions={successful_regions} websites={len(urls)} output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
