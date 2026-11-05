"""Idempotently upsert diesel buyer pilot records into the existing Sheets bridge."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests


def _merge_value(old: object, new: object) -> object:
    """Merge repeated observations without erasing previously found evidence."""
    if new is None or new == "" or new == [] or new == {}:
        return old
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


def _dedupe_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep one row per stable lead_key and combine non-empty observations."""
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
        if isinstance(row, dict) and row.get("lead_key"):
            records.append(row)
    records = _dedupe_records(records)
    if not records:
        print("No candidate rows; Google Sheets was left unchanged.")
        return 0
    headers = list(dict.fromkeys(key for row in records for key in row))
    if "lead_key" not in headers:
        raise SystemExit("The diesel lead export has no stable lead_key; refusing a non-idempotent write.")
    rows = [{key: _scalar(row.get(key)) for key in headers} for row in records]
    payload = {
        "action": "upsert",
        "tab": "Diesel Buyers",
        "token": token,
        "headers": headers,
        "rows": rows,
        "key_column": "lead_key",
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
    print(f"Upserted {len(records)} unique company row(s) into Diesel Buyers using lead_key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
