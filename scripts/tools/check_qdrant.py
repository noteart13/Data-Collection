import os
import sys

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "replace")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "replace")

from dotenv import load_dotenv
from qdrant_client import QdrantClient

def check_qdrant():
    load_dotenv()
    url = os.getenv("QDRANT_URI", "http://localhost:6333")
    
    # Priority order for API keys
    candidate_keys = [
        os.getenv("QDRANT_API", "").strip('"').strip("'") or None,
        None
    ]
    candidate_keys = [k for k in candidate_keys if k is not None] + [None]

    client = None
    for key in candidate_keys:
        try:
            test_client = QdrantClient(url=url, api_key=key, timeout=10)
            test_client.get_collections()
            client = test_client
            break
        except Exception:
            continue

    if not client:
        print("\n[ERROR] Không thể kết nối tới Qdrant.")
        return

    try:
        collections_resp = client.get_collections()
        collections = collections_resp.collections

        header = f"{'STT':<4} | {'Tên Bảng (Collection)':<30} | {'Số lượng Points (Records)':<28} | {'Status'}"
        sep = "-" * len(header)
        
        print("\n" + "=" * len(header))
        print("          THỐNG KÊ DỮ LIỆU CÁC BẢNG TRÊN QDRANT")
        print("=" * len(header))
        print(header)
        print(sep)

        total_points = 0
        for idx, col in enumerate(sorted(collections, key=lambda x: x.name), 1):
            try:
                info = client.get_collection(col.name)
                pts = getattr(info, 'points_count', 0) or 0
                status = getattr(info.status, 'value', str(info.status))
                total_points += pts
                print(f"{idx:<4} | {col.name:<30} | {pts:<28,} | {status}")
            except Exception as e:
                print(f"{idx:<4} | {col.name:<30} | Lỗi: {e}")

        print(sep)
        print(f"TỔNG CỘNG: {len(collections)} collections | Tổng số records/points: {total_points:,}")
        print("=" * len(header) + "\n")
    except Exception as e:
        print(f"Lỗi khi lấy thông tin collections: {e}")

if __name__ == "__main__":
    check_qdrant()
