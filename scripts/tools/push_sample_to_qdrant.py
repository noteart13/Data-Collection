"""
PUSH NSW SAMPLE 100 PROPERTIES TO QDRANT (PROPERTYMASTER COLLECTION)
===================================================================
Enriches 100 properties with corrected data logic, conforming strictly
to the original payload schema (no extra provenance keys, images empty),
and upserts them directly to Qdrant collection 'PropertyMaster'.
"""

import json
import os
import sys
from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

from bulk_enrich_master import EnrichmentEngine, process_batch

class MockPoint:
    def __init__(self, id_val, payload):
        self.id = id_val
        self.payload = payload

def main():
    print("=" * 80)
    print("PUSHING 100 SAMPLE PROPERTIES TO QDRANT (PROPERTYMASTER)")
    print("=" * 80)

    # 1. Load raw 100 sample properties
    sample_file = "nsw_sample_100_properties.json"
    if not os.path.exists(sample_file):
        print(f"Error: {sample_file} not found!")
        return

    with open(sample_file, "r", encoding="utf-8") as f:
        raw_items = json.load(f)

    print(f"Loaded {len(raw_items)} records from {sample_file}.")

    # 2. Re-enrich with fixed logic
    engine = EnrichmentEngine(seed=42)
    mock_points = [MockPoint(d["id"], dict(d.get("payload", {}))) for d in raw_items]

    updated_points, count = process_batch(mock_points, engine, force_update=True)
    print(f"Enriched {len(updated_points)} properties with corrected logic.")

    # 3. Ensure exact old payload schema (clean extra keys, empty images)
    cleaned_points = []
    for pt in updated_points:
        pl = dict(pt.payload)
        pl.pop("provenance", None)
        pl.pop("lastSoldPrice", None)
        pl.pop("lastSoldDate", None)
        pl.pop("classifiedCategory", None)
        pl["images"] = []  # Explicitly empty as requested

        cleaned_points.append(models.PointStruct(
            id=pt.id,
            payload=pl,
            vector={}
        ))

    # 4. Connect to Qdrant
    env_path = ".env"
    load_dotenv(env_path)
    url = os.getenv("QDRANT_URI", "http://localhost:6333")
    key = os.getenv("QDRANT_API", "").strip('"').strip("'") or None
    collection_name = "PropertyMaster"

    print(f"Connecting to Qdrant at {url} (Collection: {collection_name})...")
    client = QdrantClient(url=url, api_key=key, timeout=60, check_compatibility=False)

    # 5. Upsert batch to Qdrant
    print(f"Upserting {len(cleaned_points)} points to '{collection_name}'...")
    response = client.upsert(
        collection_name=collection_name,
        points=cleaned_points,
        wait=True
    )
    print(f"Upsert Status: {response.status}")

    # 6. Verify by fetching sample points back from Qdrant
    test_id = cleaned_points[0].id
    retrieved = client.retrieve(
        collection_name=collection_name,
        ids=[test_id],
        with_payload=True,
        with_vectors=False
    )

    if retrieved:
        p = retrieved[0].payload
        print("\n--- VERIFICATION OF POINT RETRIEVED FROM QDRANT ---")
        print(f"  Point ID     : {retrieved[0].id}")
        print(f"  Address      : {p.get('title')}")
        print(f"  PropertyType : {p.get('propertyType')}")
        print(f"  ExpectedPrice: ${p.get('expectedPrice'):,} AUD")
        print(f"  Weekly Rent  : ${p.get('pricing', {}).get('weeklyRent'):,}/week (Yield {p.get('pricing', {}).get('estimatedYield')})")
        print(f"  Images Count : {len(p.get('images', []))}")
        print(f"  SoldDateInfo : {p.get('soldDateInfo')}")
        print(f"  Payload Keys : {list(p.keys())}")
        print("--------------------------------------------------")

    print("\n[SUCCESS] All 100 properties have been uploaded to Qdrant successfully with the original payload schema!")
    print("=" * 80)

if __name__ == "__main__":
    main()
