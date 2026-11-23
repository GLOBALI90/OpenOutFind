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
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

INDEX_URL = "https://download.geofabrik.de/index-v1.json"
REGIONS = {
    "Afghanistan": ["asia/afghanistan"],
    "Egypt": ["egypt"],
    "Morocco": ["morocco"],
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
    # Match exact OSM tag keys/values only. Substring scans such as "mine" in
    # arbitrary tag text produced false positives (e.g. unrelated directory pages).
    pairs = {str(tag.k).lower(): str(tag.v).lower() for tag in tags}
    allowed_industries = {
        "oil", "chemical", "cement", "steel", "manufacturing",
        "quarry", "mining", "port", "logistics", "warehouse",
    }
    return (
        pairs.get("landuse") == "industrial"
        or pairs.get("man_made") in {"works", "mine"}
        or pairs.get("landuse") == "quarry"
        or pairs.get("power") in {"plant", "generator"}
        or pairs.get("amenity") == "port"
        or pairs.get("office") == "logistics"
        or pairs.get("building") == "industrial"
        or pairs.get("industrial") in allowed_industries
    )


def extract_region(index: dict, region_id: str, out_dir: Path, handler) -> int:
    feature = next(
        (item for item in index.get("features", [])
         if item.get("properties", {}).get("id") == region_id),
        None,
    )
    if not feature:
        raise RuntimeError(f"Geofabrik index has no region id={region_id}")
    pbf_url = feature.get("properties", {}).get("urls", {}).get("pbf")
    if not pbf_url:
        raise RuntimeError(f"Geofabrik index has no PBF URL for region id={region_id}")
    local = out_dir / (region_id.rsplit("/", 1)[-1] + ".osm.pbf")
    print(f"OSM_DOWNLOAD_START region={region_id} url={pbf_url}")
    # Geofabrik extracts are large; transient disconnects and throttling can
    # leave partial files. Retry clean downloads and validate the PBF header.
    last_error = None
    for attempt in range(1, 4):
        local.unlink(missing_ok=True)
        request = urllib.request.Request(
            pbf_url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
        )
        try:
            with urllib.request.urlopen(request, timeout=240) as response, local.open("wb") as target:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise RuntimeError(f"unexpected HTTP status {status}")
                shutil.copyfileobj(response, target, length=1024 * 1024)
            size = local.stat().st_size
            with local.open("rb") as source:
                header = source.read(4)
            if size < 100 or header != b"\\x00\\x00\\x00\\x0d":
                raise RuntimeError(f"download is not a valid PBF file (bytes={size}, header={header!r})")
            print(f"OSM_DOWNLOAD_DONE region={region_id} attempt={attempt} size_mb={size / (1024 * 1024):.1f}")
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            print(f"OSM_DOWNLOAD_RETRY region={region_id} attempt={attempt}/3 error={type(exc).__name__} detail={exc!r}")
            local.unlink(missing_ok=True)
            if attempt < 3:
                time.sleep(3 * attempt)
    if last_error is not None:
        raise RuntimeError(f"Geofabrik PBF download failed after 3 attempts: {last_error!r}") from last_error

    before = len(handler.urls)
    try:
        handler.apply_file(str(local), locations=False)
    except Exception as exc:
        raise RuntimeError(f"pyosmium failed parsing region={region_id} file_bytes={local.stat().st_size}: {exc!r}") from exc
    finally:
        local.unlink(missing_ok=True)
    count = len(handler.urls) - before
    print(f"OSM_REGION_PARSED region={region_id} new_websites={count}")
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=("Afghanistan", "Egypt", "Morocco"), required=True)
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
    # OSM is an optional enrichment source: never block web-based lead discovery
    # when Geofabrik is temporarily unavailable. Log the full exception for diagnosis.
    request = urllib.request.Request(INDEX_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            index = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"OSM_INDEX_FAILED error={type(exc).__name__} detail={exc!r}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            "# © OpenStreetMap contributors, ODbL; regional extracts supplied by Geofabrik\\n",
            encoding="utf-8",
        )
        print("OSM_OPTIONAL_SOURCE_UNAVAILABLE continuing_without_osm=true cache_written=false")
        return 0

    successful_regions = 0
    for region_id in REGIONS[args.market]:
        try:
            extract_region(index, region_id, work, handler)
            successful_regions += 1
        except Exception as exc:
            print(f"OSM_REGION_FAILED id={region_id} error={type(exc).__name__} detail={exc!r}")
    if successful_regions == 0:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            "# © OpenStreetMap contributors, ODbL; regional extracts supplied by Geofabrik\\n",
            encoding="utf-8",
        )
        print("OSM_OPTIONAL_SOURCE_UNAVAILABLE continuing_without_osm=true cache_written=false")
        return 0

    urls = sorted(handler.urls)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# © OpenStreetMap contributors, ODbL; regional extracts supplied by Geofabrik",
        *urls,
    ]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    domains_file = Path(".cache/osm_seed_domains.txt")
    domains_file.write_text(
        "# © OpenStreetMap contributors, ODbL; regional extracts supplied by Geofabrik\n"
        + "\n".join(sorted({urlparse(url).hostname.lower().removeprefix("www.") for url in urls}))
        + ("\n" if urls else ""),
        encoding="utf-8",
    )
    shutil.copyfile(output, cache_file)
    print(f"OSM_DISCOVERY_COMPLETE market={args.market} regions={successful_regions} websites={len(urls)} output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
