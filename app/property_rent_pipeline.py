import uuid
from qdrant_client import AsyncQdrantClient as QdrantClient
from qdrant_client.models import Distance, PointIdsList ,VectorParams, PointStruct, Filter, FieldCondition, MatchValue, TextIndexParams, TokenizerType, PayloadFieldSchema
from qdrant_client.models import Filter, FieldCondition,  PointStruct, MatchAny
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
    CacheMode,
)
from bs4 import BeautifulSoup
from datetime import datetime
import hashlib
import pandas as pd

PROJECT_ROOT   = Path(__file__).parent
invalid_url_file = PROJECT_ROOT/"data"/"invalid_URLs.txt"
progress_log     = PROJECT_ROOT/"data"/f"log_progress.txt"
fail_csv         = PROJECT_ROOT/"data"/f"fail_address.csv"
url_run_file     = PROJECT_ROOT/"data"/f"urls_sale.json"
from_ = 0
client = QdrantClient(url=os.getenv("QDRANT_URI", "http://localhost:6333"), api_key=os.getenv("QDRANT_API") or None)
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

async def qdrant_create(point, coll: str, client: QdrantClient):
    await client.upsert(collection_name=coll, points=[point])
    return point
async def _sync_crawl_prep(url: str, data_type: str):
    raw    = url.rstrip('/').split('/')[-1]
    main   = raw[: raw.rfind('-')]
    suffix = hashlib.sha512(raw.split('-')[-1].encode()).hexdigest()[:8]
    slug   = f"{main}-{suffix}"
    print(f'slug: {slug}')


    MAX_RETRIES  = 3
    BACKOFF_SECS = [0.5, 1.5, 3.0]

    result = None
    for attempt in range(1, MAX_RETRIES + 1):
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
        
        crawler = AsyncWebCrawler(config=browser_config)
       
        try:
            crawler_config = CrawlerRunConfig(
                cache_mode=CacheMode.DISABLED,
                keep_attrs=["id", "class"],
                keep_data_attributes=True,
                delay_before_return_html=1,
            )
            await crawler.start()
            result = await crawler.arun(url=url,config=crawler_config)
            await crawler.close()
            if result and getattr(result, "success", False) and getattr(result, "html", None):
                break
            else:
                print(f"[WARN] Attempt {attempt}/{MAX_RETRIES} returned unusable result for {url}")
        except Exception as e:
            print(f"[ERROR] Attempt {attempt}/{MAX_RETRIES} failed for {url}: {e}")

        if attempt < MAX_RETRIES:
            sleep(BACKOFF_SECS[attempt - 1])  # <-- sync sleep
    if not result or not getattr(result, "success", False) or not getattr(result, "html", None):
        print(f"[WARN] No usable result for {url}; skipping.")
        # (optional) log once here; main coroutine can also log
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
        # signal redirect
        return ("redirect", slug, None, None, None, None)

    html = result.html
    soup = BeautifulSoup(html, 'html.parser')
    try:
        historysale, saleInfo = getPriceInfo(html, data_type)
    except Exception as e:
        # no pricing info -> treat as “no history”
        print(e)
        historysale, saleInfo = None, None

    return ("ok", slug, html, soup, historysale, saleInfo)        
    # html = result.html
    # soup = BeautifulSoup(html, 'html.parser')
    # pro_meta = Get_pro_meta(html, url, slug, data_type=data_type)
    # mapping = Mapping(pro_meta, "rentleased", cls_emb = False, semantic = False)
    # final_meta = await mapping.mapping()
    # propertyrent = final_meta['propertyforsale']
    # print(propertyrent)
    
async def extract_prosale_sold_rent_lease_commercial(
    found_url: str,
    slug: str,
    html: str,
    historysale: dict,
    saleInfo: dict,
    data_type: str,
):
    collrent = "PropertyForRent"
    collrented = "PropertyRented"
    existing_rent = await qdrant_find_by_slug(slug,collrent)
    existing_rented = await qdrant_find_by_slug(slug,collrented)
    if data_type == 'rentleased':
        await scrape_pro_rentleased(
            html, existing_rent, existing_rented,
            historysale, saleInfo,
            found_url, slug, data_type
        )
        
def generate_id(unique_value):
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, unique_value))
async def upload_agency(prosale, agency):
    async def qdrant_find_by_agencyid(agencyid):
        points, _ = await client.scroll(
            collection_name="Agency",
            limit=1,
            with_payload=True,
            with_vectors=False,
            scroll_filter=Filter(
                must=FieldCondition(
                    key="agencyId",
                    match=MatchValue(value=agencyid)
                )
            )
        )
        
        return points[0] if points else None
    new_agency = await qdrant_find_by_agencyid(agency['agencyId'])
    if new_agency:
        print("No document to upload")
        prosale['agencyid'] = new_agency.id
    else:
        agency_in = agency
        agency_in['createdAt'] = datetime.now()
        agency_in['updatedAt'] = datetime.now()
        point = PointStruct(
            id=str(uuid.uuid4()),
            vector={
                "emb":DUMMY_VECTOR},
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
                must=FieldCondition(
                    key="agentId",
                    match=MatchValue(value=agentid)
                )
            )
        )
        
        return points[0] if points else None
    agentid = []
    contactInfo = []
    for a in agents:
        new_agent = await qdrant_find_by_agentid(a['agentId'])
        if new_agent:
            agentid.append(new_agent.id)
            contactInfo.append(
                {
                    "firstName": a['firstName'],
                    "lastName": a['lastName'],
                    "email": a['email'],
                    "phoneNumber": a['phoneNumber'],
                    "photo": a['photo']
                }
            )
            
        else:
            slug = convert_agent_slug(a['profileUrl'])
            unique_value = a['profileUrl']
            point_id = generate_id(unique_value)
            agent_in = a
            agent_in['slug'] = slug
            agent_in['createdAt'] = datetime.now()
            agent_in['updatedAt'] = datetime.now()
            point = PointStruct(
                id=str(point_id),
                vector={
                    "emb":DUMMY_VECTOR
                },
                payload=agent_in,    
            )
            agentid.append(point_id)
            contactInfo.append(
                {
                    "firstName": a['firstName'],
                    "lastName": a['lastName'],
                    "email": a['email'],
                    "phoneNumber": a['phoneNumber'],
                    "photo": a['photo']
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
                must=FieldCondition(
                    key="name",
                    match=MatchValue(value=name)
                )
            )
        )
        
        return points[0] if points else None
    schoolid = []  
    for s in schools:
        new_school = await qdrant_find_by_name(s['name'])
        if new_school:
            schoolid.append(new_school.id)
        else:
            point_id = generate_id(s['name'])
            school_in = s
            school_in['createdAt'] = datetime.now()
            school_in['updatedAt'] = datetime.now()
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
    collImages = "ImageRented" if status == 'leased' else "ImageRent"
    async def qdrant_find_by_url(url, coll):
        points, _ = await client.scroll(
            collection_name=coll,
            limit=1,
            with_payload=True,
            with_vectors=False,
            scroll_filter=Filter(
                must=FieldCondition(
                    key="url",
                    match=MatchValue(value=url)
                )
            )
        )
        
        return points[0] if points else None
    imageid = []
    image = []
    for i in images:

        new_image = await qdrant_find_by_url(i['url'], collImages)
        if new_image:
            imageid.append(new_image.id)
            image.append({
                'url': new_image.payload['url'],
                'category': new_image.payload['category'],
                'star': new_image.payload['star']
            })
        else:
            image_in = i
            image_in['createdAt'] = datetime.now()
            image_in['updatedAt'] = datetime.now()
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
                'category': image_in['category'],
                'star': image_in['star'],
                'proid': new_pro_idb_id
            })
    prosale['imageid'] = imageid
    prosale['images'] = image
    
    return prosale
async def scraping_data(html, found_url, slug, collections, data_type, status, new_pro_idb_id):
    pro_meta = Get_pro_meta(html, found_url, slug, data_type)
    mapping = Mapping(pro_meta, data_type, cls_emb=False, semantic=False)
    final_meta = await mapping.mapping()
    if final_meta:
        prodata = final_meta['propertyforsale']
        for col in collections:
            if col == 'agency':
                documents = final_meta[col]
                prodata = await upload_agency(prodata, documents)
            
            elif col == 'agent':
                documents = final_meta[col + 's']
                prodata = await upload_agents(prodata, documents)
            
            elif col == 'school':
                documents = final_meta[col + 's']
                prodata = await upload_schools(prodata, documents)
            elif col == 'image':
                documents = final_meta[col + 's']
                prodata = await upload_images(prodata, documents, status, new_pro_idb_id)

    return prodata

async def deleteImage(imageid, images, coll):
    status = gcs.delete_images(images)
    
    await client.delete(
        collection_name=coll,
        points_selector=[imageid]
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
                must=FieldCondition(
                    key="url",
                    match=MatchValue(value=url)
                )
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
            image_payload['updatedAt'] = datetime.now()
            
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
async def update_profile(html, collections, imageid, images, new_pro_idb, historysale, saleInfo, found_url, slug, changeStatus, status):
    prodata = await scraping_data(html, found_url, slug, collections, 'rentleased', status, new_pro_idb.id)
    prodata['updated'] = datetime.now()
    
    if changeStatus == 'rented2rented':
        await client.set_payload(
            collection_name="PropertyRented",
            payload=prodata,
            points=[new_pro_idb.id]
        )
    elif changeStatus == 'rent2rent':
        await client.set_payload(
            collection_name="PropertyForRent",
            payload=prodata,
            points=[new_pro_idb.id]
        )
    elif changeStatus == 'rent2rented':
        try:
            await deleteImage(imageid, images, "ImageRent")
        except:
            print()
        point = PointStruct(
                    id = new_pro_idb.id,
                    vector = {
                                "embSemantic": new_pro_idb.vector['embSemantic'],
                                "embSemanticNomicTextV1": new_pro_idb.vector['embSemanticNomicTextV1']
                             },
                    payload=prodata
                )
        
        t = await qdrant_create(point, "PropertyRented", client)
        await client.delete(collection_name="PropertyForRent", points_selector=[new_pro_idb.id])
    elif changeStatus == 'rented2rent':
        print(prodata)
        try:
            await deleteImage(imageid, images, "ImageRented")
        except:
            print()
        point = PointStruct(
                    id = new_pro_idb.id,
                    vector = {
                                "embSemantic": new_pro_idb.vector['embSemantic'],
                                "embSemanticNomicTextV1": new_pro_idb.vector['embSemanticNomicTextV1']
                             },
                    payload=prodata
                )
        t = await qdrant_create(point, "PropertyForRent", client)
        await client.delete(collection_name="PropertyRented", points_selector=[new_pro_idb.id])
async def upload_rentleased_profile(html, found_url, slug, collections, data_type, historysale, saleInfo,status):
    _id=str(uuid.uuid4())
    his = []
    his.append(historysale)
    prodata = await scraping_data(html, found_url, slug, collections, 'rentleased', status, _id) 
    prodata['createdAt'] = datetime.now() 
    prodata['updatedAt'] = datetime.now()
    prodata['slug'] = slug
    prodata['historySale'] = historysale
    prodata['url'] = found_url
    
    point = PointStruct(
        id=_id,
        vector={
            "embSemanticNomicTextV1": [0] * 768,
            "embSemantic": [0] * 512
        },
        payload=prodata
    )      
    t = await qdrant_create(point, "PropertyRented", client) if status == 'leased' else await qdrant_create(point, "PropertyForRent", client)
    return t

async def scrape_pro_rentleased(html, new_prorent_idb, new_prorented_idb, historysale, saleInfo, found_url, slug, data_type):
    collections = ['agency', 'agent', 'school', 'image']
    if new_prorent_idb is None and new_prorented_idb is not None:
        if saleInfo['status'] == 'leased':
            changeStatus = 'rented2rented'
            imageid = new_prorented_idb.payload['imageid']
            images = new_prorented_idb.payload['images']
            await update_profile(html, collections, imageid, images, new_prorented_idb, historysale, saleInfo, found_url, slug, changeStatus, saleInfo['status'])
        else:
            changeStatus = 'rented2rent'
            imageid = new_prorented_idb.payload['imageid']
            images = new_prorented_idb.payload['images']
            await update_profile(html, collections, imageid, images, new_prorented_idb, historysale, saleInfo, found_url, slug, changeStatus, saleInfo['status'])
    if new_prorent_idb is not None and new_prorented_idb is None:
        if saleInfo['status'] == 'for-rent':
            changeStatus = 'rent2rent'
            imageid = new_prorent_idb.payload['imageid']
            images = new_prorent_idb.payload['images']
            await update_profile(html, collections, imageid, images, new_prorent_idb, historysale, saleInfo, found_url, slug, changeStatus, saleInfo['status'])
        else:
            changeStatus = 'rent2rented'
            imageid = new_prorent_idb.payload['imageid']
            images = new_prorent_idb.payload['images']
            await update_profile(html, collections, imageid, images, new_prorent_idb, historysale, saleInfo, found_url, slug, changeStatus, saleInfo['status'])
    if new_prorent_idb is None and new_prorented_idb is None:
        prohis = await upload_rentleased_profile(html, found_url, slug, collections, data_type, historysale, saleInfo, saleInfo['status'])
    
    
async def change_status_property(slug):
    prosale = await qdrant_find_by_slug(slug=slug, coll="PropertyForRent")

    if not prosale:
        return

    payload = prosale.payload
    imageid = payload.get("imageid", [])
    images = payload.get("images", [])

    try:
        transferred = await transfer_images_between_collections(
            images, "ImageRent", "ImageRented"
        )

        if transferred:
            payload["imageid"] = [t["new_id"] for t in transferred]
            payload["updatedAt"] = datetime.now()

            print(f"Transferred {len(transferred)} images")
    except Exception as e:
        print(f"Error transferring images: {e}")


    try:
        await deleteImage(imageid, images, "ImageRent")
        print(f"Deleted {len(imageid)} images")
    except Exception as e:
        print(f"Error deleting images: {e}")

    payload["status"] = "leased"
    payload["listingOption"] = "leased"
    payload["published"] = False
    payload["updatedAt"] = datetime.now()
    payload["historySale"] = {
        "leasedDate": None,
        "leasedPrice": None,
        "agencyId": None,
    }

    try:
        point = PointStruct(
            id=prosale.id,
            vector=prosale.vector,
            payload=payload
        )

        await client.upsert(
            collection_name="PropertyRented",
            points=[point]
        )

        print("✓ Inserted into PropertyRented")
    except Exception as e:
        print(f"✗ Error inserting PropertyRented: {e}")
        return  

    try:
        await client.delete(
            collection_name="PropertyForRent",
            points_selector=PointIdsList(
                points=[prosale.id]
            )
        )
        print("✓ Deleted from PropertyForRent")
    except Exception as e:
        print(f"Error deleting PropertyForRent: {e}")
         
    
async def process_url(sem: asyncio.Semaphore, url: str, idx: int, total: int):
    async with sem:
        label = f"[{idx}/{total}]"
        start = time()
        print(f"START {label} {url}")
        with open(progress_log, "a",encoding='utf-8') as f:
            f.write(f"START {label} {url}\n")

        try:
            loop = asyncio.get_running_loop()


              
            status, slug, html, soup, historysale, saleInfo = await _sync_crawl_prep(url, "rentleased")

            if status == "redirect":
                # await remove_sale_property(slug, url)
                await change_status_property(slug)
                return

            if status != "ok":
                print(f"[WARN] Skipping (crawl failure) {url}")
                return
            # ── Quick validity check ──
            if not soup.find_all("h1", class_="css-jgr3ou"):
                print(f"Invalid URL")
                # await remove_sale_property(slug, url)
                await change_status_property(slug)
                return

            # ── Business logic ──
            await extract_prosale_sold_rent_lease_commercial(
                found_url=url,
                slug=slug,
                html=html,
                historysale=historysale,
                saleInfo=saleInfo,
                data_type="rentleased",
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
    urls = json.load(open(url_run_file))
    urls = urls[from_:]
    sem  = asyncio.Semaphore(MAX_CONCURRENCY)

    tasks = [
            asyncio.create_task(process_url(sem, u, i+1, len(urls)))
            for i, u in enumerate(urls)
        ]
    await asyncio.gather(*tasks)
    EXECUTOR.shutdown(wait=True)
asyncio.run(main())