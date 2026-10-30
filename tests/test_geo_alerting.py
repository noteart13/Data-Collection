#!/usr/bin/env python3
"""
Unit tests for Geospatial Geofencing & Automated Alerting Monitor.
Validates circular radius matching, polygon matching, multi-criteria filtering,
and alert dispatching.
"""

import pytest
from app.operations.geo_alerting import (
    GeofenceRule,
    GeofenceAlertEvent,
    GeospatialAlertMonitor
)


def test_circular_radius_geofence():
    # Sydney Martin Place center: (-33.8688, 151.2093), radius 5 km
    rule = GeofenceRule(
        name="Sydney Metro 5km",
        center_lat=-33.8688,
        center_lon=151.2093,
        radius_km=5.0
    )

    # Point ~1 km away (Surry Hills: -33.8821, 151.2145) -> Should be inside
    inside_surry, dist_surry = rule.matches_spatial(-33.8821, 151.2145)
    assert inside_surry is True
    assert dist_surry < 3.0

    # Point ~20 km away (Parramatta: -33.8150, 151.0011) -> Should be outside
    inside_parra, dist_parra = rule.matches_spatial(-33.8150, 151.0011)
    assert inside_parra is False
    assert dist_parra > 15.0


def test_polygon_geofence():
    # Custom polygon
    polygon = [
        (-33.870, 151.200),
        (-33.870, 151.220),
        (-33.860, 151.220),
        (-33.860, 151.200),
        (-33.870, 151.200)
    ]
    rule = GeofenceRule(
        name="CBD Quadrant Polygon",
        polygon_coords=polygon
    )

    inside, _ = rule.matches_spatial(-33.865, 151.210)
    assert inside is True

    outside, _ = rule.matches_spatial(-33.880, 151.210)
    assert outside is False


def test_criteria_filtering():
    rule = GeofenceRule(
        name="3-Bed Luxury Houses Under $2.5M",
        center_lat=-33.8688,
        center_lon=151.2093,
        radius_km=10.0,
        min_bed=3,
        max_price=2500000,
        property_category="house"
    )

    # Matching house
    p_good = {
        "bed": 3,
        "price": 2200000,
        "property_category": "house"
    }
    assert rule.matches_criteria(p_good) is True

    # Too expensive
    p_pricey = {
        "bed": 3,
        "price": 2800000,
        "property_category": "house"
    }
    assert rule.matches_criteria(p_pricey) is False

    # Too few bedrooms
    p_small = {
        "bed": 2,
        "price": 1800000,
        "property_category": "house"
    }
    assert rule.matches_criteria(p_small) is False

    # Wrong category
    p_unit = {
        "bed": 3,
        "price": 1500000,
        "property_category": "unit"
    }
    assert rule.matches_criteria(p_unit) is False


def test_alert_monitor_dispatch():
    monitor = GeospatialAlertMonitor()
    rule = GeofenceRule(
        name="Chatswood Investor Watchlist",
        center_lat=-33.7975,
        center_lon=151.1809,
        radius_km=3.0,
        max_price=1600000
    )
    monitor.add_rule(rule)

    listing_inside = {
        "id": "prop_123",
        "street": "22 Archer Street",
        "suburb": "Chatswood",
        "state": "NSW",
        "lat": -33.7960,
        "lon": 151.1820,
        "price": 1450000,
        "location_scores": {"overall": 9.1}
    }

    alerts = monitor.evaluate_property(listing_inside, event_type="PRICE_REDUCED")
    assert len(alerts) == 1
    a = alerts[0]
    assert isinstance(a, GeofenceAlertEvent)
    assert a.rule_name == "Chatswood Investor Watchlist"
    assert a.event_type == "PRICE_REDUCED"
    assert a.price == 1450000
    assert a.location_score == 9.1
    assert a.distance_km < 1.0
    assert a.alert_id is not None
