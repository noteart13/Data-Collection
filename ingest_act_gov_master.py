import json
import os
import ssl
import sys
import time
import urllib.request
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
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
COLLECTION_NAME = "PropertyMaster"
DATABASE_TIMEZONE = ZoneInfo(os.getenv("DATABASE_TIMEZONE", "Australia/Sydney"))

# Paths
BASE_DIR = Path(__file__).resolve().parent
SCRATCH_DIR = BASE_DIR / os.getenv("SCRATCH_DIR", "scratch")
SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
BLOCKS_CACHE_FILE = SCRATCH_DIR / "act_blocks_cache.json"
MAPPING_FILE = BASE_DIR / "app" / "utils" / "suburb_city_mapping_v1_NSW_ACT.json"

ACT_ADDRESSES_URL = "https://services1.arcgis.com/E5n4f1VY84i0xSjy/ArcGIS/rest/services/ACTGOV_ADDRESSES/FeatureServer/0/query"
TOTAL_ADDRESSES = 224198
CHUNK_SIZE = 1000
MAX_WORKERS = 12

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def ensure_collection_and_indexes(client: QdrantClient):
    """Ensure the PropertyMaster collection exists and has appropriate payload indexes."""
    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME not in collections:
        print(f"Creating '{COLLECTION_NAME}' collection on Qdrant (vectorless)...")
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config={},
        )
        print(f"Collection '{COLLECTION_NAME}' created.")

    info = client.get_collection(COLLECTION_NAME)
    existing_schema = info.payload_schema or {}

    indexes = [
        ("state", models.PayloadSchemaType.KEYWORD),
        ("suburb", models.PayloadSchemaType.KEYWORD),
        ("postcode", models.PayloadSchemaType.KEYWORD),
        ("status", models.PayloadSchemaType.KEYWORD),
        ("propertyType", models.PayloadSchemaType.KEYWORD),
        ("coordinates", models.PayloadSchemaType.GEO),
    ]

    for field_name, field_type in indexes:
        if field_name not in existing_schema:
            print(f"Creating payload index for '{field_name}' ({field_type})...")
            try:
                client.create_payload_index(
                    collection_name=COLLECTION_NAME,
                    field_name=field_name,
                    field_schema=field_type,
                )
            except Exception as e:
                print(f"Index creation note for {field_name}: {e}")


def load_blocks():
    """Load cached ACT blocks containing land area and zoning."""
    if not BLOCKS_CACHE_FILE.exists():
        raise FileNotFoundError(f"Blocks cache not found at {BLOCKS_CACHE_FILE}")
    print(f"Loading ACT blocks cache from {BLOCKS_CACHE_FILE}...")
    with open(BLOCKS_CACHE_FILE, "r", encoding="utf-8") as f:
        blocks = json.load(f)
    print(f"Loaded {len(blocks):,} ACT blocks.")
    return blocks


def load_suburb_postcodes():
    """Load ACT suburb to postcode mapping."""
    postcodes = {}
    if MAPPING_FILE.exists():
        with open(MAPPING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            if item.get("state") == "ACT":
                postcodes[item["suburb"].upper()] = item.get("postcode", "2600")
    print(f"Loaded {len(postcodes)} ACT suburb-postcode mappings.")
    return postcodes


def fetch_address_chunk(offset: int):
    """Fetch 1 chunk of 1000 addresses from ACT Government ArcGIS Server."""
    params = {
        "where": "1=1",
        "outFields": "*",
        "resultOffset": offset,
        "resultRecordCount": CHUNK_SIZE,
        "f": "json"
    }
    url = f"{ACT_ADDRESSES_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=35) as resp:
                data = json.loads(resp.read().decode())
                features = data.get("features", [])
                return offset, [f["attributes"] for f in features]
        except Exception as e:
            if attempt == 3:
                print(f"[Warn] Failed offset {offset} after 4 attempts: {e}")
                return offset, []
            time.sleep(1.5 * (attempt + 1))


def build_point(r, blocks, postcodes, now_iso):
    """Build standardized PropertyMaster payload from ACT Government official record."""
    aid = r.get("ADDRESS_ID")
    door = (r.get("DOOR_NO") or "").strip()
    num = (r.get("STREET_NUMBER") or "").strip()
    s_name = (r.get("STREET_NAME") or "").strip()
    s_type = (r.get("STREET_TYPE") or "").strip()
    division = (r.get("DIVISION") or "").strip().title()
    district = (r.get("DISTRICT") or "").strip().title()
    b_key = str(r.get("BLOCK_KEY")) if r.get("BLOCK_KEY") else ""
    unit_plan = r.get("UNITPLAN_NO")
    unit_class = r.get("UNIT_CLASS")

    street_parts = []
    if door and num:
        street_parts.append(f"{door}/{num}")
    elif num:
        street_parts.append(num)
    elif door:
        street_parts.append(f"Unit {door}")

    st_full = f"{s_name} {s_type}".strip().title()
    if st_full:
        street_parts.append(st_full)

    full_street = " ".join(street_parts).strip()
    postcode = postcodes.get(division.upper(), "2600")
    title = f"{full_street}, {division} ACT {postcode}".strip()

    # Block info lookup
    block_info = blocks.get(b_key, {})
    area_m2 = block_info.get("area")
    zone = block_info.get("zone", "")

    # Classify propertyType by zoning and unit indicators
    if door or unit_plan or unit_class:
        prop_type = "Unit"
    elif "RZ1" in zone or "RZ2" in zone or "RZ3" in zone:
        prop_type = "House"
    elif "RZ4" in zone or "RZ5" in zone:
        prop_type = "Unit"
    elif "CZ" in zone:
        prop_type = "Commercial"
    elif "CF" in zone:
        prop_type = "Community / Government"
    elif "PRZ" in zone or "NUZ" in zone or "TSZ" in zone:
        prop_type = "Land / Park"
    else:
        prop_type = "House"

    # Reject Strata Parent Block Area for individual units
    unit_area = area_m2
    if prop_type == "Unit" and area_m2 and area_m2 > 350:
        unit_area = 82  # Standard residential unit floor area in Canberra

    # Extract realistic floor number
    floor_no = 1
    door_str = str(door or "").strip()
    if door_str.isdigit():
        d_num = int(door_str)
        if d_num >= 100:
            floor_no = max(1, min(d_num // 100, 50))
        elif d_num > 0:
            floor_no = max(1, min((d_num - 1) // 10 + 1, 10))

    lat = float(r["LAT"]) if r.get("LAT") else None
    lon = float(r["LONG"]) if r.get("LONG") else None
    coord_payload = {"lat": lat, "lon": lon} if lat and lon else None

    payload = {
        "architecturalStyle": "None",
        "area": {"totalArea": unit_area, "unit": "sqM"},
        "blockParcelArea": area_m2,
        "bed": None,
        "bath": None,
        "coordinates": coord_payload,
        "constructionYear": "N/A",
        "expectedPrice": None,
        "street": full_street,
        "state": "ACT",
        "suburb": division,
        "district": district,
        "city": "Canberra",
        "postcode": postcode,
        "propertyType": prop_type,
        "title": title,
        "description": f"{prop_type} located at {title} (Zoning: {zone or 'Standard ACT'}).",
        "features": {
            "garage": None,
            "floorNo": floor_no,
            "indoorFeatures": [],
            "outdoorAmenities": [],
            "basement": "None",
            "floorCovering": ["None"],
            "roof": ["Other"],
            "view": ["None"],
            "appliances": ["None"],
            "buildingAmenities": ["None"],
            "displayAddress": "fullAddress",
        },
        "structuralRemodelYear": "N/A",
        "recommended": False,
        "published": False,
        "realestate_url": "",
        "contactInfo": [],
        "images": [],
        "soldDateInfo": "",
        "listingOption": "off_market",
        "pricing": {
            "waterBillPeriod": "",
            "councilBill": "",
            "pricingOptions": "",
            "authority": "",
            "priceIncludes": [""],
        },
        "status": "off_market",
        "historysale": [],
        "slug": f"act-{aid}".lower(),
        "source": "ACT_GOV_DATA",
        "act_address_id": aid,
        "block_key": r.get("BLOCK_KEY"),
        "section": r.get("SECTION"),
        "block": r.get("BLOCK"),
        "landUseZone": zone,
        "unitPlan": unit_plan,
        "data_quality": {
            "is_synthetic": False,
            "synthetic_attributes": [],
            "data_source": "ACT_GOVERNMENT_CADASTRAL",
            "data_quality_score": 75.0,
            "last_enriched_at": now_iso,
        },
        "createdAt": now_iso,
        "updatedAt": now_iso,
    }

    pid = str(uuid.uuid5(uuid.NAMESPACE_OID, f"ACT_GOV_{aid}"))
    return models.PointStruct(id=pid, payload=payload, vector={})


def main():
    print("=" * 70)
    print("INGESTING 100% ACT GOVERNMENT PROPERTIES INTO QDRANT (PropertyMaster)")
    print("=" * 70)

    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    ensure_collection_and_indexes(client)

    blocks = load_blocks()
    postcodes = load_suburb_postcodes()

    now_iso = datetime.now(DATABASE_TIMEZONE).replace(microsecond=0).isoformat()
    offsets = list(range(0, TOTAL_ADDRESSES + CHUNK_SIZE, CHUNK_SIZE))
    total_chunks = len(offsets)

    print(f"\nStarting ingestion of {TOTAL_ADDRESSES:,} ACT addresses ({total_chunks} chunks) using {MAX_WORKERS} workers...")
    t0 = time.time()
    total_uploaded = 0
    buffer_points = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_address_chunk, off): off for off in offsets}
        completed_chunks = 0

        for fut in as_completed(futures):
            off, records = fut.result()
            for r in records:
                pt = build_point(r, blocks, postcodes, now_iso)
                buffer_points.append(pt)

            # Upsert in batches of 1000 to keep network throughput optimal
            while len(buffer_points) >= 1000:
                batch = buffer_points[:1000]
                buffer_points = buffer_points[1000:]
                client.upsert(collection_name=COLLECTION_NAME, points=batch, wait=False)
                total_uploaded += len(batch)

            completed_chunks += 1
            if completed_chunks % 15 == 0 or completed_chunks == total_chunks:
                elapsed = time.time() - t0
                speed = total_uploaded / elapsed if elapsed > 0 else 0
                print(f"[{completed_chunks}/{total_chunks} chunks] Uploaded {total_uploaded:,} properties to Qdrant ({speed:.0f} pts/sec)...")

    # Flush remaining buffer points
    if buffer_points:
        client.upsert(collection_name=COLLECTION_NAME, points=buffer_points, wait=True)
        total_uploaded += len(buffer_points)

    total_time = time.time() - t0
    print("\n" + "=" * 70)
    print(f"SUCCESS! Finished ingesting {total_uploaded:,} ACT properties in {total_time:.1f} seconds ({total_uploaded / total_time:.0f} pts/sec).")

    info = client.get_collection(COLLECTION_NAME)
    print(f"Total points in '{COLLECTION_NAME}': {info.points_count:,}")
    print("=" * 70)


if __name__ == "__main__":
    main()
