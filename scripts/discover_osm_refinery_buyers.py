"""Low-volume OSM company-site/contact discovery for refinery-related buyers.

Queries one country per run via Overpass. OSM is a supplemental source:
tagged phone/email/website fields are incomplete and are not independently
verified. Keep the original OSM tags and attribution in the evidence CSV.
"""
from __future__ import annotations
import argparse, csv, json, urllib.error, urllib.parse, urllib.request
from pathlib import Path

COUNTRIES = {"Iran":"Iran","Afghanistan":"Afghanistan","Egypt":"Egypt","Morocco":"Morocco","Iraq":"Iraq"}
ENDPOINT = "https://overpass-api.de/api/interpreter"
UA = "RojanGlobalBuyerResearch/1.1 (public OSM query; rojanglobal.com)"
TAG_FILTERS = (
    '["industrial"~"oil|petroleum|refinery|chemical|gas|fuel",i]',
    '["craft"~"oil|chemical|fuel",i]',
    '["shop"~"fuel|wholesale",i]',
    '["office"="company"]["name"~"petroleum|oil|gas|fuel|chemical|refinery|lubricant",i]',
)
FIELDS = ["market","name","website","phone","email","whatsapp","address","tags","osm_type","osm_id"]

def pick(tags, *keys):
    for key in keys:
        value = (tags.get(key) or "").strip()
        if value: return value
    return ""

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--market",required=True); ap.add_argument("--output",default="osm_seeds.txt")
    ap.add_argument("--evidence",default="osm_buyer_evidence.csv"); ap.add_argument("--limit",type=int,default=500)
    a=ap.parse_args(); market=a.market.strip()
    Path(a.output).write_text("",encoding="utf-8")
    rows=[]; urls=[]; seen=set()
    if market in COUNTRIES:
        filters="".join(f"nwr(area.a){tag};" for tag in TAG_FILTERS)
        query=f'[out:json][timeout:45];area["name"="{COUNTRIES[market]}"]["boundary"="administrative"]["admin_level"="2"]->.a;({filters});out center tags {max(1,min(a.limit,500))};'
        request=urllib.request.Request(ENDPOINT,data=urllib.parse.urlencode({"data":query}).encode(),
            headers={"User-Agent":UA,"Content-Type":"application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(request,timeout=65) as response:
                payload=json.loads(response.read(8_000_000))
            for el in payload.get("elements",[]):
                t=el.get("tags",{})
                website=pick(t,"website","contact:website","url")
                if website and not website.startswith(("http://","https://")): website="https://"+website
                host=urllib.parse.urlparse(website).hostname if website else ""
                if host and host.lower().removeprefix("www.") not in seen:
                    seen.add(host.lower().removeprefix("www.")); urls.append(website)
                rows.append([market,pick(t,"name","brand","operator"),website,
                    pick(t,"phone","contact:phone","mobile","contact:mobile"),
                    pick(t,"email","contact:email"),
                    pick(t,"contact:whatsapp","whatsapp"),
                    " ".join(pick(t,"addr:full"),pick(t,"addr:street"),pick(t,"addr:city"),pick(t,"addr:country")).strip(),
                    json.dumps(t,ensure_ascii=False,sort_keys=True),el.get("type",""),el.get("id","")])
            print(f"OSM_DISCOVERY_DONE market={market} places={len(rows)} website_domains={len(urls)} tagged_phone={sum(bool(r[3]) for r in rows)} tagged_email={sum(bool(r[4]) for r in rows)}")
        except (urllib.error.URLError,TimeoutError,json.JSONDecodeError,OSError) as exc:
            print(f"OSM_DISCOVERY_WARNING market={market} error={type(exc).__name__}; continue without OSM")
    else:
        print(f"OSM_SKIPPED market={market!r}; unsupported country")
    Path(a.output).write_text("\n".join(urls)+("\n" if urls else ""),encoding="utf-8")
    with Path(a.evidence).open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.writer(f); w.writerow(FIELDS); w.writerows(rows)
    return 0
if __name__=="__main__": raise SystemExit(main())
