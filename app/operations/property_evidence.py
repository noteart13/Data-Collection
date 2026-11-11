"""Field-level evidence, verification, and legacy quarantine helpers."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional


GNAF_DATASET_URL = "https://data.gov.au/data/dataset/geocoded-national-address-file-g-naf"
NSW_PSI_URL = "https://www.valuergeneral.nsw.gov.au/design/bulk_psi_content/bulk_psi"
NSW_PLANNING_URL = (
    "https://www.planningportal.nsw.gov.au/opendata/dataset/"
    "environment-planning-instrument-local-environmental-plan-land-zoning"
)
NSW_CADASTRE_URL = (
    "https://portal.spatial.nsw.gov.au/server/rest/services/"
    "NSW_Land_Parcel_Property_Theme/FeatureServer"
)

VERIFIED_STATUSES = {"verified", "estimated", "derived"}
CORE_LEGACY_PATHS = (
    "bed",
    "bath",
    "features.garage",
    "features.floorNo",
    "area.totalArea",
    "constructionYear",
    "expectedPrice",
    "description",
    "historysale",
    "pricing.weeklyRent",
    "pricing.estimatedYield",
    "pricing.councilBill",
    "pricing.waterBillPeriod",
    "images",
    "features.indoorFeatures",
    "status",
    "propertyType",
    "title",
    "listingOption",
    "soldDateInfo",
    "realestate_url",
    "strataDetails",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def evidence_hash(value: Any, source_url: str, source_record_id: Optional[str]) -> str:
    raw = json.dumps(
        {"value": value, "sourceUrl": source_url, "sourceRecordId": source_record_id},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def make_evidence(
    *,
    value: Any,
    source_name: str,
    source_url: str,
    source_field: str,
    method: str,
    verification_status: str,
    retrieved_at: Optional[str] = None,
    observed_at: Optional[str] = None,
    source_record_id: Optional[str] = None,
    address_match: str = "exact",
    confidence: float = 1.0,
    dataset_version: Optional[str] = None,
    licence: Optional[str] = None,
    area_type: Optional[str] = None,
) -> Dict[str, Any]:
    evidence = {
        "sourceName": source_name,
        "sourceUrl": source_url,
        "sourceRecordId": source_record_id,
        "sourceField": source_field,
        "retrievedAt": retrieved_at or utc_now_iso(),
        "observedAt": observed_at,
        "method": method,
        "addressMatch": address_match,
        "confidence": round(float(confidence), 4),
        "verificationStatus": verification_status,
        "datasetVersion": dataset_version,
        "licence": licence,
        "evidenceHash": evidence_hash(value, source_url, source_record_id),
    }
    if area_type:
        evidence["areaType"] = area_type
    return evidence


def is_accepted_evidence(evidence: Any) -> bool:
    if not isinstance(evidence, dict):
        return False
    if evidence.get("verificationStatus") not in VERIFIED_STATUSES:
        return False
    if evidence.get("addressMatch") not in {"exact", "parcel_exact"}:
        return False
    source_url = str(evidence.get("sourceUrl") or "")
    return source_url.startswith(("https://", "http://")) and bool(evidence.get("evidenceHash"))


def evidence_matches_value(value: Any, evidence: Any) -> bool:
    if not is_accepted_evidence(evidence):
        return False
    expected = evidence_hash(value, str(evidence.get("sourceUrl")), evidence.get("sourceRecordId"))
    return expected == evidence.get("evidenceHash")


def normalize_address_slug(street: str, suburb: str, state: str, postcode: str) -> str:
    raw = f"{street} {suburb} {state} {postcode}".lower().replace("/", "-")
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-")


def address_slug_matches(observed_slug: str, candidate_slugs: Iterable[str]) -> bool:
    observed = str(observed_slug or "").strip("-/").lower()
    return bool(observed) and observed in {str(item).strip("-/").lower() for item in candidate_slugs}


def get_path(payload: Dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def has_meaningful_value(value: Any) -> bool:
    return value not in (None, "", "N/A", "None", [], {})


def quarantine_unverified_legacy(
    payload: Dict[str, Any],
    field_provenance: Dict[str, Any],
    *,
    paths: Iterable[str] = CORE_LEGACY_PATHS,
    migrated_at: Optional[str] = None,
) -> Dict[str, Any]:
    legacy = deepcopy(payload.get("legacyUnverified")) if isinstance(payload.get("legacyUnverified"), dict) else {}
    for path in paths:
        value = get_path(payload, path)
        if not has_meaningful_value(value):
            continue
        if evidence_matches_value(value, field_provenance.get(path)):
            continue
        legacy[path] = {
            "value": deepcopy(value),
            "reason": "missing_field_level_public_evidence",
            "migratedAt": migrated_at or utc_now_iso(),
        }
    return legacy


def accepted_existing_value(payload: Dict[str, Any], field_provenance: Dict[str, Any], path: str) -> Any:
    evidence = field_provenance.get(path)
    value = get_path(payload, path)
    return value if evidence_matches_value(value, evidence) else None


def legacy_gnaf_placeholder_repair_patch(
    payload: Dict[str, Any], field_provenance: Dict[str, Any]
) -> Dict[str, Any]:
    """Replace known ingest placeholders while preserving the existing payload shape."""
    source = str((payload.get("data_quality") or {}).get("data_source") or "").upper()
    if not (payload.get("gnaf_pid") or payload.get("gnafPid") or "G_NAF" in source or "GNAF" in source):
        return {}

    replacements = {
        "architecturalStyle": ("None", None),
        "constructionYear": ("N/A", None),
        "structuralRemodelYear": ("N/A", None),
        "propertyType": ("House", None),
        "features.garage": (0, None),
        "features.floorNo": (1, None),
        "features.basement": ("None", None),
        "features.floorCovering": (["None"], []),
        "features.roof": (["Other"], []),
        "features.view": (["None"], []),
        "features.appliances": (["None"], []),
        "features.buildingAmenities": (["None"], []),
        "pricing.waterBillPeriod": ("", None),
        "pricing.councilBill": ("", None),
        "pricing.pricingOptions": ("", None),
        "pricing.authority": ("", None),
        "pricing.priceIncludes": ([""], []),
        "status": ("off_market", "unknown"),
        "listingOption": ("off_market", "unknown"),
    }

    patch: Dict[str, Any] = {}
    for path, (placeholder, replacement) in replacements.items():
        if get_path(payload, path) != placeholder:
            continue
        if evidence_matches_value(placeholder, field_provenance.get(path)):
            continue
        parts = path.split(".")
        current = patch
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = deepcopy(replacement)

    # Qdrant payload setting replaces a nested object as a unit. Merge repaired
    # nested objects here so unrelated feature/pricing keys are not dropped.
    for parent in ("features", "pricing"):
        if parent in patch and isinstance(payload.get(parent), dict):
            merged = deepcopy(payload[parent])
            merged.update(patch[parent])
            patch[parent] = merged
    return patch


def gnaf_provenance(payload: Dict[str, Any], retrieved_at: str) -> Dict[str, Dict[str, Any]]:
    source = str(payload.get("source") or "").upper()
    gnaf_pid = payload.get("gnaf_pid") or payload.get("gnafPid")
    if "GNAF" not in source and "G_NAF" not in source and not gnaf_pid:
        return {}
    result = {}
    for path in ("street", "suburb", "state", "postcode", "coordinates"):
        value = get_path(payload, path)
        if has_meaningful_value(value):
            result[path] = make_evidence(
                value=value,
                source_name="G_NAF",
                source_url=GNAF_DATASET_URL,
                source_record_id=str(gnaf_pid) if gnaf_pid else None,
                source_field=path,
                method="observed",
                verification_status="verified",
                retrieved_at=retrieved_at,
                confidence=1.0 if gnaf_pid else 0.9,
                dataset_version=payload.get("gnafVersion"),
                licence="CC BY 4.0",
            )
    return result


def official_sales_evidence(
    payload: Dict[str, Any],
    retrieved_at: str,
    target_value: Optional[Any] = None,
) -> Dict[str, Any]:
    sales = payload.get("officialSaleHistory")
    if not isinstance(sales, list) or not sales:
        return {}
    valid_sales = []
    for sale in sales:
        if not isinstance(sale, dict):
            continue
        price = sale.get("price")
        date_value = sale.get("contractDate") or sale.get("settlementDate")
        if price and date_value and float(price) >= 10000:
            valid_sales.append(sale)
    if not valid_sales:
        return {}
    record_id = str(payload.get("governmentPropertyId") or payload.get("lotPlan") or "") or None
    return make_evidence(
        value=valid_sales if target_value is None else target_value,
        source_name="NSW_VALUER_GENERAL_PSI",
        source_url=NSW_PSI_URL,
        source_record_id=record_id,
        source_field="officialSaleHistory",
        method="observed",
        verification_status="verified",
        retrieved_at=retrieved_at,
        confidence=0.99 if record_id else 0.9,
        dataset_version=payload.get("_officialSalesDatasetVersion"),
        licence="CC BY-NC-ND 4.0",
    )


def annotate_portal_result(
    result: Dict[str, Any],
    *,
    source_name: str,
    source_url: str,
    retrieved_at: Optional[str] = None,
) -> Dict[str, Any]:
    if not result:
        return result
    fetched_at = retrieved_at or utc_now_iso()
    record_id = result.get("_addressSlug") or source_url.rstrip("/").split("/")[-1]
    area_type = result.get("_areaType")
    field_map = {
        "bed": ("bed", "observed", "verified"),
        "bath": ("bath", "observed", "verified"),
        "garage": ("features.garage", "observed", "verified"),
        "totalArea": ("area.totalArea", "observed", "verified"),
        "constructionYear": ("constructionYear", "observed", "verified"),
        "expectedPrice": ("expectedPrice", "model_estimate", "estimated"),
        "valuation_low": ("valuation.low", "model_estimate", "estimated"),
        "valuation_high": ("valuation.high", "model_estimate", "estimated"),
        "weeklyRent": ("pricing.weeklyRent", "model_estimate", "estimated"),
        "estimatedYield": ("pricing.estimatedYield", "model_estimate", "estimated"),
        "historysale": ("historysale", "observed", "verified"),
        "description": ("description", "observed", "verified"),
        "images": ("images", "observed", "verified"),
        "features_list": ("features.indoorFeatures", "observed", "verified"),
        "status": ("status", "observed", "verified"),
        "propertyType": ("propertyType", "observed", "verified"),
        "title": ("title", "observed", "verified"),
        "listingOption": ("listingOption", "observed", "verified"),
        "soldDateInfo": ("soldDateInfo", "observed", "verified"),
        "realestate_url": ("realestate_url", "observed", "verified"),
    }
    evidence = dict(result.get("fieldEvidence") or {})
    for source_field, (payload_path, method, status) in field_map.items():
        value = result.get(source_field)
        if not has_meaningful_value(value):
            continue
        evidence[payload_path] = make_evidence(
            value=value,
            source_name=source_name,
            source_url=source_url,
            source_record_id=str(record_id) if record_id else None,
            source_field=source_field,
            method=method,
            verification_status=status,
            retrieved_at=fetched_at,
            observed_at=result.get("valuationDate") if status == "estimated" else None,
            confidence=float(result.get("sourceConfidence") or 0.9),
            area_type=area_type if source_field == "totalArea" else None,
        )
    result["fieldEvidence"] = evidence
    result["dataSource"] = source_name
    result["realestate_url"] = source_url
    result["_retrievedAt"] = fetched_at
    return result


def summarize_provenance(field_provenance: Dict[str, Any], fetched_at: str) -> Dict[str, Any]:
    accepted = {path: ev for path, ev in field_provenance.items() if is_accepted_evidence(ev)}
    sources = sorted({str(ev.get("sourceName")) for ev in accepted.values() if ev.get("sourceName")})
    statuses = {str(ev.get("verificationStatus")) for ev in accepted.values()}
    if "verified" in statuses:
        status = "partially_verified"
    elif accepted:
        status = "derived_or_estimated_only"
    else:
        status = "unverified"
    return {
        "verificationStatus": status,
        "verifiedFieldCount": len(accepted),
        "fieldCount": len(field_provenance),
        "dataSources": sources,
        "fetchedAt": fetched_at if accepted else None,
    }
