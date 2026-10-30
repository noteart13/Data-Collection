import asyncio
import json
from time import time, sleep
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from operations.property_address import Get_pro_meta, Mapping, getPriceInfo, AgentScrapping
from operations.crawl4ai import(
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    LLMExtractionStrategy,
    JsonCssExtractionStrategy,
    CrawlResult,
    DefaultMarkdownGenerator,
    PruningContentFilter,
    CacheMode,
    LLMConfig
)
from bson import ObjectId
import cloudinary
import argparse
from bs4 import BeautifulSoup
from app import crud, schemas
from app.db.session import MongoDatabase
from datetime import datetime
from pymongo.database import Database
import hashlib
import pandas as pd
from typing import List
from pymongo.errors import ExecutionTimeout, ServerSelectionTimeoutError, AutoReconnect
from motor.core import AgnosticDatabase  # Motor type hint
import os
PROJECT_ROOT   = Path(__file__).parent
db = MongoDatabase()
MAX_CONCURRENCY = 1
url_json = PROJECT_ROOT / "data" / "urls_agent.json"
progress_log = PROJECT_ROOT / "data" / "log_progress.txt"
invalid_url_file = PROJECT_ROOT/"data"/"invalid_URLs_agent.txt"
EXECUTOR = ThreadPoolExecutor(max_workers=MAX_CONCURRENCY)
async def find_urls_in_agent(): 
    # Prepare output path
    url_file_path =  PROJECT_ROOT / "data" / "urls_agent.json"
    collection = db['Agent']

    url_list = []
    # only project "url"; _id is returned by default if you don't explicitly exclude it
    async for doc in collection.find({}, {"profileUrl": 1}):
        url = doc.get("profileUrl")
        if url:
            url_list.append(url)
        # else: silently skip any doc with no "url" key

    print(f'There are {len(url_list)} urls in sale table')

    
    output_dir = os.path.dirname(url_file_path)

    # Ensure the folder exists
    os.makedirs(output_dir, exist_ok=True)

    # Save urls to a JSON file
    with open(url_file_path, 'w',encoding='utf-8') as file:
        json.dump(url_list, file, indent=2)

async def main():
    await find_urls_in_agent()

if __name__ == '__main__':
    asyncio.run(main())