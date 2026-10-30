#!/usr/bin/env python3
"""
Unit tests for Cadastral Parcel Geometry & Spatial Engine.
Validates Ray Casting point-in-polygon, Geodesic Shoelace land area calculation,
centroid, bounding box, and Australian zoning classifications.
"""

import math
import pytest
from app.operations.cadastral_spatial import (
    ray_casting_point_in_polygon,
    calculate_polygon_area_sqm,
    calculate_polygon_centroid,
    calculate_bounding_box,
    get_zoning_description,
    CadastralParcel,
    CadastralSpatialEngine
)


def test_ray_casting_point_in_polygon():
    # Square polygon around Sydney Martin Place: (-33.87, 151.20) to (-33.86, 151.22)
    poly = [
        (-33.870, 151.200),
        (-33.870, 151.220),
        (-33.860, 151.220),
        (-33.860, 151.200),
        (-33.870, 151.200)
    ]

    # Inside point
    assert ray_casting_point_in_polygon(-33.865, 151.210, poly) is True

    # Clearly outside points
    assert ray_casting_point_in_polygon(-33.850, 151.210, poly) is False  # North
    assert ray_casting_point_in_polygon(-33.880, 151.210, poly) is False  # South
    assert ray_casting_point_in_polygon(-33.865, 151.230, poly) is False  # East
    assert ray_casting_point_in_polygon(-33.865, 151.190, poly) is False  # West


def test_polygon_area_sqm():
    # Construct a rectangular lot of ~20m frontage x 25m depth = ~500 m2
    lat, lon = -33.8688, 151.2093
    # 25m depth in latitude deg: 25 / 111139 = ~0.000225
    # 20m width in longitude deg: 20 / (111139 * cos(-33.8688 deg)) = ~0.000216
    d_lat = 25.0 / 111139.0 / 2.0
    d_lon = 20.0 / (111139.0 * math.cos(math.radians(lat))) / 2.0

    rect_poly = [
        (lat - d_lat, lon - d_lon),
        (lat - d_lat, lon + d_lon),
        (lat + d_lat, lon + d_lon),
        (lat + d_lat, lon - d_lon),
        (lat - d_lat, lon - d_lon)
    ]

    calc_area = calculate_polygon_area_sqm(rect_poly)
    # Expected area is 500 m2 (+/- 5% tolerance due to curvature projection)
    assert 480.0 <= calc_area <= 520.0, f"Expected ~500 m2, got {calc_area}"


def test_polygon_centroid_and_bbox():
    poly = [
        (-33.870, 151.200),
        (-33.870, 151.220),
        (-33.860, 151.220),
        (-33.860, 151.200)
    ]
    c_lat, c_lon = calculate_polygon_centroid(poly)
    assert round(c_lat, 3) == -33.865
    assert round(c_lon, 3) == 151.210

    bbox = calculate_bounding_box(poly)
    assert bbox == [-33.87, 151.2, -33.86, 151.22]


def test_cadastral_parcel_and_zoning():
    coords = [
        (-33.8680, 151.2090),
        (-33.8680, 151.2095),
        (-33.8685, 151.2095),
        (-33.8685, 151.2090),
        (-33.8680, 151.2090)
    ]
    parcel = CadastralParcel(
        lot="15",
        plan="DP123456",
        coordinates=coords,
        zoning_code="R2",
        state="NSW"
    )

    assert parcel.lot == "15"
    assert parcel.plan == "DP123456"
    assert parcel.zoning_code == "R2"
    assert "Low Density Residential" in parcel.zoning_description
    assert parcel.land_area_sqm > 50.0

    # GeoJSON Feature export
    feat = parcel.to_geojson_feature()
    assert feat["type"] == "Feature"
    assert feat["geometry"]["type"] == "Polygon"
    assert feat["properties"]["lot"] == "15"
    assert feat["properties"]["plan"] == "DP123456"

    # Qdrant Payload format
    payload = parcel.to_qdrant_payload()
    assert payload["cadastral_key"] == "15//DP123456"
    assert payload["zoning_code"] == "R2"
    assert "calculated_area_sqm" in payload


def test_synthesize_parcel_for_address():
    engine = CadastralSpatialEngine()
    parcel = engine.synthesize_parcel_for_address(
        street="10 Bridge Street",
        suburb="Sydney",
        state="NSW",
        lat=-33.8638,
        lon=151.2078,
        area_sqm=650.0,
        zoning_code="B4"
    )

    assert parcel.state == "NSW"
    assert parcel.zoning_code == "B4"
    assert "Mixed Use" in parcel.zoning_description
    # Land area should be within 5% of target 650 m2
    assert 620.0 <= parcel.land_area_sqm <= 680.0
    # Address point should be inside synthesized parcel polygon
    assert parcel.contains_point(-33.8638, 151.2078) is True
