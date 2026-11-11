from types import SimpleNamespace

from bulk_enrich_master import _process_point_chunk_trusted
from stream_gnaf_to_qdrant import build_property_payload


NOW = "2026-11-03T00:00:00+11:00"


def _gnaf_row():
    return {
        "ADDRESS_DETAIL_PID": "GANSW718225866",
        "FLAT_NUMBER": "602",
        "NUMBER_FIRST": "8",
        "NUMBER_LAST": "",
        "LOT_NUMBER": "",
        "STREET_LOCALITY_PID": "STREET1",
        "LOCALITY_PID": "LOCALITY1",
        "POSTCODE": "2065",
        "CONFIDENCE": "2",
        "PRIMARY_SECONDARY": "S",
    }


def test_gnaf_ingest_keeps_payload_shape_without_inventing_property_facts():
    _, payload = build_property_payload(
        _gnaf_row(),
        {"STREET1": "Duntroon Avenue"},
        {"LOCALITY1": "St Leonards"},
        {"GANSW718225866": (-33.82679777, 151.19502575)},
        "NSW",
        NOW,
    )

    assert payload["street"] == "602/8 Duntroon Avenue"
    assert payload["propertyType"] is None
    assert payload["constructionYear"] is None
    assert payload["architecturalStyle"] is None
    assert payload["structuralRemodelYear"] is None
    assert payload["features"]["garage"] is None
    assert payload["features"]["floorNo"] is None
    assert payload["features"]["basement"] is None
    assert payload["status"] == "unknown"
    assert payload["listingOption"] == "unknown"
    assert payload["pricing"] == {
        "waterBillPeriod": None,
        "councilBill": None,
        "pricingOptions": None,
        "authority": None,
        "priceIncludes": [],
    }


def test_trusted_run_repairs_known_gnaf_placeholders_without_changing_shape():
    point = SimpleNamespace(
        id="000005f3-baa2-57e9-b379-a44e74a87cae",
        payload={
            "street": "602/8 Duntroon Avenue",
            "suburb": "St Leonards",
            "state": "NSW",
            "postcode": "2065",
            "gnaf_pid": "GANSW718225866",
            "architecturalStyle": "None",
            "constructionYear": "N/A",
            "structuralRemodelYear": "N/A",
            "propertyType": "House",
            "features": {
                "garage": 0,
                "floorNo": 1,
                "indoorFeatures": [],
                "outdoorAmenities": [],
                "basement": "None",
                "floorCovering": [],
                "roof": [],
                "view": [],
                "appliances": [],
                "buildingAmenities": [],
                "displayAddress": "fullAddress",
            },
            "pricing": {
                "waterBillPeriod": "",
                "councilBill": "",
                "pricingOptions": "",
                "authority": "",
                "priceIncludes": [],
            },
            "status": "off_market",
            "listingOption": "off_market",
        },
    )
    engine = SimpleNamespace(
        portal_hydrator=None,
        property_source_adapter=None,
        property_browser_adapter=None,
        official_sales_adapter=None,
        portal_fields=frozenset(),
        no_images=True,
        preserve_payload_shape=True,
    )

    updates, _ = _process_point_chunk_trusted([point], engine, False, NOW)

    patch = updates[0]["payload"]
    assert set(patch).issubset(point.payload)
    assert patch["propertyType"] is None
    assert patch["constructionYear"] is None
    assert patch["architecturalStyle"] is None
    assert patch["structuralRemodelYear"] is None
    assert patch["features"]["garage"] is None
    assert patch["features"]["floorNo"] is None
    assert patch["features"]["basement"] is None
    assert patch["features"]["displayAddress"] == "fullAddress"
    assert patch["status"] == "unknown"
    assert patch["listingOption"] == "unknown"
    assert patch["pricing"]["waterBillPeriod"] is None
    assert patch["pricing"]["councilBill"] is None
    assert patch["pricing"]["priceIncludes"] == []

