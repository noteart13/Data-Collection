#!/usr/bin/env python3
import asyncio
import csv
import os
from pathlib import Path
from datetime import datetime

async def main():
    PROJECT_ROOT = Path(__file__).parent
    data_dir     = PROJECT_ROOT / "data"
    csv_path     = data_dir / "fail_address.csv"

    # --- 1) If we have error rows, send them to Mongo ---
    if csv_path.exists():
        db  = MongoDatabase()
        col = db.get_collection("ErrorLog_Sale")

        docs = []
        with csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                docs.append({
                    "url":      row.get("url"),
                    "error":    row.get("error"),
                    "loggedAt": datetime.now().replace(microsecond=0)
                })

        if docs:
            result = await col.insert_many(docs)
            print(f"Inserted {len(result.inserted_ids)} error log entries.")
        else:
            print("No rows found in CSV; nothing inserted.")

    else:
        print(f"No error log found at {csv_path}; skipping send step.")

if __name__ == "__main__":
    asyncio.run(main())