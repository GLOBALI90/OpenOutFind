"""Read, merge and idempotently upsert diesel research into the existing OpenOutFind tab."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests


LIST_FIELDS = {
    "commercial_emails", "business_phones", "whatsapp_numbers",
    "diesel_evidence_terms", "source_urls",
}
DICT_FIELDS = {"sector_evidence"}


def _domain_key(value: object) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    parsed = urlparse(candidate if "://" in candidate else "//" + candidate)
    return (parsed.hostname or "").lower().removeprefix("www.")


def _decode_cell(value: object) -> object:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped.startswith(("[", "{")):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return value
    return value


def _merge_value(old: object, new: object) -> object:
    """Merge repeated observations; empty new values never erase existing data."""
    old = _decode_cell(old)
    new = _decode_cell(new)
    if new is None or new == "" or new == [] or new == {}:
        return old if old is not None else new
    if old is None or old == "" or old == [] or old == {}:
        return new
    if isinstance(old, list) and isinstance(new, list):
        merged = list(old)
        for item in new:
            if item not in merged:
                merged.append(item)
        return merged
    if isinstance(old, dict) and isinstance(new, dict):
        merged = dict(old)
        for key, value in new.items():
            merged[key] = _merge_value(merged.get(key), value)
        return merged
    return new


def _merge_text(old: object, new_values: list[str]) -> str:
    values: list[str] = []
    for source in (str(old or ""), *new_values):
        for item in source.split(";"):
            item = item.strip()
            if item and item not in values:
                values.append(item)
    return "; ".join(values)


def _read_existing_leads(endpoint: str, token: str) -> list[dict[str, str]]:
    try:
        response = requests.post(
            endpoint,
            json={"action": "read", "tab": "OpenOutFind", "token": token},
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise SystemExit(f"Could not read existing OpenOutFind before upsert: {type(exc).__name__}: {exc}") from exc
    if not isinstance(result, dict) or not result.get("ok") or not isinstance(result.get("rows"), list):
        raise SystemExit("Google Sheets returned an invalid Leads table; refusing a non-idempotent write.")
    return [row for row in result["rows"] if isinstance(row, dict)]


def _dedupe_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep one row per stable lead_key and combine repeated observations in this batch."""
    merged: dict[str, dict[str, object]] = {}
    for record in records:
        key = str(record.get("lead_key", "")).strip()
        if not key:
            continue
        if key not in merged:
            merged[key] = dict(record)
            continue
        for field, value in record.items():
            merged[key][field] = _merge_value(merged[key].get(field), value)
    return list(merged.values())


def _scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/export_diesel_buyers_to_sheets.py diesel_leads.jsonl")
    endpoint = os.environ.get("GOOGLE_SHEETS_API_URL", "").strip()
    token = os.environ.get("GOOGLE_SHEETS_API_TOKEN", "").strip()
    if not endpoint or not token:
        raise SystemExit("Missing GOOGLE_SHEETS_API_URL or GOOGLE_SHEETS_API_TOKEN secret.")

    records: list[dict[str, object]] = []
    for number, line in enumerate(Path(sys.argv[1]).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSONL at line {number}: {exc}") from exc
        if isinstance(row, dict) and row.get("lead_key") and row.get("website"):
            records.append(row)
    records = _dedupe_records(records)
    if not records:
        print("No candidate rows; Google Sheets was left unchanged.")
        return 0

    existing_rows = _read_existing_leads(endpoint, token)
    existing_by_domain: dict[str, dict[str, str]] = {}
    for row in existing_rows:
        domain = _domain_key(row.get("website") or row.get("discovered_url"))
        if domain and domain not in existing_by_domain:
            existing_by_domain[domain] = {str(k): str(v or "") for k, v in row.items()}

    merged_records: list[dict[str, object]] = []
    new_count = 0
    update_count = 0
    for record in records:
        domain = _domain_key(record.get("website_domain") or record.get("website"))
        previous = existing_by_domain.get(domain, {})
        if previous:
            update_count += 1
        else:
            new_count += 1

        merged: dict[str, object] = dict(previous)
        for field, value in record.items():
            if field == "website" and previous.get("website"):
                # Keep the exact existing key value so the Apps Script bridge matches
                # the old row even if its stored URL contains www or a trailing slash.
                merged[field] = previous["website"]
            elif field in LIST_FIELDS or field in DICT_FIELDS:
                merged[field] = _merge_value(previous.get(field), value)
            elif value is not None and value != "" and value != [] and value != {}:
                merged[field] = value

        collected_at = str(record.get("collected_at_utc") or "")
        merged["first_seen_at_utc"] = previous.get("first_seen_at_utc") or collected_at
        merged["last_seen_at_utc"] = collected_at
        merged["record_status"] = "existing_company_refresh" if previous else "new_company_candidate"

        # Keep the legacy Leads columns populated for compatibility with existing views.
        merged["email"] = _merge_text(previous.get("email"), list(record.get("commercial_emails") or []))
        merged["phone"] = _merge_text(previous.get("phone"), list(record.get("business_phones") or []))
        merged["product_interest"] = "Diesel / Gasoil"
        merged["lead_segment"] = "Diesel / Gasoil buyer research"
        merged_records.append(merged)

    headers = list(dict.fromkeys(key for row in merged_records for key in row))
    rows = [{key: _scalar(row.get(key)) for key in headers} for row in merged_records]
    payload = {
        "action": "upsert",
        "tab": "OpenOutFind",
        "token": token,
        "headers": headers,
        "rows": rows,
        "key_column": "website",
    }
    try:
        response = requests.post(endpoint, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise SystemExit(f"Google Sheets upsert failed: {type(exc).__name__}: {exc}") from exc
    if not isinstance(result, dict) or not result.get("ok"):
        error = result.get("error", "invalid response") if isinstance(result, dict) else "invalid response"
        raise SystemExit(f"Google Sheets rejected the upsert: {error}")

    print(
        f"Google Sheets OpenOutFind upsert succeeded: new={new_count}, refreshed={update_count}, "
        f"submitted={len(merged_records)}, bridge_added={result.get('added')}, "
        f"bridge_merged={result.get('merged')}; key=website."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
