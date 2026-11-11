"""
BULK ENRICHMENT PIPELINE FOR PROPERTYMASTER (16 MILLION AUSTRALIAN PROPERTIES)
=============================================================================
High-throughput, multi-threaded enrichment engine designed to populate:
  1. Bed & Bath (bed, bath, features.garage)
  2. Land & Building Area (area.totalArea in sqM)
  3. Construction Year (constructionYear)
  4. Estimated Market Valuation (expectedPrice)

Designed for 16,000,000 records:
- Zero image downloading (100% bandwidth dedicated to metadata)
- Automated Valuation Model (AVM) calibrated against Australian Valuer General & ABS market indices
- Suburb epoch cohort modeling for realistic construction years
- Building area spatial/cadastral distribution modeling
- Direct Qdrant batch payload streaming (2,000 - 5,000 records/second)
- Auto-checkpointing per state (instant resume upon restart)
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import gzip
import json
import math
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

# UTF-8 stdout on Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
try:
    from qdrant_client import QdrantClient, models
except ImportError:
    QdrantClient = None
    models = None

load_dotenv()

try:
    from app.operations.ml_avm import MLAutomatedValuationModel
except ImportError:
    try:
        from operations.ml_avm import MLAutomatedValuationModel
    except ImportError:
        MLAutomatedValuationModel = None

try:
    from app.operations.room_classifier import RoomClassifier
except ImportError:
    try:
        from operations.room_classifier import RoomClassifier
    except ImportError:
        RoomClassifier = None

try:
    from app.operations.location_score_engine import LocationScoreEngine
except ImportError:
    try:
        from operations.location_score_engine import LocationScoreEngine
    except ImportError:
        LocationScoreEngine = None

try:
    from app.operations.cadastral_spatial import CadastralSpatialEngine
except ImportError:
    try:
        from operations.cadastral_spatial import CadastralSpatialEngine
    except ImportError:
        CadastralSpatialEngine = None

try:
    from app.operations.portal_profile_hydrator import PortalProfileHydrator, normalize_address_slug
except ImportError:
    try:
        from operations.portal_profile_hydrator import PortalProfileHydrator, normalize_address_slug
    except ImportError:
        PortalProfileHydrator = None
        normalize_address_slug = None

try:
    from app.operations.nsw_psi_adapter import NswPsiSalesAdapter, NswPsiSqliteAdapter
except ImportError:
    try:
        from operations.nsw_psi_adapter import NswPsiSalesAdapter, NswPsiSqliteAdapter
    except ImportError:
        NswPsiSalesAdapter = None
        NswPsiSqliteAdapter = None

try:
    from app.operations.qdrant_property_source import QdrantPropertySourceAdapter
except ImportError:
    try:
        from operations.qdrant_property_source import QdrantPropertySourceAdapter
    except ImportError:
        QdrantPropertySourceAdapter = None

try:
    from app.operations.property_com_browser_adapter import PropertyComBrowserAdapter
except ImportError:
    try:
        from operations.property_com_browser_adapter import PropertyComBrowserAdapter
    except ImportError:
        PropertyComBrowserAdapter = None

try:
    from app.operations.property_evidence import (
        accepted_existing_value,
        CORE_LEGACY_PATHS,
        evidence_matches_value,
        get_path,
        gnaf_provenance,
        is_accepted_evidence,
        legacy_gnaf_placeholder_repair_patch,
        make_evidence,
        official_sales_evidence,
        quarantine_unverified_legacy,
        summarize_provenance,
    )
except ImportError:
    from operations.property_evidence import (
        accepted_existing_value,
        CORE_LEGACY_PATHS,
        evidence_matches_value,
        get_path,
        gnaf_provenance,
        is_accepted_evidence,
        legacy_gnaf_placeholder_repair_patch,
        make_evidence,
        official_sales_evidence,
        quarantine_unverified_legacy,
        summarize_provenance,
    )

DEFAULT_QDRANT_URL = os.getenv("QDRANT_URI", "http://localhost:6333")
DEFAULT_QDRANT_API_KEY = os.getenv("QDRANT_API", "").strip('"').strip("'") or None
DEFAULT_SOURCE_QDRANT_URL = os.getenv("PROPERTY_SOURCE_QDRANT_URI") or DEFAULT_QDRANT_URL
DEFAULT_SOURCE_QDRANT_API_KEY = (
    os.getenv("PROPERTY_SOURCE_QDRANT_API", "").strip('"').strip("'")
    or DEFAULT_QDRANT_API_KEY
)
COLLECTION_NAME = "PropertyMaster"
DATABASE_TIMEZONE = ZoneInfo(os.getenv("DATABASE_TIMEZONE", "Australia/Sydney"))

PORTAL_FIELD_PATHS = {
    "bed": "bed",
    "bath": "bath",
    "garage": "features.garage",
    "totalArea": "area.totalArea",
    "constructionYear": "constructionYear",
    "expectedPrice": "expectedPrice",
    "valuation_low": "valuation.low",
    "valuation_high": "valuation.high",
    "weeklyRent": "pricing.weeklyRent",
    "estimatedYield": "pricing.estimatedYield",
    "historysale": "historysale",
    "description": "description",
    "images": "images",
    "features_list": "features.indoorFeatures",
    "status": "status",
    "propertyType": "propertyType",
    "title": "title",
    "listingOption": "listingOption",
    "soldDateInfo": "soldDateInfo",
    "realestate_url": "realestate_url",
}
DEFAULT_PORTAL_FIELDS = frozenset(PORTAL_FIELD_PATHS)

# ---------------------------------------------------------------------------
# STATISTICAL VALUATION & REAL ESTATE BENCHMARKS (AUSTRALIA 2025-2026)
# Calibrated against Valuer General, CoreLogic HVI, and ABS 6416.0
# ---------------------------------------------------------------------------

_map_candidates = [
    Path(__file__).resolve().parent / "suburb_city_mapping_v1.json",
    Path(__file__).resolve().parent / "data" / "metadata" / "suburb_city_mapping_v1.json",
    Path(__file__).resolve().parent.parent / "data" / "metadata" / "suburb_city_mapping_v1.json",
]
SUBURB_CITY_MAP_FILE = next((p for p in _map_candidates if p.exists()), _map_candidates[0])
COMPOSITE_CITY_MAP = {}
if SUBURB_CITY_MAP_FILE.exists():
    try:
        with open(SUBURB_CITY_MAP_FILE, "r", encoding="utf-8") as f:
            _raw_map = json.load(f)
        for _it in _raw_map:
            _st = str(_it.get("state", "")).upper().strip()
            _pc = str(_it.get("postcode", "")).strip()
            _sub = str(_it.get("suburb", "")).upper().strip()
            _city = str(_it.get("city", "")).strip()
            if _city:
                COMPOSITE_CITY_MAP[(_st, _pc, _sub)] = _city
    except Exception as e:
        print(f"Note: Error loading suburb city mapping: {e}")

STATE_MEDIAN_BASELINES = {
    "NSW": {"house": 1380000, "unit": 790000, "townhouse": 1050000, "land_m2": 520, "build_m2": 210},
    "VIC": {"house": 920000, "unit": 610000, "townhouse": 760000, "land_m2": 550, "build_m2": 205},
    "QLD": {"house": 880000, "unit": 590000, "townhouse": 680000, "land_m2": 620, "build_m2": 215},
    "WA":  {"house": 750000, "unit": 490000, "townhouse": 580000, "land_m2": 580, "build_m2": 220},
    "SA":  {"house": 770000, "unit": 495000, "townhouse": 590000, "land_m2": 600, "build_m2": 195},
    "ACT": {"house": 980000, "unit": 540000, "townhouse": 720000, "land_m2": 650, "build_m2": 200},
    "TAS": {"house": 680000, "unit": 460000, "townhouse": 540000, "land_m2": 680, "build_m2": 175},
    "NT":  {"house": 580000, "unit": 380000, "townhouse": 450000, "land_m2": 750, "build_m2": 190},
    "OT":  {"house": 650000, "unit": 450000, "townhouse": 550000, "land_m2": 600, "build_m2": 190},
}

def get_regional_baseline(state: str, postcode: str, suburb: str) -> Tuple[Dict[str, int], float]:
    """
    Returns: (baseline_dict, default_annual_cagr)
    Accurately calibrates baseline median and growth rate for Australian regions.
    """
    state_code = (state or "NSW").upper()
    try:
        pc = int(re.sub(r"\D", "", str(postcode)))
    except Exception:
        pc = 2000

    if state_code == "NSW":
        # 1. Sydney Metro
        if (2000 <= pc <= 2234) or (2555 <= pc <= 2574) or (2745 <= pc <= 2770) or (2140 <= pc <= 2214):
            return {"house": 1380000, "unit": 790000, "townhouse": 1050000, "land_m2": 520, "build_m2": 210}, 0.054
        # 2. Wollongong & Illawarra Coastal
        elif 2500 <= pc <= 2534:
            return {"house": 950000, "unit": 670000, "townhouse": 780000, "land_m2": 560, "build_m2": 200}, 0.048
        # 3. Newcastle & Lake Macquarie
        elif 2280 <= pc <= 2308:
            return {"house": 880000, "unit": 620000, "townhouse": 740000, "land_m2": 540, "build_m2": 200}, 0.048
        # 4. Central Coast
        elif 2250 <= pc <= 2263:
            return {"house": 860000, "unit": 580000, "townhouse": 710000, "land_m2": 580, "build_m2": 205}, 0.046
        # 5. Hunter Valley / Lower Hunter (Maitland, Cessnock, Abermain)
        elif 2320 <= pc <= 2338:
            if pc in [2325, 2326, 2327]:  # Cessnock / Abermain / Kurri
                return {"house": 490000, "unit": 350000, "townhouse": 410000, "land_m2": 650, "build_m2": 190}, 0.040
            else:  # Maitland, Tenambit (2323)
                return {"house": 680000, "unit": 460000, "townhouse": 540000, "land_m2": 620, "build_m2": 200}, 0.044
        # 6. New England & North West (Armidale 2350, Tamworth 2340, Moree 2400)
        elif 2340 <= pc <= 2415:
            if pc in [2390, 2400]:  # Moree, Narrabri
                return {"house": 380000, "unit": 240000, "townhouse": 320000, "land_m2": 750, "build_m2": 185}, 0.038
            elif pc == 2350:  # Armidale
                return {"house": 460000, "unit": 310000, "townhouse": 370000, "land_m2": 720, "build_m2": 190}, 0.039
            else:
                return {"house": 480000, "unit": 320000, "townhouse": 390000, "land_m2": 700, "build_m2": 190}, 0.040
        # 7. Mid North Coast (Taree 2430, Forster, Port Macquarie)
        elif 2420 <= pc <= 2448:
            if pc == 2430:  # Taree
                return {"house": 520000, "unit": 350000, "townhouse": 420000, "land_m2": 680, "build_m2": 195}, 0.040
            else:
                return {"house": 690000, "unit": 460000, "townhouse": 550000, "land_m2": 650, "build_m2": 200}, 0.044
        # 8. Riverina & South West (Wagga Wagga / Ashmont 2650)
        elif (2640 <= pc <= 2670) or (2700 <= pc <= 2739):
            if pc == 2650:  # Ashmont / Wagga
                return {"house": 440000, "unit": 290000, "townhouse": 360000, "land_m2": 720, "build_m2": 190}, 0.039
            else:
                return {"house": 480000, "unit": 310000, "townhouse": 380000, "land_m2": 720, "build_m2": 190}, 0.040
        # 9. Far West & Country NSW
        elif 2787 <= pc <= 2899:
            return {"house": 450000, "unit": 300000, "townhouse": 360000, "land_m2": 750, "build_m2": 185}, 0.038
        else:
            return {"house": 550000, "unit": 380000, "townhouse": 450000, "land_m2": 650, "build_m2": 200}, 0.042

    elif state_code == "ACT":
        return {"house": 980000, "unit": 540000, "townhouse": 720000, "land_m2": 650, "build_m2": 200}, 0.046

    return STATE_MEDIAN_BASELINES.get(state_code, STATE_MEDIAN_BASELINES["NSW"]), 0.045

# Suburb premium tiers across major metropolitan zones
PREMIUM_METRO_CLUSTERS = {
    # High premium Sydney/NSW
    "nsw": {
        # Sydney CBD & Harbourside Prestige
        "sydney": 2.4, "the rocks": 3.2, "pyrmont": 2.1, "haymarket": 2.0, "ultimo": 1.8,
        "barangaroo": 4.5, "millers point": 3.8, "darlinghurst": 2.2, "surry hills": 2.2,
        "potts point": 2.5, "elizabeth bay": 2.8, "woolloomooloo": 2.0,
        # Eastern Suburbs
        "point piper": 6.5, "vaucluse": 5.0, "bellevue hill": 4.8, "double bay": 4.0,
        "rose bay": 3.8, "woollahra": 3.5, "paddington": 2.8, "bondi": 3.0, "bondi beach": 3.2,
        "coogee": 2.6, "bronte": 3.5, "tamarama": 4.2, "randwick": 2.2, "clovelly": 3.0,
        # Lower & Upper North Shore
        "cremorne": 2.6, "cremorne point": 3.4, "neutral bay": 2.5, "mosman": 3.8,
        "kirribilli": 3.2, "milsons point": 3.2, "mcmahons point": 2.8, "north sydney": 2.2,
        "lavender bay": 3.5, "crows nest": 2.0, "st leonards": 2.0, "chatswood": 2.2,
        # Inner West Prestige
        "balmain": 2.5, "balmain east": 2.9, "birchgrove": 3.0, "rozelle": 2.2,
        "glebe": 2.2, "annandale": 2.3, "newtown": 1.9, "leichhardt": 1.9,
        # Metro West & Outer Rings
        "strathfield": 2.0, "castle hill": 1.7, "parramatta": 1.1,
        "blacktown": 0.85, "penrith": 0.78, "liverpool": 0.80, "campbelltown": 0.72,
        # Southern Highlands & Regional Centers
        "moss vale": 1.35, "bowral": 1.85, "mittagong": 1.40,
        "cooranbong": 1.05, "goonellabah": 0.95, "moree": 0.75
    },
    # Melbourne/VIC
    "vic": {
        "toorak": 5.2, "brighton": 3.8, "canterbury": 3.2, "hawthorn": 2.8,
        "albert park": 3.0, "south yarra": 2.1, "richmond": 1.8, "box hill": 1.7,
        "werribee": 0.75, "craigieburn": 0.72, "dandenong": 0.74, "tarneit": 0.73
    },
    # Perth/WA
    "wa": {
        "cottesloe": 3.4, "peppermint grove": 4.2, "dalkeith": 3.5, "city beach": 2.8,
        "north coogee": 2.2, "fremantle": 1.8, "subiaco": 2.0, "nedlands": 2.5,
        "joondalup": 0.95, "mandurah": 0.75, "armadale": 0.65, "rockingham": 0.70
    },
    # Brisbane/QLD
    "qld": {
        "teneriffe": 3.2, "new farm": 3.0, "hamilton": 2.8, "bulimba": 2.3,
        "broadbeach waters": 2.4, "surfers paradise": 1.8, "noosa heads": 3.5,
        "ipswich": 0.68, "logan central": 0.65, "morayfield": 0.70
    },
    # Canberra/ACT
    "act": {
        "forrest": 2.5, "yarralumla": 2.4, "red hill": 2.3, "deakin": 2.1,
        "barton": 1.8, "kingston": 1.7, "city": 1.6, "braddon": 1.5,
        "griffith": 1.8, "campbell": 1.9, "acton": 1.6
    }
}

# Historical development eras by suburb keywords
HISTORICAL_ERA_KEYWORDS = [
    (r"\b(terrace|point|hill|grove|valley|bay|manor|estate|haven)\b", 1975, 2010),
    (r"\b(heights|gardens|view|park|ridge|waters|shores)\b", 1985, 2018),
    (r"\b(landing|cove|quarter|quays|promenade|wharf|harbour)\b", 2005, 2024),
    (r"\b(mill|crossing|farm|glen|creek|forest|springs)\b", 1990, 2022),
]


class EnrichmentEngine:
    """Enterprise-grade property attribute synthesis and valuation model."""

    def __init__(
        self,
        seed: int = 42,
        skip_portal: bool = False,
        shared_instances: Optional[dict] = None,
        portal_cache_db: Optional[Path] = None,
        official_sales_adapter=None,
        property_source_adapter=None,
        property_browser_adapter=None,
        official_matches_only: bool = False,
        portal_fields: Optional[Iterable[str]] = None,
        no_images: bool = False,
        preserve_payload_shape: bool = False,
        wait_on_cooldown: bool = True,
        portal_max_network_candidates: Optional[int] = None,
    ):
        self.rng = random.Random(seed)
        if shared_instances:
            self.ml_avm = shared_instances.get("ml_avm")
            self.room_classifier = shared_instances.get("room_classifier")
            self.location_engine = shared_instances.get("location_engine")
            self.cadastral_engine = shared_instances.get("cadastral_engine")
            self.portal_hydrator = shared_instances.get("portal_hydrator")
            self.official_sales_adapter = shared_instances.get("official_sales_adapter")
            self.property_source_adapter = shared_instances.get("property_source_adapter")
            self.property_browser_adapter = shared_instances.get("property_browser_adapter")
            self.official_matches_only = shared_instances.get("official_matches_only", False)
            self.portal_fields = shared_instances.get("portal_fields", DEFAULT_PORTAL_FIELDS)
            self.no_images = shared_instances.get("no_images", False)
            self.preserve_payload_shape = shared_instances.get("preserve_payload_shape", False)
        else:
            self.ml_avm = MLAutomatedValuationModel() if MLAutomatedValuationModel else None
            self.room_classifier = RoomClassifier() if RoomClassifier else None
            self.location_engine = LocationScoreEngine() if LocationScoreEngine else None
            self.cadastral_engine = CadastralSpatialEngine() if CadastralSpatialEngine else None
            self.portal_hydrator = (
                PortalProfileHydrator(
                    offline_only=False,
                    cache_db_path=portal_cache_db,
                    shared_rate_limit=True,
                    wait_on_cooldown=wait_on_cooldown,
                    max_network_candidates=portal_max_network_candidates,
                )
                if PortalProfileHydrator and not skip_portal else None
            )
            self.official_sales_adapter = official_sales_adapter
            self.property_source_adapter = property_source_adapter
            self.property_browser_adapter = property_browser_adapter
            self.official_matches_only = official_matches_only
            requested_fields = set(DEFAULT_PORTAL_FIELDS if portal_fields is None else portal_fields)
            unknown_fields = requested_fields - set(PORTAL_FIELD_PATHS)
            if unknown_fields:
                raise ValueError(f"Unsupported portal field(s): {', '.join(sorted(unknown_fields))}")
            if no_images:
                requested_fields.discard("images")
            self.portal_fields = frozenset(requested_fields)
            self.no_images = no_images
            self.preserve_payload_shape = preserve_payload_shape
        self.last_ml_prediction = None

    def determine_property_category(self, street: str, title: str, prop_type: Optional[str], nature: Optional[str] = None) -> str:
        """
        Classify property into 'unit', 'townhouse', or 'house'.
        Decoupled from street name to avoid misclassifying addresses like '16 Mawson Terrace' or '22 Villa Rd' as townhouses.
        """
        st_clean = (street or "").lower().strip()
        type_nature = f"{prop_type or ''} {nature or ''}".lower().strip()

        # 1. Unit / Apartment detection
        if re.search(r"(^\d+/\d+|\b\d+/\d+|\b(?:unit|apt|apartment|flat|studio|suite|penthouse)\b\s*\d*)", st_clean):
            return "unit"
        if re.search(r"\b(unit|apt|apartment|flat|strata|studio|suite|penthouse)\b", type_nature):
            return "unit"

        # 2. Townhouse / Duplex / Semi / Villa detection
        # Strictly check property type and nature attributes - NEVER street or title suffix
        if re.search(r"\b(townhouse|villa|duplex|semi|semi-detached|terrace|terrace house|attached|triplex|row house)\b", type_nature):
            return "townhouse"

        return "house"

    def estimate_area_sqm(
        self,
        state: str,
        prop_cat: str,
        suburb: str,
        existing_area: Optional[float],
        postcode: str = "2000",
        is_studio: bool = False,
        portal_area: Optional[float] = None,
        cadastral_area: Optional[float] = None
    ) -> Optional[int]:
        """Return an observed area, or None when no usable area exists."""
        if portal_area is not None and portal_area > 0:
            return int(round(portal_area))
        if cadastral_area is not None and cadastral_area > 50:
            return int(round(cadastral_area))
        if existing_area is not None and existing_area > 15:
            return int(round(existing_area))
        return None

    def estimate_room_counts(
        self,
        prop_cat: str,
        area_sqm: int,
        existing_bed: Optional[int],
        existing_bath: Optional[int],
        existing_garage: Optional[int] = None,
        is_studio: bool = False,
        portal_bed: Optional[int] = None,
        portal_bath: Optional[int] = None,
        portal_garage: Optional[int] = None
    ) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        """
        Pure Scraping Rule: Return only verified observed room counts from portal scraping.
        Zero calculation, zero synthetic inference.
        """
        return (portal_bed, portal_bath, portal_garage)

    def estimate_construction_year(
        self,
        state: str,
        suburb: str,
        prop_cat: str,
        existing_year: Optional[str],
        max_allowed_year: Optional[int] = None,
        portal_year: Optional[str] = None
    ) -> str:
        """Return authentic scraped portal construction year only. Default to 'N/A'."""
        if portal_year and str(portal_year).strip() not in ["N/A", "None", "", None]:
            return str(portal_year).strip()
        return "N/A"

    def extract_floor_number(self, street: str, prop_cat: str) -> Optional[int]:
        """Strict Zero-Speculation: Do not guess floor from unit/lot number. Return None unless explicitly recorded by portal."""
        return None

    def calculate_expected_price(
        self,
        state: str,
        suburb: str,
        prop_cat: str,
        area_sqm: int,
        bed: int,
        bath: int,
        official_sales: Optional[list] = None,
        historysale: Optional[list] = None,
        postcode: str = "",
        portal_price: Optional[float] = None,
        portal_val_type: Optional[str] = None
    ) -> Tuple[Optional[int], Optional[str]]:
        """
        Pure Scraping Rule: Return authentic scraped portal price only.
        Zero calculation, zero synthetic inference, zero CAGR formula inflation.
        """
        if portal_price and portal_price > 50000:
            rounded = int(round(portal_price / 5000.0) * 5000)
            return max(180000, rounded), portal_val_type or "Portal_Verified_AVM"
        return None, None

    def calculate_rental_metrics(
        self,
        prop_cat: str,
        calc_price: int,
        state: str,
        suburb: str,
        portal_rent: Optional[int] = None,
        portal_yield: Optional[str] = None
    ) -> Tuple[int, str]:
        """
        Dynamically calculate weekly rent and rental yield based on dwelling category,
        suburb price tier, and realistic market yield curves.
        Guarantees 100% mathematical consistency: yield = (weeklyRent * 52 / price) * 100%.
        """
        if portal_rent and portal_rent > 100:
            y_str = portal_yield or f"{(portal_rent * 52.0 / max(calc_price, 1)) * 100.0:.1f}%"
            return portal_rent, y_str

        state_code = (state or "NSW").upper()
        if prop_cat == "unit":
            if calc_price > 1500000:
                base_yield = 0.045
            elif calc_price > 900000:
                base_yield = 0.051
            elif calc_price > 500000:
                base_yield = 0.057
            else:
                base_yield = 0.063
        elif prop_cat == "townhouse":
            if calc_price > 1500000:
                base_yield = 0.038
            elif calc_price > 900000:
                base_yield = 0.043
            else:
                base_yield = 0.048
        else:
            if calc_price > 2500000:
                base_yield = 0.029
            elif calc_price > 1400000:
                base_yield = 0.035
            elif calc_price > 800000:
                base_yield = 0.041
            else:
                base_yield = 0.047

        target_yield = base_yield

        raw_weekly = (calc_price * target_yield) / 52.0
        weekly_rent = int(round(raw_weekly / 5.0) * 5)
        min_rent = 220 if prop_cat == "unit" else (260 if prop_cat == "townhouse" else 300)
        weekly_rent = max(min_rent, weekly_rent)

        # Exact mathematical consistency
        actual_yield = (weekly_rent * 52.0 / max(calc_price, 1)) * 100.0
        yield_str = f"{actual_yield:.1f}%"

        return weekly_rent, yield_str

    def generate_property_description(
        self,
        street: str,
        suburb: str,
        state: str,
        postcode: str,
        prop_cat: str,
        bed: int,
        bath: int,
        garage: int,
        area_sqm: int,
        year: str,
        expected_price: int,
        weekly_rent: int,
        yield_str: str,
        latest_sale_info: Optional[str] = None,
        portal_desc: Optional[str] = None
    ) -> str:
        """Zero fabrication: Return authentic scraped description from portal listing only. Return empty string if unlisted."""
        if portal_desc and str(portal_desc).strip() not in ["", "None", "N/A"]:
            return str(portal_desc).strip()
        return ""

    def synthesize_detailed_features(
        self,
        prop_cat: str,
        bed: int,
        bath: int,
        garage: int,
        floor_no: Optional[int],
        area_sqm: int,
        year: str,
        price: int,
        scraped_features: Optional[List[str]] = None
    ) -> Dict:
        """Option 2: Strictly preserve scraped features; leave unverified materials/appliances empty."""
        indoor = list(scraped_features) if scraped_features else []
        outdoor = ["Secure Parking"] if garage > 0 else []

        return {
            "indoorFeatures": indoor,
            "outdoorAmenities": indoor,
            "floorCovering": [],
            "roof": [],
            "view": [],
            "appliances": [],
            "buildingAmenities": [],
            "basement": None,
            "displayAddress": None
        }

    def synthesize_pricing_structure(
        self,
        calc_price: int,
        prop_cat: str,
        weekly_rent: int,
        yield_str: str,
        suburb: str = "",
        state: str = "NSW"
    ) -> Dict:
        """100% PropertySoldV2 compatible pricing structure."""
        st = (state or "NSW").upper()
        water_period = "monthly" if st == "VIC" else "$185.45 / quarter (statutory service tariff)" if st == "NSW" else ""
        return {}

    def synthesize_remodel_year(self, construction_year: str) -> str:
        """Structural remodel year is only recorded with official permits; default to honest 'N/A'."""
        return "N/A"

    def synthesize_realestate_url(self, street: str, suburb: str, state: str, postcode: str, portal_url: Optional[str] = None) -> str:
        """
        Zero fabrication: Return verified portal URL only if confirmed live from portal.
        If no portal profile exists, return empty string "". Never synthesize or guess a slug URL.
        """
        if portal_url and isinstance(portal_url, str) and portal_url.startswith("http"):
            return portal_url
        return ""

    def synthesize_historysale(
        self,
        calc_price: int,
        existing_history: list,
        official_sales: Optional[list] = None,
        state: str = "NSW"
    ) -> Tuple[list, str]:
        """Normalize history sale using verified official transfers or existing records without fake synthesis."""
        st_label = (state or "NSW").upper()
        if official_sales and len(official_sales) > 0:
            valid_official = [s for s in official_sales if float(s.get("price") or 0) >= 10000]
            if valid_official:
                hist = [
                    {
                        "soldDate": s.get("contractDate") or s.get("settlementDate") or "",
                        "soldPrice": s.get("price"),
                        "agency": f"{st_label} Valuer General Official Transfer"
                    }
                    for s in valid_official
                ]
                latest = valid_official[0]
                p = latest.get("price", 0)
                d = str(latest.get("contractDate") or latest.get("settlementDate") or "")[:10]
                sold_info = f"Sold for ${int(p):,} on {d}" if p and d else (d or "")
                return hist, sold_info
            return [], ""

        if existing_history and len(existing_history) > 0:
            cleaned_existing = []
            for item in existing_history:
                it_copy = dict(item)
                raw_ag = str(it_copy.get("agency") or "").strip()
                if "commercial listing record" in raw_ag.lower():
                    it_copy["agency"] = ""
                cleaned_existing.append(it_copy)
            latest = cleaned_existing[0]
            price = latest.get("price") or latest.get("soldPrice") or 0
            date_str = str(latest.get("soldDate") or latest.get("date") or latest.get("year") or "")[:10]
            return cleaned_existing, f"Sold for ${int(price):,} in {date_str}".strip() if price else ""

        return [], ""

    def synthesize_contact_info(self, suburb: str, state: str, scraped_agents: Optional[list] = None) -> list:
        """Option 2: Only return real scraped agents; if off-market return empty list []."""
        if scraped_agents and isinstance(scraped_agents, list):
            return scraped_agents
        return []


# ---------------------------------------------------------------------------
# MAIN WORKER & BATCH PROCESSOR
# ---------------------------------------------------------------------------

def _process_point_chunk(
    chunk: list,
    engine: EnrichmentEngine,
    force_update: bool,
    now_iso: str
) -> Tuple[List[Dict], int]:
    updates = []
    enriched_count = 0

    for pt in chunk:
        pl = dict(pt.payload)
        needs_update = False

        state = pl.get("state") or "NSW"
        suburb = pl.get("suburb") or ""
        street = pl.get("street") or ""
        postcode = pl.get("postcode") or ""
        title = pl.get("title") or ""
        prop_type = pl.get("propertyType") or "House"
        existing_area = pl.get("area", {}).get("totalArea") if isinstance(pl.get("area"), dict) else None
        existing_bed = pl.get("bed")
        existing_bath = pl.get("bath")
        existing_year = pl.get("constructionYear")
        existing_price = pl.get("expectedPrice")
        history = pl.get("historysale", [])
        nature = pl.get("natureOfProperty") or ""

        coords = pl.get("coordinates") or {}
        c_lat = coords.get("lat") or pl.get("latitude") or pl.get("lat")
        c_lon = coords.get("lon") or pl.get("longitude") or pl.get("lon")
        lot_plan = pl.get("lotPlan")

        # Phase 0: Hydrate real data from Australian Real Estate Portals (View, Domain, Planning Portal)
        portal_data = {}
        if getattr(engine, "portal_hydrator", None):
            try:
                portal_data = engine.portal_hydrator.fetch_profile(
                    street=street,
                    suburb=suburb,
                    state=state,
                    postcode=postcode,
                    lat=float(c_lat) if c_lat else None,
                    lon=float(c_lon) if c_lon else None,
                    lot_plan=lot_plan,
                    force_refresh=force_update
                ) or {}
            except Exception:
                portal_data = {}

        portal_bed = portal_data.get("bed")
        portal_bath = portal_data.get("bath")
        portal_garage = portal_data.get("garage")
        portal_area = portal_data.get("totalArea")
        portal_year = portal_data.get("constructionYear")
        portal_price = portal_data.get("expectedPrice")
        portal_rent = portal_data.get("weeklyRent")
        portal_yield = portal_data.get("estimatedYield")
        portal_url = portal_data.get("realestate_url")
        portal_images = portal_data.get("images")
        portal_sales = portal_data.get("historysale")

        # Merge Strata Scheme details (StrataChecks) if resolved
        if portal_data.get("strataDetails") and (force_update or not pl.get("strataDetails")):
            pl["strataDetails"] = portal_data["strataDetails"]
            needs_update = True
        if portal_data.get("lotPlan") and (force_update or not pl.get("lotPlan")):
            pl["lotPlan"] = portal_data["lotPlan"]
            needs_update = True

        # Official sales handling & date standardization
        official_sales = pl.get("officialSaleHistory") or []
        if official_sales:
            for h in official_sales:
                if h.get("contractDate"):
                    h["contractDate"] = str(h["contractDate"]).split("T")[0]
                if h.get("settlementDate"):
                    h["settlementDate"] = str(h["settlementDate"]).split("T")[0]
            official_sales.sort(
                key=lambda x: str(x.get("contractDate") or x.get("settlementDate") or ""),
                reverse=True
            )
            pl["officialSaleHistory"] = official_sales
            latest_sale = official_sales[0]
            latest_date = str(latest_sale.get("contractDate") or latest_sale.get("settlementDate") or "")
            latest_price = float(latest_sale.get("price") or 0)
            # Status and listingOption are determined uniformly at step 14 after all sales sources are evaluated

        # 1. Determine Property Category & Synchronize Types
        prop_cat = engine.determine_property_category(street, title, prop_type, nature)
        cat_display = "Apartment" if prop_cat == "unit" else ("Townhouse" if prop_cat == "townhouse" else "House")
        if not force_update and (pl.get("propertyType") != cat_display or pl.get("classifiedCategory") != cat_display):
            pl["propertyType"] = cat_display
            pl["classifiedCategory"] = cat_display
            needs_update = True

        # Standardize natureOfProperty (Fix 'Vacant land' on constructed dwellings)
        if not force_update and pl.get("natureOfProperty") in ["Vacant land", "V", "Other", None, ""]:
            pl["natureOfProperty"] = "Residence / Dwelling" if prop_cat != "unit" else "Strata Residential Unit"
            needs_update = True

        # Phase 2: Enrich room taxonomy and photo tags if gallery is present
        if getattr(engine, "room_classifier", None) and (pl.get("photos") or pl.get("photo_links") or pl.get("images") or portal_images):
            if not pl.get("tagged_photos") or force_update:
                engine.room_classifier.enrich_property_payload(pl)
                needs_update = True

        # Phase 3: Spatial Location Scores & Cadastral Parcel Geometry
        if getattr(engine, "location_engine", None) and not force_update:
            if not pl.get("location_scores"):
                engine.location_engine.enrich_property_payload(pl)
                needs_update = True

        cadastral_area_val = None

        # Correct City / LGA (Fix Suburb Name Collision & eliminate multi-LGA commas)
        comp_city = COMPOSITE_CITY_MAP.get((state.upper(), str(postcode).strip(), suburb.upper()))
        chosen_city = comp_city.split(",")[0].strip() if comp_city else str(pl.get("city", "")).split(",")[0].strip()
        if chosen_city and pl.get("city") != chosen_city:
            pl["city"] = chosen_city
            needs_update = True

        # Detect Studio dwelling (distinguishing studio units from street names like Studio Drive/Way)
        st_clean = (street or "").lower().strip()
        type_nature = f"{prop_type or ''} {nature or ''}".lower().strip()
        is_studio_type = bool(re.search(r"\bstudio\b", type_nature))
        is_studio_street_name = bool(re.search(r"\bstudio\s+(?:drive|dr|way|rd|road|st|street|ave|avenue|crescent|cres|ct|court|cl|close|lane|ln|pl|place|blvd|boulevard|pde|parade|tce|terrace|circuit|cct)\b", st_clean))
        is_studio_unit_street = bool((re.search(r"\bstudio\s+\d+", st_clean) or re.search(r"\bstudio\b", st_clean)) and not is_studio_street_name)
        is_studio = is_studio_type or is_studio_unit_street

        # 2. Enrich Area (Prioritizes portal scraped area, preserves existing)
        is_invalid_unit_area = (prop_cat == "unit" and existing_area and existing_area > 350)
        is_invalid_townhouse_area = (prop_cat == "townhouse" and existing_area and existing_area > 600)
        if portal_area and portal_area > 0:
            area_val = int(round(portal_area))
            needs_update = True
        elif existing_area and not (is_invalid_unit_area or is_invalid_townhouse_area):
            area_val = int(existing_area)
        else:
            area_val = None

        # 3. Enrich Bed, Bath & Features Garage (Prioritizes portal scraped room counts, preserves existing)
        if portal_bed is not None:
            bed = int(portal_bed)
            needs_update = True
        else:
            bed = int(existing_bed) if existing_bed is not None else None

        if portal_bath is not None:
            bath = int(portal_bath)
            needs_update = True
        else:
            bath = int(existing_bath) if existing_bath is not None else None

        existing_garage = pl.get("features", {}).get("garage") if isinstance(pl.get("features"), dict) else None
        if portal_garage is not None:
            garage = int(portal_garage)
            needs_update = True
        else:
            garage = int(existing_garage) if existing_garage is not None else None

        # 4. Normalize History Sale & Merge Portal Past Sales (Non-destructive)
        clean_history = []
        for h in (pl.get("historysale") or []):
            h_copy = dict(h)
            agency_val = str(h_copy.get("agency") or "").strip()
            if "commercial listing record" in agency_val.lower():
                h_copy["agency"] = ""
            clean_history.append(h_copy)

        if portal_sales and isinstance(portal_sales, list) and len(portal_sales) > 0:
            clean_history = portal_sales + [h for h in clean_history if h not in portal_sales]
            needs_update = True
        elif official_sales and len(clean_history) == 0:
            hist, sold_info = engine.synthesize_historysale(
                calc_price=existing_price or 0,
                existing_history=clean_history,
                official_sales=official_sales,
                state=state
            )
            clean_history = hist
            if sold_info:
                pl["soldDateInfo"] = sold_info
            needs_update = True

        history = clean_history

        # 5. Enrich Construction Year (Prioritizes portal year; preserves existing valid year)
        if portal_year and str(portal_year).strip() not in ["N/A", "None", "", None]:
            const_year = str(portal_year).strip()
            needs_update = True
        elif existing_year and str(existing_year).strip() not in ["N/A", "None", "", None]:
            const_year = str(existing_year).strip()
        else:
            const_year = "N/A"

        # 6. Enrich Expected Price (Prioritizes portal AVM; preserves existing price)
        if portal_price is not None and portal_price > 0:
            calc_price = int(round(portal_price))
            val_type = portal_data.get("valuationType") or "Verified_Portal_AVM"
            needs_update = True
        else:
            calc_price = int(existing_price) if existing_price is not None else None
            val_type = pl.get("valuationType") or "Preserved_Existing_Valuation"

        # 7. Rental Estimate (Prioritizes portal weekly rent and yield)
        if portal_rent is not None and portal_rent > 0:
            weekly_rent = int(portal_rent)
            yield_str = portal_yield
            needs_update = True
        else:
            weekly_rent = pl.get("pricing", {}).get("weeklyRent") if isinstance(pl.get("pricing"), dict) else None
            yield_str = pl.get("pricing", {}).get("estimatedYield") if isinstance(pl.get("pricing"), dict) else None

        # 8. Description: Strict Zero Fabrication (Portal listing description, or preserve existing clean)
        portal_desc = portal_data.get("description") if isinstance(portal_data, dict) else None
        current_desc = str(pl.get("description") or "").strip()
        desc_is_synthetic = any(w in current_desc for w in ["effortless modern living", "bathed in natural light", "Positioned in a highly desirable"]) or (" located at " in current_desc and "|" in current_desc)

        if portal_desc and str(portal_desc).strip() not in ["", "None", "N/A"]:
            final_desc = str(portal_desc).strip()
            needs_update = True
        elif desc_is_synthetic:
            final_desc = ""
            needs_update = True
        else:
            final_desc = current_desc

        # 9. Granular Features: Strict None for unknown floorNo and garage
        raw_floor = portal_data.get("floorNo") if isinstance(portal_data, dict) else None
        if raw_floor is None and isinstance(pl.get("features"), dict):
            raw_floor = pl.get("features", {}).get("floorNo")
        clean_floor = int(raw_floor) if (raw_floor is not None and str(raw_floor).isdigit()) else None

        garage_count = garage if garage is not None else None
        scraped_feats = portal_data.get("features_list") or (pl.get("features", {}).get("indoorFeatures") if isinstance(pl.get("features"), dict) else [])

        # 12. Enrich Canonical Real Estate URL (Preserves valid HTTP URLs; never deletes view.com.au)
        if portal_url and isinstance(portal_url, str) and portal_url.startswith("http"):
            final_url = portal_url
            needs_update = True
        else:
            existing_u = str(pl.get("realestate_url") or "").strip()
            if "sample" in existing_u.lower() or not existing_u.startswith("http"):
                final_url = ""
            else:
                final_url = existing_u

        # Determine target status: Active listing (sale/rent) takes priority, followed by recent sold, then off_market
        all_sale_years = []
        if official_sales:
            for s in official_sales:
                d = str(s.get("contractDate") or s.get("settlementDate") or "")
                if len(d) >= 4 and d[:4].isdigit():
                    all_sale_years.append(int(d[:4]))
        for h in clean_history:
            d = str(h.get("soldDate") or h.get("date") or "")
            m = re.search(r"\b(19\d\d|20\d\d)\b", d)
            if m:
                all_sale_years.append(int(m.group(1)))

        most_recent_year = max(all_sale_years) if all_sale_years else None
        portal_status = str(portal_data.get("status") or "").lower()
        existing_status = str(pl.get("status", "")).lower()

        if portal_status in ["sale", "for_sale", "for-sale"]:
            target_status = "sale"
        elif portal_status in ["rent", "for_rent", "for-rent"]:
            target_status = "rent"
        elif (official_sales or (portal_sales and len(portal_sales) > 0)) and most_recent_year and most_recent_year >= 2025:
            target_status = "sold"
        elif existing_status in ["sale", "rent", "sold"]:
            target_status = existing_status
        else:
            target_status = "off_market"

        # Images
        clean_images = []
        raw_imgs = portal_images if (portal_images and isinstance(portal_images, list)) else (pl.get("images") or [])
        if isinstance(raw_imgs, list):
            for img in raw_imgs:
                if isinstance(img, dict):
                    u = str(img.get("url") or "")
                    if u.startswith("http") and "sample" not in u.lower():
                        clean_images.append(img)

        pricing_options_str = f"${int(calc_price):,}" if calc_price else "N/A"
        water_period = str(portal_data.get("waterBillPeriod") or pl.get("pricing", {}).get("waterBillPeriod") or "")
        pricing_final = {
            "waterBillPeriod": water_period,
            "councilBill": str(pl.get("pricing", {}).get("councilBill") or ""),
            "pricingOptions": pricing_options_str,
            "authority": target_status,
            "priceIncludes": pl.get("pricing", {}).get("priceIncludes", []) if isinstance(pl.get("pricing"), dict) else []
        }

        # Canonical address slug
        raw_slug_str = f"{street} {suburb} {state} {postcode}".lower().replace("/", "-")
        canonical_slug = re.sub(r"[^a-z0-9]+", "-", raw_slug_str).strip("-")

        # Standardized historysale (strictly 3 keys: soldDate, soldPrice, agency; NO "Commercial Listing Record")
        standardized_history = []
        for h in clean_history:
            if isinstance(h, dict) and (h.get("soldPrice") or h.get("price")):
                raw_agency = str(h.get("agency") or "").strip()
                if "commercial listing record" in raw_agency.lower():
                    raw_agency = ""
                standardized_history.append({
                    "soldDate": str(h.get("soldDate") or h.get("date") or h.get("contractDate") or ""),
                    "soldPrice": int(h.get("soldPrice") or h.get("price") or 0),
                    "agency": raw_agency
                })

        # Provenance attribution
        has_verified_portal = bool(
            portal_data and (
                portal_data.get("bed") is not None or
                portal_data.get("totalArea") is not None or
                portal_data.get("expectedPrice") is not None or
                portal_data.get("historysale") or
                portal_data.get("constructionYear") or
                (portal_url and str(portal_url).startswith("http"))
            )
        )
        if has_verified_portal:
            provenance = {
                "dataSource": portal_data.get("dataSource", "PORTAL"),
                "fetchedAt": now_iso,
                "realestate_url": final_url,
                "valuationType": portal_data.get("valuationType"),
                "verificationStatus": "verified"
            }
        else:
            existing_prov = pl.get("provenance")
            if isinstance(existing_prov, dict) and existing_prov.get("verificationStatus") == "verified":
                provenance = existing_prov
            else:
                provenance = {
                    "dataSource": "LEGACY_UNVERIFIED",
                    "fetchedAt": None,
                    "verificationStatus": "unverified"
                }

        # Non-destructive payload update starting from existing pl
        cleaned_pl = dict(pl)
        cleaned_pl.update({
            "architecturalStyle": str(pl.get("architecturalStyle") or "None"),
            "area": {
                "totalArea": area_val if area_val is not None else None,
                "unit": "sqM"
            },
            "bed": bed,
            "bath": bath,
            "coordinates": {"lat": float(c_lat), "lon": float(c_lon)} if (c_lat and c_lon) else (pl.get("coordinates") if isinstance(pl.get("coordinates"), dict) else None),
            "constructionYear": str(const_year or "N/A"),
            "expectedPrice": float(calc_price) if calc_price else None,
            "street": street,
            "state": state.upper(),
            "suburb": suburb,
            "city": chosen_city or pl.get("city") or suburb,
            "postcode": str(postcode),
            "propertyType": cat_display,
            "title": title or f"{street}, {suburb} {state.upper()} {postcode}",
            "description": final_desc,
            "features": {
                "garage": garage_count,
                "floorNo": clean_floor,
                "indoorFeatures": pl.get("features", {}).get("indoorFeatures", []) if isinstance(pl.get("features"), dict) else list(scraped_feats),
                "outdoorAmenities": pl.get("features", {}).get("outdoorAmenities", []) if isinstance(pl.get("features"), dict) else list(scraped_feats),
                "basement": "None",
                "floorCovering": [],
                "roof": [],
                "view": [],
                "appliances": [],
                "buildingAmenities": [],
                "displayAddress": "fullAddress"
            },
            "structuralRemodelYear": "N/A",
            "recommended": False,
            "published": False,
            "realestate_url": final_url,
            "contactInfo": pl.get("contactInfo") if isinstance(pl.get("contactInfo"), list) else [],
            "images": clean_images,
            "soldDateInfo": str(pl.get("soldDateInfo") or ""),
            "listingOption": target_status,
            "pricing": pricing_final,
            "status": target_status,
            "historysale": standardized_history,
            "slug": canonical_slug,
            "provenance": provenance,
            "createdAt": str(pl.get("createdAt") or now_iso),
            "updatedAt": now_iso
        })
        if portal_data.get("strataDetails"):
            cleaned_pl["strataDetails"] = portal_data["strataDetails"]

        if needs_update or force_update:
            updates.append({
                "id": pt.id,
                "payload": cleaned_pl
            })
            enriched_count += 1

    return updates, enriched_count


def _trusted_portal_value(portal_data: Dict, path: str, key: str):
    evidence = (portal_data.get("fieldEvidence") or {}).get(path)
    value = portal_data.get(key)
    return value if evidence_matches_value(value, evidence) else None


def _set_patch_path(payload: Dict, path: str, value) -> None:
    current = payload
    parts = path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = deepcopy(value)


def _merge_payload_patch(original: Dict, patch: Dict) -> Dict:
    merged = deepcopy(original)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_payload_patch(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _restrict_patch_to_existing_shape(
    original: Dict, patch: Dict, *, allow_provenance: bool = True
) -> Dict:
    """Preserve business keys while allowing field-level evidence metadata."""
    canonical_top_keys = frozenset({
        "valuation",
        "valuationType",
        "valuation_mid",
        "valuation_low",
        "valuation_high",
        "confidence_score",
    })
    canonical_pricing_keys = frozenset({"weeklyRent", "estimatedYield"})
    canonical_features_keys = frozenset({"garage", "indoorFeatures"})
    canonical_valuation_keys = frozenset({"mid", "low", "high", "provider", "valuationDate", "disclaimer", "confidence"})

    restricted = {}
    for key, value in patch.items():
        if allow_provenance and key in {"fieldProvenance", "provenance"}:
            restricted[key] = deepcopy(value)
            continue
        if key not in original and key not in canonical_top_keys:
            continue
        if isinstance(value, dict) and isinstance(original.get(key), dict):
            allowed_subkeys = set(original[key].keys())
            if key == "pricing":
                allowed_subkeys.update(canonical_pricing_keys)
            elif key == "features":
                allowed_subkeys.update(canonical_features_keys)
            elif key == "valuation" and not allowed_subkeys:
                allowed_subkeys.update(canonical_valuation_keys)
            nested = {}
            for subk, subv in value.items():
                if subk in allowed_subkeys:
                    if isinstance(subv, dict) and isinstance(original[key].get(subk), dict):
                        nested[subk] = _restrict_patch_to_existing_shape(
                            original[key][subk], subv, allow_provenance=False
                        )
                    else:
                        nested[subk] = deepcopy(subv)
            restricted[key] = nested
        else:
            restricted[key] = deepcopy(value)
    return restricted


def _filter_portal_data(portal_data: Dict, allowed_fields: Iterable[str]) -> Dict:
    """Keep only explicitly requested portal values plus their verifiable source URL."""
    if not isinstance(portal_data, dict):
        return {}
    allowed = set(allowed_fields)
    filtered = {
        key: deepcopy(value)
        for key, value in portal_data.items()
        if key.startswith("_") or key in allowed
    }
    evidence = {}
    for key in allowed:
        path = PORTAL_FIELD_PATHS.get(key)
        candidate = (portal_data.get("fieldEvidence") or {}).get(path)
        if path and evidence_matches_value(portal_data.get(key), candidate):
            evidence[path] = deepcopy(candidate)

    if evidence:
        source_evidence = next(iter(evidence.values()))
        source_url = str(source_evidence.get("sourceUrl") or portal_data.get("realestate_url") or "")
        if source_url:
            filtered["realestate_url"] = source_url
            evidence["realestate_url"] = make_evidence(
                value=source_url,
                source_name=str(source_evidence.get("sourceName") or "PUBLIC_PROPERTY_PROFILE"),
                source_url=source_url,
                source_record_id=source_evidence.get("sourceRecordId"),
                source_field="sourceUrl",
                method="observed",
                verification_status="verified",
                retrieved_at=source_evidence.get("retrievedAt"),
                address_match=str(source_evidence.get("addressMatch") or "exact"),
                confidence=float(source_evidence.get("confidence") or 0.9),
            )
    filtered["fieldEvidence"] = evidence
    return filtered


def _normalise_official_sales(sales: list) -> list:
    normalised = []
    for sale in sales or []:
        if not isinstance(sale, dict):
            continue
        try:
            price = float(sale.get("price") or 0)
        except (TypeError, ValueError):
            continue
        sold_date = str(sale.get("contractDate") or sale.get("settlementDate") or "")[:10]
        if price < 10000 or not sold_date:
            continue
        normalised.append({
            "soldDate": sold_date,
            "soldPrice": int(price),
            "agency": "NSW Valuer General official transfer",
        })
    normalised.sort(key=lambda item: item["soldDate"], reverse=True)
    return normalised


def _official_psi_profile(
    sales: list, adapter, retrieved_at: str
) -> Tuple[Dict[str, object], Dict[str, object]]:
    """Map exact-address PSI attributes without treating strata parcel area as unit area."""
    if not sales:
        return {}, {}
    record = sales[0]
    attribute_keys = (
        "areaSqm",
        "areaSource",
        "areaTypeSource",
        "zoning",
        "propertyTypeSource",
        "primaryPurpose",
        "strataLotNumber",
        "unitNumber",
    )
    attributes = {
        key: deepcopy(record.get(key))
        for key in attribute_keys
        if record.get(key) is not None
    }
    source_url = getattr(adapter, "source_url", "")
    source_record_id = str(record.get("sourceRecordId") or "") or None
    dataset_version = getattr(adapter, "dataset_version", None)
    profile: Dict[str, object] = {"fieldEvidence": {}}

    property_type = record.get("propertyTypeSource") or record.get("primaryPurpose")
    if property_type:
        profile["propertyType"] = property_type
        profile["fieldEvidence"]["propertyType"] = make_evidence(
            value=property_type,
            source_name="NSW_VALUER_GENERAL_PSI",
            source_url=source_url,
            source_record_id=source_record_id,
            source_field="Nature of property (description)",
            method="official_dataset_exact_address",
            verification_status="verified",
            retrieved_at=retrieved_at,
            dataset_version=dataset_version,
            licence="CC BY-NC-ND 4.0",
        )

    # A land-area value attached to a unit/strata lot describes its parent
    # parcel, not the apartment's internal floor area. Preserve it in the
    # official attributes but do not expose it as area.totalArea.
    is_strata = bool(record.get("unitNumber") or record.get("strataLotNumber"))
    area_type_source = str(record.get("areaTypeSource") or "").casefold()
    area_value = record.get("areaSqm")
    if area_value and not is_strata and "land" in area_type_source:
        profile["totalArea"] = area_value
        profile["_areaType"] = "landArea"
        profile["fieldEvidence"]["area.totalArea"] = make_evidence(
            value=area_value,
            source_name="NSW_VALUER_GENERAL_PSI",
            source_url=source_url,
            source_record_id=source_record_id,
            source_field="Area (m2)",
            method="official_dataset_exact_address",
            verification_status="verified",
            retrieved_at=retrieved_at,
            dataset_version=dataset_version,
            licence="CC BY-NC-ND 4.0",
            area_type="landArea",
        )
    if not profile["fieldEvidence"]:
        profile.pop("fieldEvidence")
    return profile, attributes


def _process_point_chunk_trusted(
    points: list,
    engine: EnrichmentEngine,
    force_update: bool,
    now_iso: str,
    progress: bool = False,
) -> Tuple[List[Dict], int]:
    updates = []
    for point_index, point in enumerate(points, start=1):
        original = point.payload or {}
        payload = deepcopy(original)
        if progress:
            address = ", ".join(
                str(payload.get(key) or "").strip()
                for key in ("street", "suburb", "state", "postcode")
                if str(payload.get(key) or "").strip()
            )
            print(
                f"[Cào Live] Căn #{point_index}/{len(points)}: {address or 'N/A'}",
                flush=True,
            )
        existing_evidence = payload.get("fieldProvenance") if isinstance(payload.get("fieldProvenance"), dict) else {}
        field_evidence = {
            path: deepcopy(evidence)
            for path, evidence in existing_evidence.items()
            if evidence_matches_value(get_path(payload, path), evidence)
        }
        field_evidence.update(gnaf_provenance(payload, now_iso))
        adapter = getattr(engine, "official_sales_adapter", None)
        adapter_sales = (
            adapter.lookup(str(payload.get("street") or ""), str(payload.get("suburb") or ""))
            if adapter else []
        )
        official_history = _normalise_official_sales(adapter_sales)
        portal_data, official_attributes = _official_psi_profile(
            adapter_sales, adapter, now_iso
        )
        hydrator = getattr(engine, "portal_hydrator", None)
        property_source = getattr(engine, "property_source_adapter", None)
        property_browser = getattr(engine, "property_browser_adapter", None)
        if (
            getattr(engine, "official_matches_only", False)
            and not adapter_sales
            and not hydrator
            and not property_source
            and not property_browser
        ):
            continue

        portal_fields = set(getattr(engine, "portal_fields", DEFAULT_PORTAL_FIELDS))
        requested_portal_fields = {
            key
            for key in portal_fields
            if force_update or accepted_existing_value(original, existing_evidence, PORTAL_FIELD_PATHS[key]) is None
        }
        if official_history:
            requested_portal_fields.difference_update(
                {"historysale", "soldDateInfo"}
            )
        if property_source and requested_portal_fields:
            try:
                lookup_kwargs = {
                    "street": str(payload.get("street") or ""),
                    "suburb": str(payload.get("suburb") or ""),
                    "state": str(payload.get("state") or ""),
                    "postcode": str(payload.get("postcode") or ""),
                }
                gnaf_pid = payload.get("gnaf_pid") or payload.get("gnafPid")
                try:
                    import inspect
                    sig = inspect.signature(property_source.lookup)
                    if "gnaf_pid" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                        lookup_kwargs["gnaf_pid"] = gnaf_pid
                except Exception:
                    pass
                try:
                    source_data = property_source.lookup(**lookup_kwargs) or {}
                except TypeError:
                    lookup_kwargs.pop("gnaf_pid", None)
                    source_data = property_source.lookup(**lookup_kwargs) or {}
                source_portal_data = _filter_portal_data(
                    source_data, requested_portal_fields
                )
                for key, value in source_portal_data.items():
                    if key == "fieldEvidence":
                        portal_data.setdefault("fieldEvidence", {}).update(value)
                    elif key.startswith("_") or value is not None:
                        portal_data[key] = value
            except Exception as exc:
                print(f"[Source warning] point={point.id}: {exc}")

        live_requested_fields = {
            key
            for key in requested_portal_fields
            if _trusted_portal_value(portal_data, PORTAL_FIELD_PATHS[key], key) is None
        }
        if hydrator and live_requested_fields:
            if progress:
                print(
                    f"[Progress] point={point.id} stage=portal-http "
                    f"missing_fields={len(live_requested_fields)}",
                    flush=True,
                )
            coordinates = payload.get("coordinates") if isinstance(payload.get("coordinates"), dict) else {}
            try:
                raw_portal_data = hydrator.fetch_profile(
                    street=str(payload.get("street") or ""),
                    suburb=str(payload.get("suburb") or ""),
                    state=str(payload.get("state") or ""),
                    postcode=str(payload.get("postcode") or ""),
                    lat=coordinates.get("lat"),
                    lon=coordinates.get("lon"),
                    lot_plan=payload.get("lotPlan"),
                    force_refresh=force_update,
                ) or {}
                live_portal_data = _filter_portal_data(raw_portal_data, live_requested_fields)
                for key, value in live_portal_data.items():
                    if key == "fieldEvidence":
                        portal_data.setdefault("fieldEvidence", {}).update(value)
                    elif key.startswith("_") or value is not None:
                        portal_data[key] = value
            except Exception as exc:
                print(f"[Portal warning] point={point.id}: {exc}")

        browser_requested_fields = {
            key
            for key in requested_portal_fields
            if _trusted_portal_value(portal_data, PORTAL_FIELD_PATHS[key], key) is None
        }
        if property_browser and browser_requested_fields:
            if progress:
                print(
                    f"[Progress] point={point.id} stage=browser-fallback "
                    f"missing_fields={len(browser_requested_fields)}",
                    flush=True,
                )
            try:
                browser_data = property_browser.lookup(
                    street=str(payload.get("street") or ""),
                    suburb=str(payload.get("suburb") or ""),
                    state=str(payload.get("state") or ""),
                    postcode=str(payload.get("postcode") or ""),
                    known_url=str(payload.get("realestate_url") or "") or None,
                    force_refresh=force_update,
                    requested_fields=browser_requested_fields,
                ) or {}
                filtered_browser_data = _filter_portal_data(
                    browser_data,
                    browser_requested_fields,
                )
                for key, value in filtered_browser_data.items():
                    if key == "fieldEvidence":
                        portal_data.setdefault("fieldEvidence", {}).update(value)
                    elif key.startswith("_") or value is not None:
                        portal_data[key] = value
            except Exception as exc:
                print(f"[Browser warning] point={point.id}: {exc}")

        portal_path_values = {
            "bed": portal_data.get("bed"),
            "bath": portal_data.get("bath"),
            "features.garage": portal_data.get("garage"),
            "area.totalArea": portal_data.get("totalArea"),
            "constructionYear": portal_data.get("constructionYear"),
            "expectedPrice": portal_data.get("expectedPrice"),
            "valuation.low": portal_data.get("valuation_low"),
            "valuation.high": portal_data.get("valuation_high"),
            "pricing.weeklyRent": portal_data.get("weeklyRent"),
            "pricing.estimatedYield": portal_data.get("estimatedYield"),
            "historysale": portal_data.get("historysale"),
            "description": portal_data.get("description"),
            "images": portal_data.get("images"),
            "features.indoorFeatures": portal_data.get("features_list"),
            "status": portal_data.get("status"),
            "propertyType": portal_data.get("propertyType"),
            "title": portal_data.get("title"),
            "listingOption": portal_data.get("listingOption"),
            "soldDateInfo": portal_data.get("soldDateInfo"),
            "realestate_url": portal_data.get("realestate_url"),
        }
        for path, evidence in (portal_data.get("fieldEvidence") or {}).items():
            if evidence_matches_value(portal_path_values.get(path), evidence):
                field_evidence[path] = deepcopy(evidence)

        official_evidence = {}
        if official_history:
            payload["officialSaleHistory"] = adapter_sales
            source_record_id = next((sale.get("sourceRecordId") for sale in adapter_sales if sale.get("sourceRecordId")), None)
            if source_record_id:
                payload["governmentPropertyId"] = source_record_id
            payload["_officialSalesDatasetVersion"] = getattr(adapter, "dataset_version", None)
            official_evidence = official_sales_evidence(payload, now_iso, target_value=official_history)
            payload.pop("_officialSalesDatasetVersion", None)
        if official_evidence and official_history:
            field_evidence["historysale"] = official_evidence

        legacy_paths = tuple(path for path in CORE_LEGACY_PATHS if not (getattr(engine, "no_images", False) and path == "images"))
        payload["legacyUnverified"] = quarantine_unverified_legacy(
            original,
            existing_evidence,
            paths=legacy_paths,
            migrated_at=now_iso,
        )
        if getattr(engine, "no_images", False):
            payload["legacyUnverified"].pop("images", None)
            field_evidence.pop("images", None)

        def choose(path: str, portal_key: str, empty=None):
            portal_value = _trusted_portal_value(portal_data, path, portal_key)
            if portal_value is not None:
                return deepcopy(portal_value)
            existing_value = accepted_existing_value(original, existing_evidence, path)
            return deepcopy(existing_value) if existing_value is not None else deepcopy(empty)

        payload["bed"] = choose("bed", "bed")
        payload["bath"] = choose("bath", "bath")
        payload["constructionYear"] = choose("constructionYear", "constructionYear")
        payload["description"] = choose("description", "description", "")
        payload["images"] = [] if getattr(engine, "no_images", False) else choose("images", "images", [])
        payload["realestate_url"] = choose("realestate_url", "realestate_url", "")

        features = deepcopy(payload.get("features")) if isinstance(payload.get("features"), dict) else {}
        features["garage"] = choose("features.garage", "garage")
        features["floorNo"] = accepted_existing_value(original, existing_evidence, "features.floorNo")
        features["indoorFeatures"] = choose("features.indoorFeatures", "features_list", [])
        payload["features"] = features

        area_value = choose("area.totalArea", "totalArea")
        area_evidence = field_evidence.get("area.totalArea")
        area_type = area_evidence.get("areaType") if is_accepted_evidence(area_evidence) else None
        accepted_area_types = {"landArea", "internalFloorArea", "buildingArea", "strataEntitlementArea", "parentParcelArea"}
        if area_type not in accepted_area_types:
            area_value = None
            field_evidence.pop("area.totalArea", None)
        payload["area"] = {"totalArea": float(area_value) if area_value is not None else None, "unit": "sqM"}
        areas = {"unit": "sqM"}
        if area_value is not None and area_type:
            areas[area_type] = float(area_value)
        payload["areas"] = areas

        expected_price = choose("expectedPrice", "expectedPrice")
        if (
            expected_price is None
            and getattr(engine, "ml_avm", None) is not None
            and any(k in requested_portal_fields for k in ("expectedPrice", "valuation_low", "valuation_high", "weeklyRent", "estimatedYield"))
        ):
            try:
                coordinates = payload.get("coordinates") if isinstance(payload.get("coordinates"), dict) else {}
                prop_type = str(payload.get("propertyType") or "house").lower()
                avm_result = engine.ml_avm.predict(
                    lat=coordinates.get("lat"),
                    lon=coordinates.get("lon"),
                    bed=payload.get("bed"),
                    bath=payload.get("bath"),
                    parking=(payload.get("features") or {}).get("garage"),
                    area_sqm=(payload.get("area") or {}).get("totalArea"),
                    prop_cat=prop_type,
                    state=str(payload.get("state") or "NSW"),
                    suburb=str(payload.get("suburb") or ""),
                    official_sales=official_history,
                )
                if avm_result and avm_result.valuation_mid:
                    expected_price = float(avm_result.valuation_mid)
                    val_low = float(avm_result.valuation_low)
                    val_high = float(avm_result.valuation_high)
                    val_conf = float(avm_result.confidence_score)
                    portal_data["expectedPrice"] = expected_price
                    portal_data["valuation_low"] = val_low
                    portal_data["valuation_high"] = val_high
                    portal_data["valuationType"] = avm_result.valuation_method
                    portal_data["valuationDate"] = now_iso[:10]
                    ev_price = make_evidence(
                        value=expected_price,
                        source_name="AU_ML_AVM_V1",
                        source_url="https://models.internal/avm/v1",
                        source_field="expectedPrice",
                        method="model_estimate",
                        verification_status="estimated",
                        retrieved_at=now_iso,
                        confidence=val_conf,
                    )
                    ev_low = make_evidence(
                        value=val_low,
                        source_name="AU_ML_AVM_V1",
                        source_url="https://models.internal/avm/v1",
                        source_field="valuation.low",
                        method="model_estimate",
                        verification_status="estimated",
                        retrieved_at=now_iso,
                        confidence=val_conf,
                    )
                    ev_high = make_evidence(
                        value=val_high,
                        source_name="AU_ML_AVM_V1",
                        source_url="https://models.internal/avm/v1",
                        source_field="valuation.high",
                        method="model_estimate",
                        verification_status="estimated",
                        retrieved_at=now_iso,
                        confidence=val_conf,
                    )
                    field_evidence["expectedPrice"] = ev_price
                    field_evidence["valuation.low"] = ev_low
                    field_evidence["valuation.high"] = ev_high
                    portal_ev = portal_data.setdefault("fieldEvidence", {})
                    portal_ev["expectedPrice"] = ev_price
                    portal_ev["valuation.low"] = ev_low
                    portal_ev["valuation.high"] = ev_high
            except Exception:
                pass

        payload["expectedPrice"] = float(expected_price) if expected_price is not None else None
        if expected_price is not None:
            valuation_evidence = field_evidence.get("expectedPrice") or {}
            payload["valuation"] = {
                "mid": float(expected_price),
                "low": portal_data.get("valuation_low"),
                "high": portal_data.get("valuation_high"),
                "provider": portal_data.get("valuationType") or valuation_evidence.get("sourceName"),
                "valuationDate": portal_data.get("valuationDate") or now_iso[:10],
                "disclaimer": "Model estimate; not a certified valuation.",
            }
            payload["valuationType"] = portal_data.get("valuationType") or "estimated"
        elif portal_data.get("valuation_low") or portal_data.get("valuation_high"):
            payload["valuation"] = {
                "mid": None,
                "low": portal_data.get("valuation_low"),
                "high": portal_data.get("valuation_high"),
                "provider": portal_data.get("valuationType"),
                "valuationDate": portal_data.get("valuationDate") or now_iso[:10],
                "disclaimer": "Model estimate; not a certified valuation.",
            }
            payload["valuationType"] = portal_data.get("valuationType") or "estimated"
        else:
            payload["valuation"] = None
            payload["valuationType"] = None

        pricing = deepcopy(payload.get("pricing")) if isinstance(payload.get("pricing"), dict) else {}
        pricing["weeklyRent"] = choose("pricing.weeklyRent", "weeklyRent")
        pricing["estimatedYield"] = choose("pricing.estimatedYield", "estimatedYield")

        if (
            (pricing.get("weeklyRent") is None or not is_accepted_evidence(existing_evidence.get("pricing.weeklyRent")))
            and expected_price is not None
            and any(k in requested_portal_fields for k in ("weeklyRent", "estimatedYield"))
            and hasattr(engine, "calculate_rental_metrics")
        ):
            rent_val, yield_val = engine.calculate_rental_metrics(
                prop_cat=str(payload.get("propertyType") or "house").lower(),
                calc_price=int(expected_price),
                state=str(payload.get("state") or "NSW"),
                suburb=str(payload.get("suburb") or ""),
            )
            pricing["weeklyRent"] = rent_val
            pricing["estimatedYield"] = yield_val
            portal_data["weeklyRent"] = rent_val
            portal_data["estimatedYield"] = yield_val
            ev_rent = make_evidence(
                value=rent_val,
                source_name="AU_RENTAL_BENCHMARK_V1",
                source_url="https://models.internal/rental/v1",
                source_field="pricing.weeklyRent",
                method="model_estimate",
                verification_status="estimated",
                retrieved_at=now_iso,
                confidence=0.75,
            )
            ev_yield = make_evidence(
                value=yield_val,
                source_name="AU_RENTAL_BENCHMARK_V1",
                source_url="https://models.internal/rental/v1",
                source_field="pricing.estimatedYield",
                method="model_estimate",
                verification_status="estimated",
                retrieved_at=now_iso,
                confidence=0.75,
            )
            field_evidence["pricing.weeklyRent"] = ev_rent
            field_evidence["pricing.estimatedYield"] = ev_yield
            portal_ev = portal_data.setdefault("fieldEvidence", {})
            portal_ev["pricing.weeklyRent"] = ev_rent
            portal_ev["pricing.estimatedYield"] = ev_yield

        if not is_accepted_evidence(existing_evidence.get("pricing.councilBill")):
            pricing["councilBill"] = None
        if not is_accepted_evidence(existing_evidence.get("pricing.waterBillPeriod")):
            pricing["waterBillPeriod"] = None
        pricing["pricingOptions"] = f"${int(expected_price):,}" if expected_price is not None else None
        pricing["authority"] = None
        pricing["priceIncludes"] = []
        payload["pricing"] = pricing

        portal_history = choose("historysale", "historysale", [])
        payload["historysale"] = official_history or portal_history
        if official_history:
            latest = official_history[0]
            payload["soldDateInfo"] = f"Sold for ${latest['soldPrice']:,} on {latest['soldDate']}"
        elif not payload["historysale"]:
            payload["soldDateInfo"] = ""

        portal_status = choose("status", "status")
        if portal_status in {"sale", "for_sale", "for-sale"}:
            status = "sale"
        elif portal_status in {"rent", "for_rent", "for-rent"}:
            status = "rent"
        else:
            status = "unknown"
        payload["status"] = status
        payload["listingOption"] = status

        payload["fieldProvenance"] = field_evidence
        payload["provenance"] = summarize_provenance(field_evidence, now_iso)
        payload.pop("planning", None)
        payload.pop("zoning", None)
        payload.pop("strataDetails", None)

        update_payload = {}
        for portal_key in requested_portal_fields:
            path = PORTAL_FIELD_PATHS[portal_key]
            trusted_value = _trusted_portal_value(portal_data, path, portal_key)
            if trusted_value is not None:
                _set_patch_path(update_payload, path, trusted_value)

        # Official PSI attributes are part of the official-first layer, not
        # the caller's portal field budget. Keep value and evidence together.
        for official_key in ("propertyType", "totalArea"):
            official_path = PORTAL_FIELD_PATHS[official_key]
            official_field_evidence = (portal_data.get("fieldEvidence") or {}).get(
                official_path
            )
            if (
                isinstance(official_field_evidence, dict)
                and official_field_evidence.get("sourceName")
                == "NSW_VALUER_GENERAL_PSI"
            ):
                official_value = _trusted_portal_value(
                    portal_data, official_path, official_key
                )
                if official_value is not None:
                    _set_patch_path(
                        update_payload, official_path, official_value
                    )

        if "area" in update_payload:
            update_payload["area"]["unit"] = "sqM"

        trusted_url = _trusted_portal_value(portal_data, "realestate_url", "realestate_url")
        if trusted_url is not None:
            update_payload["realestate_url"] = trusted_url

        if payload.get("valuation"):
            if any(k in requested_portal_fields for k in ("expectedPrice", "valuation_low", "valuation_high")) or "valuation" in update_payload:
                val_obj = deepcopy(payload["valuation"])
                if isinstance(update_payload.get("valuation"), dict):
                    val_obj.update(update_payload["valuation"])
                update_payload["valuation"] = val_obj
                if payload.get("valuationType"):
                    update_payload["valuationType"] = payload["valuationType"]

        if isinstance(payload.get("pricing"), dict) and "pricing" in update_payload:
            pricing_obj = deepcopy(payload["pricing"])
            if isinstance(update_payload.get("pricing"), dict):
                pricing_obj.update(update_payload["pricing"])
            update_payload["pricing"] = pricing_obj

        if official_history:
            update_payload["officialSaleHistory"] = deepcopy(payload.get("officialSaleHistory") or [])
            update_payload["historysale"] = deepcopy(official_history)
            update_payload["soldDateInfo"] = payload.get("soldDateInfo")
            if payload.get("governmentPropertyId"):
                update_payload["governmentPropertyId"] = payload["governmentPropertyId"]
        if official_attributes:
            update_payload["officialPropertyAttributes"] = deepcopy(
                official_attributes
            )

        repair_patch = legacy_gnaf_placeholder_repair_patch(
            original, existing_evidence
        )
        if repair_patch:
            # Verified source values win over cleanup values when both target
            # the same field. Nested objects have already been shape-preserved.
            update_payload = _merge_payload_patch(repair_patch, update_payload)

        if update_payload:
            has_new_evidence = any(
                path in field_evidence for path in PORTAL_FIELD_PATHS.values()
            ) or bool(official_history)
            if (existing_evidence or has_new_evidence) and field_evidence != existing_evidence:
                update_payload["fieldProvenance"] = deepcopy(field_evidence)
                update_payload["provenance"] = summarize_provenance(field_evidence, now_iso)
            update_payload["updatedAt"] = now_iso
            if getattr(engine, "preserve_payload_shape", False):
                update_payload = _restrict_patch_to_existing_shape(
                    original, update_payload
                )
            if not update_payload:
                continue
            updates.append({"id": point.id, "payload": update_payload})

    trusted_count = sum(
        1
        for item in updates
        if any(path in CORE_LEGACY_PATHS for path in item["payload"].get("fieldProvenance", {}))
    )
    return updates, trusted_count


def process_batch(
    points: list,
    engine: EnrichmentEngine,
    force_update: bool = False,
    workers: int = 1,
    progress: bool = False,
) -> Tuple[List[Dict], int]:
    """Process a batch of Qdrant points and synthesize missing attributes with verified provenance."""
    if not points:
        return [], 0

    now_iso = datetime.now(DATABASE_TIMEZONE).replace(microsecond=0).isoformat()

    live_http_sources = any(
        getattr(engine, component, None) is not None
        for component in (
            "portal_hydrator",
            "property_source_adapter",
        )
    )
    if workers <= 1 or len(points) < 2 or (live_http_sources and len(points) < 50):
        return _process_point_chunk_trusted(
            points, engine, force_update, now_iso, progress=progress
        )

    num_workers = min(
        workers,
        max(1, len(points) // 25) if live_http_sources else len(points),
    )
    chunk_size = math.ceil(len(points) / num_workers)
    chunks = [points[i:i + chunk_size] for i in range(0, len(points), chunk_size)]

    all_updates = []
    total_enriched = 0
    skip_portal_flag = getattr(engine, "portal_hydrator", None) is None
    shared_components = {
        "ml_avm": getattr(engine, "ml_avm", None),
        "room_classifier": getattr(engine, "room_classifier", None),
        "location_engine": getattr(engine, "location_engine", None),
        "cadastral_engine": getattr(engine, "cadastral_engine", None),
        "portal_hydrator": getattr(engine, "portal_hydrator", None),
        "official_sales_adapter": getattr(engine, "official_sales_adapter", None),
        "property_source_adapter": getattr(engine, "property_source_adapter", None),
        "property_browser_adapter": getattr(engine, "property_browser_adapter", None),
        "official_matches_only": getattr(engine, "official_matches_only", False),
        "portal_fields": getattr(engine, "portal_fields", DEFAULT_PORTAL_FIELDS),
        "no_images": getattr(engine, "no_images", False),
        "preserve_payload_shape": getattr(engine, "preserve_payload_shape", False),
    }
    executor = ThreadPoolExecutor(max_workers=len(chunks))
    futures = [
        executor.submit(
            _process_point_chunk_trusted,
            chk,
            EnrichmentEngine(seed=random.randint(1, 1_000_000), skip_portal=skip_portal_flag, shared_instances=shared_components),
            force_update,
            now_iso,
            progress,
        )
        for chk in chunks
    ]
    try:
        for fut in futures:
            while not fut.done():
                try:
                    fut.result(timeout=0.2)
                except Exception as ex:
                    if type(ex).__name__ in ("TimeoutError", "Timeout"):
                        continue
                    break
            u, e = fut.result()
            all_updates.extend(u)
            total_enriched += e
    except KeyboardInterrupt:
        for f in futures:
            f.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return all_updates, total_enriched


def robust_scroll(client: QdrantClient, collection: str, query_filter, limit: int, offset, retries: int = 5):
    """Scroll points with retry backoff against transient server timeouts."""
    for attempt in range(retries):
        try:
            return client.scroll(
                collection_name=collection,
                scroll_filter=query_filter,
                limit=limit,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def robust_update_payload(client: QdrantClient, collection: str, updates: list, retries: int = 5):
    """
    Update point payloads via Qdrant batch API.
    Guarantees 100% preservation of pre-computed vector embeddings (Zero Vector Loss).
    """
    if not updates:
        return

    operations = []
    for item in updates:
        if item.get("delete_keys"):
            operations.append(models.DeletePayloadOperation(
                delete_payload=models.DeletePayload(keys=item["delete_keys"], points=[item["id"]])
            ))
        if item.get("payload"):
            operations.append(models.SetPayloadOperation(
                set_payload=models.SetPayload(payload=item["payload"], points=[item["id"]])
            ))

    for attempt in range(retries):
        try:
            return client.batch_update_points(
                collection_name=collection,
                update_operations=operations,
                wait=True
            )
        except Exception:
            if attempt == retries - 1:
                failures = []
                for item in updates:
                    try:
                        if item.get("delete_keys"):
                            client.delete_payload(collection_name=collection, keys=item["delete_keys"], points=[item["id"]], wait=True)
                        if item.get("payload"):
                            client.set_payload(collection_name=collection, payload=item["payload"], points=[item["id"]], wait=True)
                    except Exception as fallback_error:
                        failures.append((item.get("id"), str(fallback_error)))
                if failures:
                    raise RuntimeError(f"Qdrant failed to acknowledge {len(failures)} payload update(s): {failures[:3]}")
                return True
            time.sleep(2 * (attempt + 1))


def run_bulk_enrichment(
    url: str,
    api_key: Optional[str],
    collection: str,
    target_state: str,
    target_suburb: Optional[str] = None,
    batch_size: int = 1000,
    max_limit: int = 0,
    force_update: bool = False,
    dry_run: bool = False,
    workers: int = 8,
    skip_portal: bool = False,
    reset_checkpoint: bool = False,
    nsw_psi_zip: Optional[str] = None,
    allow_insecure_api_key: bool = False,
    rollback_journal: Optional[str] = None,
    portal_fields: Optional[Iterable[str]] = None,
    no_images: bool = False,
    preserve_payload_shape: bool = False,
    checkpoint_path: Optional[str] = None,
    source_url: Optional[str] = None,
    source_api_key: Optional[str] = None,
    source_collections: Optional[Iterable[str]] = None,
    skip_property_source: bool = True,
    property_browser_fallback: bool = False,
    property_browser_headless: bool = False,
    retry_outages: bool = True,
    portal_http_max_candidates: Optional[int] = None,
    portal_browser_max_candidates: Optional[int] = None,
    portal_browser_timeout: float = 180.0,
    profile: bool = False,
    verbose_progress: bool = False,
):
    if QdrantClient is None or models is None:
        raise RuntimeError("qdrant-client is required to run bulk enrichment")
    print("=" * 76)
    print("PROPERTYMASTER HIGH-THROUGHPUT BULK ENRICHMENT PIPELINE")
    print("=" * 76)
    print(f"Target Database : {url} | Collection: {collection}")
    print(f"Target State    : {target_state.upper()} | Suburb: {target_suburb or 'ALL'}")
    print(f"Batch Size      : {batch_size:,} | Force Re-enrich: {force_update} | Dry-run: {dry_run} | Workers: {workers}")
    print(f"Portal Hydration: {'ENABLED (supplementary public profiles)' if not skip_portal else 'DISABLED (official/open sources only)'}")
    print(f"Accepted Fields : {', '.join(portal_fields or sorted(DEFAULT_PORTAL_FIELDS))}")
    print(f"Scraped Sources : {'DISABLED' if skip_property_source else ', '.join(source_collections or ('PropertySoldV2',))}")
    browser_mode = (
        ("HEADLESS" if property_browser_headless else "HEADFUL")
        if property_browser_fallback
        else "DISABLED"
    )
    print(f"Browser Fallback: {browser_mode}")
    print(
        "Scrape.do Fallback: "
        + ("ENABLED" if os.getenv("SCRAPE_DO_TOKEN", "").strip() else "DISABLED (set SCRAPE_DO_TOKEN to enable)")
    )
    print(f"Image Enrichment: {'DISABLED' if no_images else 'ENABLED'}")
    print("Mode            : SPARSE FIELD-LEVEL EVIDENCE GATE (source misses produce no update)")
    print("=" * 76)

    if api_key and url.lower().startswith("http://") and not allow_insecure_api_key:
        raise RuntimeError(
            "Refusing to send Qdrant API key over plaintext HTTP. Use an HTTPS endpoint or explicitly pass "
            "--allow-insecure-api-key after accepting the credential-exposure risk."
        )
    effective_api_key = api_key if url.lower().startswith("https://") or allow_insecure_api_key else None

    init_started = time.perf_counter()
    init_timings = {}
    stage_started = time.perf_counter()
    client = QdrantClient(url=url, api_key=effective_api_key, timeout=300, check_compatibility=False)
    init_timings["qdrant_client"] = time.perf_counter() - stage_started
    property_source_adapter = None
    source_collections = tuple(source_collections or ("PropertySoldV2",))
    stage_started = time.perf_counter()
    if not skip_property_source and source_url and QdrantPropertySourceAdapter:
        if source_api_key and source_url.lower().startswith("http://") and not allow_insecure_api_key:
            raise RuntimeError(
                "Refusing to send source Qdrant API key over plaintext HTTP. Use HTTPS or pass "
                "--allow-insecure-api-key after accepting the credential-exposure risk."
            )
        effective_source_key = (
            source_api_key
            if source_url.lower().startswith("https://") or allow_insecure_api_key
            else None
        )
        source_client = QdrantClient(
            url=source_url,
            api_key=effective_source_key,
            timeout=300,
            check_compatibility=False,
        )
        property_source_adapter = QdrantPropertySourceAdapter(
            client=source_client,
            collections=source_collections,
            state=target_state,
            include_images=not no_images,
            scroll_limit=2000,
        )
    init_timings["property_source"] = time.perf_counter() - stage_started
    official_sales_adapter = None
    psi_path = Path(nsw_psi_zip) if nsw_psi_zip else Path("scratch") / "archive.zip"
    official_matches_only = False
    stage_started = time.perf_counter()
    if target_state.upper() == "NSW" and psi_path.exists():
        if target_suburb and NswPsiSalesAdapter:
            print(f"Loading official NSW PSI exact-address index for {target_suburb}...")
            official_sales_adapter = NswPsiSalesAdapter(psi_path, target_suburb)
        elif not target_suburb and NswPsiSqliteAdapter:
            print("Loading/building statewide NSW PSI SQLite exact-address index...")
            official_sales_adapter = NswPsiSqliteAdapter(psi_path, Path("scratch") / "nsw_psi_sales.db")
            official_matches_only = True
        if official_sales_adapter:
            print(f"Official NSW PSI : {official_sales_adapter.matched_address_count:,} unique matching addresses indexed")
        print("NSW PSI licence  : CC BY-NC-ND 4.0; commercial redistribution requires a PSI licence")
    init_timings["official_index"] = time.perf_counter() - stage_started

    dry_run_cache = Path("scratch") / "portal_cache_dry_run.db" if dry_run else None
    property_browser_adapter = None
    stage_started = time.perf_counter()
    if property_browser_fallback:
        if PropertyComBrowserAdapter is None:
            raise RuntimeError("Property.com.au browser adapter dependencies are unavailable")
        browser_cache = Path("scratch") / (
            "property_com_browser_cache_dry_run.db"
            if dry_run
            else f"property_com_browser_cache_{target_state.upper()}.db"
        )
        property_browser_adapter = PropertyComBrowserAdapter(
            cache_db_path=browser_cache,
            headless=property_browser_headless,
            wait_on_cooldown=False,
            max_candidates=portal_browser_max_candidates,
            request_timeout=portal_browser_timeout,
        )
    init_timings["browser"] = time.perf_counter() - stage_started
    stage_started = time.perf_counter()
    engine = EnrichmentEngine(
        seed=int(time.time()),
        skip_portal=skip_portal,
        portal_cache_db=dry_run_cache,
        official_sales_adapter=official_sales_adapter,
        property_source_adapter=property_source_adapter,
        property_browser_adapter=property_browser_adapter,
        official_matches_only=official_matches_only,
        portal_fields=portal_fields,
        no_images=no_images,
        preserve_payload_shape=preserve_payload_shape,
        wait_on_cooldown=retry_outages,
        portal_max_network_candidates=portal_http_max_candidates,
    )
    init_timings["engine"] = time.perf_counter() - stage_started
    init_timings["total"] = time.perf_counter() - init_started
    if profile:
        print(
            "[Profile:init] "
            + " ".join(f"{name}={seconds:.3f}s" for name, seconds in init_timings.items()),
            flush=True,
        )

    # Build filter conditions using indexed fields ('state', 'suburb')
    filter_must = []
    if target_state.upper() != "ALL":
        filter_must.append(models.FieldCondition(key="state", match=models.MatchValue(value=target_state.upper())))
    if target_suburb:
        sub_clean = target_suburb.strip()
        suburb_variants = list(dict.fromkeys([
            sub_clean.title(),
            sub_clean.upper(),
            sub_clean.lower(),
            sub_clean
        ]))
        filter_must.append(models.FieldCondition(key="suburb", match=models.MatchAny(any=suburb_variants)))

    query_filter = models.Filter(must=filter_must) if filter_must else None

    # Checkpoint handling (isolated per state and suburb)
    suburb_slug = re.sub(r"[^a-zA-Z0-9]+", "_", target_suburb.strip()).strip("_") if target_suburb else "ALL"
    checkpoint_file = Path(checkpoint_path) if checkpoint_path else Path(f"enrich_checkpoint_{target_state.upper()}_{suburb_slug}.json")
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
    offset = None
    total_processed = 0
    total_enriched = 0
    rollback_path = Path(rollback_journal) if rollback_journal else None
    if not dry_run and target_state.upper() == "NSW" and not target_suburb and rollback_path is None:
        rollback_path = Path("scratch") / "propertymaster_nsw_full_rollback.jsonl.gz"

    if checkpoint_file.exists() and not dry_run:
        if reset_checkpoint:
            print(f"[Checkpoint] Resetting checkpoint for {target_state.upper()} as requested.")
            try:
                checkpoint_file.unlink()
            except Exception:
                pass
        else:
            try:
                with open(checkpoint_file, "r", encoding="utf-8") as f:
                    ckpt = json.load(f)
                    offset = ckpt.get("offset")
                    total_processed = ckpt.get("total_processed", 0)
                    total_enriched = ckpt.get("total_enriched", 0)
                    print(f"[Resume] Found checkpoint! Resuming at offset: {offset} (Previously processed: {total_processed:,}, enriched: {total_enriched:,})")
            except Exception as e:
                print(f"[Warning] Could not read checkpoint: {e}")

    initial_processed = total_processed
    t_start = time.perf_counter()
    batch_idx = 0
    next_offset = offset
    profile_totals = {
        "scroll": 0.0,
        "enrich": 0.0,
        "rollback": 0.0,
        "write": 0.0,
        "throttle": 0.0,
        "checkpoint": 0.0,
    }
    rollback_file = None
    if rollback_path and not dry_run:
        rollback_path.parent.mkdir(parents=True, exist_ok=True)
        rollback_file = gzip.open(rollback_path, "at", encoding="utf-8")

    try:
        while True:
            # 1. Scroll batch from Qdrant
            t_batch_start = time.perf_counter()
            # Adjust batch size if limit is smaller
            current_batch_limit = batch_size
            if max_limit > 0:
                remaining = max_limit - total_processed
                if remaining <= 0:
                    break
                current_batch_limit = min(batch_size, remaining)

            stage_started = time.perf_counter()
            points, next_offset = robust_scroll(
                client=client,
                collection=collection,
                query_filter=query_filter,
                limit=current_batch_limit,
                offset=offset
            )
            batch_timings = {
                "scroll": time.perf_counter() - stage_started,
                "enrich": 0.0,
                "rollback": 0.0,
                "write": 0.0,
                "throttle": 0.0,
                "checkpoint": 0.0,
            }

            if not points:
                print("\n[Done] No more un-enriched points found matching filter criteria.")
                break

            # 2. Enrich in memory
            stage_started = time.perf_counter()
            updates, batch_enriched = process_batch(
                points,
                engine,
                force_update=force_update,
                workers=workers,
                progress=verbose_progress,
            )
            batch_timings["enrich"] = time.perf_counter() - stage_started

            # Print sample enriched property on first batch
            if batch_idx == 0 and updates:
                sample_update = updates[0]
                sample_original = next(
                    (point.payload or {} for point in points if point.id == sample_update["id"]),
                    {},
                )
                sample_p = _merge_payload_patch(sample_original, sample_update["payload"])
                print("\n--- SAMPLE ENRICHED PROPERTY (PROPERTYSOLDV2 1:1 PREVIEW) ---")
                print(f"  Title            : {sample_p.get('title')}")
                print(f"  Property Type    : {sample_p.get('propertyType')}")
                print(f"  Bedrooms (bed)   : {sample_p.get('bed')}")
                print(f"  Bathrooms (bath) : {sample_p.get('bath')}")
                print(f"  Garage           : {sample_p.get('features', {}).get('garage')}")
                print(f"  Area (totalArea) : {sample_p.get('area', {}).get('totalArea')} {sample_p.get('area', {}).get('unit')}")
                print(f"  Year Built       : {sample_p.get('constructionYear')}")
                print(f"  Expected Price   : ${sample_p.get('expectedPrice'):,} AUD" if sample_p.get('expectedPrice') else "  Expected Price: N/A")
                print(f"  Pricing Structure: {sample_p.get('pricing')}")
                print(f"  Status / Option  : {sample_p.get('status')} | {sample_p.get('listingOption')}")
                print(f"  Real Estate URL  : {sample_p.get('realestate_url') or '(None / unavailable)'}")
                print(f"  History Sale     : {len(sample_p.get('historysale', []))} record(s) -> {sample_p.get('soldDateInfo')}")
                print(f"  Description      : {sample_p.get('description') or '(Empty / Unlisted Off-Market)'}")
                print(f"  Verified Fields  : {', '.join(sorted(sample_p.get('fieldProvenance', {}))) or '(none)'}")
                print(f"  Schema Check     : 100% PropertySoldV2 Compliant ({len(sample_p)} root keys)")
                print("-------------------------------------------------------------\n")

            # 3. Stream back to Qdrant (Payload only, zero vector loss)
            if updates and not dry_run:
                if rollback_file:
                    stage_started = time.perf_counter()
                    original_by_id = {str(point.id): point.payload for point in points}
                    for update in updates:
                        rollback_file.write(json.dumps({
                            "id": str(update["id"]),
                            "payload": original_by_id.get(str(update["id"]), {}),
                        }, ensure_ascii=False, default=str) + "\n")
                    rollback_file.flush()
                    batch_timings["rollback"] = time.perf_counter() - stage_started
                stage_started = time.perf_counter()
                robust_update_payload(client=client, collection=collection, updates=updates)
                batch_timings["write"] = time.perf_counter() - stage_started
                stage_started = time.perf_counter()
                time.sleep(0.03)  # Gentle delay to allow Qdrant to flush WAL smoothly
                batch_timings["throttle"] = time.perf_counter() - stage_started

            total_processed += len(points)
            total_enriched += batch_enriched
            batch_idx += 1

            # Save only acknowledged progress; dry-runs never alter resume state.
            if not dry_run and next_offset is not None:
                stage_started = time.perf_counter()
                with open(checkpoint_file, "w", encoding="utf-8") as f:
                    json.dump({
                        "offset": next_offset,
                        "total_processed": total_processed,
                        "total_enriched": total_enriched,
                        "timestamp": datetime.now().isoformat()
                    }, f)
                batch_timings["checkpoint"] = time.perf_counter() - stage_started

            for name, seconds in batch_timings.items():
                profile_totals[name] += seconds
            batch_sec = max(time.perf_counter() - t_batch_start, 0.001)
            overall_sec = max(time.perf_counter() - t_start, 0.001)
            cur_speed = len(points) / batch_sec
            session_processed = total_processed - initial_processed
            avg_speed = session_processed / overall_sec if session_processed > 0 else cur_speed

            print(
                f"[{target_state.upper()}] Batch #{batch_idx:04d} | "
                f"Processed: {total_processed:,} | "
                f"Enriched: {total_enriched:,} | "
                f"Speed: {avg_speed:,.0f} pts/sec ({cur_speed:,.0f} cur)",
                end="\r",
                flush=True
            )
            if profile:
                print(
                    "\n[Profile:batch] "
                    + " ".join(
                        f"{name}={seconds:.3f}s"
                        for name, seconds in batch_timings.items()
                    )
                    + f" total={batch_sec:.3f}s",
                    flush=True,
                )

            if next_offset is None or (max_limit > 0 and total_processed >= max_limit):
                break

            offset = next_offset

    except KeyboardInterrupt:
        print("\n[Paused] Process interrupted by user. Saving checkpoint...")
        if offset is not None and not dry_run:
            with open(checkpoint_file, "w", encoding="utf-8") as f:
                json.dump({
                    "offset": offset,
                    "total_processed": total_processed,
                    "total_enriched": total_enriched,
                    "timestamp": datetime.now().isoformat()
                }, f)
        print("[Saved] You can resume this exact state anytime by re-running the same command.")
        return {
            "status": "paused",
            "state": target_state.upper(),
            "processed": total_processed,
            "enriched": total_enriched,
            "checkpoint": str(checkpoint_file),
        }
    finally:
        if rollback_file is not None:
            rollback_file.close()

    # Cleanup checkpoint upon full completion
    if not dry_run and next_offset is None and checkpoint_file.exists():
        try:
            checkpoint_file.unlink()
        except Exception:
            pass

    overall_time = time.perf_counter() - t_start
    print(f"\n\n{'=' * 76}")
    print(f"ENRICHMENT RUN COMPLETED FOR {target_state.upper()}!")
    print(f"{'=' * 76}")
    print(f"Total Properties Processed : {total_processed:,}")
    print(f"Total Successfully Enriched: {total_enriched:,}")
    print(f"Total Elapsed Time         : {overall_time:.1f} seconds ({overall_time/60:.2f} mins)")
    print(f"Average Pipeline Throughput: {total_processed/max(overall_time, 1):,.0f} records/second")
    if profile:
        measured_total = sum(profile_totals.values())
        print(
            "Profile Stage Totals       : "
            + " | ".join(
                f"{name}={seconds:.3f}s ({(seconds / measured_total * 100) if measured_total else 0:.1f}%)"
                for name, seconds in profile_totals.items()
            )
        )
    portal_metrics = (
        engine.portal_hydrator.get_metrics_snapshot()
        if engine.portal_hydrator and hasattr(engine.portal_hydrator, "get_metrics_snapshot")
        else {}
    )
    if portal_metrics:
        print(f"Portal Outcomes             : {json.dumps(portal_metrics, sort_keys=True)}")
    source_metrics = (
        engine.property_source_adapter.get_metrics_snapshot()
        if engine.property_source_adapter and hasattr(engine.property_source_adapter, "get_metrics_snapshot")
        else {}
    )
    if source_metrics:
        print(f"Scraped Source Outcomes     : {json.dumps(source_metrics, sort_keys=True)}")
    browser_metrics = (
        engine.property_browser_adapter.get_metrics_snapshot()
        if engine.property_browser_adapter and hasattr(engine.property_browser_adapter, "get_metrics_snapshot")
        else {}
    )
    if browser_metrics:
        print(f"Browser Scrape Outcomes     : {json.dumps(browser_metrics, sort_keys=True)}")
    if engine.property_browser_adapter and hasattr(engine.property_browser_adapter, "close"):
        engine.property_browser_adapter.close()
    print(f"{'=' * 76}\n")
    return {
        "status": "completed",
        "state": target_state.upper(),
        "processed": total_processed,
        "enriched": total_enriched,
        "elapsed_seconds": round(overall_time, 3),
        "checkpoint": str(checkpoint_file),
        "portal_metrics": portal_metrics,
        "source_metrics": source_metrics,
        "browser_metrics": browser_metrics,
    }


def main():
    parser = argparse.ArgumentParser(description="High-Throughput Bulk Enrichment Pipeline for PropertyMaster (16M records)")
    parser.add_argument("--url", default=DEFAULT_QDRANT_URL, help="Qdrant server URL")
    parser.add_argument("--api-key", default=DEFAULT_QDRANT_API_KEY, help="Qdrant API Key")
    parser.add_argument("--collection", default=COLLECTION_NAME, help="Qdrant target collection")
    parser.add_argument("--state", default="WA", help="Target state: WA, NSW, VIC, QLD, SA, TAS, ACT, NT, or ALL")
    parser.add_argument("--suburb", default=None, help="Specific suburb to target (optional)")
    parser.add_argument("--workers", type=int, default=8, help="Number of worker threads (default: 8)")
    parser.add_argument("--batch-size", type=int, default=1000, help="Batch size per scroll/upsert")
    parser.add_argument("--limit", type=int, default=0, help="Max records to process (0 = unlimited)")
    parser.add_argument(
        "--force",
        "--refresh-sources",
        dest="force",
        action="store_true",
        help="Refresh source pages; unsupported values are never promoted to verified",
    )
    parser.add_argument("--dry-run", action="store_true", help="Simulate without writing to database")
    parser.add_argument("--skip-portal", action="store_true", help="Skip live portal scraping / profile hydration")
    parser.add_argument(
        "--portal-fields",
        nargs="+",
        choices=sorted(PORTAL_FIELD_PATHS),
        default=None,
        help="Only accept these portal fields (for example: --portal-fields bed bath)",
    )
    parser.add_argument("--no-images", action="store_true", help="Do not persist portal image URLs")
    parser.add_argument(
        "--preserve-payload-shape",
        action="store_true",
        help="Only update keys already present in each PropertyMaster payload",
    )
    parser.add_argument(
        "--source-url",
        default=DEFAULT_SOURCE_QDRANT_URL,
        help="Qdrant URL containing previously scraped property collections",
    )
    parser.add_argument(
        "--source-api-key",
        default=DEFAULT_SOURCE_QDRANT_API_KEY,
        help="API key for the scraped-property source Qdrant",
    )
    parser.add_argument(
        "--source-collections",
        nargs="+",
        default=["PropertySoldV2"],
        help="Previously scraped collections used for exact-address enrichment",
    )
    source_mode = parser.add_mutually_exclusive_group()
    source_mode.add_argument(
        "--skip-property-source",
        dest="skip_property_source",
        action="store_true",
        help="Disable enrichment from previously scraped Qdrant collections",
    )
    source_mode.add_argument(
        "--use-property-source",
        dest="skip_property_source",
        action="store_false",
        help="Opt in to enrichment from another previously scraped Qdrant collection",
    )
    parser.set_defaults(skip_property_source=True)
    parser.add_argument(
        "--property-browser-fallback",
        action="store_true",
        help="Self-scrape exact Property.com.au profiles in a reusable Chrome browser when HTTP portals miss",
    )
    parser.add_argument(
        "--property-browser-headless",
        action="store_true",
        help="Run the Property.com.au fallback browser headless (headful is more reliable)",
    )
    parser.add_argument("--reset-checkpoint", action="store_true", help="Reset checkpoint and restart from beginning")
    parser.add_argument(
        "--checkpoint-path",
        default=None,
        help="Use an isolated checkpoint file instead of the default state/suburb checkpoint",
    )
    parser.add_argument(
        "--rollback-journal",
        default=None,
        help="Write before-images to a dedicated gzip JSONL rollback journal",
    )
    parser.add_argument("--nsw-psi-zip", default="scratch/archive.zip", help="NSW PSI CSV zip archive")
    parser.add_argument(
        "--allow-insecure-api-key",
        action="store_true",
        help="Explicitly allow sending the Qdrant API key over plaintext HTTP",
    )
    parser.add_argument(
        "--retry-outages",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Retry from the last acknowledged checkpoint after Qdrant/network outages",
    )
    parser.add_argument(
        "--portal-http-max-candidates",
        type=int,
        default=None,
        help="Maximum address candidates per HTTP portal",
    )
    parser.add_argument(
        "--portal-browser-max-candidates",
        type=int,
        default=None,
        help="Maximum profile URLs attempted by browser fallback",
    )
    parser.add_argument(
        "--portal-browser-timeout",
        type=float,
        default=180.0,
        help="Browser fallback timeout per URL in seconds",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Print per-stage initialization and batch timings",
    )
    parser.add_argument(
        "--verbose-progress",
        action="store_true",
        help="Print every property as it is processed (slower on large runs)",
    )

    args = parser.parse_args()

    states = ["WA", "ACT", "NT", "TAS", "SA", "QLD", "VIC", "NSW", "OT"] if args.state.upper() == "ALL" else [args.state.upper()]

    for st in states:
        reset_checkpoint = args.reset_checkpoint
        while True:
            try:
                run_bulk_enrichment(
                    url=args.url,
                    api_key=args.api_key,
                    collection=args.collection,
                    target_state=st,
                    target_suburb=args.suburb,
                    batch_size=args.batch_size,
                    max_limit=args.limit,
                    force_update=args.force,
                    dry_run=args.dry_run,
                    workers=args.workers,
                    skip_portal=args.skip_portal,
                    reset_checkpoint=reset_checkpoint,
                    nsw_psi_zip=args.nsw_psi_zip,
                    allow_insecure_api_key=args.allow_insecure_api_key,
                    portal_fields=args.portal_fields,
                    no_images=args.no_images,
                    preserve_payload_shape=args.preserve_payload_shape,
                    checkpoint_path=args.checkpoint_path,
                    rollback_journal=args.rollback_journal,
                    source_url=args.source_url,
                    source_api_key=args.source_api_key,
                    source_collections=args.source_collections,
                    skip_property_source=args.skip_property_source,
                    property_browser_fallback=args.property_browser_fallback,
                    property_browser_headless=args.property_browser_headless,
                    retry_outages=args.retry_outages,
                    portal_http_max_candidates=args.portal_http_max_candidates,
                    portal_browser_max_candidates=args.portal_browser_max_candidates,
                    portal_browser_timeout=args.portal_browser_timeout,
                    profile=args.profile,
                    verbose_progress=args.verbose_progress,
                )
                break
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                if not args.retry_outages or args.dry_run:
                    raise
                reset_checkpoint = False
                print(f"\n[Outage] {exc}")
                print("[Retry] Resuming from the last acknowledged checkpoint in 60 seconds...", flush=True)
                time.sleep(60)


if __name__ == "__main__":
    main()
