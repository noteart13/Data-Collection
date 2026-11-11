from types import SimpleNamespace

from app.operations.property_evidence import evidence_matches_value, make_evidence
from app.operations.qdrant_property_source import SOURCE_FIELDS, _source_value
from app.operations.qdrant_property_source import QdrantPropertySourceAdapter


class FakeSourceClient:
    def __init__(self, points):
        self.points = points

    def scroll(self, **kwargs):
        return self.points, None


def test_exact_source_match_returns_verified_fields_without_images():
    source_payload = {
        "street": "3 Napier Close",
        "suburb": "Wantirna",
        "state": "VIC",
        "postcode": "3152",
        "bed": 4,
        "bath": 2,
        "area": {"totalArea": 831, "unit": "sqM"},
        "expectedPrice": 1095000,
        "description": "A real scraped property description.",
        "features": {"garage": 2},
        "historysale": [{"soldDate": "2026-04-29", "soldPrice": 1095000}],
        "images": [{"url": "https://images.example/property.jpg"}],
        "realestate_url": (
            "https://www.property.com.au/vic/wantirna-3152/"
            "napier-close/3-pid-3735391/"
        ),
    }
    # Explicit fixture evidence is required; a bare URL is not verification.
    source_payload["fieldProvenance"] = {}
    for key, (field_path, method, status) in SOURCE_FIELDS.items():
        value = _source_value(source_payload, key)
        if value is None:
            continue
        source_payload["fieldProvenance"][field_path] = make_evidence(
            value=value,
            source_name="TEST_FIXTURE",
            source_url=source_payload["realestate_url"],
            source_record_id="source-point-1",
            source_field=key,
            method=method,
            verification_status=status,
            area_type="landArea" if key == "totalArea" else None,
        )
    client = FakeSourceClient([SimpleNamespace(id="source-point-1", payload=source_payload)])
    adapter = QdrantPropertySourceAdapter(
        client=client,
        collections=("PropertySoldV2",),
        state="VIC",
        include_images=False,
    )

    result = adapter.lookup("3 Napier Close", "Wantirna", "VIC", "3152")

    assert (result["bed"], result["bath"], result["totalArea"]) == (4, 2, 831)
    assert "images" not in result
    assert result["realestate_url"].startswith("https://www.property.com.au/")
    assert evidence_matches_value(4, result["fieldEvidence"]["bed"])
    assert result["fieldEvidence"]["bed"]["sourceRecordId"] == "source-point-1"
    assert adapter.get_metrics_snapshot()["room_matches"] == 1
    assert adapter.lookup("3 Napier Close", "Wantirna", "VIC", "9999") == {}
