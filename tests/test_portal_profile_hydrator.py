import json
from types import SimpleNamespace

import requests

import app.operations.portal_profile_hydrator as portal_module
from app.operations.portal_profile_hydrator import PortalProfileHydrator


def test_portal_parser_rejects_profile_without_verifiable_address():
    hydrator = PortalProfileHydrator.__new__(PortalProfileHydrator)

    result = hydrator.parse_view_profile_json({
        "detailPage": {"bedrooms": 3, "bathrooms": 2, "landSize": 500}
    })

    assert result == {}


def test_portal_parser_accepts_profile_with_address_and_observed_fields():
    hydrator = PortalProfileHydrator.__new__(PortalProfileHydrator)

    result = hydrator.parse_view_profile_json({
        "detailPage": {
            "address": {
                "streetNumber": "10",
                "streetName": "Main",
                "streetType": "Street",
                "suburb": "Sydney",
                "state": "NSW",
                "postcode": "2000",
            },
            "bedrooms": 2,
            "bathrooms": 1,
        }
    })

    assert result["bed"] == 2
    assert result["bath"] == 1


def test_realestate_parser_requires_and_records_exact_address():
    hydrator = PortalProfileHydrator.__new__(PortalProfileHydrator)

    assert hydrator.parse_realestate_profile_json({"property": {"features": {"bedrooms": 2}}}) == {}

    result = hydrator.parse_realestate_profile_json({
        "property": {
            "address": {
                "streetNumber": "10",
                "streetName": "Main",
                "streetType": "Street",
                "suburb": "Lane Cove North",
                "state": "NSW",
                "postcode": "2066",
            },
            "features": {"bedrooms": 2},
            "buildingSize": "88 m2",
        }
    })

    assert result["_addressSlug"] == "10-main-street-lane-cove-north-nsw-2066"
    assert result["_areaType"] == "buildingArea"


def test_fetch_profile_reports_one_blocked_outcome(tmp_path):
    class BlockedSession:
        def get(self, url, timeout):
            return SimpleNamespace(status_code=403, text="")

    hydrator = PortalProfileHydrator(cache_db_path=tmp_path / "portal.db")
    hydrator._session = BlockedSession()

    result = hydrator.fetch_profile(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
    )

    assert result["_fetch_status"] == "blocked"
    metrics = hydrator.get_metrics_snapshot()
    assert (metrics["blocked"], metrics["total"]) == (1, 1)
    assert metrics["http"]["view.com.au"]["403"] >= 1


def test_portal_http_skips_an_active_cooldown_in_fail_fast_mode(tmp_path):
    hydrator = PortalProfileHydrator(
        cache_db_path=tmp_path / "portal.db",
        shared_rate_limit=True,
        wait_on_cooldown=False,
    )
    url = "https://view.com.au/property/example/"
    hydrator.rate_limiter.record_status(url, 429)

    assert hydrator._http_get(url) == (None, 429)
    metrics = hydrator.get_metrics_snapshot()
    assert metrics["http"]["view.com.au"]["cooldown_skipped"] == 1


def test_fetch_profile_reports_one_not_found_outcome(tmp_path):
    class NotFoundSession:
        def get(self, url, timeout):
            return SimpleNamespace(status_code=404, text="")

    hydrator = PortalProfileHydrator(cache_db_path=tmp_path / "portal.db")
    hydrator._session = NotFoundSession()

    result = hydrator.fetch_profile(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
    )

    assert result["_fetch_status"] == "not_found"
    metrics = hydrator.get_metrics_snapshot()
    assert (metrics["not_found"], metrics["total"]) == (1, 1)


def test_fetch_profile_reports_one_timeout_outcome(tmp_path):
    class TimeoutSession:
        def get(self, url, timeout):
            raise requests.Timeout("portal timed out")

    hydrator = PortalProfileHydrator(cache_db_path=tmp_path / "portal.db")
    hydrator._session = TimeoutSession()

    result = hydrator.fetch_profile(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
    )

    assert result["_fetch_status"] == "timeout"
    metrics = hydrator.get_metrics_snapshot()
    assert (metrics["timeout"], metrics["total"]) == (1, 1)


def test_fetch_profile_reports_parser_missing_for_unreadable_200_page(tmp_path):
    class ChangedMarkupSession:
        def get(self, url, timeout):
            return SimpleNamespace(status_code=200, text="<html><body>changed</body></html>")

    hydrator = PortalProfileHydrator(cache_db_path=tmp_path / "portal.db")
    hydrator._session = ChangedMarkupSession()

    result = hydrator.fetch_profile(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
    )

    assert result["_fetch_status"] == "parser_missing"
    metrics = hydrator.get_metrics_snapshot()
    assert (metrics["parser_missing"], metrics["total"]) == (1, 1)


def test_fetch_profile_reports_parser_missing_for_unknown_structured_shape(tmp_path):
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{"newPortalShape":{"rooms":4}}}}'
        "</script>"
    )

    class NewShapeSession:
        def get(self, url, timeout):
            return SimpleNamespace(status_code=200, text=html)

    hydrator = PortalProfileHydrator(cache_db_path=tmp_path / "portal.db")
    hydrator._session = NewShapeSession()

    result = hydrator.fetch_profile(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
    )

    assert result["_fetch_status"] == "parser_missing"
    metrics = hydrator.get_metrics_snapshot()
    assert (metrics["parser_missing"], metrics["total"]) == (1, 1)


def test_fetch_profile_reports_address_mismatch(tmp_path):
    next_data = {
        "props": {
            "pageProps": {
                "detailPage": {
                    "address": {
                        "streetNumber": "99",
                        "streetName": "Wrong",
                        "streetType": "Road",
                        "suburb": "Wantirna",
                        "state": "VIC",
                        "postcode": "3152",
                    },
                    "bedrooms": 4,
                    "bathrooms": 2,
                }
            }
        }
    }
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'

    class WrongAddressSession:
        def get(self, url, timeout):
            return SimpleNamespace(status_code=200, text=html)

    hydrator = PortalProfileHydrator(cache_db_path=tmp_path / "portal.db")
    hydrator._session = WrongAddressSession()

    result = hydrator.fetch_profile(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
    )

    assert result["_fetch_status"] == "address_mismatch"
    metrics = hydrator.get_metrics_snapshot()
    assert (metrics["address_mismatch"], metrics["total"]) == (1, 1)


def test_fetch_profile_rejects_cached_rooms_without_valid_evidence(tmp_path):
    hydrator = PortalProfileHydrator(
        cache_db_path=tmp_path / "portal.db",
        offline_only=True,
    )
    hydrator.cache.set(
        "3-napier-close-wantirna-vic-3152",
        {
            "bed": 4,
            "bath": 2,
            "fieldEvidence": {
                "bed": {"verificationStatus": "verified", "evidenceHash": "wrong"},
                "bath": {"verificationStatus": "verified", "evidenceHash": "wrong"},
            },
        },
        is_not_found=False,
        source="LEGACY_MIGRATION",
    )

    result = hydrator.fetch_profile(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
    )

    assert result["_fetch_status"] == "evidence_rejected"
    assert hydrator.get_metrics_snapshot() == {"evidence_rejected": 1, "total": 1}


def test_fetch_profile_returns_verified_rooms_and_success_metric(tmp_path):
    next_data = {
        "props": {
            "pageProps": {
                "detailPage": {
                    "address": {
                        "streetNumber": "3",
                        "streetName": "Napier",
                        "streetType": "Close",
                        "suburb": "Wantirna",
                        "state": "VIC",
                        "postcode": "3152",
                    },
                    "bedrooms": 4,
                    "bathrooms": 2,
                    "landSize": 831,
                }
            }
        }
    }
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'

    class MatchingSession:
        def get(self, url, timeout):
            return SimpleNamespace(status_code=200, text=html)

    hydrator = PortalProfileHydrator(cache_db_path=tmp_path / "portal.db")
    hydrator._session = MatchingSession()

    result = hydrator.fetch_profile(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
    )

    assert (result["bed"], result["bath"], result["totalArea"]) == (4, 2, 831.0)
    assert result["fieldEvidence"]["bed"]["sourceUrl"].startswith("https://")
    assert result["_fetch_status"] == "success"
    metrics = hydrator.get_metrics_snapshot()
    assert (metrics["success"], metrics["total"]) == (1, 1)


def test_fetch_profile_can_verify_rooms_from_structured_202_response(tmp_path):
    next_data = {
        "props": {
            "pageProps": {
                "detailPage": {
                    "address": {
                        "streetNumber": "3",
                        "streetName": "Napier",
                        "streetType": "Close",
                        "suburb": "Wantirna",
                        "state": "VIC",
                        "postcode": "3152",
                    },
                    "bedrooms": 4,
                    "bathrooms": 2,
                }
            }
        }
    }
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'

    class AcceptedSession:
        def get(self, url, timeout):
            return SimpleNamespace(status_code=202, text=html)

    hydrator = PortalProfileHydrator(cache_db_path=tmp_path / "portal.db")
    hydrator._session = AcceptedSession()

    result = hydrator.fetch_profile(
        street="3 Napier Close",
        suburb="Wantirna",
        state="VIC",
        postcode="3152",
    )

    assert (result["bed"], result["bath"], result["_fetch_status"]) == (4, 2, "success")


def test_fetch_profile_honours_an_explicit_network_candidate_limit(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        portal_module,
        "get_candidate_slugs",
        lambda *_args, **_kwargs: ["first-slug", "second-slug"],
    )
    requested_urls = []
    hydrator = PortalProfileHydrator(
        cache_db_path=tmp_path / "portal.db",
        max_network_candidates=1,
    )
    hydrator.resolve_strata_details = lambda *_args, **_kwargs: {}
    hydrator._http_get = (
        lambda url: requested_urls.append(url) or ("<html></html>", 202)
    )

    hydrator.fetch_profile("3 Napier Close", "Wantirna", "VIC", "3152")

    assert len(requested_urls) == 3
    assert all("first-slug" in url for url in requested_urls)
