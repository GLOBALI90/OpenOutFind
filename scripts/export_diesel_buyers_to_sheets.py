"""Idempotently upsert diesel buyer pilot records into the existing Sheets bridge."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/export_diesel_buyers_to_sheets.py diesel_leads.jsonl")
    endpoint = os.environ.get("GOOGLE_SHEETS_API_URL", "").strip()
    token = os.environ.get("GOOGLE_SHEETS_API_TOKEN", "").strip()
    if not endpoint or not token:
        raise SystemExit("Missing GOOGLE_SHEETS_API_URL or GOOGLE_SHEETS_API_TOKEN secret.")
    records = []
    for number, line in enumerate(Path(sys.argv[1]).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSONL at line {number}: {exc}") from exc
        if isinstance(row, dict) and row.get("lead_key"):
            records.append(row)
    if not records:
        print("No candidate rows; Google Sheets was left unchanged.")
        return 0
    headers = list(dict.fromkeys(key for row in records for key in row))
    rows = [
        {key: json.dumps(row.get(key), ensure_ascii=False) if isinstance(row.get(key), (list, dict)) else ("" if row.get(key) is None else str(row.get(key))) for key in headers}
        for row in records
    ]
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
    print(f"Upserted {len(records)} candidate row(s) into Diesel Buyers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
