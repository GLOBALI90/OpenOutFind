"""Read-only Wuapi account diagnostic. Never sends messages or prints API keys/phone numbers."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = "https://api.wuapi.dev"


def get_json(path: str, api_key: str, project: str = "") -> dict:
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    if project:
        headers["Wuapi-Project"] = project
    request = urllib.request.Request(BASE_URL + path, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", "replace")
            value = json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            error = json.loads(body)
        except json.JSONDecodeError:
            error = {}
        code = error.get("code", "http_error") if isinstance(error, dict) else "http_error"
        message = error.get("message", "") if isinstance(error, dict) else ""
        raise RuntimeError(f"WUAPI_HTTP_ERROR status={exc.code} code={code} message={message}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("WUAPI_INVALID_RESPONSE expected JSON object")
    return value


def main() -> int:
    api_key = os.environ.get("WUAPI_API_KEY", "").strip()
    project = os.environ.get("WUAPI_PROJECT", "").strip()
    if not api_key:
        print("WUAPI_DIAGNOSTIC_ABORTED reason=missing_api_key", file=sys.stderr)
        return 2

    context = get_json("/v1/me", api_key, project)
    key_info = context.get("apiKey") if isinstance(context.get("apiKey"), dict) else {}
    project_info = context.get("project") if isinstance(context.get("project"), dict) else {}
    org_info = context.get("organization") if isinstance(context.get("organization"), dict) else {}
    key_project_id = key_info.get("projectId")
    current_project_id = project_info.get("id")
    print(
        "WUAPI_CONTEXT "
        f"organization_id={org_info.get('id') or 'none'} "
        f"api_key_id={key_info.get('id') or 'unknown'} "
        f"api_key_project_id={key_project_id or 'organization_scope'} "
        f"request_project_id={current_project_id or 'none'} "
        f"request_project_name={project_info.get('name') or 'none'} "
        f"project_header_used={'yes' if project else 'no'}"
    )

    accounts = []
    cursor = None
    while True:
        query = {"limit": "100"}
        if cursor:
            query["cursor"] = cursor
        if project and not key_project_id:
            # The project header scopes an organization key; project keys already
            # carry their own scope and must not be pointed at another project.
            pass
        page = get_json("/v1/accounts?" + urllib.parse.urlencode(query), api_key, project)
        items = page.get("items")
        if not isinstance(items, list):
            raise RuntimeError("WUAPI_INVALID_ACCOUNT_LIST missing items array")
        accounts.extend(item for item in items if isinstance(item, dict))
        cursor = page.get("nextCursor")
        if not cursor:
            break

    ready = [a for a in accounts if str(a.get("status", "")).lower() == "ready"]
    statuses = {}
    for account in accounts:
        status = str(account.get("status") or "unknown").lower()
        statuses[status] = statuses.get(status, 0) + 1
    print(
        "WUAPI_ACCOUNT_SUMMARY "
        f"total={len(accounts)} ready={len(ready)} "
        f"statuses={json.dumps(statuses, sort_keys=True, separators=(',', ':'))}"
    )
    for index, account in enumerate(accounts, 1):
        account_id = str(account.get("id") or "")
        print(
            f"WUAPI_ACCOUNT index={index} id_suffix={account_id[-6:] or 'unknown'} "
            f"status={str(account.get('status') or 'unknown').lower()} "
            f"reconnecting={bool(account.get('reconnecting'))} "
            f"phone_present={'yes' if account.get('phone') else 'no'} "
            f"project_id={account.get('projectId') or 'none'} "
            f"disconnect_reason={account.get('disconnectReason') or 'none'}"
        )
    if len(ready) == 1:
        print("WUAPI_DIAGNOSTIC_RESULT exactly_one_ready_account")
    elif not ready:
        print("WUAPI_DIAGNOSTIC_RESULT no_ready_account_visible_to_this_api_key")
    else:
        print(f"WUAPI_DIAGNOSTIC_RESULT multiple_ready_accounts count={len(ready)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"WUAPI_DIAGNOSTIC_ABORTED {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
