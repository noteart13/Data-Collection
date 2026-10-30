import uuid
from qdrant_client import AsyncQdrantClient as QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, TextIndexParams, TokenizerType, PayloadFieldSchema
from qdrant_client.models import Filter, FieldCondition,  PointStruct, MatchAny
from operations.property_address import Get_pro_meta, Mapping as LegacyPropertyMapping
import asyncio
import json
from time import time, sleep
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from operations.storage import gcs
import re
import requests
import urllib.parse
import requests
import argparse
from operations.crawl4ai import(
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
)
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from datetime import datetime, timezone
import hashlib
import pandas as pd
import os
from dotenv import load_dotenv

load_dotenv()
PROJECT_ROOT = Path(__file__).parent
invalid_url_file = PROJECT_ROOT / "data" / "invalid_URLs.txt"
progress_log     = PROJECT_ROOT / "data" / "log_progress.txt"
fail_csv         = PROJECT_ROOT / "data" / "fail_address.csv"
url_run_file     = PROJECT_ROOT / "data" / "urls_sale.json"
from_ = 0

QDRANT_URL = os.getenv("QDRANT_URI", "http://localhost:6333")
QDRANT_KEY = (os.getenv("QDRANT_SCRAPE_API") or os.getenv("QDRANT_API", "")).strip('"').strip("'") or None
client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_KEY, timeout=20000)

def parse_args():
    p = argparse.ArgumentParser(description="Step 2: process sale/sold property pages")
    p.add_argument("-b", "--batch-order", type=int, default=0,
                   help="Batch index (used to pick directory data/batch_<b>)")
    p.add_argument("-t", "--type", type=str, default="scrape",
                   help="Scrape or update")
    return p.parse_args()

def get_csv_files(filepath):
    csvs = []
    for subdir, dirs, files in os.walk(filepath):
        files.sort()
        for file in files:
            filepath = subdir + os.sep + file
            if filepath.endswith(".csv"):
                csvs.append(filepath)
    return csvs
MAX_CONCURRENCY = 2
DUMMY_VECTOR = [0.0] * 768
EXECUTOR = ThreadPoolExecutor(max_workers=MAX_CONCURRENCY)
def convert_agent_slug(url):
    url = url.rstrip('/')

    slug = url.split('/')[-1]

    match = re.match(r'(.+)-(\d+)$', slug)
    if match:
        name, num = match.groups()
        hex_id = format(int(num), 'x') 
        return f"{name}-{hex_id}"
    return slug

def create_realestate_slug(url: str):
    raw = url.rstrip("/").split("/")[-1]
    if not raw:
        return ""

    parts = raw.split("-")
    try:
        parts[-1] = format(int(parts[-1]), "x")
    except Exception:
        return raw

    return "-".join(parts)


class PropertyMeta:
    """
    Adapter for the old property_address.py parser.

    The pipeline already expects PropertyMeta(html, url, data_type) with
    create_slug() and Mapping(), while operations.property_address uses
    Get_pro_meta(...) plus LegacyPropertyMapping(...).mapping().
    """

    def __init__(self, html, url, datatype):
        self.html = html
        self.url = url
        self.datatype = datatype
        self.slug = create_realestate_slug(url)
        self.meta = Get_pro_meta(html, url, self.slug, datatype)

    def create_slug(self):
        slug = (self.meta or {}).get("slug") or {}
        return slug.get("slug") or self.slug

    async def Mapping(self):
        mapped = await LegacyPropertyMapping(
            self.meta,
            self.datatype,
            cls_emb=False,
            semantic=False,
        ).mapping()

        historysale, sale_info = (self.meta or {}).get("saleInfo") or (None, None)
        mapped["historySale"] = historysale
        mapped["saleInfo"] = sale_info
        return mapped

async def qdrant_find_by_slug(slug: str, coll: str):
    points, _ = await client.scroll(
        collection_name=coll,
        limit=1,
        with_payload=True,
        with_vectors=True,
        scroll_filter=Filter(
            must=[
                FieldCondition(
                    key="slug",
                    match=MatchValue(value=slug)
                )
            ]
        )
        
    )
    
    return points[0] if points else None

def normalize_address(address):
    if not address:
        return None
    return " ".join(str(address).split())

async def qdrant_find_by_address(address, coll: str):
    address = normalize_address(address)
    if not address:
        return None

    for key in ("address", "street"):
        points, _ = await client.scroll(
            collection_name=coll,
            limit=1,
            with_payload=True,
            with_vectors=True,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key=key,
                        match=MatchValue(value=address)
                    )
                ]
            )
        )

        if points:
            return points[0]

    return None

async def qdrant_find_property(address, slug: str, coll: str):
    point = await qdrant_find_by_address(address, coll)
    if point:
        print(f"Found {coll} by address: {normalize_address(address)}")
        return point

    if slug:
        point = await qdrant_find_by_slug(slug, coll)
        if point:
            print(f"Found {coll} by slug fallback: {slug}")
        return point

    return None

async def qdrant_create(point, coll: str, client: QdrantClient):
    await client.upsert(collection_name=coll, points=[point])
    return point

def _sync_crawl_prep(url: str, data_type: str):
    slug = create_realestate_slug(url)
    print(f"slug: {slug}")

    MAX_RETRIES = 3
    BACKOFF_SECS = [0.5, 1.5, 3.0]

    token = "e9f10b4616524c4ea25208bcb089000d7269eb56e2b"
    target_url = urllib.parse.quote_plus(url)
    geo_code = "au"
    superproxy = "true"
    found_url = f"https://api.scrape.do/?token={token}&url={target_url}&super={superproxy}"
    html = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = requests.request("GET", found_url)

            if result.status_code != 200:
                print(f"[WARN] Attempt {attempt}/{MAX_RETRIES} status={result.status_code}")
            else:
                try:
                    html = result.text
                except Exception:
                    html = None
                if html:
                    break

                print(f"[WARN] Attempt {attempt}/{MAX_RETRIES} no html in response")

        except Exception as e:
            print(f"[ERROR] Attempt {attempt}/{MAX_RETRIES} failed for {url}: {e}")

        if attempt < MAX_RETRIES:
            sleep(BACKOFF_SECS[attempt - 1])

        return ("redirect", slug, None, None, None, None)

    soup = BeautifulSoup(html, "html.parser")

    historysale, saleInfo = None, None
    try:
        slug = PropertyMeta(html, url, data_type).create_slug() or slug
    except Exception as e:
        print(f"[WARN] property slug parse failed: {e}")

    return ("ok", slug, html, soup, historysale, saleInfo)
    
async def extract_prosale_sold_rent_lease_commercial(
    found_url: str,
    slug: str,
    html: str,
    historysale: dict,
    saleInfo: dict,
    data_type: str,
    final_meta=None,
):
    collsale = "PropertyForSale"
    collsold = "PropertySold"
    property_data = (final_meta or {}).get("propertyforsale") or {}
    address = property_data.get("address") or property_data.get("street")
    existing_sale = await qdrant_find_property(address, slug, collsale)
    existing_sold = await qdrant_find_property(address, slug, collsold)
    if data_type == 'salesold':
        await scrape_pro_rentleased(
            html, existing_sale, existing_sold,
            historysale, saleInfo,
            found_url, slug, data_type, final_meta
        )
        
def generate_id(unique_value):
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, unique_value))

def normalize_history_sale(historysale):
    if not historysale:
        return []
    if isinstance(historysale, list):
        return historysale
    return [historysale]

def changed_payload(existing_payload: dict, new_payload: dict):
    existing_payload = existing_payload or {}
    return {
        key: value
        for key, value in (new_payload or {}).items()
        if existing_payload.get(key) != value
    }

def existing_vectors(point):
    vector = getattr(point, "vector", None) or {}
    return {
        "embSemantic": vector.get("embSemantic", [0] * 512),
        "embSemanticNomicTextV1": vector.get("embSemanticNomicTextV1", [0] * 768),
    }

async def set_changed_payload(collection_name: str, point_id: str, existing_payload: dict, new_payload: dict):
    changes = changed_payload(existing_payload, new_payload)
    if not changes:
        print(f"No changed values for {collection_name}:{point_id}")
        return {}

    changes["updatedAt"] = datetime.now(ZoneInfo("Australia/Brisbane"))
    await client.set_payload(
        collection_name=collection_name,
        payload=changes,
        points=[point_id]
    )
    print(f"Updated {collection_name}:{point_id} fields={list(changes.keys())}")
    return changes

async def upload_agency(prosale, agency):
    if not agency or not agency.get('agencyId'):
        return prosale

    async def qdrant_find_by_agencyid(agencyid):
        points, _ = await client.scroll(
            collection_name="Agency",
            limit=1,
            with_payload=True,
            with_vectors=False,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                    key="agencyId",
                    match=MatchValue(value=agencyid)
                )
                ]
            )
        )
        
        return points[0] if points else None
    new_agency = await qdrant_find_by_agencyid(agency['agencyId'])
    if new_agency:
        print("No document to upload")
        prosale['agencyid'] = new_agency.id
    else:
        agency_in = agency
        agency_in['createdAt'] = datetime.now(ZoneInfo("Australia/Brisbane"))
        agency_in['updatedAt'] = datetime.now(ZoneInfo("Australia/Brisbane"))
        point = PointStruct(
            id=str(uuid.uuid4()),
            vector={},
            payload=agency_in,
            
        )
        new_ag = await qdrant_create(point, "Agency", client)
        prosale['agencyid'] = new_ag.id
    return prosale
        
async def upload_agents(prosale, agents):
    async def qdrant_find_by_agentid(agentid):
        points, _ = await client.scroll(
            collection_name="Agent",
            limit=1,
            with_payload=True,
            with_vectors=False,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                    key="agentId",
                    match=MatchValue(value=agentid)
                )
                ]
            )
        )
        
        return points[0] if points else None
    agentid = []
    contactInfo = []
    for a in agents or []:
        if not a.get('agentId'):
            continue

        new_agent = await qdrant_find_by_agentid(a['agentId'])
        if new_agent:
            agentid.append(new_agent.id)
            contactInfo.append(
                {
                    "firstName": a.get('firstName'),
                    "lastName": a.get('lastName'),
                    "email": a.get('email'),
                    "phoneNumber": a.get('phoneNumber'),
                    "photo": a.get('photo')
                }
            )
            
        else:
            slug = convert_agent_slug(a['profileUrl'])
            unique_value = a['profileUrl']
            point_id = generate_id(unique_value)
            agent_in = a
            agent_in['slug'] = slug
            agent_in['createdAt'] = datetime.now(ZoneInfo("Australia/Brisbane"))
            agent_in['updatedAt'] = datetime.now(ZoneInfo("Australia/Brisbane"))
            point = PointStruct(
                id=str(point_id),
                vector={
                },
                payload=agent_in,    
            )
            agentid.append(point_id)
            contactInfo.append(
                {
                    "firstName": a.get('firstName'),
                    "lastName": a.get('lastName'),
                    "email": a.get('email'),
                    "phoneNumber": a.get('phoneNumber'),
                    "photo": a.get('photo')
                }
            )
            new_ag = await qdrant_create(point, "Agent", client)
        prosale['agentid'] = agentid
        prosale['contactInfo'] = contactInfo
    return prosale
async def upload_schools(prosale, schools):
    async def qdrant_find_by_name(name):
        points, _ = await client.scroll(
            collection_name="School",
            limit=1,
            with_payload=True,
            with_vectors=False,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                    key="name",
                    match=MatchValue(value=name)
                )
                ]
            )
        )
        
        return points[0] if points else None
    schoolid = []  
    for s in schools or []:
        if not s.get('name'):
            continue

        new_school = await qdrant_find_by_name(s['name'])
        if new_school:
            schoolid.append(new_school.id)
        else:
            point_id = generate_id(s['name'])
            school_in = s
            school_in['createdAt'] = datetime.now(ZoneInfo("Australia/Brisbane"))
            school_in['updatedAt'] = datetime.now(ZoneInfo("Australia/Brisbane"))
            point = PointStruct(
                id=str(point_id),
                vector={
                    "emb":DUMMY_VECTOR
                },
                payload=school_in,    
            )
            schoolid.append(point_id)
            new_sch = await qdrant_create(point, "School", client)
    prosale['schoolid'] = schoolid
    return prosale

async def upload_images(prosale, images, status, new_pro_idb_id):
    collImages = "ImageSold" if status == 'sold' else "Image"
    async def qdrant_find_by_url(url, coll):
        points, _ = await client.scroll(
            collection_name=coll,
            limit=1,
            with_payload=True,
            with_vectors=False,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                    key="url",
                    match=MatchValue(value=url)
                )
                ]
            )
        )
        
        return points[0] if points else None
    imageid = []
    image = []
    for i in images or []:
        if not i.get('url'):
            continue

        new_image = await qdrant_find_by_url(i['url'], collImages)
        if new_image:
            imageid.append(new_image.id)
            image.append({
                'url': new_image.payload['url'],
                'category': new_image.payload.get('category'),
                'star': new_image.payload.get('star'),
                'proid': new_pro_idb_id
            })
        else:
            image_in = i
            image_in['createdAt'] = datetime.now(ZoneInfo("Australia/Brisbane"))
            image_in['updatedAt'] = datetime.now(ZoneInfo("Australia/Brisbane"))
            point_id = generate_id(i['url'])
            point = PointStruct(
                    id=point_id,
                    vector= {"emb": DUMMY_VECTOR},
                    payload=image_in
                )
            new_img = await qdrant_create(point, collImages, client)
            imageid.append(point_id)
            image.append({
                'url': image_in['url'],
                'category': image_in.get('category'),
                'star': image_in.get('star'),
                'proid': new_pro_idb_id
            })
    prosale['imageid'] = imageid
    prosale['images'] = image
    
    return prosale
async def scraping_data(html, found_url, slug, collections, data_type, status, new_pro_idb_id, final_meta=None):
    if final_meta is None:
        mapping = PropertyMeta(html, found_url, data_type)
        final_meta = await mapping.Mapping()
    if final_meta:
        prodata = final_meta['propertyforsale']
        historysale = final_meta.get("historySale")
        saleInfo = final_meta.get("saleInfo") or {}

        prodata['slug'] = slug
        prodata['url'] = found_url
        prodata['street'] = normalize_address(prodata.get('street'))
        prodata['address'] = prodata.get('address') or prodata.get('street')
        prodata.pop('historysale', None)
        prodata['historySale'] = normalize_history_sale(historysale)
        prodata['listingOption'] = saleInfo.get('listingOption', prodata.get('listingOption'))
        prodata['pricing'] = saleInfo.get('pricing', prodata.get('pricing'))
        prodata['status'] = saleInfo.get('status', prodata.get('status'))
        prodata['soldDateInfo'] = saleInfo.get('soldDateInfo', prodata.get('soldDateInfo'))

        for col in collections:
            if col == 'agency':
                documents = final_meta.get(col)
                if documents:
                    prodata = await upload_agency(prodata, documents)
            
            elif col == 'agent':
                documents = final_meta.get(col + 's', [])
                prodata = await upload_agents(prodata, documents)
            
            elif col == 'school':
                documents = final_meta.get(col + 's', [])
                prodata = await upload_schools(prodata, documents)
            elif col == 'image':
                documents = final_meta.get(col + 's', [])
                prodata = await upload_images(prodata, documents, status, new_pro_idb_id)

    return prodata

async def deleteImage(imageid, images, coll):
    status = gcs.delete_images(images)
    point_ids = imageid if isinstance(imageid, list) else [imageid]
    
    await client.delete(
        collection_name=coll,
        points_selector=point_ids
    )

async def transfer_images_between_collections(images, from_coll: str, to_coll: str):
    """
    Transfer images from one collection to another and update GCS bucket paths
    from_coll: source collection (e.g., 'ImageRent')
    to_coll: destination collection (e.g., 'ImageRented')
    """
    async def qdrant_find_by_url(url, coll):
        points, _ = await client.scroll(
            collection_name=coll,
            limit=1,
            with_payload=True,
            with_vectors=False,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                    key="url",
                    match=MatchValue(value=url)
                )
                ]
            )
        )
        return points[0] if points else None
    
    transferred_image_ids = []
    
    for img in images:
        img_url = img.get('url', '')
        if not img_url:
            continue
            
        # Find image in source collection
        existing_image = await qdrant_find_by_url(img_url, from_coll)
        
        if existing_image:
            # Create new image entry in destination collection  
            new_image_id = generate_id(img_url + to_coll)
            image_payload = existing_image.payload.copy()
            image_payload['updatedAt'] = datetime.now(ZoneInfo("Australia/Brisbane"))
            
            new_point = PointStruct(
                id=new_image_id,
                vector={"emb": DUMMY_VECTOR},
                payload=image_payload
            )
            
            # Upload to destination collection
            await qdrant_create(new_point, to_coll, client)
            transferred_image_ids.append({
                'new_id': new_image_id,
                'old_id': existing_image.id,
                'url': img_url
            })
    
    return transferred_image_ids
async def update_profile(html, collections, imageid, images, new_pro_idb, historysale, saleInfo, found_url, slug, changeStatus, status, final_meta=None):
    prodata = await scraping_data(html, found_url, slug, collections, 'salesold', status, new_pro_idb.id, final_meta)
    
    if changeStatus == 'sold2sold':
        await set_changed_payload("PropertySold", new_pro_idb.id, new_pro_idb.payload, prodata)
    elif changeStatus == 'sale2sale':
        await set_changed_payload("PropertyForSale", new_pro_idb.id, new_pro_idb.payload, prodata)
    elif changeStatus == 'sale2sold':
        try:
            await deleteImage(imageid, images, "Image")
        except:
            print()
        point = PointStruct(
                    id = new_pro_idb.id,
                    vector = existing_vectors(new_pro_idb),
                    payload=prodata
                )
        
        t = await qdrant_create(point, "PropertySold", client)
        await client.delete(collection_name="PropertyForSale", points_selector=[new_pro_idb.id])
    elif changeStatus == 'sold2sale':
        try:
            await deleteImage(imageid, images, "ImageSold")
        except:
            print()
        point = PointStruct(
                    id = new_pro_idb.id,
                    vector = existing_vectors(new_pro_idb),
                    payload=prodata
                )
        t = await qdrant_create(point, "PropertyForSale", client)
        await client.delete(collection_name="PropertySold", points_selector=[new_pro_idb.id])
async def upload_rentleased_profile(html, found_url, slug, collections, data_type, historysale, saleInfo,status, final_meta=None):
    _id=str(uuid.uuid4())
    prodata = await scraping_data(html, found_url, slug, collections, 'salesold', status, _id, final_meta)
    prodata['createdAt'] = datetime.now(ZoneInfo("Australia/Brisbane"))
    prodata['updatedAt'] = datetime.now(ZoneInfo("Australia/Brisbane"))
    print(datetime.now(ZoneInfo("Australia/Brisbane")))
    prodata['slug'] = slug
    prodata['historySale'] = normalize_history_sale(historysale)
    prodata['url'] = found_url
    
    point = PointStruct(
        id=_id,
        vector={
            "embSemanticNomicTextV1": [0] * 768,
            "embSemantic": [0] * 512
        },
        payload=prodata
    )      
    t = await qdrant_create(point, "PropertySold", client) if status == 'sold' else await qdrant_create(point, "PropertyForSale", client)
    return t

async def scrape_pro_rentleased(html, new_prosale_idb, new_prosold_idb, historysale, saleInfo, found_url, slug, data_type, final_meta=None):
    collections = ['agency', 'agent', 'school', 'image']
    if new_prosale_idb is None and new_prosold_idb is not None:
        if saleInfo['status'] == 'sold':
            changeStatus = 'sold2sold'
            imageid = new_prosold_idb.payload.get('imageid', [])
            images = new_prosold_idb.payload.get('images', [])
            await update_profile(html, collections, imageid, images, new_prosold_idb, historysale, saleInfo, found_url, slug, changeStatus, saleInfo['status'], final_meta)
        else:
            changeStatus = 'sold2sale'
            imageid = new_prosold_idb.payload.get('imageid', [])
            images = new_prosold_idb.payload.get('images', [])
            await update_profile(html, collections, imageid, images, new_prosold_idb, historysale, saleInfo, found_url, slug, changeStatus, saleInfo['status'], final_meta)
    if new_prosale_idb is not None and new_prosold_idb is None:
        if saleInfo['status'] != 'sold':
            changeStatus = 'sale2sale'
            imageid = new_prosale_idb.payload.get('imageid', [])
            images = new_prosale_idb.payload.get('images', [])
            await update_profile(html, collections, imageid, images, new_prosale_idb, historysale, saleInfo, found_url, slug, changeStatus, saleInfo['status'], final_meta)
        else:
            changeStatus = 'sale2sold'
            imageid = new_prosale_idb.payload.get('imageid', [])
            images = new_prosale_idb.payload.get('images', [])
            await update_profile(html, collections, imageid, images, new_prosale_idb, historysale, saleInfo, found_url, slug, changeStatus, saleInfo['status'], final_meta)
    if new_prosale_idb is None and new_prosold_idb is None:
        prohis = await upload_rentleased_profile(html, found_url, slug, collections, data_type, historysale, saleInfo, saleInfo['status'], final_meta)
    
    
async def change_status_property(slug):
    prosale = await qdrant_find_by_slug(slug=slug,coll= "PropertyForSale") 
    
    if prosale:
        imageid = prosale.payload.get("imageid", [])
        images = prosale.payload.get("images", [])
        
        try:
            transferred = await transfer_images_between_collections(images, "Image", "ImageSold")

            if transferred:
                new_image_ids = [t['new_id'] for t in transferred]
                prosale.payload['imageid'] = new_image_ids
                prosale.payload['updatedAt'] = datetime.now(ZoneInfo("Australia/Brisbane"))
                
                print(f"Transferred {len(transferred)} images from Image to ImageSold")
        except Exception as e:
            print(f"Error transferring images: {e}")

        try:
            await deleteImage(imageid, images, "Image")
            print(f"Deleted {len(imageid)} images from Image collection")
        except Exception as e:
            print(f"Error deleting images: {e}")

        try:
            await client.delete(collection_name="PropertyForSale", points_selector=[prosale.id])
            print(f"Deleted property from PropertyForSale collection")
        except Exception as e:
            print(f"Error deleting from PropertyForSale: {e}")
        prosale.payload['status'] = 'sold'
        prosale.payload['listingOption'] = 'sold'
        prosale.payload['published'] = False
        prosale.payload['updatedAt'] = datetime.now(ZoneInfo("Australia/Brisbane"))
        prosale.payload['historySale'] = [{
                'soldDate': None,
                'soldPrice': None,
                'agencyId': None
            }]
        

        try:
            t = await qdrant_create(prosale, "PropertySold", client)
            print(f"✓ Property transferred to PropertySold collection with status='sold'")
        except Exception as e:
            print(f"✗ Error creating in PropertySold: {e}")
        
         
    
async def process_url(sem: asyncio.Semaphore, url: str, idx: int, total: int):
    async with sem:
        label = f"[{idx}/{total}]"
        start = time()
        print(f"START {label} {url}")
        with open(progress_log, "a",encoding='utf-8') as f:
            f.write(f"START {label} {url}\n")

        try:
            loop = asyncio.get_running_loop()

            # ── Offload only crawl+parse into thread‐pool ──
              
            status, slug, html, soup, historysale, saleInfo = await loop.run_in_executor(
                EXECUTOR,
                _sync_crawl_prep,
                url,
                "salesold",
            )
            if status == "redirect":
                # await remove_sale_property(slug, url)
                await change_status_property(slug)
                return

            if status != "ok":
                # failure (flaky connection etc.) → just skip, don’t delete
                print(f"[WARN] Skipping (crawl failure) {url}")
                return
            # ── Quick validity check ──
            property_meta = PropertyMeta(html, url, "salesold")
            slug = property_meta.create_slug() or slug

            address_tag = soup.find("h1", class_="property-info-address")
            if not address_tag and not property_meta.meta:
                print(f"Invalid URL")
                # await remove_sale_property(slug, url)
                await change_status_property(slug)
                return

            final_meta = await property_meta.Mapping()
            historysale = final_meta.get("historySale")
            saleInfo = final_meta.get("saleInfo")

            if not saleInfo or not saleInfo.get("status"):
                print(f"[WARN] Missing saleInfo; skipping {url}")
                return

            # ── Business logic ──
            await extract_prosale_sold_rent_lease_commercial(
                found_url=url,
                slug=slug,
                html=html,
                historysale=historysale,
                saleInfo=saleInfo,
                data_type="salesold",
                final_meta=final_meta,
            )

            dur = time() - start
            print(f"[DONE] {label} {url} in {dur:.1f}s")
            with open(progress_log, "a",encoding='utf-8') as f:
                f.write(f"[DONE] {label} {url} in {dur:.1f}s\n")

        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            print(f"[FAIL] {label} {url} → {err}")
            with open(progress_log, "a",encoding='utf-8') as f:
                f.write(f"[FAIL] {label} {url} → {err}\n")

            pd.DataFrame([{"url": url, "error": err}]) \
              .to_csv(fail_csv, mode="a",
                      header=not fail_csv.exists(),
                      index=False)
async def main():
    args = parse_args()
    batch_order      = args.batch_order
    typ              = args.type
    # Derive paths after parsing
    global progress_log, fail_csv, invalid_url_file
    invalid_url_file = PROJECT_ROOT/"data"/"invalid_URLs_add_new.txt"
    progress_log = PROJECT_ROOT / "data" / f"log_progress_step2_batch_{batch_order}.txt"
    fail_csv     = PROJECT_ROOT / "data" / f"fail_address_add_new.csv"
    urls_old     = PROJECT_ROOT / "data" / "urls_sale_add_new.json"
    suburb_dir   = PROJECT_ROOT / "data" / f"batch_{batch_order}"

    sem      = asyncio.Semaphore(MAX_CONCURRENCY)
    if typ == 'scrape':
        address_for_sales = get_csv_files(suburb_dir)
        print(f'Number of suburbs with sale properties: {len(address_for_sales)}')
        SaleURLs = []
        for suburb_csv in address_for_sales:
            try:
                df = pd.read_csv(suburb_csv)
                SaleURLs.extend(df['SaleURL'].tolist())
            except:
                continue
        print(f'Number of sale properties: {len(SaleURLs)}')

        with open(urls_old, 'r',encoding='utf-8') as file:
            SaleURLs_excl = json.load(file)

        urls = list(set(SaleURLs) - set(SaleURLs_excl))
        urls = [found_url for found_url in urls if found_url.rstrip('/').rsplit('-', 1)[-1].isdigit()]
        print(f'Number of new sale properties: {len(urls)}')
        urls = urls[from_:]
    else:
        urls = json.load(open(url_run_file))
        urls = urls[from_:]
    tasks = [
            asyncio.create_task(process_url(sem, u, i+1, len(urls)))
            for i, u in enumerate(urls)
    ]
    await asyncio.gather(*tasks)
    EXECUTOR.shutdown(wait=True)
asyncio.run(main())
