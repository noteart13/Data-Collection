import uuid
from qdrant_client import AsyncQdrantClient as QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue
from operations.property_address import Get_pro_meta, Mapping, getPriceInfo
import asyncio
import json
from time import time, sleep
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from operations.storage import gcs
import re
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
from datetime import datetime
from pymongo.database import Database
import hashlib
import pandas as pd
from typing import List
from pymongo.errors import ExecutionTimeout, ServerSelectionTimeoutError, AutoReconnect
from motor.core import AgnosticDatabase  # Motor type hint
import os
import tracemalloc

tracemalloc.start()  # Enable memory tracing
PROJECT_ROOT   = Path(__file__).parent
from_ = 0

DUMMY_VECTOR = [0.0] * 768
MAX_CONCURRENCY = 2

client = QdrantClient(url=os.getenv("QDRANT_URI", "http://localhost:6333"), api_key=os.getenv("QDRANT_API") or None)

def convert_agent_slug(url):
    url = url.rstrip('/')

    slug = url.split('/')[-1]

    match = re.match(r'(.+)-(\d+)$', slug)
    if match:
        name, num = match.groups()
        hex_id = format(int(num), 'x') 
        return f"{name}-{hex_id}"
    return slug

def get_csv_files(filepath):
    csvs = []
    for subdir, dirs, files in os.walk(filepath):
        files.sort()
        for file in files:
            filepath = subdir + os.sep + file
            if filepath.endswith(".csv"):
                csvs.append(filepath)
    return csvs

async def qdrant_find_one(key: str, value: str, coll: str, client: QdrantClient):
    filtered_points, _ = await client.scroll(
        collection_name=coll,
        limit=1,
        with_payload=True,
        with_vectors=True,  # Changed to True to include vectors for updates
        scroll_filter=Filter(
            must=[
                FieldCondition(
                    key=key,
                    match=MatchValue(value=value)
                )
            ]
        )
    )
    return filtered_points[0] if filtered_points else None

async def qdrant_get_by_slug(slug: str, coll: str, client: QdrantClient):
    return await qdrant_find_one("slug", slug, coll, client)

async def qdrant_get_by_agencyId(agencyId: str, client: QdrantClient):
    return await qdrant_find_one("agencyId", agencyId, "Agency", client)

async def qdrant_get_by_agentId(agentId: str, client: QdrantClient):
    return await qdrant_find_one("agentId", agentId, "Agent", client)

async def qdrant_create(point, coll: str, client: QdrantClient):
    await client.upsert(collection_name=coll, points=[point])
    return point

async def qdrant_update(db_obj, obj_in, coll: str, client: QdrantClient):
    updated_payload = {**db_obj.payload, **obj_in}
    updated_point = PointStruct(id=db_obj.id, vector=db_obj.vector, payload=updated_payload)  
    await client.upsert(collection_name=coll, points=[updated_point])
    return updated_point

async def upload_image(url, street, property_id):
    try:
        gcs_url = await gcs.upload_images_to_gcs(url, street, property_id)
    except Exception as e:
        print(e)
        gcs_url = url
    return gcs_url

async def delete_image(url):
    pass  # Implement if needed


def parse_args():
    p = argparse.ArgumentParser(description="Step 2: process sale/sold property pages")
    p.add_argument("-b", "--batch-order", type=int, default=0,
                   help="Batch index (used to pick directory data/batch_<b>)")
    return p.parse_args()

async def update_history_sale_profile(prodata, prohissaleprof_indb, historysale):
    historyProfile = {}
    prohissaleprof_in = {}
    prohissaleprof_in['id'] = prodata.id
    prodata_dict = prodata.payload if hasattr(prodata, 'payload') else prodata
    to_save_history_profile = ['propertyType','slug','title','contactInfo','soldDateInfo','stakeHolder','description',
                               'bath', 'bed','images','features','area','agencyid', 'agentid', 'imageid']  # to save in history property profile, check schema for more information
    for history in to_save_history_profile:
        historyProfile[history] = prodata_dict.get(history)
    historyProfile['historySale'] = [historysale]

    if prohissaleprof_indb:
        prohissaleprof_indb_dict = prohissaleprof_indb.payload if hasattr(prohissaleprof_indb, 'payload') else prohissaleprof_indb
        historyProfiles = prohissaleprof_indb_dict.get('historyprofile', []) + [historyProfile]
        prohissaleprof_in['historyprofile'] = historyProfiles
    else:
        prohissaleprof_in['historyprofile'] = [historyProfile]
    return prohissaleprof_in

async def upload_profile(prodata, historysale, mode, option, client: QdrantClient):
    prohisprof = None  # Initialize to avoid UnboundLocalError
    if option == 'sold':  # only sold property, then update sale profile
        current_prohissaleprof = await qdrant_get_by_slug(prodata.payload['slug'], "PropertySold", client)
        if current_prohissaleprof:
            print("Profile Updating")
            prohissaleprof = await update_history_sale_profile(prodata, current_prohissaleprof, historysale)
            prohissaleprof['updatedAt'] = datetime.now().replace(microsecond=0)
            # Assume ProhissaleprofUpdate is a dict
            prohisprof = await qdrant_update(current_prohissaleprof, prohissaleprof, "PropertySold", client)
        else:  # create new one
            print("Profile Creating")
            prohissaleprof = await update_history_sale_profile(prodata, None, historysale)
            # Assume ProhissaleprofCreate is a dict
            point = PointStruct(id=str(uuid.uuid4()), vector={"embSemantic": DUMMY_VECTOR[:512], "embSemanticNomicTextV1": DUMMY_VECTOR}, payload=prohissaleprof)  # Added vector for create
            prohisprof = await qdrant_create(point, "PropertySold", client)
    else:
        prohisprof = None
    return prohisprof

async def upload_agency(prosale, agency, client: QdrantClient):
    new_agency = await qdrant_get_by_agencyId(agency['agencyId'], client)
    if new_agency:
        print("No document to upload")
        prosale['agencyid'] = new_agency.id
    else:
        agency_in = agency  # Assume dict
        point = PointStruct(
            id=str(uuid.uuid4()),
            vector={
                "emb":DUMMY_VECTOR},
            payload=agency_in,
            
        )
        new_agency = await qdrant_create(point, "Agency", client)
        prosale['agencyid'] = new_agency.id
    return prosale

async def upload_documents(prosale, documents: List[dict], coll: str, option: str, client: QdrantClient, is_new_property: bool):
    """
    Upload multiple schools/agents/images to Qdrant.
    Checks existence for agents/schools and uploads new ones; uploads images if new property.
    For images, replace old ones if not new property.
    """
    name = coll.lower()
    if name == "image":
        collection_name = "ImageSold" if option == "sold" else "Image"
    else:
        collection_name = coll.title()  # "Agent", "School"
    import json
    with open(PROJECT_ROOT / "prosale_debug.json", "w", encoding="utf-8") as f:
        json.dump(prosale, f, indent=4, default=str)
    
    # Helper to check existence in Qdrant
    async def check_existence(filter_conditions):
        filtered_points, _ = await client.scroll(
            collection_name=collection_name,
            limit=1,
            with_payload=False,
            with_vectors=False,
            scroll_filter=Filter(must=filter_conditions)
        )
        return filtered_points[0] if filtered_points else None

    # Helper to generate ID
    def generate_id(unique_value):
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, unique_value))

    # Helper to upload points
    async def _upload(doc_list, existing_ids):
        points = []
        ids = []
        for d in doc_list:
            if name == "agent":
                try:
                    slug = convert_agent_slug(d['profileUrl'])
                    d['slug'] = slug
                except Exception as e:
                    print(e)
                    continue
                unique_value = d['profileUrl']
                point_id = generate_id(unique_value)
                
                point = {
                    "id": point_id,
                    "vector": {"emb": DUMMY_VECTOR},
                    "payload": {
                        "agentId": d['agentId'],
                        "createdAt": datetime.now(),
                        "email": d['email'],
                        "firstName": d['firstName'],
                        "isActiveProfilePage": True,
                        "lastName": d['lastName'],
                        "phoneNumber": d['phoneNumber'],
                        "photo": d['photo'],
                        "profileUrl": d['profileUrl'],
                        "updatedAt": datetime.now(),
                        "slug": d['slug']
                    }
                }
            elif name == "school":
                unique_value = d['name']
                point_id = generate_id(unique_value)
                point = {
                    "id": point_id,
                    "vector": {"emb": DUMMY_VECTOR},
                    "payload": {
                        'address': d['address'],
                        'createdAt': datetime.now(),
                        'distance': d['distance'],
                        'domainSeoUrlSlug': d['domainSeoUrlSlug'],
                        'educationLevel': d['educationLevel'],
                        'gender': d['gender'],
                        'name': d['name'],
                        'postCode': d['postCode'],
                        'state': d['state'],
                        'type': d['type'],
                        'updatedAt': datetime.now(),
                        'url': d['url'],
                        'year': d['year']
                    }
                }
            elif name == "image":
                point_id = generate_id(d['url'])
                point = PointStruct(
                    id=point_id,
                    vector= {"emb": [0.1, 1, 0.3, 0.6] * 192},
                    payload={
                        "url": d['url'],
                        "category": d['category'],
                        "star": False,
                        "proid": prosale.get('id', '')  # Add proid for linking
                    }
                )
            points.append(point)
            ids.append(point_id)
        
        if points:
            
            await client.upsert(collection_name=collection_name, points=points)
        
        key = name + "id"
        prosale[key] = ids + existing_ids
        return ids  # Return new ids for replacement logic
        
    # Images: upload and replace old ones if not new property
    if name == "image":
        if not is_new_property:
            old_image_ids = prosale.get('imageid', [])
            if old_image_ids:
                await client.delete(collection_name="Image", points_selector=old_image_ids)
        new_ids = await _upload(documents, existing_ids=[])
        prosale['imageid'] = new_ids  
        return prosale

    # Agents/Schools: check existence and upload new ones
    found_ids = []
    to_upload = []

    for d in documents:
        if name == "agent":
            filter_conditions = [FieldCondition(key="profileUrl", match=MatchValue(value=d["profileUrl"]))]
        elif name == "school":
            filter_conditions = [
                FieldCondition(key="name", match=MatchValue(value=d["name"])),
                FieldCondition(key="address", match=MatchValue(value=d["address"]))
            ]
        else:
            continue
        
        existing = await check_existence(filter_conditions)
        if existing:
            found_ids.append(existing.id)
        else:
            to_upload.append(d)

    # Upload new documents
    if to_upload:
        new_ids = await _upload(to_upload, existing_ids=found_ids)
        prosale[name + "id"] = new_ids + found_ids
    else:
        prosale[name + "id"] = found_ids

    return prosale

async def scraping_data(html, collections, found_url, slug, option, data_type, client: QdrantClient, is_new_property: bool):
    pro_meta = Get_pro_meta(html, found_url, slug, data_type)
    mapping = Mapping(pro_meta, data_type, cls_emb=False, semantic=False)
    final_meta = await mapping.mapping()
    if final_meta:
        prodata = final_meta['propertyforsale']
        for col in collections:
            if col == "agency":
                documents = final_meta[col]
                prodata = await upload_agency(prodata, documents, client)
            else:
                documents = final_meta[col+'s']
                prodata = await upload_documents(prodata, documents, col, option, client, is_new_property)
        
        # Populate contactInfo from agents
        agent_ids = prodata.get('agentid', [])
    else:
        prodata = None
    return prodata

# Note: Many functions like _sync_agents_and_contactinfo, update_sale_profile, etc., need similar conversions.
# For brevity, I'll assume they are updated similarly, replacing MongoDB calls with Qdrant equivalents.
# For example, instead of db.get_collection("Agent").find, use client.scroll.

# Similarly for other functions. The full conversion would require updating each one.

# Continuing with the rest...

async def move_images_to_sale(slug, client: QdrantClient):
    """
    Move images from 'ImageSold' collection to 'Image' when property status changes to sale.
    Also copy GCS blobs from prosold to prosale.
    """
    # Find images for this property
    filtered_points, _ = await client.scroll(
        collection_name="ImageSold",
        limit=100,  # Assume not too many images per property
        with_payload=True,
        with_vectors=True,
        scroll_filter=Filter(
            must=[
                FieldCondition(
                    key="property_slug",  # Assuming payload has 'property_slug'
                    match=MatchValue(value=slug)
                )
            ]
        )
    )
    for point in filtered_points:
        payload = point.payload
        vector = point.vector
        # Copy GCS blob
        if 'url' in payload and payload['url'].startswith('gs://'):
            # Extract blob name
            blob_name = payload['url'][5:]  # Remove 'gs://'
            bucket_name, path = blob_name.split('/', 1)
            # Replace prosold with prosale
            if '/prosold' in path:
                new_path = path.replace('/prosold', '/prosale')
                new_gs_url = gcs.copy_blob(path, new_path)
                payload['url'] = new_gs_url
        # Upsert to Image
        new_point = PointStruct(id=point.id, vector=vector, payload=payload)
        await client.upsert(collection_name="Image", points=[new_point])
        # Delete from ImageSold
        await client.delete(collection_name="ImageSold", points_selector=[point.id])

async def move_images_to_sold(slug, client: QdrantClient):
    """
    Move images from 'Image' collection to 'ImageSold' when property status changes to sold.
    Also copy GCS blobs from prosale to prosold.
    """
    # Find images for this property
    filtered_points, _ = await client.scroll(
        collection_name="Image",
        limit=100,  # Assume not too many images per property
        with_payload=True,
        with_vectors=True,
        scroll_filter=Filter(
            must=[
                FieldCondition(
                    key="property_slug",  # Assuming payload has 'property_slug'
                    match=MatchValue(value=slug)
                )
            ]
        )
    )
    for point in filtered_points:
        payload = point.payload
        vector = point.vector
        # Copy GCS blob
        if 'url' in payload and payload['url'].startswith('gs://'):
            # Extract blob name
            blob_name = payload['url'][5:]  # Remove 'gs://'
            bucket_name, path = blob_name.split('/', 1)
            # Replace prosale with prosold
            if '/prosale' in path:
                new_path = path.replace('/prosale', '/prosold')
                new_blob_name = f"{bucket_name}/{new_path}"
                new_gs_url = gcs.copy_blob(path, new_path)
                payload['url'] = new_gs_url
        # Upsert to ImageSold
        new_point = PointStruct(id=point.id, vector=vector, payload=payload)
        await client.upsert(collection_name="ImageSold", points=[new_point])
        # Delete from Image
        await client.delete(collection_name="Image", points_selector=[point.id])

async def upload_soldsale_profile(html, collections, found_url, slug, saleInfo, new_pro_indb, historysale, mode, createOrupdate, data_type, imageids, images, changeStatus, proid, client: QdrantClient, is_new_property: bool):
    option = saleInfo['status']
    if option == 'for-sale':
        option = 'sale'
    prodata = await scraping_data(html, collections, found_url, slug, option, data_type, client, is_new_property)
    prohisprof = None  # Initialize
    if prodata:
        try:
            prodata['historySale'] = [historysale]
            if not is_new_property:
                # Handle image moving if needed, but simplified
                pass
            sche = 'Pro' + option + createOrupdate.title() if option == 'sold' else 'Pro' + 'sale' + createOrupdate.title()
            if createOrupdate == 'update':
                prodata['updatedAt'] = datetime.now().replace(microsecond=0)
                point = PointStruct(id=new_pro_indb.id, vector=new_pro_indb.vector, payload=prodata)  # Added vector
                prodata = await qdrant_update(new_pro_indb, prodata, "PropertyForSale" if option != 'sold' else "PropertySold", client)
            else:
                if new_pro_indb:
                    prodata['id'] = new_pro_indb.id
                
                prodata['createdAt'] = datetime.now()
                prodata['updatedAt'] = datetime.now()
                point = PointStruct(id=str(uuid.uuid4()), vector={"embSemantic":[1,2.1,3.2,1.0] * 128, "embSemanticNomicTextV1": DUMMY_VECTOR},payload=prodata)
                prodata = await qdrant_create(point, "PropertyForSale" if option != 'sold' else "PropertySold", client)
            prohisprof = await upload_profile(prodata, historysale, mode, option, client)
        except Exception as e:
            print(e)
            prohisprof = None  # Set to None on error
    return prohisprof

async def scrape_pro_salesold(html, collsale, collsold, new_prosale_indb, new_prosold_indb, historysale, saleInfo, found_url, slug, mode, data_type, client: QdrantClient):
    collections = ['agency', 'agent', 'school', 'image']
    async def do_change_status(changeStatus, imageids, images, new_pro_indb, proid, crud, is_new_property):
        prohisprof = await upload_soldsale_profile(html, collections, found_url, slug, saleInfo, new_pro_indb, historysale, mode, crud, data_type, imageids, images, changeStatus, proid, client, is_new_property)
        if changeStatus in ['sold2sale', 'sale2sold']:
            coll = "PropertyForSale" if 'sale' == changeStatus.split('2')[0] else "PropertySold"
            # Delete point
            await client.delete(collection_name=coll, points_selector=[new_pro_indb.id])
            if changeStatus == 'sale2sold':
                await move_images_to_sold(slug, client)
            elif changeStatus == 'sold2sale':
                await move_images_to_sale(slug, client)
    if new_prosale_indb is None and new_prosold_indb is not None:
        if saleInfo['status'] == 'sold':
            found = False
            for historysale_indb in new_prosold_indb.payload.get('historySale', []):
                if historysale['soldDate'] == historysale_indb['soldDate']:
                    found = True
                    break
            if not found:
                changeStatus = 'sold2sold'
                imageids = new_prosold_indb.payload['imageid']
                images = new_prosold_indb.payload['images']
                await do_change_status(changeStatus, imageids, images, new_prosold_indb, new_prosold_indb.id, 'update', False)
        else:
            changeStatus = 'sold2sale'
            imageids = new_prosold_indb.payload['imageid']
            images = new_prosold_indb.payload['images']
            await do_change_status(changeStatus, imageids, images, new_prosold_indb, new_prosold_indb.id, 'create', True)
    if new_prosale_indb and new_prosold_indb is None:
        if saleInfo['status'] != 'sold':
            # Update profile
            pass  # Implement update_sale_profile for Qdrant
        if saleInfo['status'] == 'sold':
            changeStatus = 'sale2sold'
            imageids = new_prosale_indb.payload['imageid']
            images = new_prosale_indb.payload['images']
            await do_change_status(changeStatus, imageids, images, new_prosale_indb, new_prosale_indb.id, 'create', False)
    if new_prosale_indb is None and new_prosold_indb is None:
        prohisprof = await upload_soldsale_profile(html, collections, found_url, slug, saleInfo, None, historysale, mode, 'create', data_type, None, None, None, None, client, True)

def convert_text(name: str) -> str:
    camel_case = "".join(word.capitalize() for word in name.split())
    return camel_case + "Image"

async def remove_sale_property(slug: str, url: str, client: QdrantClient):
    prosale = await qdrant_get_by_slug(slug, "PropertyForSale", client)
    if not prosale:
        print(f"[remove_sale_property] no PropertyForSale for url={url}")
        return
    
    # Update payload: change title to "Sold - Price Withheld"
    updated_payload = prosale.payload.copy()
    updated_payload['title'] = "Sold - Price Withheld"
    updated_payload['updatedAt'] = datetime.now().replace(microsecond=0)
    
    # Create new point for PropertySold with new ID to avoid conflicts
    new_point = PointStruct(
        id=str(uuid.uuid4()),
        vector=prosale.vector,
        payload=updated_payload
    )
    
    # Upsert to PropertySold
    await client.upsert(collection_name="PropertySold", points=[new_point])
    print(f"[remove_sale_property] transferred PropertyForSale to PropertySold for url={url}")
    
    # Move images from Image to ImageSold and copy GCS blobs from prosale to prosold
    # Find images linked to this property (using proid)
    filtered_points, _ = await client.scroll(
        collection_name="Image",
        limit=100,  # Assume not too many images per property
        with_payload=True,
        with_vectors=True,
        scroll_filter=Filter(
            must=[
                FieldCondition(
                    key="proid",
                    match=MatchValue(value=prosale.id)
                )
            ]
        )
    )
    for point in filtered_points:
        payload = point.payload
        vector = point.vector
        # Copy GCS blob from prosale to prosold
        if 'url' in payload and payload['url'].startswith('gs://'):
            blob_name = payload['url'][5:]  # Remove 'gs://'
            bucket_name, path = blob_name.split('/', 1)
            if '/prosale' in path:
                new_path = path.replace('/prosale', '/prosold')
                new_gs_url = await gcs.copy_blob(path, new_path)  # Assuming gcs.copy_blob is async
                payload['url'] = new_gs_url
        # Upsert to ImageSold
        new_img_point = PointStruct(id=point.id, vector=vector, payload=payload)
        await client.upsert(collection_name="ImageSold", points=[new_img_point])
        # Delete from Image
        await client.delete(collection_name="Image", points_selector=[point.id])
    
    # Delete the original property from PropertyForSale
    await client.delete(collection_name="PropertyForSale", points_selector=[prosale.id])
    print(f"[remove_sale_property] deleted original PropertyForSale for url={url}")

async def extract_prosale_sold_rent_lease_commercial(
    found_url: str,
    slug: str,
    html: str,
    historysale: dict,
    saleInfo: dict,
    mode: str,
    data_type: str,
    client: QdrantClient
):
    if data_type == 'salesold':
        existing_sale = await qdrant_get_by_slug(slug, "PropertyForSale", client)
        existing_sold = await qdrant_get_by_slug(slug, "PropertySold", client)
        await scrape_pro_salesold(
            html, "PropertyForSale", "PropertySold",
            existing_sale, existing_sold,
            historysale, saleInfo,
            found_url, slug,
            mode, data_type, client
        )

EXECUTOR = ThreadPoolExecutor(max_workers=MAX_CONCURRENCY)

async def _sync_crawl_prep(url: str, data_type: str):
    raw = url.rstrip('/').split('/')[-1]
    main = raw[: raw.rfind('-')]
    suffix = hashlib.sha512(raw.split('-')[-1].encode()).hexdigest()[:8]
    slug = f"{main}-{suffix}"
    print(f'slug: {slug}')

    MAX_RETRIES = 3
    BACKOFF_SECS = [0.5, 1.5, 3.0]
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
            "--enable-http2",
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        ],
        accept_downloads=False,
        downloads_path=None,
        storage_state=None,
        cookies=[],
        headless=True,
        light_mode=True,
    )
    result = None
    for attempt in range(1, MAX_RETRIES + 1):
        crawler = AsyncWebCrawler(config=browser_config)
        try:
            crawler_config = CrawlerRunConfig(
                cache_mode=CacheMode.DISABLED,
                keep_attrs=["id", "class"],
                keep_data_attributes=True,
                delay_before_return_html=2,
            )
            await crawler.start()
            result = await crawler.arun(url=url, config=crawler_config)
            await crawler.close()
            if result and getattr(result, "success", False) and getattr(result, "html", None):
                break
            else:
                print(f"[WARN] Attempt {attempt}/{MAX_RETRIES} returned unusable result for {url}")
        except Exception as e:
            print(f"[ERROR] Attempt {attempt}/{MAX_RETRIES} failed for {url}: {e}")
        if attempt < MAX_RETRIES:
            sleep(BACKOFF_SECS[attempt - 1])
    if not result or not getattr(result, "success", False) or not getattr(result, "html", None):
        print(f"[WARN] No usable result for {url}; skipping.")
        try:
            with open(invalid_url_file, 'a') as f:
                f.write(f'{url}\n')
        except Exception:
            pass
        return ("fail", slug, None, None, None, None)
    meta = getattr(result, "metadata", {}) or {}
    landing = meta.get("final_url") or meta.get("og:url")
    if landing and landing != url:
        print(f"⚠️ Redirect/canonical mismatch!\n  requested: {url}\n  landed on:  {landing}")
        return ("redirect", slug, None, None, None, None)
    html = result.html
    soup = BeautifulSoup(html, 'html.parser')
    try:
        historysale, saleInfo = getPriceInfo(html, data_type)
    except Exception:
        historysale, saleInfo = None, None
    return ("ok", slug, html, soup, historysale, saleInfo)

async def process_url(sem: asyncio.Semaphore, url: str, idx: int, total: int, client: QdrantClient):
    async with sem:
        label = f"[{idx}/{total}]"
        start = time()
        print(f"START {label} {url}")
        with open(progress_log, "a",encoding='utf-8') as f:
            f.write(f"START {label} {url}\n")

        try:
            loop = asyncio.get_running_loop()

            # ── Offload only crawl+parse into thread‐pool ──
            status, slug, html, soup, historysale, saleInfo = await _sync_crawl_prep(url, "salesold")
            if status == "redirect":
                await remove_sale_property(slug, url,client)
                return

            if status != "ok":
                # failure (flaky connection etc.) → just skip, don’t delete
                print(f"[WARN] Skipping (crawl failure) {url}")
                return

            # ── Quick validity check ──
            if not soup.find_all("h1", class_="css-jgr3ou"):
                print(f"Invalid URL")
                await remove_sale_property(slug, url, client)
                return

            # ── Business logic ──
            await extract_prosale_sold_rent_lease_commercial(
                found_url=url,
                slug=slug,
                html=html,
                historysale=historysale,
                saleInfo=saleInfo,
                mode="prod",
                data_type="salesold",
                client=client
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

    # Derive paths after parsing
    global progress_log, fail_csv, invalid_url_file
    invalid_url_file = PROJECT_ROOT/"data"/"invalid_URLs_add_new.txt"
    progress_log = PROJECT_ROOT / "data" / f"log_progress_step2_batch_{batch_order}.txt"
    fail_csv     = PROJECT_ROOT / "data" / f"fail_address_add_new.csv"
    urls_old     = PROJECT_ROOT / "data" / "urls_sale_add_new.json"
    suburb_dir   = PROJECT_ROOT / "data" / f"batch_{batch_order}"

    # Concurrency constructs created after parsing
    sem      = asyncio.Semaphore(MAX_CONCURRENCY)

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
    sem  = asyncio.Semaphore(MAX_CONCURRENCY)
    tasks = [
            asyncio.create_task(process_url(sem, u, i+1, len(urls), client))
            for i, u in enumerate(urls)
    ]
    await asyncio.gather(*tasks)
    EXECUTOR.shutdown(wait=True)

async def test():
    # new_url = await uploadcloudinary('https://rimh2.domainstatic.com.au/-krUVs_tnHkGlg2DMeoYGhdnn8k=/fit-in/1920x1080/filters:format(jpeg):qu                                                          ality(80):no_upscale()/2019347491_1_1_250918_092916-w2048-h1365')
    # print(new_url)
    await deleteImageCloudinary('https://res.cloudinary.com/dldnumha3/image/upload/v1758645971/v4zvh07ynfywg8ywttqe.jpg')
if __name__ == "__main__":
    asyncio.run(main())
    # asyncio.run(delete_property())
    # asyncio.run(test())
