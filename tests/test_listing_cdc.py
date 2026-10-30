"""
Unit tests for Real-Time Property Listing Change Data Capture (CDC) Engine.
"""

from app.operations.listing_cdc import ListingCDCEngine, CDCEvent


def test_price_extraction():
    engine = ListingCDCEngine()
    assert engine.extract_numeric_price("$1,250,000") == 1250000.0
    assert engine.extract_numeric_price("Offers Over $850,000") == 850000.0
    assert engine.extract_numeric_price("Contact Agent") is None
    assert engine.extract_numeric_price("$500 pw") is None  # < 10000 ignored as sale price
    print("test_price_extraction passed.")


def test_status_transition_detection():
    engine = ListingCDCEngine(now_iso="2026-10-30T10:00:00Z")

    existing_payload = {
        "title": "15 Elizabeth Bay Rd, Elizabeth Bay NSW 2011",
        "status": "for_sale",
        "expectedPrice": 2200000.0,
        "historysale": [],
    }

    scraped_sold = {
        "address": "15 Elizabeth Bay Rd, Elizabeth Bay NSW 2011",
        "status": "sold",
        "sold_price": 2150000.0,
        "sold_date": "2026-10-28",
        "agency": "Ray White Prestige",
    }

    updated_pl, events = engine.process_listing_update(
        point_id="point-123",
        existing_payload=existing_payload,
        scraped_data=scraped_sold
    )

    assert updated_pl["status"] == "sold"
    assert updated_pl["listingOption"] == "sold"
    assert len(updated_pl["historysale"]) == 1
    assert updated_pl["historysale"][0]["soldPrice"] == 2150000.0
    assert updated_pl["historysale"][0]["soldDate"] == "2026-10-28"

    event_types = [e.event_type for e in events]
    assert "STATUS_CHANGED" in event_types
    assert "SOLD_RECORDED" in event_types
    print("test_status_transition_detection passed.")


def test_price_reduction_detection():
    engine = ListingCDCEngine(now_iso="2026-10-30T10:00:00Z")

    existing_payload = {
        "title": "45 Ocean St, Bondi NSW 2026",
        "status": "for_sale",
        "expectedPrice": 3000000.0,
    }

    scraped_reduced = {
        "address": "45 Ocean St, Bondi NSW 2026",
        "status": "for_sale",
        "advertised_price": "$2,850,000 - Price Reduced!",
    }

    updated_pl, events = engine.process_listing_update(
        point_id="point-456",
        existing_payload=existing_payload,
        scraped_data=scraped_reduced
    )

    assert updated_pl["expectedPrice"] == 2850000.0
    assert "price_change_history" in updated_pl
    assert len(updated_pl["price_change_history"]) == 1

    history = updated_pl["price_change_history"][0]
    assert history["old_price"] == 3000000.0
    assert history["new_price"] == 2850000.0
    assert history["delta"] == -150000.0
    assert history["pct_change"] == -5.0

    event_types = [e.event_type for e in events]
    assert "PRICE_REDUCED" in event_types
    print("test_price_reduction_detection passed.")


if __name__ == "__main__":
    test_price_extraction()
    test_status_transition_detection()
    test_price_reduction_detection()
    print("All ListingCDCEngine tests passed successfully!")
