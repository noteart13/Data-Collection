import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.operations.portal_profile_hydrator import (
    PortalProfileHydrator,
    get_candidate_slugs,
    normalize_address_slug
)

def test_building_range_candidate_slugs():
    street = "6/8 Mindarie Street"
    suburb = "Lane Cove North"
    state = "NSW"
    postcode = "2066"
    building_range = "8-14 Mindarie Street"

    slugs = get_candidate_slugs(street, suburb, state, postcode, building_range=building_range)
    print("Generated slugs:", slugs)

    assert "6-8-14-mindarie-street-lane-cove-north-nsw-2066" in slugs, "Must contain full building-range slug"
    assert "6-8-14-mindarie-st-lane-cove-north-nsw-2066" in slugs, "Must contain abbreviated street building-range slug"

def test_sibling_strata_resolution_does_not_copy_unit_fields(tmp_path):
    hydrator = PortalProfileHydrator(offline_only=True, cache_db_path=tmp_path / "portal.db")
    hydrator.cache.set_building_strata(
        "5/8 mindarie_lane cove north_nsw",
        {
            "strataPlan": "SP106542",
            "lotNumber": 5,
            "buildingStreetRange": "8-14 Mindarie Street",
            "totalLotsInPlan": 42,
            "unitEntitlementArea": 87,
            "lastSoldPrice": 1200000,
            "source": "StrataChecks",
            "sourceUrl": "https://stratachecks.com.au/plan/106542",
        },
    )
    strata = hydrator.resolve_strata_details("6/8 Mindarie Street", "Lane Cove North", "NSW")

    assert strata.get("strataPlan") == "SP106542", "Must resolve to strata plan SP106542"
    assert strata.get("lotNumber") is None
    assert strata.get("buildingStreetRange") == "8-14 Mindarie Street", "Must resolve building range 8-14 Mindarie Street"
    assert "unitEntitlementArea" not in strata
    assert "lastSoldPrice" not in strata

if __name__ == "__main__":
    test_building_range_candidate_slugs()
    test_sibling_strata_resolution()
    print("ALL BUILDING RANGE & STRATA RESOLUTION TESTS PASSED 100%!")
