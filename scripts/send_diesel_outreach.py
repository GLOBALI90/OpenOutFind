"""Consent-gated outreach without buyer-sector qualification filters.

Email requires explicit email eligibility and a valid email address.
WhatsApp requires explicit opt-in and a confirmed WhatsApp number.
Both channels honor do_not_contact/unsubscribed, prior-send status, and daily caps.
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import json
import os
import re
import smtplib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

SHEETS_URL = os.environ.get("GOOGLE_SHEETS_API_URL", "").strip()
SHEETS_TOKEN = os.environ.get("GOOGLE_SHEETS_API_TOKEN", "").strip()
COMPANY = "Rozhan Global"
COMPANY_DETAILS = (
    "Rozhan Tejarat Sirvan | Rozhan Global\n"
    "Website: https://www.rojanglobal.com\n"
    "Email: info@rojanglobal.com\n"
    "WhatsApp: +989023517939 | Customer service: +989003028171"
)
EMAIL_DAILY_LIMIT = 20
WHATSAPP_DAILY_LIMIT = 5
BUYER_SECTOR_EVIDENCE = {
    "mining": {"mine operator", "mineral processing", "mining operations", "mining fleet"},
    "transport_logistics": {"trucking", "haulage", "logistics fleet", "transport fleet", "truck fleet"},
    "shipping_ports": {"marine fleet", "port operator", "vessel fleet", "shipping company"},
    "construction": {"construction equipment", "earthmoving", "heavy equipment fleet"},
    "power_generation": {"power plant", "backup generator", "power generation"},
    "cement_manufacturing": {"cement plant", "cement manufacturing"},
}
UTC_TODAY = dt.datetime.now(dt.timezone.utc).date().isoformat()


def _request_json(url: str, payload: dict | None = None, token: str = "", headers: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Accept": "application/json"}
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=data, headers=request_headers, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", "replace")
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc


def _read_leads() -> list[dict]:
    if not SHEETS_URL or not SHEETS_TOKEN:
        raise RuntimeError("Google Sheets bridge secrets are required.")
    _, result = _request_json(SHEETS_URL, {"action": "read", "tab": "Leads", "token": SHEETS_TOKEN})
    if not isinstance(result, dict) or not result.get("ok") or not isinstance(result.get("rows"), list):
        raise RuntimeError("Google Sheets returned an invalid Leads table; refusing to send.")
    return [dict(row) for row in result["rows"] if isinstance(row, dict)]


def _save_row(row: dict) -> None:
    headers = list(row.keys())
    payload = {
        "action": "upsert", "tab": "Leads", "token": SHEETS_TOKEN,
        "headers": headers,
        "rows": [{key: _cell(value) for key, value in row.items()}],
        "key_column": "website",
    }
    _, result = _request_json(SHEETS_URL, payload)
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"Google Sheets status update failed: {result!r}")


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _true(value) -> bool:
    return str(value or "").strip().lower() in {"true", "yes", "1", "approved"}


def _values(row: dict, *keys: str) -> list[str]:
    found: list[str] = []
    for key in keys:
        value = row.get(key, "")
        if isinstance(value, list):
            items = value
        else:
            raw = str(value or "").strip()
            if not raw:
                continue
            try:
                decoded = json.loads(raw)
                items = decoded if isinstance(decoded, list) else [raw]
            except json.JSONDecodeError:
                items = raw.replace(",", ";").split(";")
        for item in items:
            item = str(item or "").strip()
            if item and item not in found:
                found.append(item)
    return found


def _suppressed(row: dict) -> bool:
    return _true(row.get("do_not_contact")) or _true(row.get("unsubscribed"))


def _qualified_buyer_row(row: dict) -> bool:
    if str(row.get("buyer_type") or "").strip().lower() != "direct_diesel_evidence":
        return False
    if not _values(row, "diesel_evidence_terms"):
        return False
    raw = row.get("sector_evidence") or ""
    if isinstance(raw, dict):
        evidence = raw
    else:
        try:
            evidence = json.loads(str(raw))
        except (TypeError, ValueError):
            return False
    if not isinstance(evidence, dict):
        return False
    return any(
        sector in BUYER_SECTOR_EVIDENCE
        and bool(set(str(term).lower() for term in (terms or [])) & allowed)
        for sector, terms in evidence.items()
        for allowed in [BUYER_SECTOR_EVIDENCE.get(sector, set())]
    )


def _verified_whatsapp_numbers(row: dict) -> list[str]:
    # Legacy "whatsapp" values may be ordinary telephone numbers. Only trust the
    # dedicated field when the collector recorded an explicit wa.me/API link.
    status = str(row.get("whatsapp_status") or "").strip().lower()
    if status not in {"explicit_link", "wa_link_confirmed"}:
        return []
    return [
        number for number in _values(row, "whatsapp_numbers")
        if re.fullmatch(r"\+[1-9]\d{7,14}", number)
    ]


def _email_message(row: dict, recipient: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = email.utils.formataddr((COMPANY, os.environ["GMAIL_USERNAME"]))
    msg["To"] = recipient
    msg["Reply-To"] = "info@rojanglobal.com"
    msg["Subject"] = "Long-Term Diesel Supply Proposal | Rozhan Global"
    msg["Message-ID"] = email.utils.make_msgid()
    msg["List-Unsubscribe"] = "<mailto:info@rojanglobal.com?subject=Unsubscribe>"
    name = str(row.get("company_name") or "Procurement Team").strip()
    msg.set_content(
        f"Hello {name},\n\n"
        "We are Rozhan Global, the international trading brand of Rozhan Tejarat Sirvan. "
        "We would like to explore whether your company has a requirement for a reliable, "
        "long-term supply of diesel (gasoil) for its operations.\n\n"
        "Depending on your specifications, destination, and volume, we would be pleased "
        "to discuss a supply arrangement that supports continuity and competitive procurement. "
        "If this is relevant, please share your required specifications, estimated monthly "
        "volume, delivery destination, and preferred supply terms.\n\n"
        "If you do not wish to receive further business emails from us, please reply "
        "'Unsubscribe' and we will suppress future outreach.\n\n"
        f"{COMPANY_DETAILS}\n"
    )
    return msg


def _send_email(row: dict, recipient: str) -> None:
    username = os.environ.get("GMAIL_USERNAME", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not username or not password:
        raise RuntimeError("GMAIL_USERNAME or GMAIL_APP_PASSWORD secret is missing.")
    message = _email_message(row, recipient)
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as smtp:
        smtp.login(username, password)
        smtp.send_message(message)


def _wuapi_account_id(api_key: str) -> str:
    _, result = _request_json("https://api.wuapi.dev/v1/accounts", token=api_key)
    # Wuapi's current AccountList schema returns the page under "items".
    accounts = result.get("items") if isinstance(result, dict) else None
    if not isinstance(accounts, list):
        raise RuntimeError("Wuapi account list response was not in the documented format.")
    ready = [item for item in accounts if isinstance(item, dict) and str(item.get("status", "")).lower() == "ready" and item.get("id")]
    if len(ready) != 1:
        raise RuntimeError(f"Expected exactly one ready Wuapi account; found {len(ready)}. No WhatsApp messages sent.")
    return str(ready[0]["id"])


def _send_whatsapp(api_key: str, account_id: str, recipient: str, body: str, idem_key: str) -> None:
    status, _ = _request_json(
        "https://api.wuapi.dev/v1/messages",
        {"accountId": account_id, "to": recipient, "type": "text", "text": body},
        token=api_key,
        headers={"Idempotency-Key": idem_key},
    )
    if status not in {200, 201, 202}:
        raise RuntimeError(f"Wuapi returned unexpected status {status}.")


def _message_text(row: dict) -> str:
    name = str(row.get("company_name") or "your team").strip()
    return (
        f"Hello {name},\n\n"
        "We are Rozhan Global, the international trading brand of Rozhan Tejarat Sirvan. "
        "We would like to explore a long-term business relationship by offering diesel "
        "(gasoil) supply for your operational and fuel procurement needs. Depending on "
        "your specifications, destination, and volume, we would be pleased to discuss "
        "supply continuity and competitive procurement terms.\n\n"
        "If this is relevant, please share your required specifications, estimated monthly "
        "volume, delivery destination, and preferred supply terms.\n\n"
        f"{COMPANY_DETAILS}"
    )


def _daily_count(rows: list[dict], channel: str) -> int:
    status_key = f"{channel}_send_status"
    date_key = f"{channel}_sent_at_utc"
    return sum(
        1 for row in rows
        if str(row.get(status_key, "")).strip().lower() in {"sent", "queued"}
        and str(row.get(date_key, "")).startswith(UTC_TODAY)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", action="store_true")
    parser.add_argument("--whatsapp", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Print eligibility and readiness only; never send messages.")
    args = parser.parse_args()
    if not args.email and not args.whatsapp:
        print("No channel selected; collection only. No messages sent.")
        return 0

    rows = _read_leads()
    email_sent = _daily_count(rows, "email")
    wa_sent = _daily_count(rows, "whatsapp")
    email_eligible = sum(1 for row in rows if _true(row.get("email_outreach_eligible")) and not _suppressed(row))
    email_ready = sum(
        1 for row in rows
        if _true(row.get("email_outreach_eligible")) and not _suppressed(row)
        and any(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) for value in _values(row, "commercial_emails", "email"))
        and str(row.get("email_send_status") or "").strip().lower() not in {"sent", "queued", "sending"}
    )
    whatsapp_opted_in = sum(1 for row in rows if _true(row.get("whatsapp_opt_in")) and not _suppressed(row))
    whatsapp_ready = sum(
        1 for row in rows
        if _true(row.get("whatsapp_opt_in")) and not _suppressed(row)
        and _verified_whatsapp_numbers(row)
        and str(row.get("whatsapp_send_status") or "").strip().lower() not in {"sent", "queued", "sending"}
    )
    found_emails = {value for row in rows for value in _values(row, "commercial_emails", "email")}
    found_phones = {value for row in rows for value in _values(row, "business_phones", "phone")}
    found_whatsapp = {value for row in rows for value in _verified_whatsapp_numbers(row)}
    email_contact_rows = sum(bool(_values(row, "commercial_emails", "email")) for row in rows)
    phone_contact_rows = sum(bool(_values(row, "business_phones", "phone")) for row in rows)
    whatsapp_contact_rows = sum(bool(_verified_whatsapp_numbers(row)) for row in rows)
    email_blocked_no_approval = sum(
        1 for row in rows if _values(row, "commercial_emails", "email")
        and not _true(row.get("email_outreach_eligible")) and not _suppressed(row)
    )
    whatsapp_blocked_no_optin = sum(
        1 for row in rows if _verified_whatsapp_numbers(row)
        and not _true(row.get("whatsapp_opt_in")) and not _suppressed(row)
    )
    print(
        f"CONTACT_INVENTORY rows={len(rows)} "
        f"companies_with_email={email_contact_rows} unique_emails={len(found_emails)} "
        f"companies_with_phone={phone_contact_rows} unique_phone_numbers={len(found_phones)} "
        f"companies_with_whatsapp={whatsapp_contact_rows} unique_whatsapp_numbers={len(found_whatsapp)}"
    )
    print(
        f"OUTREACH_PREFLIGHT rows={len(rows)} "
        f"email_eligible={email_eligible} email_ready={email_ready} "
        f"email_blocked_no_approval={email_blocked_no_approval} "
        f"whatsapp_opted_in={whatsapp_opted_in} whatsapp_ready={whatsapp_ready} "
        f"whatsapp_blocked_no_optin={whatsapp_blocked_no_optin} "
        f"suppressed={sum(_suppressed(row) for row in rows)}"
    )
    if args.dry_run:
        print("DRY_RUN_ONLY no messages sent; preflight report above is the result.")
        return 0

    wa_key = os.environ.get("WUAPI_API_KEY", "").strip()
    wa_account = _wuapi_account_id(wa_key) if args.whatsapp and wa_key else ""
    if args.whatsapp and not wa_key:
        raise RuntimeError("WUAPI_API_KEY secret is missing; no WhatsApp messages sent.")

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for row in rows:
        if _suppressed(row):
            continue
        website = str(row.get("website") or row.get("discovered_url") or "").strip()
        if not website:
            continue

        if args.email and email_sent < EMAIL_DAILY_LIMIT and _true(row.get("email_outreach_eligible")):
            prior = str(row.get("email_send_status") or "").strip().lower()
            recipients = _values(row, "commercial_emails", "email")
            valid_recipients = [value for value in recipients if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value)]
            if prior not in {"sent", "queued", "sending"} and valid_recipients:
                recipient = valid_recipients[0]
                try:
                    row["email_send_status"] = "sending"
                    row["email_send_started_at_utc"] = now
                    _save_row(row)
                    _send_email(row, recipient)
                    row["email_send_status"] = "sent"
                    row["email_sent_at_utc"] = now
                    row["email_sent_to"] = recipient
                    _save_row(row)
                    email_sent += 1
                    print(f"EMAIL_SENT website={website} recipient={recipient}")
                except Exception as exc:
                    row["email_send_status"] = "failed"
                    row["email_send_error"] = f"{type(exc).__name__}: {exc}"[:300]
                    _save_row(row)
                    print(f"EMAIL_FAILED website={website} reason={type(exc).__name__}")
            elif recipients and not valid_recipients:
                print(f"EMAIL_SKIPPED_INVALID_ADDRESS website={website}")

        if args.whatsapp and wa_sent < WHATSAPP_DAILY_LIMIT and _true(row.get("whatsapp_opt_in")):
            prior = str(row.get("whatsapp_send_status") or "").strip().lower()
            numbers = _verified_whatsapp_numbers(row)
            if prior not in {"sent", "queued", "sending"} and numbers:
                recipient = numbers[0]
                if not recipient.startswith("+"):
                    print(f"WHATSAPP_SKIPPED_INVALID_NUMBER website={website}")
                    continue
                idem = hashlib.sha256(f"rozhan:{website}:whatsapp:{UTC_TODAY}".encode()).hexdigest()
                try:
                    row["whatsapp_send_status"] = "sending"
                    row["whatsapp_send_started_at_utc"] = now
                    _save_row(row)
                    _send_whatsapp(wa_key, wa_account, recipient, _message_text(row), idem)
                    row["whatsapp_send_status"] = "queued"
                    row["whatsapp_sent_at_utc"] = now
                    row["whatsapp_sent_to"] = recipient
                    _save_row(row)
                    wa_sent += 1
                    print(f"WHATSAPP_QUEUED website={website}")
                except Exception as exc:
                    row["whatsapp_send_status"] = "failed"
                    row["whatsapp_send_error"] = f"{type(exc).__name__}: {exc}"[:300]
                    _save_row(row)
                    print(f"WHATSAPP_FAILED website={website} reason={type(exc).__name__}")

    print(f"OUTREACH_COMPLETE email_daily_count={email_sent}/{EMAIL_DAILY_LIMIT} whatsapp_daily_count={wa_sent}/{WHATSAPP_DAILY_LIMIT}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"OUTREACH_ABORTED {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
