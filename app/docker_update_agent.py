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
import os
PROJECT_ROOT   = Path(__file__).parent
db = MongoDatabase()
agentcoll = db.get_collection('Agent')
prosalecoll = db.get_collection('PropertyForSale')
prosoldcoll = db.get_collection('PropertySold')
prorentcoll = db.get_collection('PropertyForRent')
prorentedcoll = db.get_collection('PropertyRented')
MAX_CONCURRENCY = 2
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
async def sync_crawl_prep(url: str):
    raw    = url.rstrip('/').split('/')[-1]
    main   = raw[: raw.rfind('-')]
    suffix = hashlib.sha512(raw.split('-')[-1].encode()).hexdigest()[:8]
    slug   = f"{main}-{suffix}"
    print(f'slug: {slug}')
    
    crawler_config = CrawlerRunConfig(
            cache_mode=CacheMode.DISABLED,
            simulate_user=True,
            page_timeout=30000,
        )
    browser_config = BrowserConfig(
            use_persistent_context=False,  
            user_data_dir=None,  
            extra_args=[
                "--disable-cache",  
                "--disable-application-cache",  
                "--disable-offline-load-stale-cache",  
                "--disk-cache-size=0",  
                "--media-cache-size=0",  
                "--disable-extensions",  
                "--disable-plugins", 
                "--disable-dev-shm-usage", 
                "--memory-pressure-off",  
                "--max_old_space_size=512",  
                "--incognito",  
                "--no-first-run",  
                "--no-default-browser-check",  
                "--disable-default-apps",  
                "--disable-sync", 
                "--disable-translate",  
                "--disable-features=TranslateUI", 
                "--disable-ipc-flooding-protection",
                "--enable-http2",  # Explicitly enable HTTP/2 to avoid protocol errors
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",  # Mimic a real browser
            ],
            accept_downloads=False,
            downloads_path=None,
            storage_state=None,  
            cookies=[], 
            headless=True, 
            light_mode=True,  
        )
    MAX_RETRIES  = 3
    BACKOFF_SECS = [0.5, 1.5, 3.0]
    result = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            loop = asyncio.get_running_loop()
            crawler = AsyncWebCrawler(config=browser_config)
            result = await crawler.arun(url=url,config=crawler_config)
            if result and getattr(result, "success", False) and getattr(result, "html", None):
                break
            else:

                print(f"[WARN] Attempt {attempt}/{MAX_RETRIES} returned unusable result for {url}")
                return ('fail',None,None)
                    
        except Exception as e:
            print(f"[ERROR] Attempt {attempt}/{MAX_RETRIES} failed for {url}: {e}")
            

        if attempt < MAX_RETRIES:
            sleep(BACKOFF_SECS[attempt - 1])  # <-- sync sleep
        if not result or not getattr(result, "success", False) or not getattr(result, "html", None):
            print(f"[WARN] No usable result for {url}; skipping.")
            try:
                with open(invalid_url_file, 'a') as f:
                    f.write(f'{url}\n')
            except Exception as e:
                print(e)
            return ("fail", slug, None)
        
    html = result.html
    try:
        scrapping = AgentScrapping(html=html)
        mapping = scrapping.extract_all_data()
        return ("success", slug, mapping)
            
    except Exception as e:
        print(e)
        return ("fail", slug, None)
async def update_agent(url, prometa,status):
    doc = await agentcoll.find_one({'profileUrl': url})
    if not doc:
        print(f"Agent not found for {url}")
        return
    if status == 'success':
        # Map scraped keys to DB keys
        prometa_mapped = prometa.copy()
        if 'starRating' in prometa_mapped:
            prometa_mapped['rating'] = prometa_mapped.pop('starRating')
        if 'aboutAgent' in prometa_mapped:
            about = prometa_mapped['aboutAgent']
            prometa_mapped['aboutAgent'] = {
                'bio': about.get('profile'),
                'videoUrl': about.get('agentVideoUrl'),
                'personalWebsite': about.get('personalWebsiteUrl')
            }
        
        def get_updates(current, new):
            updates = {}
            for key, value in new.items():
                if key not in current:
                    updates[key] = value
                elif isinstance(value, dict) and isinstance(current[key], dict):
                    sub_updates = get_updates(current[key], value)
                    if sub_updates:
                        updates[key] = sub_updates
                elif current[key] != value:
                    updates[key] = value
            return updates
        
        updates = get_updates(doc, prometa_mapped)
        
        # Skip updating slug and profileUrl
        if 'slug' in updates:
            del updates['slug']
        if 'profileUrl' in updates:
            del updates['profileUrl']
        
        # Get property IDs
        prosaleids, prosoldids, prorentids, prorentedids = await get_proid(doc['_id'])
        
        # Check and add property IDs if changed
        current_sale_ids = set(doc.get('propertysaleids', []))
        new_sale_ids = set(p['_id'] for p in prosaleids)
        if current_sale_ids != new_sale_ids:
            updates['propertysaleids'] = list(new_sale_ids)
        
        current_sold_ids = set(doc.get('propertysoldids', []))
        new_sold_ids = set(p['_id'] for p in prosoldids)
        if current_sold_ids != new_sold_ids:
            updates['propertysoldids'] = list(new_sold_ids)
        
        current_rent_ids = set(doc.get('propertyrentids', []))
        new_rent_ids = set(p['_id'] for p in prorentids)
        if current_rent_ids != new_rent_ids:
            updates['propertyrentids'] = list(new_rent_ids)
        
        current_rented_ids = set(doc.get('propertyrentedids', []))
        new_rented_ids = set(p['_id'] for p in prorentedids)
        if current_rented_ids != new_rented_ids:
            updates['propertyrentedids'] = list(new_rented_ids)
        
        if updates:
            await agentcoll.update_one({'profileUrl': url}, {'$set': updates})
            print(f"Updated {url} with changes: {updates}")
        else:
            print(f"No changes for {url}")
    else:
        # Only update property IDs if status is not success, and only if changed
        prosaleids, prosoldids, prorentids, prorentedids = await get_proid(doc['_id'])
        
        updates = {}
        
        current_sale_ids = set(doc.get('propertysaleids', []))
        new_sale_ids = set(p['_id'] for p in prosaleids)
        if current_sale_ids != new_sale_ids:
            updates['propertysaleids'] = list(new_sale_ids)
        
        current_sold_ids = set(doc.get('propertysoldids', []))
        new_sold_ids = set(p['_id'] for p in prosoldids)
        if current_sold_ids != new_sold_ids:
            updates['propertysoldids'] = list(new_sold_ids)
        
        current_rent_ids = set(doc.get('propertyrentids', []))
        new_rent_ids = set(p['_id'] for p in prorentids)
        if current_rent_ids != new_rent_ids:
            updates['propertyrentids'] = list(new_rent_ids)
        
        current_rented_ids = set(doc.get('propertyrentedids', []))
        new_rented_ids = set(p['_id'] for p in prorentedids)
        if current_rented_ids != new_rented_ids:
            updates['propertyrentedids'] = list(new_rented_ids)
        
        if updates:
            await agentcoll.update_one({'profileUrl': url}, {'$set': updates})
            print(f"Updated property IDs for {url} with changes: {updates}")
        else:
            print(f"No property ID changes for {url}")
        
async def get_proid(id):
    async def get_id(coll,proIdx):
        pipeline = [
          {
            "$search": {
              "index": proIdx,
                "in": {
                    "path": "agentid",
                    "value": id
                }
            }
          },
          {
            "$project": {
                '_id':1
            }
          }
        ]
        
        result = await coll.aggregate(pipeline).to_list()
        return result
    prosale_task = asyncio.create_task(get_id(prosalecoll, "ProSaleAutoComplete"))
    prosold_task = asyncio.create_task(get_id(prosoldcoll, "ProSoldAutoComplete"))
    prorent_task = asyncio.create_task(get_id(prorentcoll, "ProRentAutoComplete"))
    prorented_task = asyncio.create_task(get_id(prorentedcoll, "ProRentedAutoComplete"))

    prosaleids, prosoldids, prorentids, prorentedids = await asyncio.gather(
        prosale_task, prosold_task, prorent_task, prorented_task
    )
    return (prosaleids, prosoldids, prorentids, prorentedids)
async def process_url(sem: asyncio.Semaphore, url: str, idx: int, total: int):
    async with sem:
        label = f"[{idx}/{total}]"
        start = time()
        print(f"START {label} {url}")
        with open(progress_log, "a",encoding='utf-8') as f:
            f.write(f"START {label} {url}\n")
            try:
                status, slug, metadata = await sync_crawl_prep(url)
                if status == 'success' and metadata:
                    await update_agent(url,metadata,status)
                    f.write(f"[COMPLETED] {url}")
                elif status == 'fail':
                    await update_agent(url,metadata,status)
                else:
                    print(f"[ERROR]: Fail to scrapping data from {url}")
                    f.write(f"[ERROR]: Fail to scrapping data from {url}")
                    pass
            except Exception as e:
                print(e)
                pass
async def main():
    await find_urls_in_agent()
    from_ = 0
    with open(url_json, 'r',encoding='utf-8') as file:
        urls = json.load(file)
    urls = urls[from_:]
    sem  = asyncio.Semaphore(MAX_CONCURRENCY)
    tasks = [
        asyncio.create_task(process_url(sem, u, i+1, len(urls)))
        for i, u in enumerate(urls)
    ]
    await asyncio.gather(*tasks)
    EXECUTOR.shutdown(wait=True)

if __name__ == '__main__':
    asyncio.run(main())
    # asyncio.run(get_proid('https://www.domain.com.au/real-estate-agent/eddie-singh-1871475'))