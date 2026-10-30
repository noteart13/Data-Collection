#!/usr/bin/env python3
"""
Room Scene Classifier & Auto-Tagging Engine for Australian Real Estate.
Classifies listing photo galleries into structured room categories:
frontview, kitchen, bathroom, bedroom, living_room, dining, backyard, balcony, floorplan.
Enriches property payloads in Qdrant with tagged_photos and room statistics.
"""

import os
import re
import logging
from typing import List, Dict, Any, Optional, Tuple, Set

logger = logging.getLogger("room_classifier")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Standard Australian Real Estate Photo Categories
ROOM_TAXONOMY: List[str] = [
    "frontview",
    "living_room",
    "kitchen",
    "bathroom",
    "bedroom",
    "dining",
    "backyard",
    "balcony",
    "floorplan",
    "other"
]

# Regex patterns matching room types in URL slugs, filenames, or portal caption metadata
CATEGORY_PATTERNS: Dict[str, re.Pattern] = {
    "floorplan": re.compile(
        r"\b(floorplan|floor plan|floor_plan|siteplan|site plan|site_plan|schematic|layout|cadastral|blueprint)\b",
        re.IGNORECASE
    ),
    "frontview": re.compile(
        r"\b(front|facade|façade|exterior|elevation|streetview|street view|hero|entrance|entry)\b",
        re.IGNORECASE
    ),
    "kitchen": re.compile(
        r"\b(kitchen|pantry|scullery|island bench|cooktop|oven|benches)\b",
        re.IGNORECASE
    ),
    "bathroom": re.compile(
        r"\b(bathroom|bath|ensuite|en suite|powder room|vanity|shower|tub)\b",
        re.IGNORECASE
    ),
    "bedroom": re.compile(
        r"\b(bedroom|bed|master bed|wardrobe|walk in|nursery|dorm)\b",
        re.IGNORECASE
    ),
    "living_room": re.compile(
        r"\b(living|lounge|family room|sitting room|rumpus|theatre|media room)\b",
        re.IGNORECASE
    ),
    "dining": re.compile(
        r"\b(dining|meals|banquette|dining room)\b",
        re.IGNORECASE
    ),
    "backyard": re.compile(
        r"\b(backyard|back yard|garden|lawn|pool|swimming pool|patio|alfresco|deck|courtyard|shed|pergola)\b",
        re.IGNORECASE
    ),
    "balcony": re.compile(
        r"\b(balcony|verandah|veranda|terrace|decking|porch)\b",
        re.IGNORECASE
    ),
}


class RoomClassifier:
    """
    Automated Room & Scene Classifier for real estate listing images.
    Combines filename tokens, portal image captions, and URL structures.
    """

    def __init__(self):
        self.categories = ROOM_TAXONOMY

    def classify_image_url(self, url: str, caption: Optional[str] = None, index: int = 0) -> str:
        """
        Classifies an individual image into one of the standard room categories.
        Uses image url, caption metadata, and position heuristic.
        """
        if not url:
            return "other"

        combined_text = f"{url} {caption or ''}".lower()
        # Replace URL and file delimiters (-, _, ., /, \) with spaces so word boundary \b works reliably
        normalized_text = re.sub(r"[\-_\.\/\\?&=#]", " ", combined_text)

        # 1. Floorplan check (highest priority: floorplans must never be mixed with rooms)
        if CATEGORY_PATTERNS["floorplan"].search(normalized_text):
            return "floorplan"

        # 2. Check specific interior & exterior room categories
        for cat in ["kitchen", "bathroom", "bedroom", "living_room", "backyard", "balcony", "dining"]:
            pattern = CATEGORY_PATTERNS[cat]
            if pattern.search(normalized_text):
                return cat

        # 3. Check frontview / facade
        if CATEGORY_PATTERNS["frontview"].search(normalized_text):
            return "frontview"

        # 4. Position-based heuristics:
        # In Australian portals (Domain/Realestate), index 0 is nearly always the hero/facade/frontview
        if index == 0:
            return "frontview"

        return "other"

    def classify_gallery(
        self,
        photo_items: List[Any]
    ) -> Dict[str, Any]:
        """
        Processes a full list of photos (URLs or dicts with url/caption) for a property.
        Returns:
            - tagged_photos: Dict[category, List[str]]
            - room_counts: Dict[category, int]
            - has_floorplan: bool
            - primary_photo: str
        """
        tagged: Dict[str, List[str]] = {cat: [] for cat in self.categories}
        all_urls: List[str] = []

        for idx, item in enumerate(photo_items):
            url = ""
            caption = None

            if isinstance(item, str):
                url = item.strip()
            elif isinstance(item, dict):
                url = item.get("url") or item.get("link") or item.get("src") or ""
                caption = item.get("caption") or item.get("title") or item.get("alt")

            if not url:
                continue

            all_urls.append(url)
            cat = self.classify_image_url(url, caption=caption, index=idx)
            tagged[cat].append(url)

        # Compute room photo statistics
        counts = {cat: len(urls) for cat, urls in tagged.items() if len(urls) > 0}
        has_fp = len(tagged["floorplan"]) > 0

        # Determine primary photo: prefer first frontview, then first image
        primary = None
        if tagged["frontview"]:
            primary = tagged["frontview"][0]
        elif all_urls:
            primary = all_urls[0]

        return {
            "tagged_photos": tagged,
            "room_counts_detected": counts,
            "has_floorplan": has_fp,
            "total_photos": len(all_urls),
            "primary_photo": primary
        }

    def enrich_property_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enriches a property dictionary with tagged photo metadata in-place.
        Compatible with Qdrant PropertyMaster / PropertySoldV2 schemas.
        """
        # Look for photo sources
        photos = payload.get("photos") or payload.get("photo_links") or payload.get("images") or []
        if not photos and payload.get("primary_photo"):
            photos = [payload["primary_photo"]]

        if not photos:
            return payload

        res = self.classify_gallery(photos)
        payload["tagged_photos"] = res["tagged_photos"]
        payload["room_counts_detected"] = res["room_counts_detected"]
        payload["has_floorplan"] = res["has_floorplan"]
        if not payload.get("primary_photo") and res["primary_photo"]:
            payload["primary_photo"] = res["primary_photo"]

        return payload
