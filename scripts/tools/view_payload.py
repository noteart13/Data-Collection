#!/usr/bin/env python3
"""
Utility script to inspect and view payloads of properties in Qdrant (PropertyMaster).
Allows filtering by status ('sold', 'sale', 'rent', 'off_market') and location.
"""

import argparse
import json
import os
import sys
import warnings
from pathlib import Path
from dotenv import load_dotenv

# Suppress harmless Qdrant insecure connection warning over HTTP
warnings.filterwarnings("ignore", category=UserWarning, module="qdrant_client")

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

# Load environment
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

try:
    from qdrant_client import QdrantClient, models
except ImportError:
    print("[ERROR] qdrant_client is not installed. Please activate your .venv first.")
    sys.exit(1)


def view_payloads(
    status: str = "sold",
    state: str = None,
    suburb: str = None,
    collection: str = "PropertyMaster",
    limit: int = 1,
    url: str = None,
    api_key: str = None,
    raw_json: bool = False
):
    qdrant_url = url or os.getenv("QDRANT_URI", "http://localhost:6333")
    qdrant_key = api_key or os.getenv("QDRANT_API", "").strip('"').strip("'") or None

    # Don't send api-key to localhost
    effective_key = None if ("localhost" in qdrant_url or "127.0.0.1" in qdrant_url) else qdrant_key

    client = QdrantClient(url=qdrant_url, api_key=effective_key, timeout=30, check_compatibility=False)

    must_conditions = []

    if status.lower() != "all":
        st_val = status.lower()
        if st_val == "sale":
            match_variants = ["sale", "for_sale", "For Sale"]
        elif st_val == "rent":
            match_variants = ["rent", "for_rent", "For Rent"]
        elif st_val == "sold":
            match_variants = ["sold", "Sold"]
        else:
            match_variants = [st_val]

        must_conditions.append(
            models.FieldCondition(key="status", match=models.MatchAny(any=match_variants))
        )

    if state:
        must_conditions.append(
            models.FieldCondition(key="state", match=models.MatchValue(value=state.upper()))
        )

    if suburb:
        must_conditions.append(
            models.FieldCondition(key="suburb", match=models.MatchValue(value=suburb.title()))
        )

    scroll_filter = models.Filter(must=must_conditions) if must_conditions else None

    print("\n" + "=" * 70)
    print(" QDRANT PAYLOAD INSPECTOR")
    print("=" * 70)
    print(f"Target URL       : {qdrant_url}")
    print(f"Collection       : {collection}")
    print(f"Filter Status    : {status.upper()}")
    if state:
        print(f"Filter State     : {state.upper()}")
    if suburb:
        print(f"Filter Suburb    : {suburb.title()}")
    print(f"Limit Records    : {limit}")
    print("-" * 70)

    try:
        points, next_offset = client.scroll(
            collection_name=collection,
            scroll_filter=scroll_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False
        )
    except Exception as e:
        print(f"[ERROR] Failed to query Qdrant: {e}")
        return

    if not points:
        print(f"[NOTE] Không tìm thấy point nào khớp với bộ lọc (status='{status}', state='{state}', suburb='{suburb}').")
        if collection == "PropertyMaster" and status.lower() in ["sale", "rent"]:
            print("\n💡 GIẢI THÍCH:")
            print("  Trong 'PropertyMaster' (16M địa chỉ G-NAF toàn quốc), các bất động sản hiện được phân loại:")
            print("  - 'sold': Các căn có lịch sử giao dịch bán gần đây (2025-2026).")
            print("  - 'off_market': Các căn nhà dân cư bình thường không niêm yết trên thị trường.")
            print("\n  Các tin rao bán (SALE) và cho thuê (RENT) hiện tại nằm ở các Collections riêng trên Qdrant:")
            print("  👉 Xem tin đang bán   : python cli.py view-payload --collection PropertyForSale --status all --limit 1")
            print("  👉 Xem tin cho thuê   : python cli.py view-payload --collection PropertyForRent --status all --limit 1")
            print("  👉 Xem nhà off-market : python cli.py view-payload --status off_market --limit 1")
        else:
            print("Mẹo: Thử đổi --status all hoặc kiểm tra lại tên suburb/state.")
        print("=" * 70 + "\n")
        return

    print(f"Tìm thấy {len(points)} point(s) kết quả:\n")

    for idx, pt in enumerate(points, 1):
        pl = pt.payload or {}
        print("=" * 70)
        print(f" RECORD #{idx} | POINT ID: {pt.id}")
        print("=" * 70)

        if not raw_json:
            # Highlight summary key info first
            addr = pl.get("title") or f"{pl.get('street', '')}, {pl.get('suburb', '')} {pl.get('state', '')} {pl.get('postcode', '')}"
            print(f"📍 Address        : {addr}")
            print(f"🏷️ Status / Option: status={pl.get('status')} | listingOption={pl.get('listingOption')}")
            print(f"🏠 Property Type  : {pl.get('propertyType')} ({pl.get('natureOfProperty')})")
            print(f"🛏️ Rooms          : {pl.get('bed')} Bed | {pl.get('bath')} Bath | {pl.get('features', {}).get('garage')} Car")
            print(f"📐 Area           : {pl.get('area', {}).get('totalArea')} {pl.get('area', {}).get('unit', 'sqM')}")
            print(f"📅 Year Built     : {pl.get('constructionYear')}")
            
            # Pricing breakdown
            exp_p = pl.get('expectedPrice')
            print(f"💰 Expected Price : ${exp_p:,} AUD" if exp_p else "💰 Expected Price : N/A")
            
            pricing = pl.get("pricing") or {}
            w_rent = pricing.get("weeklyRent")
            if w_rent:
                print(f"💵 Est. Rent      : ${w_rent:,}/week (Yield: {pricing.get('estimatedYield')})")

            # Sold information
            hist_sales = pl.get("historysale") or []
            off_sales = pl.get("officialSaleHistory") or []
            sold_info = pl.get("soldDateInfo")
            if sold_info:
                print(f"📜 Sold Date Info : {sold_info}")
            if off_sales:
                print(f"🏛️ Official Sales : {len(off_sales)} record(s) (Valuer General/ACT Gov)")
                for os_item in off_sales[:2]:
                    print(f"   - ${os_item.get('price'):,} on {os_item.get('contractDate') or os_item.get('settlementDate')}")
            if hist_sales:
                print(f"📊 History Sales  : {len(hist_sales)} record(s)")
                for hs_item in hist_sales[:2]:
                    print(f"   - ${hs_item.get('soldPrice') or hs_item.get('price'):,} on {hs_item.get('soldDate') or hs_item.get('date')}")

            # Location intelligence
            loc_scores = pl.get("location_scores") or {}
            if loc_scores:
                print(f"🧭 Location Score : {loc_scores.get('overall_score')}/10.0 (CBD: {loc_scores.get('distance_to_cbd_km')} km)")

            # Planning & Strata
            planning = pl.get("planning") or {}
            if planning:
                print(f"🏛️ Planning / LEP : Zone {pl.get('zoning', planning.get('zoningCode'))} ({planning.get('zoningDescription', '')}) | HOB: {planning.get('maxBuildingHeight')} | FSR: {planning.get('floorSpaceRatio')}")
            strata = pl.get("strataDetails") or {}
            if strata:
                print(f"🏢 Strata Scheme  : Plan {strata.get('strataPlan')} | Lot {strata.get('lotNumber')} | Entitlement: {strata.get('unitEntitlementArea')} m²")

            # Data quality
            dq = pl.get("data_quality") or {}
            if dq:
                print(f"🛡️ Data Quality   : Score {dq.get('data_quality_score')}/100 | Source: {dq.get('data_source')} | Synthetic: {dq.get('is_synthetic')}")

            print("\n--- CHI TIẾT TOÀN BỘ JSON PAYLOAD ---")
        
        print(json.dumps(pl, indent=2, ensure_ascii=False))
        print("\n")


def main():
    parser = argparse.ArgumentParser(description="View property payloads (sold, sale, rent) on Qdrant")
    parser.add_argument("--status", default="sold", choices=["sold", "sale", "rent", "off_market", "all"], help="Listing status (default: sold)")
    parser.add_argument("--state", default=None, help="Filter by State (NSW, VIC, QLD, ACT, WA, etc.)")
    parser.add_argument("--suburb", default=None, help="Filter by Suburb name")
    parser.add_argument("--collection", default="PropertyMaster", help="Target collection (default: PropertyMaster)")
    parser.add_argument("--limit", type=int, default=1, help="Number of points to retrieve (default: 1)")
    parser.add_argument("--url", default=None, help="Qdrant server URL (override .env)")
    parser.add_argument("--api-key", default=None, help="Qdrant API Key (override .env)")
    parser.add_argument("--raw-json", action="store_true", help="Print only raw JSON without summary headers")

    args = parser.parse_args()

    view_payloads(
        status=args.status,
        state=args.state,
        suburb=args.suburb,
        collection=args.collection,
        limit=args.limit,
        url=args.url,
        api_key=args.api_key,
        raw_json=args.raw_json
    )


if __name__ == "__main__":
    main()
