"""Upsert OpenOutFind JSONL lead records into the existing Apps Script Sheets bridge.

The bridge protocol is shared with the user's existing collector:
POST {action, tab, token, headers, rows, key_column}.
No credentials are read from source files or printed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests


def scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/export_to_google_sheets.py leads.jsonl")

    endpoint = os.environ.get("GOOGLE_SHEETS_API_URL", "").strip()
    token = os.environ.get("GOOGLE_SHEETS_API_TOKEN", "").strip()
    if not endpoint or not token:
        raise SystemExit(
            "Missing GOOGLE_SHEETS_API_URL or GOOGLE_SHEETS_API_TOKEN GitHub Actions secret."
        )

    records: list[dict[str, object]] = []
    for line_number, line in enumerate(Path(sys.argv[1]).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSONL at line {line_number}: {exc}") from exc
        # The CLI can emit run metadata as well as lead rows. Only persist actual leads.
        if isinstance(item, dict) and item.get("lead_id"):
            records.append(item)

    if not records:
        print("No lead rows were emitted; Google Sheets was left unchanged.")
        return 0

    headers = list(dict.fromkeys(key for record in records for key in record.keys()))
    if "lead_id" not in headers:
        raise SystemExit("The lead export has no lead_id key; refusing a non-idempotent write.")

    rows = [{header: scalar(record.get(header)) for header in headers} for record in records]
    payload = {
        "action": "upsert",
        "tab": "OpenOutFind",
        "token": token,
        "headers": headers,
        "rows": rows,
        "key_column": "lead_id",
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

    print(f"Google Sheets upsert succeeded for {len(records)} lead row(s) in tab OpenOutFind.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
