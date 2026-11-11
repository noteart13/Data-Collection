"""Exact-address adapter for previously scraped property-profile collections."""

from __future__ import annotations

from copy import deepcopy
import threading
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urlparse

try:
    from qdrant_client import models
except ImportError:  # pragma: no cover - exercised only in minimal environments
    models = None

from app.operations.property_evidence import evidence_matches_value, normalize_address_slug


SOURCE_FIELDS = {
    "bed": ("bed", "observed", "verified"),
    "bath": ("bath", "observed", "verified"),
    "garage": ("features.garage", "observed", "verified"),
    "totalArea": ("area.totalArea", "observed", "verified"),
    "constructionYear": ("constructionYear", "observed", "verified"),
    "expectedPrice": ("expectedPrice", "model_estimate", "estimated"),
    "valuation_low": ("valuation.low", "model_estimate", "estimated"),
    "valuation_high": ("valuation.high", "model_estimate", "estimated"),
    "title": ("title", "observed", "verified"),
    "weeklyRent": ("pricing.weeklyRent", "observed", "verified"),
    "estimatedYield": ("pricing.estimatedYield", "model_estimate", "estimated"),
    "historysale": ("historysale", "observed", "verified"),
    "description": ("description", "observed", "verified"),
    "features_list": ("features.indoorFeatures", "observed", "verified"),
    "status": ("status", "observed", "verified"),
    "realestate_url": ("realestate_url", "observed", "verified"),
}


def _is_http_url(value: Any) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _source_value(payload: Dict[str, Any], key: str):
    if key == "garage":
        features = payload.get("features") if isinstance(payload.get("features"), dict) else {}
        return features.get("garage")
    if key == "totalArea":
        area = payload.get("area") if isinstance(payload.get("area"), dict) else {}
        return area.get("totalArea")
    if key in {"valuation_low", "valuation_high"}:
        valuation = payload.get("valuation") if isinstance(payload.get("valuation"), dict) else {}
        return valuation.get("low" if key == "valuation_low" else "high")
    if key in {"weeklyRent", "estimatedYield"}:
        pricing = payload.get("pricing") if isinstance(payload.get("pricing"), dict) else {}
        return pricing.get(key)
    if key == "features_list":
        features = payload.get("features") if isinstance(payload.get("features"), dict) else {}
        return features.get("indoorFeatures")
    return payload.get(key)


def _meaningful(key: str, value: Any) -> bool:
    if value in (None, "", "N/A", "None", [], {}):
        return False
    if key in {"bed", "bath", "totalArea", "expectedPrice", "weeklyRent"}:
        try:
            return float(value) > 0
        except (TypeError, ValueError):
            return False
    return True


def _has_source_evidence(payload: Dict[str, Any], key: str) -> bool:
    provenance = payload.get("fieldProvenance")
    if not isinstance(provenance, dict):
        return False
    path = SOURCE_FIELDS[key][0]
    return evidence_matches_value(_source_value(payload, key), provenance.get(path))


class QdrantPropertySourceAdapter:
    """Index source payloads and return only exact-address, URL-anchored values."""

    def __init__(
        self,
        *,
        client,
        collections: Iterable[str],
        state: str,
        include_images: bool = False,
        scroll_limit: int = 1000,
    ):
        self.client = client
        self.collections = tuple(collections)
        self.state = str(state or "").upper()
        self.include_images = include_images
        self.scroll_limit = max(1, int(scroll_limit))
        self._loaded = False
        self._load_lock = threading.Lock()
        self._records: Dict[str, Tuple[int, Dict[str, Any], str, str]] = {}
        self._metrics = {
            "loaded": 0,
            "exact_matches": 0,
            "bed_matches": 0,
            "bath_matches": 0,
            "room_matches": 0,
            "misses": 0,
            "rejected_no_url": 0,
            "rejected_evidence": 0,
            "rejected_identity": 0,
        }

    def _key(self, payload: Dict[str, Any]) -> str:
        return normalize_address_slug(
            str(payload.get("street") or ""),
            str(payload.get("suburb") or ""),
            str(payload.get("state") or ""),
            str(payload.get("postcode") or ""),
        )

    def _score(self, payload: Dict[str, Any]) -> int:
        return sum(
            1
            for key in SOURCE_FIELDS
            if _meaningful(key, _source_value(payload, key))
            and _has_source_evidence(payload, key)
        )

    def _load(self) -> None:
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            for collection in self.collections:
                offset = None
                while True:
                    query_filter = None
                    if models is not None and self.state:
                        query_filter = models.Filter(
                            must=[
                                models.FieldCondition(
                                    key="state",
                                    match=models.MatchValue(value=self.state),
                                )
                            ]
                        )
                    points, next_offset = self.client.scroll(
                        collection_name=collection,
                        scroll_filter=query_filter,
                        limit=self.scroll_limit,
                        offset=offset,
                        with_payload=True,
                        with_vectors=False,
                    )
                    for point in points or []:
                        payload = deepcopy(point.payload or {})
                        source_url = str(payload.get("realestate_url") or "")
                        if not _is_http_url(source_url):
                            self._metrics["rejected_no_url"] += 1
                            continue
                        key = self._key(payload)
                        if not key:
                            continue
                        record = (self._score(payload), payload, collection, str(point.id))
                        existing = self._records.get(key)
                        if existing is None or record[0] > existing[0]:
                            self._records[key] = record
                    if next_offset is None:
                        break
                    offset = next_offset
            self._metrics["loaded"] = len(self._records)
            self._loaded = True

    def lookup(
        self, street: str, suburb: str, state: str, postcode: str,
        gnaf_pid: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._load()
        key = normalize_address_slug(street, suburb, state, postcode)
        record = self._records.get(key)
        if record is None:
            self._metrics["misses"] += 1
            return {}

        _, payload, collection, point_id = record
        source_pid = payload.get("gnaf_pid") or payload.get("gnafPid")
        if gnaf_pid and source_pid and str(gnaf_pid).strip().upper() != str(source_pid).strip().upper():
            self._metrics["rejected_identity"] += 1
            return {}
        source_url = str(payload.get("realestate_url") or "")
        source_host = urlparse(source_url).netloc.lower().removeprefix("www.")
        source_name = f"SCRAPED_{source_host.upper().replace('.', '_')}"
        result: Dict[str, Any] = {
            "_addressSlug": key,
            "_sourceCollection": collection,
            "_sourceRecordId": point_id,
            "_fetch_status": "success",
        }
        evidence: Dict[str, Any] = {}

        for source_key, (path, method, verification_status) in SOURCE_FIELDS.items():
            value = _source_value(payload, source_key)
            if not _meaningful(source_key, value):
                continue
            result[source_key] = deepcopy(value)
            # A URL and address match do not establish observation of a value.
            # Reuse the original evidence, including estimate/derived status,
            # retrieval time, area type and confidence; never mint verification.
            if not _has_source_evidence(payload, source_key):
                self._metrics["rejected_evidence"] += 1
                result.pop(source_key, None)
                continue
            evidence[path] = deepcopy(payload["fieldProvenance"][path])

        if not evidence:
            self._metrics["misses"] += 1
            return {}
        result["fieldEvidence"] = evidence
        result["dataSource"] = source_name
        self._metrics["exact_matches"] += 1
        if "bed" in result:
            self._metrics["bed_matches"] += 1
        if "bath" in result:
            self._metrics["bath_matches"] += 1
        if "bed" in result and "bath" in result:
            self._metrics["room_matches"] += 1
        return result

    def get_metrics_snapshot(self) -> Dict[str, int]:
        return dict(self._metrics)
