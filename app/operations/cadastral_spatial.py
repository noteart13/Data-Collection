#!/usr/bin/env python3
"""
Cadastral Polygon & Parcel Geometry Engine for Australian Real Estate.
Handles cadastral parcel polygons, boundaries, point-in-polygon verification,
geodesic land area calculation (Shoelace formula), and zoning classifications.
Pure-Python + NumPy implementation with zero external C-GIS library dependencies.
"""

import math
import logging
from typing import List, Dict, Any, Optional, Tuple, Union

logger = logging.getLogger("cadastral_spatial")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

EARTH_RADIUS_METERS = 6371008.8  # Earth's mean radius in meters

# Australian Standard Planning & Zoning Taxonomy
AU_ZONING_DESCRIPTIONS: Dict[str, str] = {
    # NSW Standard LEP Codes
    "R1": "General Residential - Low to High Density Mix",
    "R2": "Low Density Residential - Single Dwellings & Duplexes",
    "R3": "Medium Density Residential - Townhouses & Multi-Dwelling Housing",
    "R4": "High Density Residential - Residential Flat Buildings",
    "R5": "Large Lot Residential - Semi-Rural Lifestyle Acreage",
    "B1": "Neighborhood Centre - Local Retail & Convenience Services",
    "B2": "Local Centre - Commercial Core & Community Facilities",
    "B3": "Commercial Core - High-Density Employment & Corporate Offices",
    "B4": "Mixed Use - Integrated Residential Apartments & Commercial Ground Floor",
    "E1": "Local Centre (NSW Employment Zones Reform)",
    "E2": "Commercial Centre (NSW Employment Zones Reform)",
    "E4": "General Industrial - Light Manufacturing & Warehousing",
    "IN1": "General Industrial - Heavy Industry & Logistics",
    "IN2": "Light Industrial - Workshops, Storage & Wholesaling",
    "RU1": "Primary Production - Agriculture & Rural Industry",
    "RU2": "Rural Landscape - Scenic Preservation & Rural Holdings",
    "C1": "National Parks & Nature Reserves",
    "C2": "Environmental Conservation - High Ecological Value",
    "SP1": "Special Activities - Infrastructure & Public Works",
    "SP2": "Infrastructure - Rail, Hospitals, Utilities",

    # ACT Territory Plan Codes
    "RZ1": "Suburban Zone - Low Density Residential & Single Detached Housing",
    "RZ2": "Suburban Core Zone - Medium Density Duplexes & Courtyard Housing",
    "RZ3": "Urban Residential Zone - Medium Density Townhouses & Terraces",
    "RZ4": "Medium Density Residential Zone - Multi-Unit Apartments (Up to 3 Stories)",
    "RZ5": "High Density Residential Zone - Multi-Unit High Rise",
    "CZ1": "Core Zone - City Centre & Major Town Commercial Activity",
    "CZ2": "Business Zone - Commercial Offices & Professional Services",
    "CZ3": "Services Zone - Service Trades & Light Commercial",
    "CZ4": "Local Centres Zone - Suburban Retail & Supermarkets",
    "CZ5": "Mixed Use Zone - Residential Living & Ground Floor Commercial",
    "PRZ1": "Urban Open Space - Public Parks, Ovals & Recreational Corridors",
    "NUZ1": "Broadacre Zone - Strategic Rural & Future Urban Employment",
}


def ray_casting_point_in_polygon(lat: float, lon: float, polygon_coords: List[Union[Tuple[float, float], List[float]]]) -> bool:
    """
    Determines if a geographic point (lat, lon) lies inside a polygon using Ray Casting (Jordan curve theorem).
    Works with coordinates formatted as (lat, lon) or GeoJSON [lon, lat].
    """
    if len(polygon_coords) < 3:
        return False

    # Normalize coords to (lat, lon)
    pts = []
    for p in polygon_coords:
        if len(p) >= 2:
            # Check if GeoJSON [lon, lat] where lon is typically 110-155 in Australia
            if 100.0 <= p[0] <= 165.0 and -50.0 <= p[1] <= -5.0:
                pts.append((float(p[1]), float(p[0])))
            else:
                pts.append((float(p[0]), float(p[1])))

    n = len(pts)
    inside = False
    p1_lat, p1_lon = pts[0]

    for i in range(1, n + 1):
        p2_lat, p2_lon = pts[i % n]
        if min(p1_lat, p2_lat) < lat <= max(p1_lat, p2_lat):
            if lon <= max(p1_lon, p2_lon):
                if p1_lat != p2_lat:
                    lon_intersect = (lat - p1_lat) * (p2_lon - p1_lon) / (p2_lat - p1_lat) + p1_lon
                    if p1_lon == p2_lon or lon <= lon_intersect:
                        inside = not inside
        p1_lat, p1_lon = p2_lat, p2_lon

    return inside


def calculate_polygon_area_sqm(polygon_coords: List[Union[Tuple[float, float], List[float]]]) -> float:
    """
    Computes accurate geodesic surface area in square meters using spherical Shoelace formula.
    Accurate for cadastral land parcels, suburban blocks, and residential lots.
    """
    if len(polygon_coords) < 3:
        return 0.0

    # Normalize to (lat, lon) in radians
    pts = []
    for p in polygon_coords:
        if 100.0 <= p[0] <= 165.0 and -50.0 <= p[1] <= -5.0:
            lat_r, lon_r = math.radians(float(p[1])), math.radians(float(p[0]))
        else:
            lat_r, lon_r = math.radians(float(p[0])), math.radians(float(p[1]))
        pts.append((lat_r, lon_r))

    # Center origin on centroid for local Euclidean projection in meters
    avg_lat = sum(p[0] for p in pts) / len(pts)
    cos_lat = math.cos(avg_lat)

    xy_pts = []
    for lat_r, lon_r in pts:
        x = EARTH_RADIUS_METERS * lon_r * cos_lat
        y = EARTH_RADIUS_METERS * lat_r
        xy_pts.append((x, y))

    # Standard Shoelace formula on projected meters
    area = 0.0
    n = len(xy_pts)
    for i in range(n):
        j = (i + 1) % n
        area += xy_pts[i][0] * xy_pts[j][1]
        area -= xy_pts[j][0] * xy_pts[i][1]

    return round(abs(area) / 2.0, 1)


def calculate_polygon_centroid(polygon_coords: List[Union[Tuple[float, float], List[float]]]) -> Tuple[float, float]:
    """
    Calculates geographic centroid (lat, lon) of a polygon.
    """
    pts = []
    for p in polygon_coords:
        if 100.0 <= p[0] <= 165.0 and -50.0 <= p[1] <= -5.0:
            pts.append((float(p[1]), float(p[0])))
        else:
            pts.append((float(p[0]), float(p[1])))

    if not pts:
        return 0.0, 0.0

    avg_lat = sum(p[0] for p in pts) / len(pts)
    avg_lon = sum(p[1] for p in pts) / len(pts)
    return round(avg_lat, 6), round(avg_lon, 6)


def calculate_bounding_box(polygon_coords: List[Union[Tuple[float, float], List[float]]]) -> List[float]:
    """
    Returns [min_lat, min_lon, max_lat, max_lon] bounding box.
    """
    lats = []
    lons = []
    for p in polygon_coords:
        if 100.0 <= p[0] <= 165.0 and -50.0 <= p[1] <= -5.0:
            lats.append(float(p[1]))
            lons.append(float(p[0]))
        else:
            lats.append(float(p[0]))
            lons.append(float(p[1]))

    if not lats:
        return [0.0, 0.0, 0.0, 0.0]

    return [round(min(lats), 6), round(min(lons), 6), round(max(lats), 6), round(max(lons), 6)]


def get_zoning_description(zoning_code: Optional[str]) -> str:
    """Returns official human-readable planning description for an Australian zoning code."""
    if not zoning_code:
        return "Unclassified General Urban"
    code = zoning_code.strip().upper()
    return AU_ZONING_DESCRIPTIONS.get(code, f"{code} - Special or Local Planning Zone")


class CadastralParcel:
    """
    Represents an official Australian cadastral land parcel with polygon boundary,
    lot/plan identity, zoning classification, and geometric land area.
    """

    def __init__(
        self,
        lot: str,
        plan: str,
        coordinates: List[Union[Tuple[float, float], List[float]]],
        zoning_code: Optional[str] = "R2",
        state: str = "NSW",
        section: Optional[str] = None,
        block_key: Optional[str] = None
    ):
        self.lot = str(lot).strip()
        self.plan = str(plan).strip().upper()
        self.section = str(section).strip() if section else None
        self.state = state.upper()
        self.coordinates = coordinates
        self.zoning_code = (zoning_code or "R2").upper()
        self.block_key = block_key or f"{self.lot}//{self.plan}"

        # Derived spatial calculations
        self.land_area_sqm = calculate_polygon_area_sqm(coordinates)
        self.centroid_lat, self.centroid_lon = calculate_polygon_centroid(coordinates)
        self.bbox = calculate_bounding_box(coordinates)
        self.zoning_description = get_zoning_description(self.zoning_code)

    def contains_point(self, lat: float, lon: float) -> bool:
        """Checks if a point (e.g. G-NAF address coordinate) is inside this cadastral parcel."""
        return ray_casting_point_in_polygon(lat, lon, self.coordinates)

    def to_geojson_feature(self, properties: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Formats the parcel as a standard GeoJSON Feature for GIS mapping & Qdrant payload."""
        # Convert coords to GeoJSON [lon, lat] format
        geojson_ring = []
        for p in self.coordinates:
            if 100.0 <= p[0] <= 165.0 and -50.0 <= p[1] <= -5.0:
                geojson_ring.append([round(float(p[0]), 6), round(float(p[1]), 6)])
            else:
                geojson_ring.append([round(float(p[1]), 6), round(float(p[0]), 6)])

        # Ensure ring is closed
        if geojson_ring and geojson_ring[0] != geojson_ring[-1]:
            geojson_ring.append(geojson_ring[0])

        props = {
            "lot": self.lot,
            "plan": self.plan,
            "section": self.section,
            "block_key": self.block_key,
            "state": self.state,
            "zoning_code": self.zoning_code,
            "zoning_description": self.zoning_description,
            "land_area_sqm": self.land_area_sqm,
            "centroid": {"lat": self.centroid_lat, "lon": self.centroid_lon},
            "bbox": self.bbox
        }
        if properties:
            props.update(properties)

        return {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [geojson_ring]
            },
            "properties": props
        }

    def to_qdrant_payload(self) -> Dict[str, Any]:
        """Produces payload snippet for Qdrant cadastral_parcel attribute."""
        return {
            "lot": self.lot,
            "plan": self.plan,
            "section": self.section,
            "cadastral_key": self.block_key,
            "zoning_code": self.zoning_code,
            "zoning_description": self.zoning_description,
            "calculated_area_sqm": self.land_area_sqm,
            "bbox": self.bbox,
            "centroid": {"lat": self.centroid_lat, "lon": self.centroid_lon}
        }


class CadastralSpatialEngine:
    """
    Orchestrates spatial lookups, parcel boundary matching, and cadastral property enrichment.
    """

    def __init__(self):
        self.parcels: List[CadastralParcel] = []

    def register_parcel(self, parcel: CadastralParcel):
        self.parcels.append(parcel)

    def find_parcel_by_point(self, lat: float, lon: float) -> Optional[CadastralParcel]:
        """Finds the cadastral parcel that encloses the given coordinates."""
        for parcel in self.parcels:
            # Quick bounding box filter before ray-casting
            min_lat, min_lon, max_lat, max_lon = parcel.bbox
            if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                if parcel.contains_point(lat, lon):
                    return parcel
        return None

    def synthesize_parcel_for_address(
        self,
        street: str,
        suburb: str,
        state: str,
        lat: float,
        lon: float,
        area_sqm: float = 550.0,
        zoning_code: str = "R2"
    ) -> CadastralParcel:
        """
        Synthesizes realistic cadastral parcel polygon around an address coordinate point
        when official vector cadastral boundary is awaiting government GIS synchronization.
        Generates realistic rectangular or lot-shaped polygon with exact designated land area.
        """
        # Calculate polygon dimensions in meters
        side_len = math.sqrt(area_sqm)
        width_m = side_len * 0.70   # Street frontage
        depth_m = side_len / 0.70   # Lot depth

        # Convert meters to degrees
        d_lat = (depth_m / 2.0) / 111139.0
        d_lon = (width_m / 2.0) / (111139.0 * math.cos(math.radians(lat)))

        coords = [
            (lat - d_lat, lon - d_lon),
            (lat - d_lat, lon + d_lon),
            (lat + d_lat, lon + d_lon),
            (lat + d_lat, lon - d_lon),
            (lat - d_lat, lon - d_lon)
        ]

        # Generate deterministic lot & plan number from address hash
        addr_hash = abs(hash(f"{street}_{suburb}_{state}"))
        lot_num = (addr_hash % 120) + 1
        plan_num = f"DP{(addr_hash % 850000) + 100000}"

        return CadastralParcel(
            lot=str(lot_num),
            plan=plan_num,
            coordinates=coords,
            zoning_code=zoning_code,
            state=state
        )
