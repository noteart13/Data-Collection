#!/usr/bin/env python3
"""
Unit tests for Location Score & School Catchment Zones Engine.
Validates multi-dimensional scoring (Schools, Transit, Amenities, Overall),
proximity lookups, and payload enrichment.
"""

import pytest
from app.operations.location_score_engine import LocationScoreEngine, haversine_km


def test_haversine_distance():
    # Sydney Town Hall to Martin Place (~800m)
    dist = haversine_km(-33.8732, 151.2068, -33.8688, 151.2093)
    assert 0.5 <= dist <= 1.0


def test_location_scores_inner_sydney():
    engine = LocationScoreEngine()
    # Coordinates in Chatswood near Chatswood Station & Public School
    lat, lon = -33.7975, 151.1809
    scores = engine.calculate_scores(lat, lon, state="NSW", suburb="Chatswood")

    assert 0.0 <= scores["overall"] <= 10.0
    assert 0.0 <= scores["schools"] <= 10.0
    assert 0.0 <= scores["transit"] <= 10.0
    assert 0.0 <= scores["amenities"] <= 10.0

    # Chatswood Station is right there (< 100m) -> Transit score should be very high (>= 9.0)
    assert scores["transit"] >= 9.0
    assert scores["nearest_station"]["distance_km"] < 0.20

    # Verify overall formula: round(0.35 * sch + 0.35 * tr + 0.30 * am, 1)
    expected_overall = round(0.35 * scores["schools"] + 0.35 * scores["transit"] + 0.30 * scores["amenities"], 1)
    assert abs(scores["overall"] - expected_overall) <= 0.15


def test_location_scores_canberra():
    engine = LocationScoreEngine()
    # Coordinates in Civic Canberra near Alinga St Light Rail
    lat, lon = -35.2798, 149.1293
    scores = engine.calculate_scores(lat, lon, state="ACT", suburb="City")

    assert scores["overall"] >= 8.0
    assert scores["transit"] >= 9.0
    assert scores["nearest_station"]["name"] == "Alinga Street Light Rail"
    assert scores["distance_to_cbd_km"] <= 1.0


def test_location_scores_outer_suburban():
    engine = LocationScoreEngine()
    # Coordinates in distant outskirts (e.g. 50 km from Sydney CBD)
    lat, lon = -33.6000, 150.6500
    scores = engine.calculate_scores(lat, lon, state="NSW", suburb="Rural Fringe")

    # In remote outskirts, distance to infrastructure increases, transit score drops
    assert scores["transit"] <= 6.5
    assert scores["distance_to_cbd_km"] > 40.0


def test_enrich_property_payload():
    engine = LocationScoreEngine()
    payload = {
        "street": "15 Victoria Avenue",
        "suburb": "Chatswood",
        "state": "NSW",
        "coordinates": {"lat": -33.7975, "lon": 151.1809}
    }

    enriched = engine.enrich_property_payload(payload)
    assert "location_scores" in enriched
    assert "overall" in enriched["location_scores"]
    assert "schools" in enriched["location_scores"]
    assert "transit" in enriched["location_scores"]
    assert "amenities" in enriched["location_scores"]
