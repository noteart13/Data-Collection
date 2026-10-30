import argparse
import csv
import io
import json
import os
import re
import sys
import threading
import time
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

# UTF-8 stdout on Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

load_dotenv()
QDRANT_URL = os.getenv("QDRANT_URI", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API", "").strip('"').strip("'") or None
MASTER_COLLECTION = "PropertyMaster"
DATABASE_TIMEZONE = ZoneInfo(os.getenv("DATABASE_TIMEZONE", "Australia/Sydney"))


def parse_and_normalize_date(date_str: Optional[str]) -> str:
    """Parse various Australian date formats to standardized ISO YYYY-MM-DD."""
    if not date_str:
        return ""
    s = str(date_str).strip()
    if not s or s.lower() in ("null", "none", "n/a"):
        return ""

    if "T" in s:
        s = s.split("T")[0]
    elif " " in s:
        s = s.split(" ")[0]

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%Y%m%d", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            if 1900 <= dt.year <= 2030:
                return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    m1 = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", s)
    if m1:
        y, m, d = int(m1.group(1)), int(m1.group(2)), int(m1.group(3))
        if 1900 <= y <= 2030 and 1 <= m <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{m:02d}-{d:02d}"

    m2 = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$", s)
    if m2:
        d, m, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        if 1900 <= y <= 2030 and 1 <= m <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{m:02d}-{d:02d}"

    return ""

def normalize_street(street: str) -> str:
    s = street.lower().strip()
    s = re.sub(r'[,.]', ' ', s)
    # Strip unit/apt prefix markers so 'Unit 1/10 Smith St' matches '1/10 Smith St'
    s = re.sub(r'\b(unit|apt|apartment|suite|lot)\b\s*', '', s)
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

def load_sales_data(zip_path: str = "scratch/archive.zip"):
    """Load and index 1.51M NSW sales by suburb in memory."""
    print(f"Loading NSW sales from {zip_path}...")
    t0 = time.time()
    sales_by_suburb = defaultdict(list)
    total_valid = 0

    with zipfile.ZipFile(zip_path, 'r') as z:
        csv_names = [n for n in z.namelist() if n.endswith('.csv')]
        if not csv_names:
            raise FileNotFoundError("No CSV file found inside zip archive!")
        csv_name = csv_names[0]
        print(f"Reading {csv_name} from archive...")

        with z.open(csv_name) as raw_f:
            f = io.TextIOWrapper(raw_f, encoding='utf-8', errors='replace')
            reader = csv.DictReader(f)
            for row in reader:
                loc = (row.get("Property locality") or "").strip().title()
                num = (row.get("Property house number") or "").strip()
                st_name = (row.get("Property street name") or "").strip()
                price_str = (row.get("Purchase price") or "").strip()

                if not (loc and num and st_name and price_str):
                    continue

                try:
                    price = float(price_str)
                    if price <= 1000:
                        continue
                except ValueError:
                    continue

                unit = (row.get("Property unit number") or "").strip()
                full_st = f"{unit}/{num} {st_name}".strip() if unit else f"{num} {st_name}".strip()
                norm_st = normalize_street(full_st)

                area_val = None
                area_str = (row.get("Area (m2)") or "").strip()
                if area_str:
                    try:
                        area_val = float(area_str)
                    except ValueError:
                        pass

                c_date = parse_and_normalize_date(row.get("Contract date"))
                s_date = parse_and_normalize_date(row.get("Settlement date"))

                sales_by_suburb[loc].append({
                    "full_st": full_st,
                    "norm_st": norm_st,
                    "price": price,
                    "contract_date": c_date,
                    "settlement_date": s_date,
                    "area_m2": area_val,
                    "zoning": (row.get("Zoning") or "").strip(),
                    "nature": (row.get("Nature of property (description)") or "").strip(),
                    "prop_id": (row.get("Property ID") or "").strip(),
                    "legal_desc": (row.get("Property legal description") or "").strip(),
                })
                total_valid += 1

    dt = time.time() - t0
    print(f"Loaded {total_valid:,} valid sales transactions across {len(sales_by_suburb):,} suburbs in {dt:.1f}s.")
    return sales_by_suburb

def merge_suburb_sales(client: QdrantClient, suburb: str, sales: list, now_iso: str, dry_run: bool = False):
    """Safely merge sales into matching PropertyMaster points for a given suburb."""
    # Step 1: Scroll all points for this suburb from PropertyMaster
    offset = None
    master_points = []
    while True:
        try:
            pts, next_offset = client.scroll(
                collection_name=MASTER_COLLECTION,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(key="state", match=models.MatchValue(value="NSW")),
                        models.FieldCondition(key="suburb", match=models.MatchValue(value=suburb))
                    ]
                ),
                limit=2000,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )
        except Exception:
            time.sleep(1)
            pts, next_offset = client.scroll(
                collection_name=MASTER_COLLECTION,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(key="state", match=models.MatchValue(value="NSW")),
                        models.FieldCondition(key="suburb", match=models.MatchValue(value=suburb))
                    ]
                ),
                limit=2000,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )

        if not pts:
            break
        master_points.extend(pts)
        if next_offset is None:
            break
        offset = next_offset

    if not master_points:
        return 0, 0

    # Index master points by normalized street
    master_by_norm = {}
    for pt in master_points:
        st = pt.payload.get("street") or ""
        norm = normalize_street(st)
        if norm:
            master_by_norm[norm] = pt

    points_to_update = {}
    matched_sales_by_pid = defaultdict(list)
    pt_by_pid = {}

    for s in sales:
        norm = s["norm_st"]
        if norm in master_by_norm:
            pt = master_by_norm[norm]
            pid = pt.id
            matched_sales_by_pid[pid].append(s)
            pt_by_pid[pid] = pt

    matched_count = len(matched_sales_by_pid)

    for pid, s_list in matched_sales_by_pid.items():
        pt = pt_by_pid[pid]
        payload = dict(pt.payload)

        # 1. Update officialSaleHistory
        if "officialSaleHistory" not in payload or not isinstance(payload["officialSaleHistory"], list):
            payload["officialSaleHistory"] = []

        # Standardize existing entries' dates
        for h in payload["officialSaleHistory"]:
            if h.get("contractDate"):
                h["contractDate"] = parse_and_normalize_date(h["contractDate"])
            if h.get("settlementDate"):
                h["settlementDate"] = parse_and_normalize_date(h["settlementDate"])

        existing_sale_keys = {
            (h.get("contractDate"), h.get("price")) for h in payload["officialSaleHistory"]
        }

        for s in s_list:
            c_date = s["contract_date"]
            s_date = s["settlement_date"]
            p_val = s["price"]
            if (c_date, p_val) not in existing_sale_keys:
                payload["officialSaleHistory"].append({
                    "price": p_val,
                    "contractDate": c_date,
                    "settlementDate": s_date,
                    "type": "official_transfer",
                    "source": "NSW Valuer General PSI"
                })
                existing_sale_keys.add((c_date, p_val))

            # Merge attributes from sales if available
            if s["area_m2"] and s["area_m2"] > 0:
                current_area = payload.get("area") or {}
                if not current_area.get("totalArea"):
                    payload["area"] = {"totalArea": s["area_m2"], "unit": "sqM"}

            if s["zoning"] and not payload.get("zoning"):
                payload["zoning"] = s["zoning"]
            if s["nature"]:
                nature_val = s["nature"]
                if nature_val in ["Vacant land", "V"] and (payload.get("bed") or payload.get("propertyType") in ["House", "Townhouse", "Apartment", "Unit"]):
                    nature_val = "Residence / Dwelling"
                if not payload.get("natureOfProperty"):
                    payload["natureOfProperty"] = nature_val
            if s["prop_id"] and not payload.get("governmentPropertyId"):
                payload["governmentPropertyId"] = s["prop_id"]
            if s["legal_desc"] and not payload.get("lotPlan"):
                payload["lotPlan"] = s["legal_desc"]

        # Sort officialSaleHistory descending by parsed contractDate (or settlementDate)
        def sale_sort_key(item):
            c = item.get("contractDate") or ""
            st = item.get("settlementDate") or ""
            return c if c else st

        payload["officialSaleHistory"].sort(key=sale_sort_key, reverse=True)

        if payload["officialSaleHistory"]:
            latest_sale = payload["officialSaleHistory"][0]
            latest_date = latest_sale.get("contractDate") or latest_sale.get("settlementDate") or ""
            latest_price = float(latest_sale["price"])

            payload["soldDateInfo"] = latest_date

            if payload.get("status") == "off_market":
                payload["status"] = "sold"
                payload["listingOption"] = "sold"

            # Only initialize expectedPrice if none exists AND the sale is recent (>= 2025)
            # Otherwise, allow AVM in bulk_enrich_master to calculate indexed 2026 valuation
            sale_year = 0
            if len(latest_date) >= 4 and latest_date[:4].isdigit():
                sale_year = int(latest_date[:4])
            if not payload.get("expectedPrice") and sale_year >= 2025:
                payload["expectedPrice"] = latest_price

        # Ensure exact original schema (clean temporary keys, images empty)
        payload.pop("provenance", None)
        payload.pop("lastSoldPrice", None)
        payload.pop("lastSoldDate", None)
        if "images" not in payload or payload["images"] is None:
            payload["images"] = []

        # Update Data Quality Provenance
        dq = payload.get("data_quality") or {}
        dq["data_source"] = "NSW_VALUER_GENERAL_OFFICIAL"
        dq["has_official_sales"] = True
        dq["data_quality_score"] = min(100.0, float(dq.get("data_quality_score", 70.0)) + 15.0)
        dq["last_quality_audit"] = now_iso
        payload["data_quality"] = dq

        payload["updatedAt"] = now_iso

        # NEVER touch images, bed, bath, or features!
        points_to_update[pid] = models.PointStruct(
            id=pid,
            payload=payload,
            vector={}
        )

    if points_to_update and not dry_run:
        # Upsert in gentle chunks of 250 with delay to protect server RAM
        pts_list = list(points_to_update.values())
        for i in range(0, len(pts_list), 250):
            chunk = pts_list[i:i + 250]
            client.upsert(
                collection_name=MASTER_COLLECTION,
                points=chunk,
                wait=False
            )
            time.sleep(0.05)  # Gentle buffer to prevent server memory pressure

    return len(sales), len(points_to_update)

def main():
    parser = argparse.ArgumentParser(description="Merge 1.51M NSW sales records into PropertyMaster")
    parser.add_argument("--suburb", default=None, help="Process a specific suburb (optional)")
    parser.add_argument("--concurrency", type=int, default=2, help="Number of worker threads (default 2 for stability)")
    parser.add_argument("--dry-run", action="store_true", help="Match records without writing to Qdrant")
    parser.add_argument("--limit-suburbs", type=int, default=None, help="Limit number of suburbs to process")
    parser.add_argument("--zip-path", default=os.getenv("NSW_SALES_ZIP", "scratch/archive.zip"), help="Path to NSW sales zip archive")
    args = parser.parse_args()

    # Load sales data
    sales_by_suburb = load_sales_data(zip_path=args.zip_path)
    now_iso = datetime.now(DATABASE_TIMEZONE).replace(microsecond=0).isoformat()
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=60)

    if args.suburb:
        sub = args.suburb.title()
        if sub not in sales_by_suburb:
            print(f"No sales found in dataset for suburb '{sub}'.")
            return
        sales = sales_by_suburb[sub]
        total_sales, updated = merge_suburb_sales(client, sub, sales, now_iso, dry_run=args.dry_run)
        action_word = "Identified for update" if args.dry_run else "Enriched & Updated"
        print(f"[{sub}] Sales: {total_sales:,} | {action_word}: {updated:,} ({updated/total_sales*100:.1f}%)")
        return

    # Checkpoint setup
    checkpoint_file = "merge_sales_checkpoint_NSW.txt"
    completed_suburbs = set()
    if os.path.exists(checkpoint_file):
        with open(checkpoint_file, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s:
                    completed_suburbs.add(s)
        print(f"Resuming: {len(completed_suburbs):,} suburbs already processed in previous run.")

    all_suburbs = sorted(list(sales_by_suburb.keys()))
    remaining_suburbs = [s for s in all_suburbs if s not in completed_suburbs]

    if args.limit_suburbs:
        remaining_suburbs = remaining_suburbs[:args.limit_suburbs]

    print(f"Starting merge on {len(remaining_suburbs):,} remaining suburbs (Concurrency: {args.concurrency}, Dry-run: {args.dry_run})...\n")

    lock = threading.Lock()
    checkpoint_lock = threading.Lock()
    total_sales_processed = 0
    total_properties_enriched = 0
    done_count = len(completed_suburbs)
    total_target = len(all_suburbs)
    start_time = time.time()

    def worker(sub):
        nonlocal total_sales_processed, total_properties_enriched, done_count
        thread_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=60)
        sales = sales_by_suburb[sub]
        s_count, u_count = merge_suburb_sales(thread_client, sub, sales, now_iso, dry_run=args.dry_run)

        with lock:
            total_sales_processed += s_count
            total_properties_enriched += u_count
            done_count += 1
            idx = done_count

        if not args.dry_run:
            with checkpoint_lock:
                with open(checkpoint_file, "a", encoding="utf-8") as f:
                    f.write(sub + "\n")

        if idx % 10 == 0 or idx == total_target or u_count > 0:
            elapsed = time.time() - start_time
            rate = idx / elapsed if elapsed > 0 else 0
            print(
                f"[{idx}/{total_target} suburbs | {rate:.1f} sub/s] "
                f"Current: {sub:<20} | Sales: {s_count:>4} | Enriched: {u_count:>4} | Total Enriched: {total_properties_enriched:,}",
                flush=True
            )

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(worker, sub) for sub in remaining_suburbs]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                print(f"Error in worker thread: {e}", file=sys.stderr)

    total_time = time.time() - start_time
    print("\n" + "=" * 70)
    print(f"MERGE SALES COMPLETED!")
    print(f"Total Suburbs Processed : {total_target:,}")
    print(f"Total Sales Processed   : {total_sales_processed:,}")
    print(f"Total Master Properties Enriched: {total_properties_enriched:,}")
    print(f"Time Taken              : {total_time:.1f}s ({total_time / 60:.1f} mins)")
    print("=" * 70)

if __name__ == "__main__":
    main()
