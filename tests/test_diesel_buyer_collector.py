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
