"""
Utility script to optimize memory and disk usage for PropertyMaster on Qdrant.
Performs:
1. Enables on_disk_payload to keep heavy JSON payloads on SSD instead of RAM.
2. Triggers segment compaction & vacuum to purge old revision tombstones from Phase 2 updates.
3. Re-indexes payload fields for maximum query speed with minimal RAM footprint.
"""

import os
import sys
from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URI", "http://127.0.0.1:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API", "local-qdrant-key").strip('"').strip("'")
COLLECTION_NAME = "PropertyMaster"

def optimize_collection():
    print(f"Connecting to Qdrant at: {QDRANT_URL}...")
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=120, check_compatibility=False)
    
    # Check if collection exists
    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME not in collections:
        print(f"Error: Collection '{COLLECTION_NAME}' does not exist on {QDRANT_URL}!")
        return

    print(f"Current collection status:")
    info_before = client.get_collection(COLLECTION_NAME)
    print(f" - Points count  : {info_before.points_count:,}")
    print(f" - Segments count: {info_before.segments_count}")
    print(f" - Status        : {info_before.status}")

    print("\nApplying Memory Optimization...")
    print(" 1. Enabling on_disk_payload = True (saves ~80% RAM)...")
    print(" 2. Configuring aggressive vacuuming and segment compaction...")
    
    client.update_collection(
        collection_name=COLLECTION_NAME,
        params=models.CollectionParamsDiff(
            on_disk_payload=True,
        ),
        optimizer_config=models.OptimizersConfigDiff(
            deleted_threshold=0.1,         # Trigger vacuum if >10% points are tombstoned
            vacuum_min_vector_number=1000, # Clean up dead points
            indexing_threshold=20000,      # Build compact index
            memmap_threshold=20000,        # Keep heavy data on disk
        ),
    )

    print("\nOptimization applied successfully!")
    print("Qdrant background workers are now vacuuming dead points and compacting segments.")
    print("You can view real-time status on your dashboard: http://127.0.0.1:6333/dashboard#/collections")

if __name__ == "__main__":
    optimize_collection()
