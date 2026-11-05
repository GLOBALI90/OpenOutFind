from diesel_leads.collector import PageParser, _contact_data, _normalise_phone, collect_company


def test_whatsapp_only_verified_from_explicit_click_to_chat_link():
    emails, phones, whatsapp, status = _contact_data(
        "Sales: +86 138 0013 8000. Email sales@example.com",
        ["https://wa.me/8613800138000"],
    )
    assert emails == ["sales@example.com"]
    assert phones == ["+8613800138000"]
    assert whatsapp == ["+8613800138000"]
    assert status == "explicit_link"


def test_phone_is_not_assumed_to_be_whatsapp():
    _, phones, whatsapp, status = _contact_data("Call +1 (212) 555-0199", [])
    assert phones == ["+12125550199"]
    assert whatsapp == []
    assert status == "not_verified"


def test_page_parser_reads_title_and_links():
    parser = PageParser()
    parser.feed('<title>Example Industrial Co</title><a href="/contact">Contact</a><p>Diesel fuel</p>')
    assert "Example Industrial Co" in parser.title_parts
    assert "/contact" in parser.links
    assert "Diesel fuel" in parser.parts


def test_company_without_relevant_evidence_is_not_a_lead(monkeypatch):
    class Robots:
        def can_fetch(self, agent, url):
            return True

    class Response:
        pass

    monkeypatch.setattr("diesel_leads.collector._robots_parser", lambda url: Robots())
    monkeypatch.setattr(
        "diesel_leads.collector._fetch",
        lambda url: (
            url,
            type("Page", (), {"parts": ["Welcome to Example Company"], "links": [], "site_name": "Example"})(),
        ),
    )
    monkeypatch.setattr("diesel_leads.collector.time.sleep", lambda seconds: None)
    assert collect_company("https://example.com", "China") is None


def test_direct_diesel_evidence_is_separated_from_sector_only(monkeypatch):
    class Robots:
        def can_fetch(self, agent, url):
            return True

    pages = {
        "https://diesel.example": ["Example Fuel", "We operate diesel generators and a logistics fleet."],
        "https://sector.example": ["Example Logistics", "We operate a trucking and haulage fleet."],
    }
    monkeypatch.setattr("diesel_leads.collector._robots_parser", lambda url: Robots())
    monkeypatch.setattr(
        "diesel_leads.collector._fetch",
        lambda url: (url, type("Page", (), {"parts": pages[url], "links": [], "site_name": pages[url][0]})()),
    )
    monkeypatch.setattr("diesel_leads.collector.time.sleep", lambda seconds: None)
    direct = collect_company("https://diesel.example", "China")
    sector = collect_company("https://sector.example", "Africa")
    assert direct["buyer_type"] == "direct_diesel_evidence"
    assert sector["buyer_type"] == "sector_candidate_needs_confirmation"
    assert direct["qualification_score"] > sector["qualification_score"]


def test_sheets_export_deduplicates_and_merges_non_empty_observations():
    from scripts.export_diesel_buyers_to_sheets import _dedupe_records

    rows = _dedupe_records([
        {
            "lead_key": "same-company",
            "commercial_emails": ["sales@example.com"],
            "business_phones": ["+12125550100"],
            "source_urls": ["https://example.com/contact"],
            "qualification_score": 65,
        },
        {
            "lead_key": "same-company",
            "commercial_emails": [],
            "business_phones": ["+12125550101"],
            "source_urls": ["https://example.com/fuel"],
            "qualification_score": 70,
        },
    ])
    assert len(rows) == 1
    assert rows[0]["commercial_emails"] == ["sales@example.com"]
    assert rows[0]["business_phones"] == ["+12125550100", "+12125550101"]
    assert rows[0]["source_urls"] == [
        "https://example.com/contact",
        "https://example.com/fuel",
    ]
    assert rows[0]["qualification_score"] == 70


def test_existing_company_domain_normalizes_www_and_paths():
    from diesel_leads.collector import _domain_key

    assert _domain_key("https://www.example.com/contact") == "example.com"
    assert _domain_key("example.com/about") == "example.com"


def test_empty_refresh_does_not_erase_old_contact_observations():
    from scripts.export_diesel_buyers_to_sheets import _merge_value

    assert _merge_value(["sales@example.com"], []) == ["sales@example.com"]
    assert _merge_value({"mining": ["diesel"]}, {}) == {"mining": ["diesel"]}
