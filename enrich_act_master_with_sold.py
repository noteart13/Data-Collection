import json
import os
import re
import sys
import time
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
MASTER_COLLECTION = "PropertyMaster"
SOLD_COLLECTION = "PropertySoldV2"
DATABASE_TIMEZONE = ZoneInfo(os.getenv("DATABASE_TIMEZONE", "Australia/Sydney"))


def normalize_street(street: str) -> str:
    """Normalize street address to increase matching precision."""
    s = street.lower().strip()
    s = re.sub(r'[,.]', ' ', s)
    # Common abbreviations
    s = re.sub(r'\bstreet\b', 'st', s)
    s = re.sub(r'\broad\b', 'rd', s)
    s = re.sub(r'\bavenue\b', 'ave', s)
    s = re.sub(r'\bcrescent\b', 'cres', s)
    s = re.sub(r'\bcircuit\b', 'cct', s)
    s = re.sub(r'\bplace\b', 'pl', s)
    s = re.sub(r'\bcourt\b', 'ct', s)
    s = re.sub(r'\bdrive\b', 'dr', s)
    s = re.sub(r'\bparade\b', 'pde', s)
    s = re.sub(r'\bclose\b', 'cl', s)
    s = re.sub(r'\blane\b', 'ln', s)
    s = re.sub(r'\bterrace\b', 'tce', s)
    s = re.sub(r'\bhighway\b', 'hwy', s)
    s = re.sub(r'\bboulevard\b', 'bvd', s)
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def load_all_act_sold(client: QdrantClient):
    """Retrieve all ACT sold properties from PropertySoldV2."""
    print("Fetching all ACT sold properties from PropertySoldV2...")
    sold_map = {}
    offset = None
    total_fetched = 0

    while True:
        points, next_offset = client.scroll(
            collection_name=SOLD_COLLECTION,
            scroll_filter=models.Filter(
                must=[models.FieldCondition(key="state", match=models.MatchValue(value="ACT"))]
            ),
            limit=1000,
            offset=offset,
            with_payload=True,
            with_vectors=False
        )
        if not points:
            break

        for p in points:
            pl = p.payload
            street = pl.get("street") or ""
            suburb = (pl.get("suburb") or "").strip().lower()
            if street and suburb:
                norm = normalize_street(street)
                key = (suburb, norm)
                sold_map[key] = pl

        total_fetched += len(points)
        print(f"Loaded {total_fetched:,} sold records from Qdrant...", end="\r", flush=True)

        if next_offset is None:
            break
        offset = next_offset

    print(f"\nTotal unique ACT sold addresses indexed: {len(sold_map):,}")
    return sold_map


def enrich_master(client: QdrantClient, sold_map: dict):
    """Iterate through suburbs and enrich matching records in PropertyMaster."""
    print("\nStarting enrichment of PropertyMaster with real market sales data...")
    now_iso = datetime.now(DATABASE_TIMEZONE).replace(microsecond=0).isoformat()
    
    # Get list of unique suburbs in sold_map
    suburbs = sorted(list(set(suburb for suburb, _ in sold_map.keys())))
    print(f"Targeting {len(suburbs)} suburbs with historical sold records...")

    total_enriched = 0
    t0 = time.time()

    for i, suburb in enumerate(suburbs, 1):
        # Fetch properties in PropertyMaster for this suburb
        suburb_sold_keys = {norm: pl for (sub, norm), pl in sold_map.items() if sub == suburb}
        if not suburb_sold_keys:
            continue

        offset = None
        suburb_enriched = 0
        points_to_update = []

        while True:
            master_pts, next_offset = client.scroll(
                collection_name=MASTER_COLLECTION,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(key="state", match=models.MatchValue(value="ACT")),
                        models.FieldCondition(key="suburb", match=models.MatchValue(value=suburb.title()))
                    ]
                ),
                limit=1000,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )
            if not master_pts:
                break

            for pt in master_pts:
                pl = pt.payload
                st = pl.get("street") or ""
                norm = normalize_street(st)

                if norm in suburb_sold_keys:
                    sold_info = suburb_sold_keys[norm]
                    # Update fields with real market data
                    updated_payload = dict(pl)
                    updated_payload["bed"] = sold_info.get("bed")
                    updated_payload["bath"] = sold_info.get("bath")
                    if sold_info.get("features", {}).get("garage") is not None:
                        updated_payload["features"]["garage"] = sold_info["features"]["garage"]
                    if sold_info.get("images"):
                        updated_payload["images"] = sold_info["images"]
                    if sold_info.get("historysale"):
                        updated_payload["historysale"] = sold_info["historysale"]
                    if sold_info.get("expectedPrice"):
                        updated_payload["expectedPrice"] = sold_info["expectedPrice"]
                    if sold_info.get("soldDateInfo"):
                        updated_payload["soldDateInfo"] = sold_info["soldDateInfo"]
                    if sold_info.get("realestate_url"):
                        updated_payload["realestate_url"] = sold_info["realestate_url"]

                    updated_payload["status"] = "sold"
                    updated_payload["listingOption"] = "sold"
                    updated_payload["updatedAt"] = now_iso

                    points_to_update.append(models.PointStruct(
                        id=pt.id,
                        payload=updated_payload,
                        vector={}
                    ))
                    suburb_enriched += 1

            if next_offset is None:
                break
            offset = next_offset

        if points_to_update:
            client.upsert(collection_name=MASTER_COLLECTION, points=points_to_update, wait=False)
            total_enriched += len(points_to_update)

        if i % 10 == 0 or i == len(suburbs):
            print(f"[{i}/{len(suburbs)} suburbs] Enriched {total_enriched:,} properties so far...")

    dt = time.time() - t0
    print("\n" + "=" * 70)
    print(f"ENRICHMENT COMPLETE: Enriched {total_enriched:,} ACT properties in {dt:.1f}s!")
    print("=" * 70)


def main():
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    sold_map = load_all_act_sold(client)
    if sold_map:
        enrich_master(client, sold_map)


if __name__ == "__main__":
    main()
