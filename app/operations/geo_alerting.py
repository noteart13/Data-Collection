#!/usr/bin/env python3
"""
Geospatial Geofencing & Real-Time Alerting Monitor for Australian Real Estate.
Monitors geographic areas (radius circles or custom polygon boundaries),
matches incoming listings or CDC price/status changes against buyer/investor criteria,
and triggers GeofenceAlertEvents.
"""

import math
import uuid
import logging
from typing import List, Dict, Any, Optional, Tuple, Union
from datetime import datetime

from app.operations.cadastral_spatial import ray_casting_point_in_polygon

logger = logging.getLogger("geo_alerting")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * (math.sin(dl / 2.0) ** 2)
    return r * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


class GeofenceRule:
    """
    Defines a geographic surveillance area with property filtering criteria.
    Can be a circular radius or a custom polygonal boundary (e.g. school catchment / council boundary).
    """

    def __init__(
        self,
        name: str,
        center_lat: Optional[float] = None,
        center_lon: Optional[float] = None,
        radius_km: Optional[float] = None,
        polygon_coords: Optional[List[Any]] = None,
        min_bed: Optional[int] = None,
        max_price: Optional[float] = None,
        property_category: Optional[str] = None,
        min_location_score: Optional[float] = None,
        rule_id: Optional[str] = None
    ):
        self.rule_id = rule_id or str(uuid.uuid4())
        self.name = name
        self.center_lat = center_lat
        self.center_lon = center_lon
        self.radius_km = radius_km
        self.polygon_coords = polygon_coords
        self.min_bed = min_bed
        self.max_price = max_price
        self.property_category = property_category.lower() if property_category else None
        self.min_location_score = min_location_score

    def matches_spatial(self, lat: float, lon: float) -> Tuple[bool, Optional[float]]:
        """
        Evaluates whether coordinates fall inside the circular radius or polygon.
        Returns: (is_inside, distance_to_center_km)
        """
        # 1. Circle radius check
        if self.center_lat is not None and self.center_lon is not None and self.radius_km is not None:
            dist = haversine_distance_km(lat, lon, self.center_lat, self.center_lon)
            if dist <= self.radius_km:
                return True, round(dist, 2)
            return False, round(dist, 2)

        # 2. Polygon boundary check
        if self.polygon_coords and len(self.polygon_coords) >= 3:
            inside = ray_casting_point_in_polygon(lat, lon, self.polygon_coords)
            return inside, 0.0

        return False, None

    def matches_criteria(self, property_data: Dict[str, Any]) -> bool:
        """Evaluates price, bedroom, category, and location score criteria."""
        # Bed filter
        if self.min_bed is not None:
            bed = property_data.get("bed") or property_data.get("bedrooms")
            if bed is None or int(bed) < self.min_bed:
                return False

        # Price filter
        if self.max_price is not None:
            price = property_data.get("price") or property_data.get("expectedPrice")
            if price is not None and float(price) > self.max_price:
                return False

        # Category filter
        if self.property_category:
            p_cat = (property_data.get("property_category") or property_data.get("propertyType") or "").lower()
            if self.property_category not in p_cat:
                return False

        # Location score filter
        if self.min_location_score is not None:
            l_scores = property_data.get("location_scores") or {}
            score = l_scores.get("overall") or 0.0
            if float(score) < self.min_location_score:
                return False

        return True


class GeofenceAlertEvent:
    """Structured alert record fired when a property matches a GeofenceRule."""

    def __init__(
        self,
        rule_name: str,
        property_id: str,
        address: str,
        suburb: str,
        state: str,
        price: Optional[float],
        event_type: str,
        location_score: Optional[float],
        distance_km: Optional[float],
        rule_id: str
    ):
        self.alert_id = str(uuid.uuid4())
        self.rule_id = rule_id
        self.rule_name = rule_name
        self.property_id = property_id
        self.address = address
        self.suburb = suburb
        self.state = state
        self.price = price
        self.event_type = event_type
        self.location_score = location_score
        self.distance_km = distance_km
        self.timestamp = datetime.utcnow().isoformat() + "Z"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "property_id": self.property_id,
            "address": self.address,
            "suburb": self.suburb,
            "state": self.state,
            "price": self.price,
            "event_type": self.event_type,
            "location_score": self.location_score,
            "distance_km": self.distance_km,
            "timestamp": self.timestamp
        }


class GeospatialAlertMonitor:
    """
    Manages geofencing surveillance rules and evaluates real estate listings in real-time.
    """

    def __init__(self):
        self.rules: List[GeofenceRule] = []

    def add_rule(self, rule: GeofenceRule):
        self.rules.append(rule)
        logger.info(f"Registered Geofence Rule: '{rule.name}' (ID: {rule.rule_id})")

    def evaluate_property(
        self,
        property_data: Dict[str, Any],
        event_type: str = "NEW_LISTING"
    ) -> List[GeofenceAlertEvent]:
        """
        Evaluates a property against all registered rules.
        Returns list of matching GeofenceAlertEvents.
        """
        coords = property_data.get("coordinates") or {}
        lat = coords.get("lat") or property_data.get("latitude") or property_data.get("lat")
        lon = coords.get("lon") or property_data.get("longitude") or property_data.get("lon")

        if lat is None or lon is None:
            return []

        lat_f = float(lat)
        lon_f = float(lon)

        triggered_alerts = []
        for rule in self.rules:
            is_inside, dist_km = rule.matches_spatial(lat_f, lon_f)
            if not is_inside:
                continue

            if not rule.matches_criteria(property_data):
                continue

            # Build alert event
            prop_id = str(property_data.get("id") or property_data.get("gnaf_pid") or property_data.get("slug") or uuid.uuid4())
            street = property_data.get("street") or property_data.get("title") or "Unnamed Address"
            suburb = property_data.get("suburb") or ""
            state = property_data.get("state") or "NSW"
            price = property_data.get("price") or property_data.get("expectedPrice")
            l_scores = property_data.get("location_scores") or {}
            loc_score = l_scores.get("overall")

            alert = GeofenceAlertEvent(
                rule_name=rule.name,
                property_id=prop_id,
                address=f"{street}, {suburb} {state}".strip(", "),
                suburb=suburb,
                state=state,
                price=float(price) if price else None,
                event_type=event_type,
                location_score=loc_score,
                distance_km=dist_km,
                rule_id=rule.rule_id
            )
            triggered_alerts.append(alert)
            logger.info(f"Geofence Alert TRIGGERED: [{rule.name}] -> {alert.address} (${alert.price:,} AUD)")

        return triggered_alerts
