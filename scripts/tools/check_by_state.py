import os
import sys
from concurrent.futures import ThreadPoolExecutor

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "replace")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "replace")

from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

STATES = ["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"]

TARGET_COLLECTIONS = [
    "PropertyMaster",
    "PropertySoldV2",
    "PropertySold",
    "PropertySoldQld",
    "PropertySoldQldV3",
    "PropertyForSale",
    "PropertyForSaleV2",
    "PropertyForRent",
    "PropertyRented",
    "Agent",
    "School",
]

def count_for_state(client, col, state, is_text):
    cond = models.MatchText(text=state) if is_text else models.MatchValue(value=state)
    try:
        cnt = client.count(
            collection_name=col,
            count_filter=models.Filter(must=[models.FieldCondition(key="state", match=cond)]),
            exact=True
        )
        return state, cnt.count
    except Exception:
        return state, 0

def check_collection(client, col):
    try:
        info = client.get_collection(col)
        total_points = getattr(info, "points_count", 0) or 0
        schema = info.payload_schema or {}
        
        is_text = False
        if "state" in schema:
            dtype = str(schema["state"].data_type)
            if "text" in dtype.lower():
                is_text = True
        
        state_counts = {}
        with ThreadPoolExecutor(max_workers=len(STATES)) as executor:
            futures = [executor.submit(count_for_state, client, col, s, is_text) for s in STATES]
            for f in futures:
                s, count = f.result()
                state_counts[s] = count
                
        sum_states = sum(state_counts.values())
        other = max(0, total_points - sum_states)
        return {
            "collection": col,
            "counts": state_counts,
            "other": other,
            "total": total_points
        }
    except Exception as e:
        return {"collection": col, "error": str(e)}

def main():
    load_dotenv()
    url = os.getenv("QDRANT_URI", "http://localhost:6333")
    api_key = os.getenv("QDRANT_API", "").strip('"').strip("'") or None
    
    client = QdrantClient(url=url, api_key=api_key, timeout=60)
    
    # Allow filtering by collection from cli args
    target_cols = TARGET_COLLECTIONS
    if len(sys.argv) > 1:
        specified = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
        if specified:
            target_cols = specified

    print("\n" + "=" * 105)
    print("                      THỐNG KÊ SỐ LƯỢNG DATA CHI TIẾT THEO TỪNG BANG (QDRANT)")
    print("=" * 105)
    
    header = f"{'Collection':<20} | " + " | ".join(f"{s:>7}" for s in STATES) + f" | {'Khác':>7} | {'Tổng Points':>11}"
    sep = "-" * len(header)
    print(header)
    print(sep)
    
    totals = {s: 0 for s in STATES}
    total_other = 0
    grand_total = 0

    for col in target_cols:
        res = check_collection(client, col)
        if "error" in res:
            print(f"{col:<20} | Lỗi: {res['error']}")
            continue
            
        c = res["counts"]
        row_str = f"{col:<20} | " + " | ".join(f"{c.get(s, 0):>7,}" for s in STATES) + f" | {res['other']:>7,} | {res['total']:>11,}"
        print(row_str)
        
        for s in STATES:
            totals[s] += c.get(s, 0)
        total_other += res["other"]
        grand_total += res["total"]

    print(sep)
    summary_row = f"{'TỔNG CỘNG':<20} | " + " | ".join(f"{totals[s]:>7,}" for s in STATES) + f" | {total_other:>7,} | {grand_total:>11,}"
    print(summary_row)
    print("=" * len(header) + "\n")

if __name__ == "__main__":
    main()
