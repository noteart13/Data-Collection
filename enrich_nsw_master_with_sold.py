import argparse
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
CHECKPOINT_FILE = "enrich_sold_checkpoint_NSW.txt"


def normalize_street(street: str) -> str:
    """Normalize street address to maximize match rate between commercial & official registers."""
    s = street.lower().strip()
    s = re.sub(r'[,.]', ' ', s)
    # Strip unit/apt prefix markers so 'Unit 1/10 Smith St' matches '1/10 Smith St'
    s = re.sub(r'\b(unit|apt|apartment|suite|lot)\b\s*', '', s)
    # Simplify hyphenated numbers, e.g. 16-20 Kendall St -> 16 Kendall St
    s = re.sub(r'(\d+)\s*-\s*\d+', r'\1', s)
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


def load_nsw_sold_batch(client: QdrantClient, limit: int = 200000):
    """Retrieve NSW sold properties from PropertySoldV2."""
    print("Fetching NSW sold commercial listings from PropertySoldV2...")
    sold_map = {}
    offset = None
    total_fetched = 0

    while True:
        points, next_offset = client.scroll(
            collection_name=SOLD_COLLECTION,
            scroll_filter=models.Filter(
                must=[models.FieldCondition(key="state", match=models.MatchValue(value="NSW"))]
            ),
            limit=2000,
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
        print(f"Loaded {total_fetched:,} NSW sold records from Qdrant...", end="\r", flush=True)

        if next_offset is None or total_fetched >= limit:
            break
        offset = next_offset

    print(f"\nTotal unique NSW sold addresses indexed: {len(sold_map):,}")
    return sold_map


def enrich_nsw_master(client: QdrantClient, sold_map: dict, target_suburb: str = None, limit_suburbs: int = None, dry_run: bool = False):
    """Enrich matching NSW records in PropertyMaster safely with checkpoint tracking."""
    print("\nStarting commercial enrichment of NSW records in PropertyMaster...")
    now_iso = datetime.now(DATABASE_TIMEZONE).replace(microsecond=0).isoformat()
    
    completed_suburbs = set()
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip().lower()
                if s:
                    completed_suburbs.add(s)
        print(f"Resuming: {len(completed_suburbs):,} suburbs already enriched in previous runs.")

    all_suburbs = sorted(list(set(suburb for suburb, _ in sold_map.keys())))
    if target_suburb:
        suburbs = [target_suburb.lower()]
    else:
        suburbs = [s for s in all_suburbs if s not in completed_suburbs]
        if limit_suburbs:
            suburbs = suburbs[:limit_suburbs]

    print(f"Targeting {len(suburbs):,} NSW suburbs (Dry-run: {dry_run})...")

    total_enriched = 0
    t0 = time.time()

    for i, suburb in enumerate(suburbs, 1):
        suburb_sold_keys = {norm: pl for (sub, norm), pl in sold_map.items() if sub == suburb}
        if not suburb_sold_keys:
            continue

        offset = None
        points_to_update = []

        while True:
            try:
                master_pts, next_offset = client.scroll(
                    collection_name=MASTER_COLLECTION,
                    scroll_filter=models.Filter(
                        must=[
                            models.FieldCondition(key="state", match=models.MatchValue(value="NSW")),
                            models.FieldCondition(key="suburb", match=models.MatchValue(value=suburb.title()))
                        ]
                    ),
                    limit=1000,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False
                )
            except Exception as e:
                time.sleep(1)
                master_pts, next_offset = client.scroll(
                    collection_name=MASTER_COLLECTION,
                    scroll_filter=models.Filter(
                        must=[
                            models.FieldCondition(key="state", match=models.MatchValue(value="NSW")),
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
                    updated_payload = dict(pl)

                    # Non-destructive assignment (only assign when source has valid data)
                    if sold_info.get("bed") is not None:
                        updated_payload["bed"] = sold_info["bed"]
                    if sold_info.get("bath") is not None:
                        updated_payload["bath"] = sold_info["bath"]

                    if "features" not in updated_payload or not isinstance(updated_payload["features"], dict):
                        updated_payload["features"] = {}
                    if sold_info.get("features", {}).get("garage") is not None:
                        updated_payload["features"]["garage"] = sold_info["features"]["garage"]

                    if sold_info.get("images"):
                        updated_payload["images"] = sold_info["images"]
                    if sold_info.get("historysale"):
                        updated_payload["historysale"] = sold_info["historysale"]
                    if sold_info.get("expectedPrice"):
                        updated_payload["expectedPrice"] = sold_info["expectedPrice"]
                        updated_payload["lastSoldPrice"] = sold_info["expectedPrice"]
                    if sold_info.get("soldDateInfo"):
                        updated_payload["soldDateInfo"] = sold_info["soldDateInfo"]
                        updated_payload["lastSoldDate"] = sold_info["soldDateInfo"]
                    if sold_info.get("realestate_url"):
                        updated_payload["realestate_url"] = sold_info["realestate_url"]

                    if "provenance" not in updated_payload or not isinstance(updated_payload["provenance"], dict):
                        updated_payload["provenance"] = {}
                    updated_payload["provenance"]["commercialScrape"] = {
                        "source": "Commercial Portal Listing",
                        "hasImages": bool(sold_info.get("images")),
                        "verified": True
                    }

                    updated_payload["status"] = "sold"
                    updated_payload["listingOption"] = "sold"
                    updated_payload["updatedAt"] = now_iso

                    points_to_update.append(models.PointStruct(
                        id=pt.id,
                        payload=updated_payload,
                        vector={}
                    ))

            if next_offset is None:
                break
            offset = next_offset

        if points_to_update and not dry_run:
            # Batch upsert in gentle chunks of 250 with delay to protect server RAM
            for j in range(0, len(points_to_update), 250):
                chunk = points_to_update[j:j + 250]
                client.upsert(collection_name=MASTER_COLLECTION, points=chunk, wait=False)
                time.sleep(0.05)

        total_enriched += len(points_to_update)

        if not dry_run:
            with open(CHECKPOINT_FILE, "a", encoding="utf-8") as f:
                f.write(suburb + "\n")

        if i % 15 == 0 or i == len(suburbs) or len(points_to_update) > 0:
            print(f"[{i}/{len(suburbs)} suburbs] Suburb: {suburb.title():<20} | Enriched: {len(points_to_update):>4} | Total Enriched: {total_enriched:,}")

    dt = time.time() - t0
    print("\n" + "=" * 70)
    print(f"NSW COMMERCIAL ENRICHMENT COMPLETE: Enriched {total_enriched:,} properties in {dt:.1f}s!")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Enrich PropertyMaster with commercial data (bed, bath, images) from PropertySoldV2")
    parser.add_argument("--suburb", default=None, help="Process a single suburb (e.g. 'Valentine')")
    parser.add_argument("--limit-suburbs", type=int, default=None, help="Limit number of suburbs to process")
    parser.add_argument("--dry-run", action="store_true", help="Match records without updating Qdrant")
    args = parser.parse_args()

    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=60)
    sold_map = load_nsw_sold_batch(client)
    if sold_map:
        enrich_nsw_master(client, sold_map, target_suburb=args.suburb, limit_suburbs=args.limit_suburbs, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
