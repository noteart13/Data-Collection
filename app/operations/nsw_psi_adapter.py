"""Exact-address adapter for NSW Valuer General Property Sales Information."""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import threading
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


PSI_INDEX_SCHEMA_VERSION = "2"


def parse_optional_float(value: Any) -> float | None:
    try:
        parsed = float(str(value or "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def psi_property_attributes(row: Dict[str, Any]) -> Dict[str, Any]:
    """Preserve official PSI attributes without guessing their payload meaning."""
    return {
        "areaSqm": parse_optional_float(row.get("Area (m2)")),
        "areaSource": str(row.get("Area (source)") or "").strip() or None,
        "areaTypeSource": str(row.get("Area type (source)") or "").strip() or None,
        "zoning": str(row.get("Zoning") or "").strip() or None,
        "propertyTypeSource": str(
            row.get("Nature of property (description)") or ""
        ).strip() or None,
        "primaryPurpose": str(
            row.get("Primary purpose (normalised)") or ""
        ).strip() or None,
        "strataLotNumber": str(row.get("Strata lot number") or "").strip() or None,
        "unitNumber": str(row.get("Property unit number") or "").strip() or None,
    }

def normalize_nsw_address(street: str) -> str:
    value = str(street or "").lower().strip()
    value = re.sub(r"\b(unit|apt|apartment|suite|lot)\b\s*", "", value)
    value = re.sub(r"(\d+)\s*-\s*\d+", r"\1", value)
    replacements = {
        "street": "st", "road": "rd", "avenue": "ave", "crescent": "cres",
        "circuit": "cct", "place": "pl", "court": "ct", "drive": "dr",
        "parade": "pde", "close": "cl", "lane": "ln", "terrace": "tce",
        "highway": "hwy", "boulevard": "bvd",
    }
    for full_name, abbreviation in replacements.items():
        value = re.sub(rf"\b{full_name}\b", abbreviation, value)
    value = re.sub(r"[^a-z0-9 ]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_nsw_date(value: Any) -> str:
    text = str(value or "").strip().split("T")[0].split(" ")[0]
    for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, date_format).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


class NswPsiSalesAdapter:
    source_url = "https://www.valuergeneral.nsw.gov.au/design/bulk_psi_content/bulk_psi"

    def __init__(self, zip_path: Path, suburb: str):
        self.zip_path = Path(zip_path)
        self.suburb = str(suburb or "").strip().casefold()
        self.dataset_version = ""
        self._sales_by_address: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._load()

    def _load(self) -> None:
        if not self.zip_path.exists():
            return
        with zipfile.ZipFile(self.zip_path, "r") as archive:
            csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not csv_names:
                return
            csv_name = csv_names[0]
            version_match = re.search(r"(20\d{6})", csv_name)
            self.dataset_version = version_match.group(1) if version_match else csv_name
            with archive.open(csv_name) as raw_file:
                text_file = io.TextIOWrapper(raw_file, encoding="utf-8", errors="replace")
                for row in csv.DictReader(text_file):
                    if str(row.get("Property locality") or "").strip().casefold() != self.suburb:
                        continue
                    house_number = str(row.get("Property house number") or "").strip()
                    street_name = str(row.get("Property street name") or "").strip()
                    unit_number = str(row.get("Property unit number") or "").strip()
                    try:
                        price = float(row.get("Purchase price") or 0)
                    except (TypeError, ValueError):
                        continue
                    contract_date = parse_nsw_date(row.get("Contract date"))
                    settlement_date = parse_nsw_date(row.get("Settlement date"))
                    if not house_number or not street_name or price < 10000 or not (contract_date or settlement_date):
                        continue
                    street = f"{unit_number}/{house_number} {street_name}" if unit_number else f"{house_number} {street_name}"
                    key = normalize_nsw_address(street)
                    sale = {
                        "price": price,
                        "contractDate": contract_date,
                        "settlementDate": settlement_date,
                        "type": "official_transfer",
                        "source": "NSW Valuer General PSI",
                        "sourceRecordId": str(row.get("Property ID") or "").strip() or None,
                    }
                    sale.update(psi_property_attributes(row))
                    self._sales_by_address[key].append(sale)
        for sales in self._sales_by_address.values():
            sales.sort(key=lambda item: item.get("contractDate") or item.get("settlementDate") or "", reverse=True)

    @property
    def matched_address_count(self) -> int:
        return len(self._sales_by_address)

    def lookup(self, street: str, suburb: str = "") -> List[Dict[str, Any]]:
        return [dict(item) for item in self._sales_by_address.get(normalize_nsw_address(street), [])]


class NswPsiSqliteAdapter:
    source_url = NswPsiSalesAdapter.source_url

    def __init__(self, zip_path: Path, db_path: Path):
        self.zip_path = Path(zip_path)
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.dataset_version = ""
        self.matched_address_count = 0
        self._ensure_index()

    def _connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(str(self.db_path), timeout=60, check_same_thread=False)
            connection.execute("PRAGMA query_only=ON")
            self._local.connection = connection
        return connection

    def _ensure_index(self) -> None:
        if not self.zip_path.exists():
            raise FileNotFoundError(self.zip_path)
        source_signature = f"{self.zip_path.stat().st_size}:{self.zip_path.stat().st_mtime_ns}"
        if self.db_path.exists():
            try:
                connection = sqlite3.connect(str(self.db_path))
                metadata = dict(connection.execute("SELECT key, value FROM metadata").fetchall())
                connection.close()
                if (
                    metadata.get("source_signature") == source_signature
                    and metadata.get("schema_version") == PSI_INDEX_SCHEMA_VERSION
                ):
                    self.dataset_version = metadata.get("dataset_version", "")
                    self.matched_address_count = int(metadata.get("address_count", "0"))
                    return
            except (sqlite3.Error, ValueError):
                pass

        temp_path = self.db_path.with_suffix(self.db_path.suffix + ".building")
        if temp_path.exists():
            temp_path.unlink()
        connection = sqlite3.connect(str(temp_path), timeout=60)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute(
            "CREATE TABLE sales ("
            "suburb_key TEXT, address_key TEXT, price REAL, contract_date TEXT, "
            "settlement_date TEXT, property_id TEXT, area_sqm REAL, area_source TEXT, "
            "area_type_source TEXT, zoning TEXT, property_type_source TEXT, "
            "primary_purpose TEXT, strata_lot_number TEXT, unit_number TEXT)"
        )
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")

        inserted = 0
        with zipfile.ZipFile(self.zip_path, "r") as archive:
            csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not csv_names:
                raise FileNotFoundError("No CSV file found in NSW PSI archive")
            csv_name = csv_names[0]
            version_match = re.search(r"(20\d{6})", csv_name)
            self.dataset_version = version_match.group(1) if version_match else csv_name
            batch = []
            with archive.open(csv_name) as raw_file:
                text_file = io.TextIOWrapper(raw_file, encoding="utf-8", errors="replace")
                for row in csv.DictReader(text_file):
                    house_number = str(row.get("Property house number") or "").strip()
                    street_name = str(row.get("Property street name") or "").strip()
                    unit_number = str(row.get("Property unit number") or "").strip()
                    suburb_key = str(row.get("Property locality") or "").strip().casefold()
                    try:
                        price = float(row.get("Purchase price") or 0)
                    except (TypeError, ValueError):
                        continue
                    contract_date = parse_nsw_date(row.get("Contract date"))
                    settlement_date = parse_nsw_date(row.get("Settlement date"))
                    if not suburb_key or not house_number or not street_name or price < 10000 or not (contract_date or settlement_date):
                        continue
                    street = f"{unit_number}/{house_number} {street_name}" if unit_number else f"{house_number} {street_name}"
                    attributes = psi_property_attributes(row)
                    batch.append((
                        suburb_key,
                        normalize_nsw_address(street),
                        price,
                        contract_date,
                        settlement_date,
                        str(row.get("Property ID") or "").strip() or None,
                        attributes["areaSqm"],
                        attributes["areaSource"],
                        attributes["areaTypeSource"],
                        attributes["zoning"],
                        attributes["propertyTypeSource"],
                        attributes["primaryPurpose"],
                        attributes["strataLotNumber"],
                        attributes["unitNumber"],
                    ))
                    if len(batch) >= 10000:
                        connection.executemany(
                            "INSERT INTO sales VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            batch,
                        )
                        inserted += len(batch)
                        batch.clear()
                if batch:
                    connection.executemany(
                        "INSERT INTO sales VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        batch,
                    )
                    inserted += len(batch)

        connection.execute("CREATE INDEX idx_sales_address ON sales(suburb_key, address_key)")
        address_count = connection.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM sales GROUP BY suburb_key, address_key)"
        ).fetchone()[0]
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            [
                ("source_signature", source_signature),
                ("schema_version", PSI_INDEX_SCHEMA_VERSION),
                ("dataset_version", self.dataset_version),
                ("address_count", str(address_count)),
                ("sale_count", str(inserted)),
            ],
        )
        connection.commit()
        connection.close()
        if self.db_path.exists():
            self.db_path.unlink()
        temp_path.replace(self.db_path)
        self.matched_address_count = int(address_count)

    def lookup(self, street: str, suburb: str = "") -> List[Dict[str, Any]]:
        rows = self._connection().execute(
            "SELECT price, contract_date, settlement_date, property_id, area_sqm, "
            "area_source, area_type_source, zoning, property_type_source, "
            "primary_purpose, strata_lot_number, unit_number FROM sales "
            "WHERE suburb_key = ? AND address_key = ? "
            "ORDER BY contract_date DESC, settlement_date DESC",
            (str(suburb or "").strip().casefold(), normalize_nsw_address(street)),
        ).fetchall()
        return [
            {
                "price": row[0],
                "contractDate": row[1],
                "settlementDate": row[2],
                "type": "official_transfer",
                "source": "NSW Valuer General PSI",
                "sourceRecordId": row[3],
                "areaSqm": row[4],
                "areaSource": row[5],
                "areaTypeSource": row[6],
                "zoning": row[7],
                "propertyTypeSource": row[8],
                "primaryPurpose": row[9],
                "strataLotNumber": row[10],
                "unitNumber": row[11],
            }
            for row in rows
        ]
