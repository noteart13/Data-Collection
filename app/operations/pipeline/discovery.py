"""
Stage 1: Discovery Stage.
Decoupled discovery providers for finding target addresses, URLs, and statutory records.
"""

from abc import ABC, abstractmethod
from typing import Iterator, Dict, Any, List, Optional
import random


class BaseDiscovery(ABC):
    """Abstract interface for data source discovery."""

    @abstractmethod
    def discover(self, limit: Optional[int] = None, offset: int = 0) -> Iterator[Dict[str, Any]]:
        """Yield discovered records or URL descriptors."""
        pass


class SyntheticDiscovery(BaseDiscovery):
    """
    High-volume synthetic Australian property generator for reproducible benchmarks,
    dry-runs, and stress-testing at 1,000, 10,000, and 100,000 scales.
    """

    SUBURBS = [
        ("Sydney", "NSW", "2000", -33.8688, 151.2093),
        ("Parramatta", "NSW", "2150", -33.8150, 151.0011),
        ("Chatswood", "NSW", "2067", -33.7975, 151.1809),
        ("Melbourne", "VIC", "3000", -37.8136, 144.9631),
        ("Carlton", "VIC", "3053", -37.8000, 144.9667),
        ("Brisbane City", "QLD", "4000", -27.4705, 153.0260),
        ("Fortitude Valley", "QLD", "4006", -27.4580, 153.0360),
        ("Canberra", "ACT", "2601", -35.2809, 149.1300),
        ("Kingston", "ACT", "2604", -35.3160, 149.1430),
        ("Perth", "WA", "6000", -31.9505, 115.8605),
        ("Adelaide", "SA", "5000", -34.9285, 138.6007),
        ("Hobart", "TAS", "7000", -42.8821, 147.3272),
        ("Darwin", "NT", "0800", -12.4634, 130.8456),
    ]

    STREET_NAMES = [
        "George St", "Pitt St", "Collins St", "Bourke St", "Queen St",
        "King St", "Elizabeth St", "Macquarie St", "Crown St", "Oxford St",
        "Flinders Lane", "Swanston St", "Ann St", "Adelaide St", "Northbourne Ave"
    ]

    PROPERTY_TYPES = ["house", "unit", "townhouse", "terrace", "duplex"]

    def __init__(self, seed: int = 42, error_rate: float = 0.02):
        self.seed = seed
        self.error_rate = error_rate
        self.rng = random.Random(seed)

    def discover(self, limit: Optional[int] = None, offset: int = 0) -> Iterator[Dict[str, Any]]:
        count = 0
        current_idx = offset
        # Seeded random sequence
        rng = random.Random(self.seed + offset)

        while True:
            if limit is not None and count >= limit:
                break

            current_idx += 1
            count += 1

            suburb_info = rng.choice(self.SUBURBS)
            suburb, state, pcode, base_lat, base_lon = suburb_info
            street_no = rng.randint(1, 450)
            street_name = rng.choice(self.STREET_NAMES)
            prop_type = rng.choice(self.PROPERTY_TYPES)

            # Add slight jitter to coords (~ 500m)
            lat = round(base_lat + rng.uniform(-0.015, 0.015), 6)
            lon = round(base_lon + rng.uniform(-0.015, 0.015), 6)

            record_id = f"SYNTH_{state}_{current_idx:07d}"

            # Controlled error injection for testing quality & error rates
            has_error = rng.random() < self.error_rate
            if has_error:
                error_type = rng.choice(["corrupt_coords", "invalid_postcode", "empty_street"])
                if error_type == "corrupt_coords":
                    lat = 999.0  # Invalid latitude
                elif error_type == "invalid_postcode":
                    pcode = "INVALID"
                elif error_type == "empty_street":
                    street_name = ""

            yield {
                "id": record_id,
                "offset": current_idx,
                "street": f"{street_no} {street_name}".strip(),
                "suburb": suburb,
                "state": state,
                "postcode": pcode,
                "propertyType": prop_type,
                "coordinates": {"lat": lat, "lon": lon},
                "source": "SYNTHETIC_BENCHMARK",
                "raw_discovered_at": "2026-11-11T00:00:00Z"
            }


class GnafDiscovery(BaseDiscovery):
    """Discovers addresses from G-NAF entries."""

    def __init__(self, addresses: Optional[List[Dict[str, Any]]] = None):
        self.addresses = addresses or []

    def discover(self, limit: Optional[int] = None, offset: int = 0) -> Iterator[Dict[str, Any]]:
        sliced = self.addresses[offset:]
        count = 0
        for idx, item in enumerate(sliced, start=offset):
            if limit is not None and count >= limit:
                break
            count += 1
            item["offset"] = idx
            yield item


class OpenDataDiscovery(BaseDiscovery):
    """Discovers records from Open Data registers (ACT ArcGIS, NSW Valuer General)."""

    def __init__(self, records: Optional[List[Dict[str, Any]]] = None):
        self.records = records or []

    def discover(self, limit: Optional[int] = None, offset: int = 0) -> Iterator[Dict[str, Any]]:
        sliced = self.records[offset:]
        count = 0
        for idx, item in enumerate(sliced, start=offset):
            if limit is not None and count >= limit:
                break
            count += 1
            item["offset"] = idx
            yield item
