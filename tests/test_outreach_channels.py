"""Regression tests for independent email and WhatsApp outreach channels."""
import importlib.util
from pathlib import Path
import sys

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "send_diesel_outreach.py"
SPEC = importlib.util.spec_from_file_location("send_diesel_outreach", MODULE_PATH)
outreach = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(outreach)


def test_email_continues_when_wuapi_account_lookup_fails(monkeypatch, capsys):
    row = {
        "website": "https://buyer.example",
        "company_name": "Example Diesel Buyer",
        "email_outreach_eligible": "TRUE",
        "commercial_emails": ["procurement@buyer.example"],
        "email_send_status": "not_sent",
        "whatsapp_opt_in": "TRUE",
        "whatsapp_numbers": ["+12025550123"],
        "whatsapp_send_status": "not_sent",
    }
    sent = []

    monkeypatch.setattr(outreach, "_read_leads", lambda: [row])
    monkeypatch.setattr(outreach, "_daily_count", lambda rows, channel: 0)

    def unavailable_account(_api_key):
        raise RuntimeError("provider reports zero ready accounts")

    monkeypatch.setattr(outreach, "_wuapi_account_id", unavailable_account)
    monkeypatch.setattr(outreach, "_save_row", lambda updated: None)
    monkeypatch.setattr(
        outreach, "_send_email",
        lambda updated, recipient: sent.append((updated["website"], recipient)),
    )
    monkeypatch.setenv("WUAPI_API_KEY", "test-key")
    monkeypatch.setenv("GMAIL_USERNAME", "sender@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "not-used-by-mock")
    monkeypatch.setattr(
        sys, "argv",
        ["send_diesel_outreach.py", "--email", "--whatsapp"],
    )

    assert outreach.main() == 0
    assert sent == [("https://buyer.example", "procurement@buyer.example")]
    output = capsys.readouterr().out
    assert "WHATSAPP_CHANNEL_UNAVAILABLE" in output
    assert "email channel will continue" in output
    assert "EMAIL_SENT website=https://buyer.example" in output
    assert "OUTREACH_COMPLETE" in output
