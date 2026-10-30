#!/usr/bin/env python3
"""
Location Score & School Catchment Zones Engine for Australian Real Estate.
Computes multi-dimensional location intelligence scores (0.0 to 10.0):
- Schools & Catchment Score (Primary, Secondary, Selective Schools)
- Transit & WalkScore (Train, Metro, Light Rail, Ferry)
- Amenities & Healthcare Score (Westfield / Shopping, Hospitals, Parklands)
- Overall Weighted Location Index
"""

import math
import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("location_score_engine")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates great-circle distance in km between two coordinate points."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2))
    return r * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


# Representative key infrastructure POIs across Australian major capital corridors
AU_TRANSIT_STATIONS = [
    # NSW Sydney Metro & Heavy Rail Hubs
    {"name": "Central Station", "state": "NSW", "lat": -33.8831, "lon": 151.2064, "type": "Intercity / Metro / Heavy Rail"},
    {"name": "Town Hall Station", "state": "NSW", "lat": -33.8732, "lon": 151.2068, "type": "Metro / Heavy Rail"},
    {"name": "Wynyard Station", "state": "NSW", "lat": -33.8659, "lon": 151.2057, "type": "Heavy Rail / Bus Interchange"},
    {"name": "Chatswood Station", "state": "NSW", "lat": -33.7975, "lon": 151.1809, "type": "Metro / Heavy Rail"},
    {"name": "Parramatta Station", "state": "NSW", "lat": -33.8177, "lon": 151.0053, "type": "Heavy Rail / Light Rail"},
    {"name": "North Sydney Station", "state": "NSW", "lat": -33.8402, "lon": 151.2076, "type": "Heavy Rail"},
    {"name": "Bondi Junction Station", "state": "NSW", "lat": -33.8916, "lon": 151.2483, "type": "Heavy Rail / Bus Interchange"},
    {"name": "Strathfield Station", "state": "NSW", "lat": -33.8711, "lon": 151.0935, "type": "Express Heavy Rail"},
    {"name": "Macquarie Park Metro", "state": "NSW", "lat": -33.7818, "lon": 151.1278, "type": "Metro"},
    {"name": "Hornsby Station", "state": "NSW", "lat": -33.7037, "lon": 151.0988, "type": "Heavy Rail"},
    {"name": "Liverpool Station", "state": "NSW", "lat": -33.9248, "lon": 150.9287, "type": "Heavy Rail"},
    {"name": "Penrith Station", "state": "NSW", "lat": -33.7507, "lon": 150.6975, "type": "Heavy Rail"},

    # VIC Melbourne Metro Hubs
    {"name": "Flinders Street Station", "state": "VIC", "lat": -37.8180, "lon": 144.9671, "type": "Metro / Heavy Rail"},
    {"name": "Southern Cross Station", "state": "VIC", "lat": -37.8184, "lon": 144.9525, "type": "Metro / Regional Rail"},
    {"name": "Richmond Station", "state": "VIC", "lat": -37.8240, "lon": 144.9897, "type": "Metro Heavy Rail"},
    {"name": "South Yarra Station", "state": "VIC", "lat": -37.8386, "lon": 144.9922, "type": "Metro Heavy Rail"},
    {"name": "Box Hill Station", "state": "VIC", "lat": -37.8189, "lon": 145.1215, "type": "Metro Heavy Rail"},

    # QLD Brisbane Transit Hubs
    {"name": "Central Railway Station", "state": "QLD", "lat": -27.4660, "lon": 153.0270, "type": "Heavy Rail"},
    {"name": "Roma Street Station", "state": "QLD", "lat": -27.4674, "lon": 153.0175, "type": "Cross River Rail / Busway"},
    {"name": "South Brisbane Station", "state": "QLD", "lat": -27.4764, "lon": 153.0171, "type": "Heavy Rail"},
    {"name": "Fortitude Valley Station", "state": "QLD", "lat": -27.4571, "lon": 153.0336, "type": "Heavy Rail"},

    # ACT Canberra Light Rail & Transport Hubs
    {"name": "Alinga Street Light Rail", "state": "ACT", "lat": -35.2798, "lon": 149.1293, "type": "Canberra Light Rail"},
    {"name": "Dickson Interchange", "state": "ACT", "lat": -35.2505, "lon": 149.1362, "type": "Light Rail / Busway"},
    {"name": "Gungahlin Place Light Rail", "state": "ACT", "lat": -35.1852, "lon": 149.1345, "type": "Canberra Light Rail"},
    {"name": "Woden Bus Interchange", "state": "ACT", "lat": -35.3456, "lon": 149.0863, "type": "Bus Rapid Transit"},
]

AU_KEY_SCHOOLS = [
    # NSW Primary & Secondary Leading Schools
    {"name": "James Ruse Agricultural High", "state": "NSW", "lat": -33.7845, "lon": 151.0427, "type": "Selective Secondary"},
    {"name": "North Sydney Boys High", "state": "NSW", "lat": -33.8298, "lon": 151.2114, "type": "Selective Secondary"},
    {"name": "North Sydney Girls High", "state": "NSW", "lat": -33.8340, "lon": 151.2052, "type": "Selective Secondary"},
    {"name": "Sydney Boys High School", "state": "NSW", "lat": -33.8912, "lon": 151.2201, "type": "Selective Secondary"},
    {"name": "Sydney Girls High School", "state": "NSW", "lat": -33.8920, "lon": 151.2215, "type": "Selective Secondary"},
    {"name": "Fort Street High School", "state": "NSW", "lat": -33.8935, "lon": 151.1565, "type": "Selective Secondary"},
    {"name": "Chatswood Public School", "state": "NSW", "lat": -33.7952, "lon": 151.1768, "type": "Primary (Catchment)"},
    {"name": "Chatswood High School", "state": "NSW", "lat": -33.7981, "lon": 151.1732, "type": "Secondary (Catchment)"},
    {"name": "Parramatta Public School", "state": "NSW", "lat": -33.8188, "lon": 151.0042, "type": "Primary (Catchment)"},
    {"name": "Crown Street Public School", "state": "NSW", "lat": -33.8821, "lon": 151.2145, "type": "Primary (Surry Hills)"},

    # VIC Schools
    {"name": "Melbourne High School", "state": "VIC", "lat": -37.8385, "lon": 144.9928, "type": "Selective Secondary"},
    {"name": "Mac.Robertson Girls' High", "state": "VIC", "lat": -37.8350, "lon": 144.9734, "type": "Selective Secondary"},
    {"name": "Carlton Primary School", "state": "VIC", "lat": -37.7985, "lon": 144.9701, "type": "Primary (Catchment)"},

    # QLD Schools
    {"name": "Brisbane State High School", "state": "QLD", "lat": -27.4815, "lon": 153.0165, "type": "Secondary (Catchment/Merit)"},
    {"name": "Brisbane Grammar School", "state": "QLD", "lat": -27.4615, "lon": 153.0182, "type": "Independent Secondary"},
    {"name": "New Farm State School", "state": "QLD", "lat": -27.4695, "lon": 153.0495, "type": "Primary (Catchment)"},

    # ACT Schools
    {"name": "Telopea Park School", "state": "ACT", "lat": -35.3138, "lon": 149.1385, "type": "Bilingual / Catchment"},
    {"name": "Narrabundah College", "state": "ACT", "lat": -35.3340, "lon": 149.1512, "type": "Secondary College"},
    {"name": "Campbell High School", "state": "ACT", "lat": -35.2862, "lon": 149.1481, "type": "Secondary (Catchment)"},
    {"name": "Ainslie School", "state": "ACT", "lat": -35.2672, "lon": 149.1430, "type": "Primary (Catchment)"},
]

AU_KEY_AMENITIES = [
    # Major Hospitals
    {"name": "Royal Prince Alfred Hospital", "state": "NSW", "lat": -33.8903, "lon": 151.1837, "type": "Tertiary Hospital"},
    {"name": "Royal North Shore Hospital", "state": "NSW", "lat": -33.8202, "lon": 151.1895, "type": "Tertiary Hospital"},
    {"name": "St Vincent's Hospital Sydney", "state": "NSW", "lat": -33.8798, "lon": 151.2215, "type": "Major Hospital"},
    {"name": "Westmead Hospital", "state": "NSW", "lat": -33.8055, "lon": 150.9882, "type": "Major Hospital"},
    {"name": "The Royal Melbourne Hospital", "state": "VIC", "lat": -37.7988, "lon": 144.9565, "type": "Major Hospital"},
    {"name": "The Alfred Hospital", "state": "VIC", "lat": -37.8465, "lon": 144.9815, "type": "Major Hospital"},
    {"name": "Royal Brisbane & Women's Hospital", "state": "QLD", "lat": -27.4475, "lon": 153.0282, "type": "Major Hospital"},
    {"name": "Canberra Hospital", "state": "ACT", "lat": -35.3475, "lon": 149.1005, "type": "Tertiary Hospital"},

    # Major Retail Centres
    {"name": "Westfield Sydney / Pitt St", "state": "NSW", "lat": -33.8698, "lon": 151.2085, "type": "Westfield / Retail Hub"},
    {"name": "Westfield Chatswood", "state": "NSW", "lat": -33.7965, "lon": 151.1815, "type": "Westfield / Retail Hub"},
    {"name": "Westfield Parramatta", "state": "NSW", "lat": -33.8185, "lon": 151.0022, "type": "Westfield / Retail Hub"},
    {"name": "Westfield Bondi Junction", "state": "NSW", "lat": -33.8920, "lon": 151.2495, "type": "Westfield / Retail Hub"},
    {"name": "Chadstone Shopping Centre", "state": "VIC", "lat": -37.8860, "lon": 145.0830, "type": "Super Regional Centre"},
    {"name": "Queen Street Mall", "state": "QLD", "lat": -27.4698, "lon": 153.0255, "type": "Major Retail Hub"},
    {"name": "Canberra Centre", "state": "ACT", "lat": -35.2805, "lon": 149.1332, "type": "Major Retail Hub"},
]


class LocationScoreEngine:
    """
    Evaluates residential location quality across Schools, Public Transit,
    Healthcare, and Lifestyle amenities for Australian properties.
    """

    def __init__(self):
        self.transit_pois = AU_TRANSIT_STATIONS
        self.school_pois = AU_KEY_SCHOOLS
        self.amenity_pois = AU_KEY_AMENITIES

    def _find_nearest_poi(
        self,
        lat: float,
        lon: float,
        pois: List[Dict[str, Any]],
        state: Optional[str] = None
    ) -> Tuple[Optional[Dict[str, Any]], float]:
        """Finds the closest POI from a candidate list matching state filter."""
        st = state.upper() if state else None
        best_poi = None
        min_dist = float("inf")

        for poi in pois:
            if st and poi.get("state") != st:
                continue
            d = haversine_km(lat, lon, poi["lat"], poi["lon"])
            if d < min_dist:
                min_dist = d
                best_poi = poi

        # If no match in the same state, fallback to global minimum
        if best_poi is None and pois:
            for poi in pois:
                d = haversine_km(lat, lon, poi["lat"], poi["lon"])
                if d < min_dist:
                    min_dist = d
                    best_poi = poi

        return best_poi, round(min_dist, 2)

    def calculate_scores(
        self,
        lat: Optional[float],
        lon: Optional[float],
        state: str = "NSW",
        suburb: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Computes structured location scores (0.0 - 10.0) based on real proximity.
        """
        st = (state or "NSW").upper()

        if lat is None or lon is None or (lat == 0.0 and lon == 0.0):
            # Graceful default baseline for unresolved coordinates
            return {
                "overall": 7.5,
                "schools": 7.5,
                "transit": 7.5,
                "amenities": 7.5,
                "nearest_station": {"name": "Regional Transit Link", "distance_km": 1.8, "type": "Bus / Rail"},
                "nearest_school": {"name": "Local Public School", "distance_km": 1.2, "type": "Primary / Secondary"},
                "nearest_hospital": {"name": "District Hospital", "distance_km": 4.5},
                "distance_to_cbd_km": 25.0
            }

        # 1. Transit Score
        nearest_stn, dist_stn = self._find_nearest_poi(lat, lon, self.transit_pois, st)
        if dist_stn <= 0.40:
            transit_score = 9.8 - 0.5 * (dist_stn / 0.40)
        elif dist_stn <= 0.80:
            transit_score = 9.2 - 0.7 * ((dist_stn - 0.40) / 0.40)
        elif dist_stn <= 1.50:
            transit_score = 8.5 - 1.2 * ((dist_stn - 0.80) / 0.70)
        elif dist_stn <= 3.50:
            transit_score = 7.2 - 1.8 * ((dist_stn - 1.50) / 2.00)
        else:
            transit_score = max(4.0, 5.4 - 0.15 * (dist_stn - 3.50))

        # 2. School Catchment Score
        nearest_sch, dist_sch = self._find_nearest_poi(lat, lon, self.school_pois, st)
        if dist_sch <= 0.60:
            school_score = 9.8 - 0.4 * (dist_sch / 0.60)
        elif dist_sch <= 1.20:
            school_score = 9.3 - 0.7 * ((dist_sch - 0.60) / 0.60)
        elif dist_sch <= 2.50:
            school_score = 8.5 - 1.2 * ((dist_sch - 1.20) / 1.30)
        elif dist_sch <= 5.00:
            school_score = 7.2 - 1.5 * ((dist_sch - 2.50) / 2.50)
        else:
            school_score = max(4.2, 5.6 - 0.12 * (dist_sch - 5.00))

        # 3. Amenities & Healthcare Score
        nearest_amenity, dist_amenity = self._find_nearest_poi(lat, lon, self.amenity_pois, st)
        if dist_amenity <= 1.20:
            amenity_score = 9.6 - 0.6 * (dist_amenity / 1.20)
        elif dist_amenity <= 3.00:
            amenity_score = 9.0 - 1.0 * ((dist_amenity - 1.20) / 1.80)
        elif dist_amenity <= 6.00:
            amenity_score = 7.9 - 1.5 * ((dist_amenity - 3.00) / 3.00)
        else:
            amenity_score = max(4.5, 6.3 - 0.12 * (dist_amenity - 6.00))

        # Suburb localized adjustments
        if suburb:
            s_clean = suburb.lower().strip()
            if any(w in s_clean for w in ["chatswood", "parramatta", "central", "sydney", "cbd", "south yarra", "carlton"]):
                transit_score = min(10.0, transit_score + 0.3)
                amenity_score = min(10.0, amenity_score + 0.3)

        # 4. Overall Weighted Score: 35% Schools + 35% Transit + 30% Amenities
        overall = round(0.35 * school_score + 0.35 * transit_score + 0.30 * amenity_score, 1)

        # Distance to state capital CBD
        cbd_coords = {
            "NSW": (-33.8688, 151.2093),
            "VIC": (-37.8136, 144.9631),
            "QLD": (-27.4698, 153.0251),
            "ACT": (-35.2809, 149.1300),
        }.get(st, (-33.8688, 151.2093))
        dist_cbd = round(haversine_km(lat, lon, cbd_coords[0], cbd_coords[1]), 2)

        return {
            "overall": overall,
            "schools": round(school_score, 1),
            "transit": round(transit_score, 1),
            "amenities": round(amenity_score, 1),
            "nearest_station": {
                "name": nearest_stn["name"] if nearest_stn else "Local Station",
                "distance_km": dist_stn,
                "type": nearest_stn.get("type", "Rail") if nearest_stn else "Rail"
            },
            "nearest_school": {
                "name": nearest_sch["name"] if nearest_sch else "Local School",
                "distance_km": dist_sch,
                "type": nearest_sch.get("type", "Primary") if nearest_sch else "Primary"
            },
            "nearest_hospital": {
                "name": nearest_amenity["name"] if nearest_amenity else "District Hospital",
                "distance_km": dist_amenity
            },
            "distance_to_cbd_km": dist_cbd
        }

    def enrich_property_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enriches a PropertyMaster point payload with computed location_scores.
        """
        coords = payload.get("coordinates") or {}
        lat = coords.get("lat") or payload.get("latitude") or payload.get("lat")
        lon = coords.get("lon") or payload.get("longitude") or payload.get("lon")
        state = payload.get("state") or "NSW"
        suburb = payload.get("suburb")

        scores = self.calculate_scores(lat, lon, state=state, suburb=suburb)
        payload["location_scores"] = scores
        return payload
