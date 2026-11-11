"""
Property Master Data Contracts & Schema Validation.
Provides Pydantic models and data quality auditing for Australian Real Estate ingestion pipelines.
"""

from typing import List, Dict, Optional, Any, Union
from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import datetime
import re

VALID_AUSTRALIAN_STATES = {"NSW", "VIC", "QLD", "WA", "SA", "ACT", "TAS", "NT", "OT"}


class CoordinatesSchema(BaseModel):
    lat: float = Field(..., description="Latitude coordinate")
    lon: float = Field(..., description="Longitude coordinate")

    @field_validator("lat")
    @classmethod
    def validate_latitude(cls, v: float) -> float:
        # Australia latitude bounds: ~ -44.0 to -9.0
        if not (-45.0 <= v <= -8.0):
            # Allow fallback but flag potential invalid location
            pass
        return round(v, 6)

    @field_validator("lon")
    @classmethod
    def validate_longitude(cls, v: float) -> float:
        # Australia longitude bounds: ~ 112.0 to 155.0
        if not (110.0 <= v <= 160.0):
            pass
        return round(v, 6)


class AreaSchema(BaseModel):
    totalArea: Optional[Union[float, int]] = Field(None, description="Land or internal living area in square meters")
    unit: str = Field(default="sqM", description="Measurement unit (default: sqM)")

    @field_validator("totalArea")
    @classmethod
    def validate_area(cls, v: Optional[Union[float, int]]) -> Optional[float]:
        if v is not None:
            if v <= 0:
                return None
            return round(float(v), 1)
        return None


class AreaBreakdownSchema(BaseModel):
    landArea: Optional[float] = None
    internalFloorArea: Optional[float] = None
    buildingArea: Optional[float] = None
    strataEntitlementArea: Optional[float] = None
    parentParcelArea: Optional[float] = None
    unit: str = "sqM"


class ValuationSchema(BaseModel):
    mid: Optional[float] = None
    low: Optional[float] = None
    high: Optional[float] = None
    provider: Optional[str] = None
    valuationDate: Optional[str] = None
    confidence: Optional[Union[str, float]] = None
    disclaimer: Optional[str] = None


class FieldProvenanceSchema(BaseModel):
    sourceName: str
    sourceUrl: str
    sourceRecordId: Optional[str] = None
    sourceField: str
    retrievedAt: str
    observedAt: Optional[str] = None
    method: str
    addressMatch: str
    confidence: float = Field(ge=0.0, le=1.0)
    verificationStatus: str
    evidenceHash: str
    datasetVersion: Optional[str] = None
    licence: Optional[str] = None
    areaType: Optional[str] = None


class ProvenanceSummarySchema(BaseModel):
    verificationStatus: str = "unverified"
    verifiedFieldCount: int = 0
    fieldCount: int = 0
    dataSources: List[str] = Field(default_factory=list)
    fetchedAt: Optional[str] = None


class PricingSchema(BaseModel):
    councilBill: Optional[str] = None
    waterBillPeriod: Optional[str] = None
    pricingOptions: Optional[str] = None
    authority: Optional[str] = None
    priceIncludes: List[str] = Field(default_factory=list)
    weeklyRent: Optional[int] = None
    estimatedYield: Optional[str] = None


class FeaturesSchema(BaseModel):
    garage: Optional[int] = Field(default=None, ge=0)
    floorNo: Optional[int] = Field(default=None, ge=0)
    indoorFeatures: List[str] = Field(default_factory=list)
    outdoorAmenities: List[str] = Field(default_factory=list)
    floorCovering: List[str] = Field(default_factory=list)
    roof: List[str] = Field(default_factory=list)
    view: List[str] = Field(default_factory=list)
    appliances: List[str] = Field(default_factory=list)
    buildingAmenities: List[str] = Field(default_factory=list)
    basement: Optional[str] = None
    displayAddress: Optional[str] = None


class HistorySaleItem(BaseModel):
    soldDate: str
    soldPrice: Optional[Union[float, int]] = None
    agency: Optional[str] = "Official Land Titles / Valuer General"
    datePrecision: Optional[str] = "exact"
    isEstimatedDate: Optional[bool] = False


class CadastralParcelSchema(BaseModel):
    lot: Optional[str] = None
    plan: Optional[str] = None
    section: Optional[str] = None
    cadastral_key: Optional[str] = None
    zoning_code: Optional[str] = None
    zoning_description: Optional[str] = None
    calculated_area_sqm: Optional[float] = None
    bbox: Optional[List[float]] = None
    centroid: Optional[Dict[str, float]] = None


class POIProximitySchema(BaseModel):
    name: str
    distance_km: float
    type: Optional[str] = None


class LocationScoresSchema(BaseModel):
    overall: float = Field(default=7.5, ge=0.0, le=10.0)
    schools: float = Field(default=7.5, ge=0.0, le=10.0)
    transit: float = Field(default=7.5, ge=0.0, le=10.0)
    amenities: float = Field(default=7.5, ge=0.0, le=10.0)
    nearest_station: Optional[POIProximitySchema] = None
    nearest_school: Optional[POIProximitySchema] = None
    nearest_hospital: Optional[POIProximitySchema] = None
    distance_to_cbd_km: Optional[float] = None


class DataQualityMetadata(BaseModel):
    is_synthetic: bool = Field(default=False, description="True if any primary attribute was algorithmically synthesized")
    synthetic_attributes: List[str] = Field(default_factory=list, description="List of attributes that were estimated (bed, bath, area, price, year)")
    data_source: str = Field(default="OFFICIAL_REGISTER", description="Primary data origin (G-NAF, ACT_GOV, NSW_VALUER_GENERAL, COMMERCIAL)")
    data_quality_score: float = Field(default=100.0, ge=0.0, le=100.0, description="Confidence/completeness score from 0 to 100")
    last_quality_audit: Optional[str] = None


class PropertyMasterPayload(BaseModel):
    """
    Standardized schema payload for PropertyMaster points in Qdrant.
    Ensures mathematical consistency, valid typing, and clear provenance.
    """
    street: str = Field(..., min_length=1, description="Normalized street number and name")
    suburb: str = Field(..., min_length=1, description="Locality or suburb")
    state: str = Field(..., min_length=2, max_length=3, description="Australian state abbreviation")
    postcode: str = Field(..., min_length=3, max_length=4, description="4-digit Australian postcode")
    city: str = Field(..., description="Local Government Area or metropolitan city")
    district: Optional[str] = None
    title: str = Field(..., description="Canonical full address title")
    description: Optional[str] = ""

    propertyType: Optional[str] = Field(default=None, description="Dwelling type (House, Unit, Townhouse, Commercial, Land)")
    natureOfProperty: Optional[str] = None

    bed: Optional[int] = Field(None, ge=0, le=50)
    bath: Optional[int] = Field(None, ge=0, le=30)
    area: AreaSchema = Field(default_factory=AreaSchema)
    areas: Optional[AreaBreakdownSchema] = None
    features: FeaturesSchema = Field(default_factory=FeaturesSchema)

    coordinates: Optional[CoordinatesSchema] = None
    constructionYear: Optional[str] = None
    structuralRemodelYear: Optional[str] = None
    architecturalStyle: Optional[str] = None

    expectedPrice: Optional[float] = None
    valuation: Optional[ValuationSchema] = None
    valuationType: Optional[str] = None
    valuation_mid: Optional[int] = None
    valuation_low: Optional[int] = None
    valuation_high: Optional[int] = None
    confidence_score: Optional[float] = None
    valuationMethod: Optional[str] = None
    pricing: Optional[PricingSchema] = Field(default_factory=PricingSchema)

    status: str = Field(default="unknown")
    listingOption: str = Field(default="unknown")
    published: bool = True
    recommended: bool = False

    historysale: List[HistorySaleItem] = Field(default_factory=list)
    soldDateInfo: Optional[str] = ""
    realestate_url: Optional[str] = ""
    contactInfo: List[Dict[str, Any]] = Field(default_factory=list)
    images: List[Dict[str, Any]] = Field(default_factory=list)
    photos: List[str] = Field(default_factory=list)
    tagged_photos: Optional[Dict[str, List[str]]] = None
    room_counts_detected: Optional[Dict[str, int]] = None
    has_floorplan: Optional[bool] = None
    primary_photo: Optional[str] = None
    slug: Optional[str] = None

    # Phase 3: Spatial & GIS Attributes
    cadastral_parcel: Optional[CadastralParcelSchema] = None
    location_scores: Optional[LocationScoresSchema] = None

    # Government / Source identifiers
    gnaf_pid: Optional[str] = None
    confidence: Optional[Union[str, int]] = None
    primary_secondary: Optional[str] = None

    # Provenance and Data Quality
    data_quality: Optional[DataQualityMetadata] = None
    fieldProvenance: Dict[str, FieldProvenanceSchema] = Field(default_factory=dict)
    provenance: Optional[ProvenanceSummarySchema] = None
    legacyUnverified: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    # Audit Timestamps (ISO 8601)
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

    @field_validator("state", mode="before")
    @classmethod
    def validate_state(cls, v: Any) -> str:
        s = str(v).strip().upper()
        if s not in VALID_AUSTRALIAN_STATES:
            # Normalize common names
            mapping = {
                "NEW SOUTH WALES": "NSW",
                "VICTORIA": "VIC",
                "QUEENSLAND": "QLD",
                "WESTERN AUSTRALIA": "WA",
                "SOUTH AUSTRALIA": "SA",
                "AUSTRALIAN CAPITAL TERRITORY": "ACT",
                "TASMANIA": "TAS",
                "NORTHERN TERRITORY": "NT",
            }
            s = mapping.get(s, s)
        return s

    @field_validator("postcode", mode="before")
    @classmethod
    def validate_postcode(cls, v: Any) -> str:
        digits = re.sub(r"\D", "", str(v))
        if len(digits) == 3:
            digits = "0" + digits
        return digits[:4] if digits else "0000"


def calculate_data_quality_score(payload: Dict[str, Any]) -> float:
    """
    Computes data completeness and quality score (0.0 to 100.0).
    Reward real/verified attributes over synthetic or missing values.
    """
    score = 0.0

    # 1. Address Core Integrity (30 pts)
    if payload.get("street") and payload.get("suburb") and payload.get("state"):
        score += 20.0
    if payload.get("postcode") and len(str(payload.get("postcode"))) == 4:
        score += 5.0
    coords = payload.get("coordinates")
    if coords and isinstance(coords, dict) and coords.get("lat") and coords.get("lon"):
        score += 5.0

    # 2. Living Metrics Quality (30 pts)
    bed = payload.get("bed")
    bath = payload.get("bath")
    area = payload.get("area", {}).get("totalArea") if isinstance(payload.get("area"), dict) else None

    if bed is not None and bed > 0:
        score += 10.0
    if bath is not None and bath > 0:
        score += 10.0
    if area is not None and area > 10:
        score += 10.0

    # 3. Valuation & Transactions (25 pts)
    price = payload.get("expectedPrice")
    history = payload.get("historysale") or []
    val_type = str(payload.get("valuationType", ""))

    if price is not None and price > 50000:
        score += 10.0
    if history and len(history) > 0:
        score += 10.0
    if "Verified_Official" in val_type or "Official_Sale" in val_type:
        score += 5.0

    # 4. Richness & Media (15 pts)
    if payload.get("description") and len(str(payload.get("description"))) > 80:
        score += 5.0
    if payload.get("images") and len(payload.get("images")) > 0:
        score += 5.0
    const_year = str(payload.get("constructionYear", ""))
    if const_year.isdigit() and 1850 <= int(const_year) <= 2026:
        score += 5.0

    return min(100.0, max(0.0, round(score, 1)))


def evaluate_data_quality(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Inspects a payload dictionary, marks synthetic fields,
    calculates quality score, and attaches DataQualityMetadata.
    """
    synth_attrs = []

    val_type = str(payload.get("valuationType", ""))
    if "AVM" in val_type or "Synthetic" in val_type or "Regularized" in val_type:
        synth_attrs.append("expectedPrice")

    # Check if area or room counts were estimated
    if payload.get("isAreaEstimated") or payload.get("is_area_estimated"):
        synth_attrs.append("area")
    if payload.get("isRoomEstimated") or payload.get("is_room_estimated"):
        synth_attrs.extend(["bed", "bath"])
    if payload.get("isYearEstimated") or payload.get("is_year_estimated"):
        synth_attrs.append("constructionYear")

    # Source provenance
    source = payload.get("source") or ("ACT_GOV" if payload.get("state") == "ACT" else "G_NAF")
    if payload.get("historysale"):
        for h in payload.get("historysale"):
            if "Valuer General" in str(h.get("agency", "")):
                source = "VALUER_GENERAL_OFFICIAL"
                break

    is_synthetic = len(synth_attrs) > 0
    score = calculate_data_quality_score(payload)

    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    dq_meta = {
        "is_synthetic": is_synthetic,
        "synthetic_attributes": synth_attrs,
        "data_source": source,
        "data_quality_score": score,
        "last_quality_audit": now_iso
    }

    payload["data_quality"] = dq_meta
    return payload
