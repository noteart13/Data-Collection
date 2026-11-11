import io
import os
import sys
import time
import uuid
import zipfile
import urllib.request
import zlib
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "replace")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "replace")

from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URI", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API", "").strip('"').strip("'") or None
DEFAULT_COLLECTION = "PropertyMaster"
DATABASE_TIMEZONE = ZoneInfo(os.getenv("DATABASE_TIMEZONE", "Australia/Sydney"))
GNAF_ZIP_URL = "https://data.gov.au/data/dataset/19432f89-dc3a-4ef3-b943-5326ef1dbecc/resource/b023544a-5836-4d43-b6d8-da2f73e8d2bf/download/g-naf_aug26_allstates_gda2020_psv_110.zip"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

# Load suburb to city mapping if available
MAPPING_FILE = Path(__file__).resolve().parent / "suburb_city_mapping_v1.json"
SUBURB_MAPPING = {}
if MAPPING_FILE.exists():
    try:
        import json
        with open(MAPPING_FILE, "r", encoding="utf-8") as f:
            mapping_data = json.load(f)
        SUBURB_MAPPING = {
            (item["state"].lower(), item["suburb"].lower()): item.get("city", item["suburb"])
            for item in mapping_data if "state" in item and "suburb" in item
        }
        print(f"Loaded {len(SUBURB_MAPPING):,} suburb-to-city mappings.")
    except Exception as e:
        print(f"Note: error loading suburb_city_mapping: {e}")


class DirectGnafExtractor:
    """Extracts specific tables from the remote G-NAF zip via single-request HTTP Range Requests."""
    def __init__(self, url: str):
        self.url = url
        req = urllib.request.Request(self.url, method="HEAD", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            self.file_size = int(resp.headers.get("Content-Length"))

        # Fetch Central Directory from the end of the remote zip
        tail_size = 65536
        tail_data = self._fetch_range(self.file_size - tail_size, self.file_size - 1)
        eocd_idx = tail_data.rfind(b"PK\x05\x06")
        if eocd_idx == -1:
            raise RuntimeError("End of Central Directory not found in remote zip.")

        cd_size = int.from_bytes(tail_data[eocd_idx + 12:eocd_idx + 16], "little")
        cd_offset = int.from_bytes(tail_data[eocd_idx + 16:eocd_idx + 20], "little")
        cd_data = self._fetch_range(cd_offset, cd_offset + cd_size - 1)

        mock_zip = io.BytesIO()
        mock_zip.seek(cd_offset)
        mock_zip.write(cd_data)
        mock_zip.write(tail_data[eocd_idx:])
        mock_zip.seek(0)
        self.zf = zipfile.ZipFile(mock_zip)

    def _fetch_range(self, start: int, end: int) -> bytes:
        req = urllib.request.Request(self.url, headers={**HEADERS, "Range": f"bytes={start}-{end}"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()

    def get_decompressed_stream(self, path: str):
        """Fetch and decompress a zip entry directly in memory via 1 HTTP request."""
        info = self.zf.getinfo(path)
        header = self._fetch_range(info.header_offset, info.header_offset + 30 + 512)
        fn_len = int.from_bytes(header[26:28], "little")
        ex_len = int.from_bytes(header[28:30], "little")
        data_start = info.header_offset + 30 + fn_len + ex_len
        data_end = data_start + info.compress_size - 1

        raw_compressed = self._fetch_range(data_start, data_end)
        decompressed = zlib.decompress(raw_compressed, -15)
        return io.BytesIO(decompressed)


def ensure_collection(client: QdrantClient, collection_name: str):
    """Ensure the PropertyMaster collection exists with vectorless config matching PropertySoldV2."""
    collections = [c.name for c in client.get_collections().collections]
    if collection_name not in collections:
        print(f"Creating collection '{collection_name}' on Qdrant (vectorless)...")
        client.create_collection(
            collection_name=collection_name,
            vectors_config={},
        )
        print(f"Collection '{collection_name}' created successfully.")

    # Create payload indexes
    info = client.get_collection(collection_name)
    existing_schema = info.payload_schema or {}

    indexes = [
        ("state", models.PayloadSchemaType.KEYWORD),
        ("suburb", models.PayloadSchemaType.KEYWORD),
        ("postcode", models.PayloadSchemaType.KEYWORD),
        ("status", models.PayloadSchemaType.KEYWORD),
        ("coordinates", models.PayloadSchemaType.GEO),
    ]

    for field_name, field_type in indexes:
        if field_name not in existing_schema:
            print(f"Creating payload index for '{field_name}' ({field_type})...")
            try:
                client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=field_type,
                    wait=False,
                )
            except Exception as e:
                print(f"Index creation note for {field_name}: {e}")


def load_locality_map(extractor: DirectGnafExtractor, state: str):
    localities = {}
    path = f"G-NAF/G-NAF AUGUST 2026/Standard/{state}_LOCALITY_psv.psv"
    try:
        f = extractor.get_decompressed_stream(path)
        headers = f.readline().decode("utf-8").strip().split("|")
        idx_pid = headers.index("LOCALITY_PID")
        idx_name = headers.index("LOCALITY_NAME")
        for line in f:
            parts = line.decode("utf-8").strip().split("|")
            if len(parts) > max(idx_pid, idx_name):
                localities[parts[idx_pid]] = parts[idx_name].title()
    except Exception as e:
        print(f"Warning loading localities for {state}: {e}")
    return localities


def load_street_map(extractor: DirectGnafExtractor, state: str):
    streets = {}
    path = f"G-NAF/G-NAF AUGUST 2026/Standard/{state}_STREET_LOCALITY_psv.psv"
    try:
        f = extractor.get_decompressed_stream(path)
        headers = f.readline().decode("utf-8").strip().split("|")
        idx_pid = headers.index("STREET_LOCALITY_PID")
        idx_name = headers.index("STREET_NAME")
        idx_type = headers.index("STREET_TYPE_CODE")
        for line in f:
            parts = line.decode("utf-8").strip().split("|")
            if len(parts) > max(idx_pid, idx_name, idx_type):
                s_name = parts[idx_name].title()
                s_type = parts[idx_type].title() if parts[idx_type] else ""
                streets[parts[idx_pid]] = f"{s_name} {s_type}".strip()
    except Exception as e:
        print(f"Warning loading streets for {state}: {e}")
    return streets


def load_geocode_map(extractor: DirectGnafExtractor, state: str):
    geocodes = {}
    path = f"G-NAF/G-NAF AUGUST 2026/Standard/{state}_ADDRESS_DEFAULT_GEOCODE_psv.psv"
    try:
        f = extractor.get_decompressed_stream(path)
        headers = f.readline().decode("utf-8").strip().split("|")
        idx_pid = headers.index("ADDRESS_DETAIL_PID")
        idx_lon = headers.index("LONGITUDE")
        idx_lat = headers.index("LATITUDE")
        for line in f:
            parts = line.decode("utf-8").strip().split("|")
            if len(parts) > max(idx_pid, idx_lon, idx_lat):
                lat_str = parts[idx_lat]
                lon_str = parts[idx_lon]
                if lat_str and lon_str:
                    try:
                        geocodes[parts[idx_pid]] = (float(lat_str), float(lon_str))
                    except ValueError:
                        pass
    except Exception as e:
        print(f"Warning loading geocodes for {state}: {e}")
    return geocodes


def build_property_payload(r, streets, localities, geocodes, state, now_iso):
    pid = r.get("ADDRESS_DETAIL_PID")
    flat = r.get("FLAT_NUMBER")
    num1 = r.get("NUMBER_FIRST")
    num2 = r.get("NUMBER_LAST")
    lot = r.get("LOT_NUMBER")
    s_pid = r.get("STREET_LOCALITY_PID")
    loc_pid = r.get("LOCALITY_PID")
    postcode = r.get("POSTCODE") or ""

    street_name = streets.get(s_pid, "")
    suburb = localities.get(loc_pid, "")
    city = SUBURB_MAPPING.get((state.lower(), suburb.lower()), suburb)

    num_part = ""
    if flat and num1:
        num_part = f"{flat}/{num1}"
    elif num1:
        num_part = f"{num1}-{num2}" if num2 else num1
    elif lot:
        num_part = f"Lot {lot}"

    full_street = f"{num_part} {street_name}".strip() if num_part else street_name
    title = f"{full_street}, {suburb} {state} {postcode}".strip()

    coords = geocodes.get(pid)
    coord_payload = {"lat": coords[0], "lon": coords[1]} if coords else None

    payload = {
        "architecturalStyle": None,
        "area": {"totalArea": None, "unit": "sqM"},
        "bed": None,
        "bath": None,
        "coordinates": coord_payload,
        "constructionYear": None,
        "expectedPrice": None,
        "street": full_street,
        "state": state,
        "suburb": suburb,
        "city": city,
        "postcode": postcode,
        "propertyType": None,
        "title": title,
        "description": "",
        "features": {
            "garage": None,
            "floorNo": None,
            "indoorFeatures": [],
            "outdoorAmenities": [],
            "basement": None,
            "floorCovering": [],
            "roof": [],
            "view": [],
            "appliances": [],
            "buildingAmenities": [],
            "displayAddress": "fullAddress",
        },
        "structuralRemodelYear": None,
        "recommended": False,
        "published": False,
        "realestate_url": "",
        "contactInfo": [],
        "images": [],
        "soldDateInfo": "",
        "listingOption": "unknown",
        "pricing": {
            "waterBillPeriod": None,
            "councilBill": None,
            "pricingOptions": None,
            "authority": None,
            "priceIncludes": [],
        },
        "status": "unknown",
        "historysale": [],
        "slug": pid.lower(),
        "gnaf_pid": pid,
        "confidence": r.get("CONFIDENCE"),
        "primary_secondary": r.get("PRIMARY_SECONDARY"),
        "data_quality": {
            "is_synthetic": False,
            "synthetic_attributes": [],
            "data_source": "G_NAF_OFFICIAL",
            "data_quality_score": 65.0,
            "last_enriched_at": now_iso,
        },
        "createdAt": now_iso,
        "updatedAt": now_iso,
    }

    point_id = str(uuid.uuid5(uuid.NAMESPACE_OID, pid))
    return point_id, payload


def get_state_count(client: QdrantClient, collection_name: str, state: str) -> int:
    try:
        res = client.count(
            collection_name=collection_name,
            count_filter=models.Filter(
                must=[models.FieldCondition(key="state", match=models.MatchValue(value=state.upper()))]
            ),
            exact=True
        )
        return res.count
    except Exception:
        return 0


def robust_upsert(client: QdrantClient, collection_name: str, batch: list, max_retries: int = 5):
    for attempt in range(1, max_retries + 1):
        try:
            client.upsert(collection_name=collection_name, points=batch, wait=False)
            return
        except Exception as e:
            if attempt == max_retries:
                raise
            print(f"\n[Retry {attempt}/{max_retries}] Upsert to {collection_name} failed ({e}). Retrying in {attempt * 2}s...", flush=True)
            time.sleep(attempt * 2)


def process_state(client: QdrantClient, extractor: DirectGnafExtractor, state: str, collection_name: str, limit: int = None, batch_size: int = 1000, force: bool = False):
    print(f"\n[{state}] Starting processing...")

    # Fast resume check: skip already completed states
    existing_count = get_state_count(client, collection_name, state)
    if existing_count > 0 and not force and not limit:
        print(f"[{state}] Already found {existing_count:,} existing addresses in Qdrant.")
        # Load geocodes to verify completeness
        geocodes = load_geocode_map(extractor, state)
        if existing_count >= int(len(geocodes) * 0.98):
            print(f"[{state}] State is already fully uploaded ({existing_count:,} >= {len(geocodes):,}). Skipping to next state!")
            return existing_count
        print(f"[{state}] Continuing upload to ensure 100% coverage...")
    else:
        print(f"[{state}] Loading geocodes (lat/lon)...")
        geocodes = load_geocode_map(extractor, state)
        print(f"[{state}] Loaded {len(geocodes):,} geocodes.")

    print(f"[{state}] Loading localities...")
    localities = load_locality_map(extractor, state)
    print(f"[{state}] Loaded {len(localities):,} localities.")

    print(f"[{state}] Loading street names...")
    streets = load_street_map(extractor, state)
    print(f"[{state}] Loaded {len(streets):,} streets.")

    addr_path = f"G-NAF/G-NAF AUGUST 2026/Standard/{state}_ADDRESS_DETAIL_psv.psv"
    now_iso = datetime.now(DATABASE_TIMEZONE).replace(microsecond=0).isoformat()

    print(f"[{state}] Fetching & decompressing address details in memory...")
    f = extractor.get_decompressed_stream(addr_path)
    print(f"[{state}] Streaming address details and uploading to Qdrant...")

    batch = []
    total_uploaded = 0

    headers = f.readline().decode("utf-8").strip().split("|")
    for line in f:
        parts = line.decode("utf-8").strip().split("|")
        if len(parts) < len(headers):
            continue
        r = dict(zip(headers, parts))

        # Skip retired addresses
        if r.get("DATE_RETIRED"):
            continue

        point_id, payload = build_property_payload(r, streets, localities, geocodes, state, now_iso)
        batch.append(models.PointStruct(id=point_id, payload=payload, vector={}))

        if len(batch) >= batch_size:
            robust_upsert(client, collection_name, batch)
            total_uploaded += len(batch)
            batch = []
            print(f"[{state}] Uploaded {total_uploaded:,} addresses...", end="\r", flush=True)

        if limit and total_uploaded + len(batch) >= limit:
            break

    if batch:
        robust_upsert(client, collection_name, batch)
        total_uploaded += len(batch)

    print(f"\n[{state}] Completed! Total addresses uploaded: {total_uploaded:,}")
    return total_uploaded


def main():
    parser = argparse.ArgumentParser(description="Stream G-NAF directly to Qdrant PropertyMaster collection.")
    parser.add_argument("--state", default="ACT", help="State to process (ACT, NT, TAS, WA, SA, QLD, VIC, NSW, OT, or ALL). Default: ACT")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help=f"Target collection name (default: {DEFAULT_COLLECTION})")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of addresses to import (useful for testing)")
    parser.add_argument("--batch-size", type=int, default=1000, help="Batch size for Qdrant upsert (default: 1000)")
    parser.add_argument("--url", default=None, help=f"Qdrant server URL (default from .env: {QDRANT_URL})")
    parser.add_argument("--api-key", default=None, help="Qdrant API key (default from .env)")
    parser.add_argument("--force", action="store_true", help="Force re-importing all states from scratch")
    args = parser.parse_args()

    target_url = args.url or QDRANT_URL
    if args.api_key is not None:
        target_api_key = args.api_key.strip('"').strip("'")
    elif "localhost" in target_url or "127.0.0.1" in target_url:
        target_api_key = None
    else:
        target_api_key = QDRANT_API_KEY

    print(f"Connecting to Qdrant at: {target_url}...")
    client = QdrantClient(url=target_url, api_key=target_api_key, timeout=300, check_compatibility=False)
    ensure_collection(client, args.collection)

    print(f"Connecting to remote G-NAF package via HTTP Range Requests...")
    extractor = DirectGnafExtractor(GNAF_ZIP_URL)
    print(f"Connected successfully to G-NAF August 2026 release ({extractor.file_size / (1024*1024*1024):.2f} GB)!")

    states = [args.state.upper()]
    if args.state.upper() == "ALL":
        states = ["ACT", "NT", "TAS", "WA", "SA", "QLD", "VIC", "NSW", "OT"]

    grand_total = 0
    for s in states:
        uploaded = process_state(client, extractor, s, args.collection, limit=args.limit, batch_size=args.batch_size, force=args.force)
        grand_total += uploaded

    print(f"\n=======================================================")
    print(f" ALL DONE! Grand total addresses in {args.collection}: {grand_total:,}")
    print(f"=======================================================\n")


if __name__ == "__main__":
    main()
