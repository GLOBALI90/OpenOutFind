"""Stage 1: collect public procurement-news RSS items for human review."""
from __future__ import annotations
import argparse, json, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

QUERIES = {
 "diesel_rfq": '"diesel" OR "gasoil" OR "diesel fuel" (RFQ OR tender OR procurement)',
 "fuel_supply": '"fuel supply" OR "diesel supply" (tender OR bid OR procurement)',
 "mining_fuel": '(mine OR mining OR haulage) diesel (procurement OR tender OR supplier)',
 "chemicals": '(methanol OR urea OR ammonia OR "base oil") (RFQ OR tender OR procurement)',
 "industrial_chemicals": '("caustic soda" OR sulfur OR sulphur OR "sulfuric acid") (RFQ OR tender OR procurement)',
 "instrumentation": '("control valve" OR SCADA OR flowmeter OR actuator) (RFQ OR tender OR procurement)',
 "africa": '(Africa OR Kenya OR Tanzania OR Uganda OR Ghana OR Nigeria OR "South Africa") diesel procurement tender',
 "asia": '(China OR India OR Pakistan OR Bangladesh) diesel fuel procurement tender',
 "middle_east": '(UAE OR Oman OR Iraq OR Saudi Arabia) diesel fuel procurement tender',
}
def main():
 p=argparse.ArgumentParser()
 p.add_argument("--market",default="Africa"); p.add_argument("--days",type=int,default=30)
 p.add_argument("--output",default="public_buyer_signals.jsonl"); a=p.parse_args()
 market=a.market.lower()
 selected=dict(QUERIES)
 if market=="africa": selected={k:v for k,v in selected.items() if k not in ("asia","middle_east")}
 elif market=="china": selected={k:v for k,v in selected.items() if k in ("diesel_rfq","fuel_supply","chemicals","instrumentation","asia")}
 elif market=="asia": selected={k:v for k,v in selected.items() if k!="africa"}
 elif market=="middle east": selected={k:v for k,v in selected.items() if k not in ("africa","asia")}
 seen=set(); count=0; errors=0
 with Path(a.output).open("w",encoding="utf-8") as out:
  for qname,query in selected.items():
   try:
    q=f"({query}) when:{max(1,min(a.days,90))}d"
    url="https://news.google.com/rss/search?"+urllib.parse.urlencode({"q":q,"hl":"en-US","gl":"US","ceid":"US:en"})
    req=urllib.request.Request(url,headers={"User-Agent":"RojanGlobalPublicSignalResearch/1.0","Accept":"application/rss+xml, application/xml"})
    with urllib.request.urlopen(req,timeout=25) as resp: data=resp.read(2_000_000)
    root=ET.fromstring(data); batch=0
    for item in root.findall(".//item"):
     title=(item.findtext("title") or "").strip(); link=(item.findtext("link") or "").strip()
     if not title or not link or link in seen: continue
     seen.add(link); source=item.find("source"); publisher=(source.text or "").strip() if source is not None else ""
     desc=item.findtext("description") or ""
     out.write(json.dumps({"signal_type":"public_rss_procurement_signal","query_id":qname,"query":query,
       "title":title,"description":desc,"publisher":publisher,"published":item.findtext("pubDate") or "",
       "discovery_url":link,"discovered_at_utc":datetime.now(timezone.utc).isoformat(),
       "buyer_status":"unverified_public_signal"},ensure_ascii=False)+"\n")
     count+=1; batch+=1
    print(f"DISCOVERY query={qname} items={batch}")
   except Exception as e:
    errors+=1; print(f"DISCOVERY_ERROR query={qname} error={type(e).__name__}: {e}")
   time.sleep(1)
 print(f"DISCOVERY_SUMMARY market={a.market} queries={len(selected)} unique_signals={count} errors={errors} output={a.output}")
 return 0 if count or not errors else 2
if __name__=="__main__": raise SystemExit(main())
