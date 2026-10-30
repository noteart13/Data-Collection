#!/usr/bin/env python3
"""
Unit tests for Room Scene Classifier & Auto-Tagging Engine.
Validates classification of photo URLs, portal caption matching,
gallery processing, and in-place payload enrichment.
"""

import pytest
from app.operations.room_classifier import RoomClassifier, ROOM_TAXONOMY


def test_classify_individual_urls():
    clf = RoomClassifier()

    # Floorplan detection
    assert clf.classify_image_url("https://media.domain.com.au/p/floorplan-1.jpg") == "floorplan"
    assert clf.classify_image_url("https://media.rea.com.au/p/architectural_layout_2d.png") == "floorplan"

    # Kitchen
    assert clf.classify_image_url("https://media.domain.com.au/p/kitchen-island-miele.jpg") == "kitchen"

    # Bathroom / Ensuite
    assert clf.classify_image_url("https://media.domain.com.au/p/master-ensuite-bath.jpg") == "bathroom"

    # Bedroom
    assert clf.classify_image_url("https://media.domain.com.au/p/bedroom-wardrobe.jpg") == "bedroom"

    # Backyard / Pool
    assert clf.classify_image_url("https://media.domain.com.au/p/swimming-pool-alfresco.jpg") == "backyard"

    # Balcony
    assert clf.classify_image_url("https://media.domain.com.au/p/terrace-balcony-view.jpg") == "balcony"

    # Frontview (Hero at index 0)
    assert clf.classify_image_url("https://media.domain.com.au/p/image_001.jpg", index=0) == "frontview"
    assert clf.classify_image_url("https://media.domain.com.au/p/facade-streetscape.jpg", index=3) == "frontview"


def test_classify_with_caption():
    clf = RoomClassifier()
    # Uninformative URL but explicit caption
    url = "https://cdn.photos.com/p/88472910.jpg"
    cat = clf.classify_image_url(url, caption="Stunning designer kitchen with stone benches", index=2)
    assert cat == "kitchen"

    cat_fp = clf.classify_image_url(url, caption="2D Architectural site plan and schematic", index=4)
    assert cat_fp == "floorplan"


def test_classify_gallery():
    clf = RoomClassifier()
    gallery = [
        "https://cdn.photos.com/p/facade_front.jpg",
        "https://cdn.photos.com/p/kitchen_01.jpg",
        "https://cdn.photos.com/p/kitchen_02.jpg",
        "https://cdn.photos.com/p/bathroom_ensuite.jpg",
        "https://cdn.photos.com/p/bedroom_master.jpg",
        "https://cdn.photos.com/p/bedroom_guest.jpg",
        "https://cdn.photos.com/p/swimming_pool_garden.jpg",
        "https://cdn.photos.com/p/floorplan.pdf.jpg"
    ]

    res = clf.classify_gallery(gallery)
    assert res["has_floorplan"] is True
    assert res["primary_photo"] == "https://cdn.photos.com/p/facade_front.jpg"
    assert res["total_photos"] == 8

    counts = res["room_counts_detected"]
    assert counts.get("kitchen") == 2
    assert counts.get("bedroom") == 2
    assert counts.get("bathroom") == 1
    assert counts.get("backyard") == 1
    assert counts.get("floorplan") == 1


def test_enrich_property_payload():
    clf = RoomClassifier()
    payload = {
        "street": "100 George Street",
        "suburb": "Sydney",
        "state": "NSW",
        "photos": [
            "https://cdn.photos.com/hero_front.jpg",
            "https://cdn.photos.com/p_kitchen.jpg",
            "https://cdn.photos.com/p_bath.jpg",
            "https://cdn.photos.com/floorplan_1.jpg"
        ]
    }

    enriched = clf.enrich_property_payload(payload)
    assert "tagged_photos" in enriched
    assert "room_counts_detected" in enriched
    assert enriched["has_floorplan"] is True
    assert enriched["primary_photo"] == "https://cdn.photos.com/hero_front.jpg"
    assert len(enriched["tagged_photos"]["kitchen"]) == 1
    assert len(enriched["tagged_photos"]["bathroom"]) == 1
    assert len(enriched["tagged_photos"]["floorplan"]) == 1
