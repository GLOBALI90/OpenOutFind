"""Send this run's newly qualified buyer records to the configured Telegram chat.

Requires GitHub Actions secrets TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
If either is absent, skip safely; collection and Sheets export remain successful.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
FILE = Path(sys.argv[1] if len(sys.argv) > 1 else "diesel_leads.jsonl")


def send(text: str) -> None:
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text[:3900],
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=25) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError("Telegram API returned ok=false")


def main() -> int:
    if not TOKEN or not CHAT_ID:
        print("TELEGRAM_NOTIFY_SKIPPED: add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in repository Actions secrets.")
        return 0
    records = []
    if FILE.exists():
        for line in FILE.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("website"):
                records.append(row)

    intro = [
        "🛢️ Rojan Global | گزارش ران جمع‌آوری مشتری",
        f"رکوردهای واجد شرایط این اجرا: {len(records)}",
        "اطلاعات از منابع عمومی جمع‌آوری شده؛ تقاضای خرید/آمادگی معامله تأیید مستقل نشده است.",
    ]
    message = "\n".join(intro)
    if not records:
        send(message + "\nدر این اجرا مشتری واجد شرایط جدیدی پیدا نشد.")
        print("TELEGRAM_NOTIFY_SENT records=0")
        return 0

    chunks = [message]
    for index, row in enumerate(records, 1):
        company = str(row.get("company_name") or row.get("website_domain") or "نام نامشخص")
        domain = str(row.get("website") or "")
        emails = row.get("commercial_emails") or row.get("email") or []
        phones = row.get("business_phones") or row.get("phone") or []
        whatsapp = row.get("whatsapp_numbers") or []
        if isinstance(emails, str): emails = [emails]
        if isinstance(phones, str): phones = [phones]
        if isinstance(whatsapp, str): whatsapp = [whatsapp]
        block = (
            f"\n\n{index}) {company}\n"
            f"وب‌سایت: {domain}\n"
            f"محصول/زمینه: {row.get('product_interest', '')}\n"
            f"ایمیل: {', '.join(map(str, emails)) or 'ثبت نشده'}\n"
            f"تلفن: {', '.join(map(str, phones)) or 'ثبت نشده'}\n"
            f"واتساپ عمومی: {', '.join(map(str, whatsapp)) or 'تأیید/ثبت نشده'}"
        )
        if len(chunks[-1]) + len(block) > 3600:
            chunks.append(block.lstrip())
        else:
            chunks[-1] += block
    for chunk in chunks:
        send(chunk)
    print(f"TELEGRAM_NOTIFY_SENT records={len(records)} messages={len(chunks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
