from diesel_leads.collector import (
    _contact_data,
    _is_qualified_buyer,
    _normalise_phone,
)


def test_phone_normalisation_rejects_long_ids_and_dates():
    assert _normalise_phone("+27 82 123 4567") == "+27821234567"
    assert _normalise_phone("20261106") == ""
    assert _normalise_phone("12345678901234567890") == ""
    assert _normalise_phone("1111111111") == ""


def test_qualification_requires_diesel_and_specific_buyer_sector():
    assert _is_qualified_buyer(["diesel"], {"mining": ["mine operator"]})
    assert not _is_qualified_buyer([], {"mining": ["mine operator"]})
    assert not _is_qualified_buyer(["diesel"], {"agriculture": ["agriculture"]})
    assert not _is_qualified_buyer(["diesel"], {})


def test_only_explicit_whatsapp_links_are_classified_as_whatsapp():
    emails, phones, whatsapp, status, evidence = _contact_data(
        "Call +27 82 123 4567 or email buyer@example.com",
        ["https://wa.me/27821234567"],
    )
    assert "+27821234567" in whatsapp
    assert status == "explicit_link"
    assert evidence == ["https://wa.me/27821234567"]
    assert "buyer@example.com" in emails



def test_sender_never_treats_legacy_phone_field_as_whatsapp():
    from scripts.send_diesel_outreach import _verified_whatsapp_numbers

    assert _verified_whatsapp_numbers({
        "whatsapp": "+27821234567",
        "whatsapp_numbers": "[]",
        "whatsapp_status": "not_verified",
    }) == []
    assert _verified_whatsapp_numbers({
        "whatsapp": "+27821234567",
        "whatsapp_numbers": '["+27821234567"]',
        "whatsapp_status": "explicit_link",
    }) == ["+27821234567"]
    assert _verified_whatsapp_numbers({
        "whatsapp_numbers": '["+123456789012345678"]',
        "whatsapp_status": "explicit_link",
    }) == []



def test_outreach_rejects_old_sector_only_and_non_buyer_rows():
    from scripts.send_diesel_outreach import _qualified_buyer_row

    good = {
        "buyer_type": "direct_diesel_evidence",
        "diesel_evidence_terms": '["diesel"]',
        "sector_evidence": '{"mining":["mine operator"]}',
    }
    assert _qualified_buyer_row(good)
    assert not _qualified_buyer_row({
        "buyer_type": "sector_candidate_needs_confirmation",
        "diesel_evidence_terms": "[]",
        "sector_evidence": '{"mining":["mining"]}',
    })
    assert not _qualified_buyer_row({
        "buyer_type": "direct_diesel_evidence",
        "diesel_evidence_terms": '["diesel"]',
        "sector_evidence": '{"agriculture":["agriculture"]}',
    })


def test_discovery_includes_afghanistan_multi_fuel_market():
    from scripts import discover_diesel_buyer_sites as discovery

    assert set(discovery.SOURCE_QUERIES) == {"Afghanistan", "Egypt", "Morocco"}
    assert len(discovery.AFGHANISTAN_QUERIES) >= 12
    assert any("افغانستان" in query for query in discovery.AFGHANISTAN_QUERIES)
    assert any("natural gas" in query.lower() and "lpg" in query.lower() for query in discovery.AFGHANISTAN_QUERIES)
    assert len(discovery.EGYPT_QUERIES) >= 10
    assert len(discovery.MOROCCO_QUERIES) >= 10
    assert any("مصر" in query for query in discovery.EGYPT_QUERIES)
    assert any("Maroc" in query for query in discovery.MOROCCO_QUERIES)


def test_fuel_importers_and_distributors_are_qualified_diesel_buyers():
    assert _is_qualified_buyer(
        ["diesel"],
        {"fuel_importer_distributor": ["diesel importer", "fuel distributor"]},
    )


def test_osm_discovery_includes_afghanistan_region():
    from scripts import discover_osm_industrial_sites as osm_discovery

    assert osm_discovery.REGIONS == {
        "Afghanistan": ["asia/afghanistan"],
        "Egypt": ["egypt"],
        "Morocco": ["morocco"],
    }


def test_unavailable_wuapi_account_does_not_raise_and_can_leave_email_channel_available(monkeypatch, capsys):
    from scripts import send_diesel_outreach as outreach

    def unavailable(_api_key):
        raise RuntimeError("Expected exactly one ready Wuapi account; found 0.")

    monkeypatch.setattr(outreach, "_wuapi_account_id", unavailable)
    assert outreach._resolve_whatsapp_account("test-key") == ""
    output = capsys.readouterr().out
    assert "WHATSAPP_CHANNEL_UNAVAILABLE" in output
    assert "email_channel_will_continue" in output


def test_missing_wuapi_key_does_not_abort_outreach(monkeypatch, capsys):
    from scripts import send_diesel_outreach as outreach

    assert outreach._resolve_whatsapp_account("") == ""
    output = capsys.readouterr().out
    assert "missing_api_key" in output
    assert "email_channel_will_continue" in output


def test_multi_fuel_qualification_requires_product_and_specific_sector():
    assert _is_qualified_buyer(["lpg"], {"cement_manufacturing": ["cement plant"]})
    assert _is_qualified_buyer(["natural gas"], {"mining": ["mine operator"]})
    assert not _is_qualified_buyer([], {"mining": ["mine operator"]})
    assert not _is_qualified_buyer(["lpg"], {})
