from app.operations.property_evidence import evidence_matches_value
from app.operations.property_com_browser_adapter import (
    PropertyComBrowserAdapter,
    _looks_like_block_page,
)


PROPERTY_URL = (
    "https://www.property.com.au/vic/wantirna-3152/"
    "napier-close/3-pid-3735391/"
)


def scraped_profile(**overrides):
    profile = {
        "bed": 4,
        "bath": 2,
        "street": "3 Napier Close",
        "suburb": "Wantirna",
        "state": "VIC",
        "postcode": "3152",
        "propertyType": "House",
        "realestate_url": PROPERTY_URL,
    }
    profile.update(overrides)
    return profile


def test_lookup_returns_only_exact_address_with_verifiable_evidence(tmp_path):
    adapter = PropertyComBrowserAdapter(
        profile_loader=lambda _url: scraped_profile(),
        url_finder=lambda **_address: [PROPERTY_URL],
        cache_db_path=tmp_path / "browser.db",
    )

    result = adapter.lookup(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
    )

    assert result["bed"] == 4
    assert result["bath"] == 2
    assert result["propertyType"] == "House"
    assert result["realestate_url"] == PROPERTY_URL
    assert result["_addressSlug"] == "3-napier-close-wantirna-vic-3152"
    assert evidence_matches_value(result["bed"], result["fieldEvidence"]["bed"])
    assert evidence_matches_value(result["bath"], result["fieldEvidence"]["bath"])
    assert evidence_matches_value(
        result["propertyType"], result["fieldEvidence"]["propertyType"]
    )


def test_lookup_rejects_a_scraped_profile_for_a_different_address(tmp_path):
    adapter = PropertyComBrowserAdapter(
        profile_loader=lambda _url: scraped_profile(street="5 Napier Close"),
        url_finder=lambda **_address: [PROPERTY_URL],
        cache_db_path=tmp_path / "browser.db",
    )

    result = adapter.lookup(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
    )

    assert result == {}


def test_lookup_uses_the_known_property_url_before_search(tmp_path):
    searched = []
    adapter = PropertyComBrowserAdapter(
        profile_loader=lambda _url: scraped_profile(),
        url_finder=lambda **address: searched.append(address) or [],
        cache_db_path=tmp_path / "browser.db",
    )

    result = adapter.lookup(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
        known_url=PROPERTY_URL,
    )

    assert result["bed"] == 4
    assert searched == []


def test_lookup_skips_browser_launch_during_active_cooldown(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PORTAL_BROWSER_MAX_CANDIDATES", "1")
    adapter = PropertyComBrowserAdapter(
        url_finder=lambda **_address: [PROPERTY_URL],
        cache_db_path=tmp_path / "browser.db",
        rate_limit_root=tmp_path / "rate-limits",
        wait_on_cooldown=False,
    )
    adapter.rate_limiter.record_status(PROPERTY_URL, 429)

    result = adapter.lookup(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
    )

    assert result == {}
    assert adapter.get_metrics_snapshot()["primary_blocked"] == 1


def test_lookup_honours_an_explicit_candidate_limit(tmp_path):
    attempted = []
    second_url = PROPERTY_URL.replace("3-pid", "5-pid")
    adapter = PropertyComBrowserAdapter(
        profile_loader=lambda url: attempted.append(url) or {},
        url_finder=lambda **_address: [PROPERTY_URL, second_url],
        cache_db_path=tmp_path / "browser.db",
        max_candidates=1,
    )

    adapter.lookup(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
    )

    assert attempted == [PROPERTY_URL]


def test_lookup_does_not_exhaust_candidates_for_unsupported_fields(tmp_path):
    attempted = []
    second_url = PROPERTY_URL.replace("3-pid", "5-pid")
    adapter = PropertyComBrowserAdapter(
        profile_loader=lambda url: attempted.append(url) or scraped_profile(),
        url_finder=lambda **_address: [PROPERTY_URL, second_url],
        cache_db_path=tmp_path / "browser.db",
        max_candidates=2,
    )

    result = adapter.lookup(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
        requested_fields={"bed", "bath", "features_list"},
    )

    assert (result["bed"], result["bath"]) == (4, 2)
    assert attempted == [PROPERTY_URL]
    metrics = adapter.get_metrics_snapshot()
    assert metrics["lookup_seconds"] >= 0
    assert metrics["primary_seconds"] >= 0
    assert metrics["avg_lookup_seconds"] >= 0


def test_lookup_uses_stealth_loader_for_supported_non_property_portal(
    tmp_path,
):
    attempted = []
    adapter = PropertyComBrowserAdapter(
        profile_loader=lambda _url: {},
        stealth_profile_loader=lambda url: attempted.append(url) or scraped_profile(),
        url_finder=lambda **_address: [],
        cache_db_path=tmp_path / "browser.db",
        max_candidates=1,
    )

    result = adapter.lookup(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
        requested_fields={"bed", "bath"},
    )

    assert result["bed"] == 4
    assert attempted == ["https://view.com.au/property/3-napier-close-wantirna-vic-3152/"]


def test_lookup_merges_rooms_from_another_self_scraped_portal(tmp_path):
    domain_url = (
        "https://www.domain.com.au/property-profile/"
        "3-napier-close-wantirna-vic-3152"
    )

    def load_profile(url):
        if url == PROPERTY_URL:
            return scraped_profile(bed=None, bath=None)
        if url == domain_url:
            return scraped_profile(bed=4, bath=2, realestate_url=domain_url)
        return {}

    adapter = PropertyComBrowserAdapter(
        profile_loader=load_profile,
        url_finder=lambda **_address: [],
        cache_db_path=tmp_path / "browser.db",
    )

    result = adapter.lookup(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
        known_url=PROPERTY_URL,
        requested_fields={"bed", "bath", "propertyType"},
    )

    assert result["propertyType"] == "House"
    assert (result["bed"], result["bath"]) == (4, 2)
    assert result["fieldEvidence"]["bed"]["sourceUrl"] == domain_url


def test_lookup_uses_stealth_fallback_when_primary_browser_is_partial(tmp_path):
    stealth_calls = []
    adapter = PropertyComBrowserAdapter(
        profile_loader=lambda _url: scraped_profile(bed=None, bath=None),
        stealth_profile_loader=lambda url: stealth_calls.append(url) or scraped_profile(),
        url_finder=lambda **_address: [],
        cache_db_path=tmp_path / "browser.db",
    )

    result = adapter.lookup(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
        known_url=PROPERTY_URL,
        requested_fields={"bed", "bath", "propertyType"},
    )

    assert stealth_calls == [PROPERTY_URL]
    assert (result["bed"], result["bath"]) == (4, 2)


def test_lookup_rejects_mismatched_stealth_fallback_data(tmp_path):
    adapter = PropertyComBrowserAdapter(
        profile_loader=lambda _url: scraped_profile(bed=None, bath=None),
        stealth_profile_loader=lambda _url: scraped_profile(
            street="5 Napier Close", bed=5, bath=3
        ),
        url_finder=lambda **_address: [],
        cache_db_path=tmp_path / "browser.db",
    )

    result = adapter.lookup(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
        known_url=PROPERTY_URL,
        requested_fields={"bed", "bath", "propertyType"},
    )

    assert result.get("bed") is None
    assert result.get("bath") is None


def test_off_market_property_is_not_promoted_to_sold(tmp_path):
    adapter = PropertyComBrowserAdapter(
        profile_loader=lambda _url: scraped_profile(
            status="sold",
            listingOption="sold",
            historysale=[{"soldDate": None, "soldPrice": None}],
            _sourceStatusText="Off market",
        ),
        url_finder=lambda **_address: [PROPERTY_URL],
        cache_db_path=tmp_path / "browser.db",
    )

    result = adapter.lookup(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
    )

    assert "status" not in result
    assert "listingOption" not in result
    assert "historysale" not in result


def test_block_page_classifier_catches_common_portal_challenges():
    assert _looks_like_block_page("<title>Verify you are human</title>")
    assert _looks_like_block_page("normal property profile") is False


def test_lookup_uses_configured_scrape_do_after_partial_direct_result(
    tmp_path, monkeypatch
):
    adapter = PropertyComBrowserAdapter(
        profile_loader=lambda _url: scraped_profile(bed=None, bath=None),
        url_finder=lambda **_address: [PROPERTY_URL],
        cache_db_path=tmp_path / "browser.db",
        scrape_do_token="configured-secret",
    )
    calls = []
    monkeypatch.setattr(
        adapter,
        "_load_scrape_do_profile",
        lambda url: calls.append(url) or scraped_profile(),
    )

    result = adapter.lookup(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
    )

    assert calls == [PROPERTY_URL]
    assert (result["bed"], result["bath"]) == (4, 2)
    assert result["fieldEvidence"]["bed"]["sourceUrl"] == PROPERTY_URL


def test_lookup_bounds_guessed_browser_urls(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("PORTAL_BROWSER_MAX_CANDIDATES", "2")
    adapter = PropertyComBrowserAdapter(
        profile_loader=lambda url: calls.append(url) or {},
        url_finder=lambda **_address: [],
        cache_db_path=tmp_path / "browser.db",
    )

    result = adapter.lookup(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
    )

    assert result == {}
    assert len(calls) == 2
    assert calls[0].startswith("https://view.com.au/property/")
    assert calls[1].startswith("https://www.domain.com.au/property-profile/")
