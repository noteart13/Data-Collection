"""
Enterprise Data Quality Framework for Australian Property Master Datasets.
Evaluates Completeness, Accuracy, and Duplication metrics according to statutory
and industry data engineering standards. Generates structured scorecards and audit reports.
"""

import re
from typing import Dict, Any, List, Optional, Tuple, Set
from dataclasses import dataclass, field, asdict
from datetime import datetime

# Geographic bounds for mainland Australia + Tasmania + Territories
AU_LAT_MIN, AU_LAT_MAX = -44.5, -9.0
AU_LON_MIN, AU_LON_MAX = 112.0, 154.5

AU_STATE_POSTCODE_PREFIXES = {
    "NSW": [("1", "2"), ("2", "3")],           # 1000 - 2999
    "ACT": [("02", "03"), ("26", "27")],       # 0200 - 0299, 2600 - 2620
    "VIC": [("3", "4"), ("8", "9")],           # 3000 - 3999, 8000 - 8999
    "QLD": [("4", "5"), ("9", "10")],          # 4000 - 4999, 9000 - 9999
    "SA":  [("5", "6")],                       # 5000 - 5999
    "WA":  [("6", "7")],                       # 6000 - 6999
    "TAS": [("7", "8")],                       # 7000 - 7999
    "NT":  [("08", "09")],                     # 0800 - 0899
}

CRITICAL_ADDRESS_FIELDS = ["street", "suburb", "state", "postcode"]
CORE_METRIC_FIELDS = ["bed", "bath", "area", "expectedPrice"]


@dataclass
class QualityScorecard:
    total_records: int = 0
    unique_records: int = 0
    duplicate_records: int = 0
    duplicate_rate_pct: float = 0.0

    # Completeness metrics
    field_completeness_pct: Dict[str, float] = field(default_factory=dict)
    overall_completeness_pct: float = 0.0

    # Accuracy metrics
    geo_accuracy_pct: float = 0.0
    postcode_accuracy_pct: float = 0.0
    numeric_plausibility_pct: float = 0.0
    overall_accuracy_pct: float = 0.0

    # Provenance metrics
    statutory_open_data_count: int = 0
    enriched_sales_count: int = 0
    enriched_cadastre_count: int = 0
    synthetic_count: int = 0

    # Quality Grade (A, B, C, D, F)
    composite_quality_score: float = 0.0
    quality_grade: str = "N/A"
    audit_timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        """Render scorecard as a clean Markdown report."""
        md = []
        md.append("### 📊 Australian Property Data Quality Scorecard")
        md.append(f"**Audit Timestamp:** `{self.audit_timestamp}` | **Overall Grade:** **{self.quality_grade}** (`{self.composite_quality_score:.1f}/100`)\n")
        
        md.append("#### 1. Volume & Deduplication")
        md.append("| Metric | Count | Ratio |")
        md.append("| :--- | :--- | :--- |")
        md.append(f"| Total Records Processed | {self.total_records:,} | 100.0% |")
        md.append(f"| Unique Addresses | {self.unique_records:,} | {100.0 - self.duplicate_rate_pct:.2f}% |")
        md.append(f"| Duplicates Detected | {self.duplicate_records:,} | {self.duplicate_rate_pct:.2f}% |\n")

        md.append("#### 2. Field Completeness (Data Density)")
        md.append("| Field Name | Non-Null % | Completeness Status |")
        md.append("| :--- | :--- | :--- |")
        for f, pct in sorted(self.field_completeness_pct.items()):
            status = "🟢 Excellent" if pct >= 95 else "🟡 Acceptable" if pct >= 70 else "🔴 Sparse"
            md.append(f"| `{f}` | {pct:.1f}% | {status} |")
        md.append(f"| **Overall Completeness** | **{self.overall_completeness_pct:.1f}%** | - |\n")

        md.append("#### 3. Domain Accuracy & Validation")
        md.append("| Validation Dimension | Valid % | Standard Constraint |")
        md.append("| :--- | :--- | :--- |")
        md.append(f"| Geospatial Bounding Box | {self.geo_accuracy_pct:.1f}% | Lat [-44.5, -9.0], Lon [112.0, 154.5] |")
        md.append(f"| Postcode State Alignment | {self.postcode_accuracy_pct:.1f}% | Australia Post official state ranges |")
        md.append(f"| Physical Plausibility | {self.numeric_plausibility_pct:.1f}% | Positive bed, bath, area, price |")
        md.append(f"| **Overall Accuracy** | **{self.overall_accuracy_pct:.1f}%** | - |\n")

        md.append("#### 4. Provenance & Enrichment Breakdown")
        md.append("| Data Provenance Tier | Record Count | Description |")
        md.append("| :--- | :--- | :--- |")
        md.append(f"| Statutory Open Data (G-NAF/ACT) | {self.statutory_open_data_count:,} | Official Commonwealth / Territory Register |")
        md.append(f"| Sales History Enriched | {self.enriched_sales_count:,} | NSW Valuer General / Settled Sales |")
        md.append(f"| Cadastral Geometry Enriched | {self.enriched_cadastre_count:,} | ArcGIS Land Polygon / LEP Zoning |")
        md.append(f"| Synthetic / Estimated Fields | {self.synthetic_count:,} | Regressed / Imputed Attributes |")
        return "\n".join(md)


class DataQualityAuditor:
    """
    Audits batches of Australian property records against statutory quality dimensions.
    """

    def __init__(self):
        self.seen_address_keys: Set[str] = set()
        self.seen_spatial_keys: Set[str] = set()

    def audit_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validates an individual record and returns verification flags.
        """
        issues = []
        is_geo_valid = False
        is_postcode_valid = False
        is_numeric_valid = True

        # 1. Geo Check
        coords = record.get("coordinates")
        lat = None
        lon = None
        if isinstance(coords, dict):
            lat = coords.get("lat")
            lon = coords.get("lon")
        elif "lat" in record and "lon" in record:
            lat = record.get("lat")
            lon = record.get("lon")

        if lat is not None and lon is not None:
            try:
                f_lat, f_lon = float(lat), float(lon)
                if (AU_LAT_MIN <= f_lat <= AU_LAT_MAX) and (AU_LON_MIN <= f_lon <= AU_LON_MAX):
                    is_geo_valid = True
                else:
                    issues.append(f"Coordinates ({f_lat}, {f_lon}) outside Australian territorial bounds")
            except (ValueError, TypeError):
                issues.append("Non-numeric coordinates")
        else:
            issues.append("Missing coordinates")

        # 2. State & Postcode Check
        state = str(record.get("state", "")).strip().upper()
        postcode = str(record.get("postcode", "")).strip()
        # Normalize 3 digits (e.g. ACT / NT)
        if len(postcode) == 3:
            postcode = "0" + postcode

        if len(postcode) == 4 and postcode.isdigit() and state in AU_STATE_POSTCODE_PREFIXES:
            p_val = int(postcode)
            ranges = AU_STATE_POSTCODE_PREFIXES[state]
            matched = False
            for low_s, high_s in ranges:
                low = int(low_s.ljust(4, "0"))
                high = int(high_s.ljust(4, "9"))
                if low <= p_val <= high:
                    matched = True
                    break
            if matched:
                is_postcode_valid = True
            else:
                issues.append(f"Postcode '{postcode}' does not match state '{state}'")
        else:
            if not postcode:
                issues.append("Missing postcode")

        # 3. Numeric Plausibility Check
        bed = record.get("bed")
        if bed is not None:
            try:
                b_val = float(bed)
                if not (0 <= b_val <= 30):
                    is_numeric_valid = False
                    issues.append(f"Implausible bed count: {b_val}")
            except (ValueError, TypeError):
                is_numeric_valid = False

        bath = record.get("bath")
        if bath is not None:
            try:
                b_val = float(bath)
                if not (0 <= b_val <= 20):
                    is_numeric_valid = False
                    issues.append(f"Implausible bath count: {b_val}")
            except (ValueError, TypeError):
                is_numeric_valid = False

        area_val = None
        if isinstance(record.get("area"), dict):
            area_val = record.get("area", {}).get("totalArea")
        elif "area" in record and isinstance(record.get("area"), (int, float)):
            area_val = record.get("area")

        if area_val is not None:
            try:
                a_val = float(area_val)
                if not (5.0 <= a_val <= 5_000_000):
                    is_numeric_valid = False
                    issues.append(f"Implausible land area: {a_val}")
            except (ValueError, TypeError):
                is_numeric_valid = False

        price = record.get("expectedPrice")
        if price is not None:
            try:
                p_val = float(price)
                if p_val < 0:
                    is_numeric_valid = False
                    issues.append("Negative price")
            except (ValueError, TypeError):
                is_numeric_valid = False

        return {
            "is_valid": len(issues) == 0,
            "is_geo_valid": is_geo_valid,
            "is_postcode_valid": is_postcode_valid,
            "is_numeric_valid": is_numeric_valid,
            "issues": issues,
            "lat": lat,
            "lon": lon
        }

    def audit_batch(self, records: List[Dict[str, Any]]) -> QualityScorecard:
        """
        Executes complete quality audit across a batch of records.
        """
        scorecard = QualityScorecard()
        scorecard.total_records = len(records)
        if not records:
            return scorecard

        field_counts = {
            "street": 0, "suburb": 0, "state": 0, "postcode": 0,
            "coordinates": 0, "bed": 0, "bath": 0, "area": 0,
            "expectedPrice": 0, "historysale": 0, "cadastral_parcel": 0
        }

        geo_valid_count = 0
        postcode_valid_count = 0
        numeric_valid_count = 0
        duplicate_count = 0

        self.seen_address_keys.clear()
        self.seen_spatial_keys.clear()

        for rec in records:
            # 1. Deduplication key
            street = str(rec.get("street", "")).strip().lower()
            suburb = str(rec.get("suburb", "")).strip().lower()
            state = str(rec.get("state", "")).strip().upper()
            postcode = str(rec.get("postcode", "")).strip()

            addr_key = f"{street}|{suburb}|{state}|{postcode}" if (street and suburb) else None

            # Spatial key rounded to ~1m
            coords = rec.get("coordinates")
            spatial_key = None
            if isinstance(coords, dict) and coords.get("lat") and coords.get("lon"):
                try:
                    spatial_key = f"{round(float(coords['lat']), 4)}|{round(float(coords['lon']), 4)}"
                except (ValueError, TypeError):
                    pass

            is_duplicate = False
            if addr_key:
                if addr_key in self.seen_address_keys:
                    is_duplicate = True
                else:
                    self.seen_address_keys.add(addr_key)

            if spatial_key and not is_duplicate:
                if spatial_key in self.seen_spatial_keys and addr_key:
                    is_duplicate = True
                else:
                    self.seen_spatial_keys.add(spatial_key)

            if is_duplicate:
                duplicate_count += 1

            # 2. Field Density
            for k in ["street", "suburb", "state", "postcode"]:
                if rec.get(k):
                    field_counts[k] += 1

            if rec.get("coordinates") or (rec.get("lat") and rec.get("lon")):
                field_counts["coordinates"] += 1

            if rec.get("bed") is not None:
                field_counts["bed"] += 1
            if rec.get("bath") is not None:
                field_counts["bath"] += 1

            area = rec.get("area")
            if area and (isinstance(area, (int, float)) or (isinstance(area, dict) and area.get("totalArea"))):
                field_counts["area"] += 1

            if rec.get("expectedPrice") is not None:
                field_counts["expectedPrice"] += 1

            if rec.get("historysale") and len(rec.get("historysale")) > 0:
                field_counts["historysale"] += 1
                scorecard.enriched_sales_count += 1

            if rec.get("cadastral_parcel") or rec.get("geometry"):
                field_counts["cadastral_parcel"] += 1
                scorecard.enriched_cadastre_count += 1

            # Provenance Tier
            src = str(rec.get("source", "")).upper()
            if "GNAF" in src or "G_NAF" in src or "ACT" in src or "VALUER" in src:
                scorecard.statutory_open_data_count += 1

            dq = rec.get("data_quality")
            if isinstance(dq, dict) and dq.get("is_synthetic"):
                scorecard.synthetic_count += 1
            elif "AVM" in str(rec.get("valuationType", "")):
                scorecard.synthetic_count += 1

            # Audit Record accuracy
            audit_res = self.audit_record(rec)
            if audit_res["is_geo_valid"]:
                geo_valid_count += 1
            if audit_res["is_postcode_valid"]:
                postcode_valid_count += 1
            if audit_res["is_numeric_valid"]:
                numeric_valid_count += 1

        n = float(len(records))
        scorecard.duplicate_records = duplicate_count
        scorecard.unique_records = len(records) - duplicate_count
        scorecard.duplicate_rate_pct = round((duplicate_count / n) * 100.0, 2)

        # Field Completeness %
        total_comp = 0.0
        for f, cnt in field_counts.items():
            pct = round((cnt / n) * 100.0, 1)
            scorecard.field_completeness_pct[f] = pct
            total_comp += pct
        scorecard.overall_completeness_pct = round(total_comp / len(field_counts), 1)

        # Accuracy %
        scorecard.geo_accuracy_pct = round((geo_valid_count / n) * 100.0, 1)
        scorecard.postcode_accuracy_pct = round((postcode_valid_count / n) * 100.0, 1)
        scorecard.numeric_plausibility_pct = round((numeric_valid_count / n) * 100.0, 1)
        scorecard.overall_accuracy_pct = round(
            (scorecard.geo_accuracy_pct + scorecard.postcode_accuracy_pct + scorecard.numeric_plausibility_pct) / 3.0,
            1
        )

        # Composite Score (40% Accuracy, 40% Completeness, 20% Uniqueness)
        uniqueness_factor = max(0.0, 100.0 - (scorecard.duplicate_rate_pct * 2))
        composite = (
            (scorecard.overall_completeness_pct * 0.40) +
            (scorecard.overall_accuracy_pct * 0.40) +
            (uniqueness_factor * 0.20)
        )
        scorecard.composite_quality_score = round(composite, 1)

        # Grade assignment
        if composite >= 90.0:
            scorecard.quality_grade = "A (Bank-Grade)"
        elif composite >= 80.0:
            scorecard.quality_grade = "B (Production-Ready)"
        elif composite >= 65.0:
            scorecard.quality_grade = "C (Acceptable)"
        elif composite >= 50.0:
            scorecard.quality_grade = "D (Needs Refinement)"
        else:
            scorecard.quality_grade = "F (Critical Errors)"

        return scorecard
