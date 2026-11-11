"""
Stage 3: Validation Stage.
Validates extracted property payloads against schema contracts,
detects geographic anomalies, and scores data quality.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple
import logging

from app.operations.data_quality import DataQualityAuditor

logger = logging.getLogger("pipeline_validator")


class BaseValidator(ABC):
    """Abstract interface for payload validation."""

    @abstractmethod
    def validate(self, payload: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], str]:
        """
        Validates payload.
        Returns: (is_valid: bool, validated_payload: Dict, error_message: str)
        """
        pass


class PropertySchemaValidator(BaseValidator):
    """
    Validates Australian Property payloads against statutory schema rules,
    coordinate bounding boxes, postcode alignments, and attaches quality metadata.
    """

    def __init__(self, reject_invalid_geo: bool = True):
        self.reject_invalid_geo = reject_invalid_geo
        self.auditor = DataQualityAuditor()

    def validate(self, payload: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], str]:
        cleaned = dict(payload)

        # 1. Mandatory Core Address Validation
        street = str(cleaned.get("street", "")).strip()
        suburb = str(cleaned.get("suburb", "")).strip()
        state = str(cleaned.get("state", "")).strip().upper()

        if not street:
            return False, cleaned, "Missing or blank street address"
        if not suburb:
            return False, cleaned, "Missing or blank suburb"
        if not state:
            return False, cleaned, "Missing state identifier"

        # 2. Run Data Quality Auditor on record
        audit = self.auditor.audit_record(cleaned)
        if not audit["is_valid"]:
            error_details = "; ".join(audit["issues"])
            if self.reject_invalid_geo and not audit["is_geo_valid"]:
                return False, cleaned, f"Geospatial Validation Error: {error_details}"
            if not audit["is_postcode_valid"]:
                return False, cleaned, f"Postcode Alignment Error: {error_details}"

        # 3. Attach standard Data Quality metadata
        quality_score = 0.0
        if audit["is_valid"]:
            quality_score = 80.0
            if cleaned.get("is_enriched"):
                quality_score += 15.0
            if cleaned.get("historysale"):
                quality_score += 5.0

        cleaned["data_quality"] = {
            "score": round(quality_score, 1),
            "is_valid": audit["is_valid"],
            "is_enriched": cleaned.get("is_enriched", False),
            "audit_issues": audit["issues"]
        }

        return True, cleaned, ""
