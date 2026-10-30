"""
Unit tests for Property Master Schema and Data Quality Engine.
"""

from app.schemas.property_schema import (
    PropertyMasterPayload,
    CoordinatesSchema,
    AreaSchema,
    PricingSchema,
    FeaturesSchema,
    DataQualityMetadata,
    calculate_data_quality_score,
    evaluate_data_quality,
)


def test_schema_instantiation():
    sample_data = {
        "street": "10 Martin Place",
        "suburb": "Sydney",
        "state": "NSW",
        "postcode": "2000",
        "city": "Sydney",
        "title": "10 Martin Place, Sydney NSW 2000",
        "propertyType": "Commercial",
        "expectedPrice": 15000000.0,
        "coordinates": {"lat": -33.8688, "lon": 151.2093},
        "area": {"totalArea": 450.0, "unit": "sqM"},
        "bed": 0,
        "bath": 4,
    }

    payload = PropertyMasterPayload(**sample_data)
    assert payload.street == "10 Martin Place"
    assert payload.suburb == "Sydney"
    assert payload.state == "NSW"
    assert payload.postcode == "2000"
    assert payload.coordinates.lat == -33.8688
    assert payload.coordinates.lon == 151.2093
    print("test_schema_instantiation passed.")


def test_state_and_postcode_normalization():
    sample_data = {
        "street": "123 Collins St",
        "suburb": "Melbourne",
        "state": "Victoria",  # Full name should normalize to VIC
        "postcode": 3000,     # Int should normalize to string '3000'
        "city": "Melbourne",
        "title": "123 Collins St, Melbourne VIC 3000",
    }

    payload = PropertyMasterPayload(**sample_data)
    assert payload.state == "VIC"
    assert payload.postcode == "3000"
    print("test_state_and_postcode_normalization passed.")


def test_data_quality_scoring():
    high_quality_payload = {
        "street": "50 Bridge St",
        "suburb": "Sydney",
        "state": "NSW",
        "postcode": "2000",
        "coordinates": {"lat": -33.86, "lon": 151.21},
        "bed": 3,
        "bath": 2,
        "area": {"totalArea": 120.0},
        "expectedPrice": 2500000.0,
        "historysale": [{"soldDate": "2024-05-10", "soldPrice": 2400000}],
        "valuationType": "Verified_Official_Recent_Sale",
        "description": "A magnificent apartment overlooking Sydney Harbour with premium finishes and private parking.",
        "constructionYear": "2018",
        "images": [{"url": "https://example.com/img1.jpg"}],
    }

    score = calculate_data_quality_score(high_quality_payload)
    assert score >= 90.0, f"Expected high quality score >= 90, got {score}"

    enriched = evaluate_data_quality(high_quality_payload)
    assert "data_quality" in enriched
    assert enriched["data_quality"]["data_quality_score"] >= 90.0
    assert enriched["data_quality"]["is_synthetic"] is False
    print("test_data_quality_scoring passed.")


def test_synthetic_data_flagging():
    synthetic_payload = {
        "street": "1/45 Generic Rd",
        "suburb": "Blacktown",
        "state": "NSW",
        "postcode": "2148",
        "valuationType": "Regional_Calibrated_AVM",
        "isAreaEstimated": True,
        "isRoomEstimated": True,
    }

    enriched = evaluate_data_quality(synthetic_payload)
    dq = enriched["data_quality"]
    assert dq["is_synthetic"] is True
    assert "expectedPrice" in dq["synthetic_attributes"]
    assert "area" in dq["synthetic_attributes"]
    assert "bed" in dq["synthetic_attributes"]
    print("test_synthetic_data_flagging passed.")


if __name__ == "__main__":
    test_schema_instantiation()
    test_state_and_postcode_normalization()
    test_data_quality_scoring()
    test_synthetic_data_flagging()
    print("All Property Master schema tests passed successfully!")
