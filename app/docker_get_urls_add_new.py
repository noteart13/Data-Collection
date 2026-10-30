import json
import asyncio
import os
from pathlib import Path
from qdrant_client import AsyncQdrantClient as QdrantClient

import os
from dotenv import load_dotenv

load_dotenv()
client = QdrantClient(url=os.getenv("QDRANT_URI", "http://localhost:6333"), api_key=os.getenv("QDRANT_API") or None)

PROJECT_ROOT   = Path(__file__).parent
async def find_urls_in_sale():

    url_file_path = PROJECT_ROOT / "data" / "urls_sale_add_new.json"
    collection_name = "PropertyForSale"
    url_list = []
    offset = None

    while True:
        points, next_offset = await client.scroll(
            collection_name=collection_name,
            limit=100,
            offset=offset,
            with_payload=["url"],
            with_vectors=False
        )
        
        offset = next_offset
        if not points:
            break

        for point in points:
            url = point.payload.get("url")
            if url:
                url_list.append(url)

        if offset is None:
            break

    print(f'There are {len(url_list)} urls in sale collection')

    output_dir = os.path.dirname(url_file_path)
    os.makedirs(output_dir, exist_ok=True)

    with open(url_file_path, 'w', encoding='utf-8') as file:
        json.dump(url_list, file, indent=2)

if __name__ == '__main__':
    asyncio.run(find_urls_in_sale())
