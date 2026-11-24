"""Stage 2: qualify discovered RSS signals using explicit buying evidence.

No lead is promoted to a confirmed buyer and no contact is sent by this module.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PRODUCT_TERMS = (
    "diesel", "gasoil", "gas oil", "diesel fuel", "fuel supply", "fuel oil",
    "methanol", "urea", "ammonia", "base oil", "sulfur", "sulphur",
    "caustic soda", "control valve", "scada", "flowmeter", "flow meter",
    "actuator", "instrumentation",
)
EXPLICIT_DEMAND = (
    "request for quotation", "request for quote", "rfq", "invitation to tender",
    "invitation to bid", "tender notice", "open tender", "procurement notice",
    "seeking suppliers", "seeking bids", "supply contract", "purchase order",
    "supply requirement", "call for bids", "expression of interest",
)
GENERAL_PROCUREMENT = ("procurement", "tender", "supplier", "purchase", "contract", "bid")
DATE_RE = re.compile(r"\b(?:due|deadline|closing date|closes|by)\s*[:\-]?\s*([A-Z][a-z]+\s+\d{1,2},?\s+20\d{2}|\d{1,2}[./-]\d{1,2}[./-]20\d{2})", re.I)


def qualify(row: dict) -> dict:
    text = " ".join(str(row.get(k) or "") for k in
                    ("title", "publisher", "description", "query")).lower()
    products = sorted({term for term in PRODUCT_TERMS if term in text})
    explicit = sorted({term for term in EXPLICIT_DEMAND if term in text})
    general = sorted({term for term in GENERAL_PROCUREMENT if term in text})
    # Weighted signal classification is a triage label, not proof of buyer status.
    score = min(100, (35 if products else 0) + min(45, 15 * len(explicit))
                + (10 if general else 0) + (10 if row.get("publisher") else 0))
    if products and explicit:
        tier = "priority_review"
    elif products and general:
        tier = "manual_review"
    elif products:
        tier = "weak_product_mention"
    else:
        tier = "not_product_relevant"
    row.update({
        "qualification_stage": "rss_title_and_description_only",
        "matched_product_terms": products,
        "explicit_procurement_terms": explicit,
        "general_procurement_terms": general,
        "qualification_score": score,
        "qualification_tier": tier,
        "deadline_text": (DATE_RE.search(text).group(0) if DATE_RE.search(text) else ""),
        "buyer_status": "unverified_public_signal",
        "next_action": "open_original_source_and_confirm_buyer_need",
    })
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="public_buyer_signals.jsonl")
    parser.add_argument("--output", default="qualified_buyer_signals.jsonl")
    args = parser.parse_args()
    source, target = Path(args.input), Path(args.output)
    if not source.exists():
        raise SystemExit(f"Missing discovery input: {source}")
    counts = {}
    seen = set()
    total = 0
    with source.open(encoding="utf-8") as inp, target.open("w", encoding="utf-8") as out:
        for line in inp:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = row.get("discovery_url") or row.get("title")
            if not key or key in seen:
                continue
            seen.add(key)
            item = qualify(row)
            counts[item["qualification_tier"]] = counts.get(item["qualification_tier"], 0) + 1
            out.write(json.dumps(item, ensure_ascii=False) + "\n")
            total += 1
    print("QUALIFICATION_SUMMARY total=%d tiers=%s output=%s" %
          (total, json.dumps(counts, sort_keys=True), target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
