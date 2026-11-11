"""
Real-time status monitor for PropertyMaster enrichment pipeline.
Run anytime in a separate PowerShell window to see live progress:
    python monitor_enrichment.py
"""

import os
import sys
import json
import sqlite3
import time
from pathlib import Path
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URI", "http://127.0.0.1:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API", "local-qdrant-key").strip('"').strip("'")
COLLECTION = "PropertyMaster"
CHECKPOINT_PATH = Path("scratch/enrich_checkpoint_NSW.json")
CACHE_DB_PATH = Path("scratch/portal_cache.db")

def print_separator(title=""):
    print("=" * 65)
    if title:
        print(f"  {title}")
        print("=" * 65)

def check_checkpoint():
    print("\n--- 1. TIẾN TRÌNH THEO CHECKPOINT ---")
    if not CHECKPOINT_PATH.exists():
        print("  - Trạng thái : Đang xử lý Batch đầu tiên (chưa lưu checkpoint)")
        return
    try:
        with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"  - Đã xử lý (Scanned)  : {data.get('total_processed', 0):,} căn")
        print(f"  - Đã làm giàu (Enriched): {data.get('total_enriched', 0):,} căn")
        print(f"  - Lần cập nhật cuối    : {data.get('timestamp', 'N/A')}")
    except Exception as e:
        print(f"  - Lỗi đọc checkpoint: {e}")

def check_cache():
    print("\n--- 2. KHO DỮ LIỆU CÀO WEB (PORTAL CACHE) ---")
    if not CACHE_DB_PATH.exists():
        print("  - File cache SQLite chưa được tạo.")
        return
    try:
        conn = sqlite3.connect(str(CACHE_DB_PATH), timeout=5.0)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM portal_cache")
        total_cache = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM portal_cache WHERE is_not_found = 0")
        found_cache = cur.fetchone()[0]
        print(f"  - Tổng số profile đã tra cứu : {total_cache:,}")
        print(f"  - Số căn tìm thấy thông tin : {found_cache:,}")
        
        # Latest 3 scraped items
        cur.execute("SELECT slug, updated_at FROM portal_cache ORDER BY updated_at DESC LIMIT 3")
        rows = cur.fetchall()
        if rows:
            print("  - 3 căn vừa tra cứu gần nhất:")
            for slug, updated in rows:
                print(f"      • {slug} ({updated})")
        conn.close()
    except Exception as e:
        print(f"  - Đang đọc cache: {e}")

def check_qdrant():
    print("\n--- 3. TRẠNG THÁI DATABASE QDRANT ---")
    try:
        from qdrant_client import QdrantClient, models
        client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=10, check_compatibility=False)
        info = client.get_collection(COLLECTION)
        print(f"  - Collection      : {COLLECTION}")
        print(f"  - Tổng số căn     : {info.points_count:,}")
        print(f"  - Trạng thái DB   : {info.status}")

        # Count enriched points in NSW
        enriched_count = client.count(
            collection_name=COLLECTION,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(key="state", match=models.MatchValue(value="NSW")),
                    models.FieldCondition(key="bed", is_null=False),
                ]
            )
        ).count

        price_count = client.count(
            collection_name=COLLECTION,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(key="state", match=models.MatchValue(value="NSW")),
                    models.FieldCondition(key="expectedPrice", is_null=False),
                ]
            )
        ).count

        print(f"  - Căn đã có số phòng (bed) : {enriched_count:,} căn")
        print(f"  - Căn đã có giá (price)    : {price_count:,} căn")
    except Exception as e:
        print(f"  - Không thể kết nối Qdrant: {e}")

def main():
    print_separator("PROPERTYMASTER - LIVE MONITORING STATUS")
    print(f"Thời gian kiểm tra: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    check_checkpoint()
    check_cache()
    check_qdrant()
    print("\n" + "=" * 65)
    print("Mẹo: Bạn có thể mở dashboard xem trực quan tại:")
    print("     http://127.0.0.1:6333/dashboard#/collections/PropertyMaster")
    print("=" * 65 + "\n")

if __name__ == "__main__":
    main()
