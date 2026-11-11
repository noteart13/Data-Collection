import argparse
import json
import os
import re
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

# UTF-8 stdout on Windows
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "replace")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "replace")

load_dotenv()
QDRANT_URL = os.getenv("QDRANT_URI", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API", "").strip('"').strip("'") or None
MASTER_COLLECTION = "PropertyMaster"

def normalize_street(street: str) -> str:
    s = street.lower().strip()
    s = re.sub(r'[,.]', ' ', s)
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

def score_point(pt) -> int:
    """Score point quality: higher is better."""
    pl = pt.payload or {}
    score = 0
    # Keep enriched records above all else
    if pl.get("status") == "sold":
        score += 10000
    if pl.get("images"):
        score += 5000
    if pl.get("bed") is not None:
        score += 2000
    if pl.get("bath") is not None:
        score += 1000
        
    # Official metadata quality
    if pl.get("primary_secondary") != "S":
        score += 50
    try:
        conf = int(pl.get("confidence") or 0)
        score += conf * 10
    except (ValueError, TypeError):
        pass
        
    if pl.get("coordinates") and pl["coordinates"].get("lat"):
        score += 20
    return score

def deduplicate_suburb(client: QdrantClient, state: str, suburb: str, dry_run: bool = False):
    offset = None
    all_points = []
    
    while True:
        try:
            pts, next_offset = client.scroll(
                collection_name=MASTER_COLLECTION,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(key="state", match=models.MatchValue(value=state)),
                        models.FieldCondition(key="suburb", match=models.MatchValue(value=suburb))
                    ]
                ),
                limit=2000,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )
        except Exception as e:
            # Retry once on network glitch
            time.sleep(1)
            pts, next_offset = client.scroll(
                collection_name=MASTER_COLLECTION,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(key="state", match=models.MatchValue(value=state)),
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
        all_points.extend(pts)
        if next_offset is None:
            break
        offset = next_offset

    if not all_points:
        return 0, 0

    # Group by normalized street (only group if street contains a digit/number)
    groups = defaultdict(list)
    for p in all_points:
        st = p.payload.get("street") or ""
        if re.search(r'\d', st):
            norm = normalize_street(st)
            if norm:
                groups[norm].append(p)

    ids_to_delete = []
    for norm_st, pts_group in groups.items():
        if len(pts_group) > 1:
            # Sort descending by score
            pts_group.sort(key=score_point, reverse=True)
            # Winner is index 0; remaining indices are duplicates
            for dup in pts_group[1:]:
                ids_to_delete.append(dup.id)

    if ids_to_delete and not dry_run:
        # Delete duplicate points in chunks of 500
        for i in range(0, len(ids_to_delete), 500):
            chunk = ids_to_delete[i:i + 500]
            client.delete(
                collection_name=MASTER_COLLECTION,
                points_selector=models.PointIdsList(points=chunk),
                wait=False
            )

    return len(all_points), len(ids_to_delete)

def load_suburbs_for_state(state: str):
    suburbs = set()
    mapping_file = "suburb_city_mapping_v1.json"
    if os.path.exists(mapping_file):
        try:
            with open(mapping_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    if item.get("state", "").upper() == state.upper():
                        sub = (item.get("suburb") or "").strip().title()
                        if sub:
                            suburbs.add(sub)
        except Exception as e:
            print(f"Error loading {mapping_file}: {e}")
    return sorted(list(suburbs))

def main():
    parser = argparse.ArgumentParser(description="Deduplicate addresses in PropertyMaster")
    parser.add_argument("--state", default="NSW", help="State to deduplicate (e.g. NSW, ACT)")
    parser.add_argument("--suburb", default=None, help="Specific suburb to deduplicate (optional)")
    parser.add_argument("--dry-run", action="store_true", help="Report duplicates without deleting")
    parser.add_argument("--limit-suburbs", type=int, default=None, help="Limit number of suburbs to process")
    parser.add_argument("--concurrency", type=int, default=6, help="Number of concurrent worker threads")
    args = parser.parse_args()

    state = args.state.upper()
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=60)

    if args.suburb:
        sub = args.suburb.title()
        total, deleted = deduplicate_suburb(client, state, sub, dry_run=args.dry_run)
        action_word = "Identified" if args.dry_run else "Deleted"
        print(f"[{state} - {sub}] Total: {total:,} | {action_word} duplicates: {deleted:,}")
        return

    suburbs = load_suburbs_for_state(state)
    print(f"Loaded {len(suburbs):,} unique suburbs for state {state}.")

    # Load checkpoint
    checkpoint_file = f"dedup_checkpoint_{state}.txt"
    completed_suburbs = set()
    if os.path.exists(checkpoint_file):
        with open(checkpoint_file, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s:
                    completed_suburbs.add(s)
        print(f"Resuming: {len(completed_suburbs):,} suburbs already processed in previous run.")

    remaining_suburbs = [s for s in suburbs if s not in completed_suburbs]
    if args.limit_suburbs:
        remaining_suburbs = remaining_suburbs[:args.limit_suburbs]
    print(f"Starting deduplication on {len(remaining_suburbs):,} remaining suburbs (Concurrency: {args.concurrency}, Dry-run: {args.dry_run})...\n")

    lock = threading.Lock()
    checkpoint_lock = threading.Lock()
    total_scanned = 0
    total_deleted = 0
    suburbs_done = len(completed_suburbs)
    total_suburbs = len(suburbs)
    start_time = time.time()

    def worker(sub):
        nonlocal total_scanned, total_deleted, suburbs_done
        # Worker gets its own client instance for thread safety
        thread_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=60)
        scanned, deleted = deduplicate_suburb(thread_client, state, sub, dry_run=args.dry_run)
        
        with lock:
            total_scanned += scanned
            total_deleted += deleted
            suburbs_done += 1
            idx = suburbs_done

        # Write to checkpoint
        if not args.dry_run:
            with checkpoint_lock:
                with open(checkpoint_file, "a", encoding="utf-8") as f:
                    f.write(sub + "\n")

        if idx % 10 == 0 or idx == total_suburbs or deleted > 0:
            elapsed = time.time() - start_time
            rate = idx / elapsed if elapsed > 0 else 0
            print(
                f"[{idx}/{total_suburbs} suburbs | {rate:.1f} sub/s] "
                f"Current: {sub:<20} | Scanned: {total_scanned:,} | Duplicates removed: {total_deleted:,}",
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
    print(f"DEDUPLICATION FINISHED FOR {state}!")
    print(f"Total Suburbs Processed : {total_suburbs:,}")
    print(f"Total Addresses Scanned : {total_scanned:,}")
    action_str = "Identified" if args.dry_run else "Deleted"
    print(f"Total Duplicates {action_str} : {total_deleted:,}")
    print(f"Time Taken              : {total_time:.1f}s ({total_time / 60:.1f} mins)")
    print("=" * 70)

if __name__ == "__main__":
    main()
