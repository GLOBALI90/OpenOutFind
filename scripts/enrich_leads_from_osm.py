"""Merge explicitly tagged OSM public business contacts into crawled company candidates.
Match only by exact website host; do not attach unmatched POI contacts to a lead.
"""
import csv, json, re, sys
from pathlib import Path
from urllib.parse import urlparse

leads=Path(sys.argv[1] if len(sys.argv)>1 else "diesel_leads.jsonl")
evidence=Path(sys.argv[2] if len(sys.argv)>2 else "osm_buyer_evidence.csv")
def domain(value):
    u=urlparse(value if "://" in value else "https://"+value)
    return (u.hostname or "").lower().removeprefix("www.")
contacts={}
if evidence.exists():
    with evidence.open(encoding="utf-8-sig",newline="") as f:
        for r in csv.DictReader(f):
            if r.get("market")=="market": continue
            d=domain(r.get("website",""))
            if not d: continue
            item=contacts.setdefault(d,{"phone":set(),"email":set(),"whatsapp":set(),"osm_names":set()})
            for field,target in (("phone","phone"),("email","email"),("whatsapp","whatsapp")):
                val=(r.get(field) or "").strip()
                if val: item[target].add(val)
            if r.get("name"): item["osm_names"].add(r["name"].strip())
if not leads.exists(): sys.exit(0)
out=[]
for line in leads.read_text(encoding="utf-8").splitlines():
    if not line.strip(): continue
    row=json.loads(line); match=contacts.get(domain(row.get("website","")))
    if match:
        for field,key in (("business_phones","phone"),("commercial_emails","email"),("whatsapp_numbers","whatsapp")):
            vals=set(row.get(field) or [])
            if isinstance(row.get(field),str): vals.update(x.strip() for x in row[field].split(";") if x.strip())
            vals.update(match[key])
            row[field]=sorted(vals)
            sheetfield={"business_phones":"phone","commercial_emails":"email","whatsapp_numbers":"whatsapp"}[field]
            row[sheetfield]="; ".join(row[field])
        row["source"]="OSM public POI tags + public company website"
        row["osm_contact_evidence"]="OpenStreetMap tagged contact data; exact website-domain match"
        row["osm_company_names"]=sorted(match["osm_names"])
    out.append(json.dumps(row,ensure_ascii=False))
leads.write_text("\n".join(out)+("\n" if out else ""),encoding="utf-8")
print(f"OSM_CONTACT_ENRICHMENT matched_company_domains={sum(1 for r in out if 'osm_contact_evidence' in json.loads(r))} OSM_domains_with_contact={sum(bool(x['phone'] or x['email'] or x['whatsapp']) for x in contacts.values())}")
