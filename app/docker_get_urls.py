
import json
import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
from qdrant_client import AsyncQdrantClient as QdrantClient
import argparse

load_dotenv()
PROJECT_ROOT = Path(__file__).parent
QDRANT_URL = os.getenv("QDRANT_URI", "http://localhost:6333")
QDRANT_KEY = (os.getenv("QDRANT_SCRAPE_API") or os.getenv("QDRANT_API", "")).strip('"').strip("'") or None
client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_KEY)

def parse_args():
    p = argparse.ArgumentParser(description="Step 2: process sale/sold property pages")
    p.add_argument("-s", "--status", type=str, default="sale",
                   help="Scrape or update")
    return p.parse_args()
async def find_urls_in_sale():
    agrs = parse_args()
    status = agrs.status
    collection_name = "PropertyForSale" if status == 'sale' else "PropertyForRent"

    url_list = []
    offset = None

    while True:
        points, next_offset = await client.scroll(
            collection_name=collection_name,
            limit=100,
            offset=offset,
            with_payload=['url'],
            with_vectors=False
        )

        if not points:
            break

        for point in points:
            url = point.payload.get("url")
            if url:
                url_list.append(url)

        offset = next_offset

        if offset is None:
            break

    print(f'There are {len(url_list)} urls in sale collection ')

    # Save file
    url_file_path = PROJECT_ROOT / "data" / "urls_sale.json"
    os.makedirs(url_file_path.parent, exist_ok=True)

    with open(url_file_path, 'w') as file:
        json.dump(url_list, file, indent=2)

if __name__ == '__main__':
    asyncio.run(find_urls_in_sale())
