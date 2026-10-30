import asyncio
import json
from time import time, sleep
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from operations.property_address import Get_pro_meta, Mapping, getPriceInfo
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
import cloudinary
import re
from bson import ObjectId
import argparse
from bs4 import BeautifulSoup
from app import crud, schemas
from datetime import datetime
from pymongo.database import Database
import hashlib
import pandas as pd
from typing import List
from pymongo.errors import ExecutionTimeout, ServerSelectionTimeoutError, AutoReconnect
from motor.core import AgnosticDatabase  # Motor type hint
import os
def get_csv_files(filepath):
    csvs = []
    for subdir, dirs, files in os.walk(filepath):
        files.sort()
        for file in files:
            filepath = subdir + os.sep + file
            if filepath.endswith(".csv"):
                csvs.append(filepath)
    return csvs

PROJECT_ROOT   = Path(__file__).parent
invalid_url_file = PROJECT_ROOT/"data"/"invalid_URLs.txt"
progress_log     = PROJECT_ROOT/"data"/f"log_progress.txt"
fail_csv         = PROJECT_ROOT/"data"/f"fail_address.csv"
url_run_file     = PROJECT_ROOT/"data"/f"urls_sale.json"
from_ = 0

def convert_agent_slug(url):
    url = url.rstrip('/')

    slug = url.split('/')[-1]

    match = re.match(r'(.+)-(\d+)$', slug)
    if not match:
        return slug  

    name, num = match.groups()
    hex_id = format(int(num), 'x') 
    return f"{name}-{hex_id}"



# Allow override via env-var; defaults to 2 if not set
MAX_CONCURRENCY = 2
async def uploadcloudinary(url):
        try:
            response = cloudinary.uploader.upload(
                url,
                transformation=[
                    {"fetch_format": "auto"}, # f_auto
                    {"quality": "auto"}, # q_auto
                ]
            )
            cloudinary_url = response['secure_url']
        except:
            cloudinary_url = url
        print('Completed to upload img to cloudinary')
        return cloudinary_url
    
async def deleteImageCloudinary(url):
    try:
        public_id = url.split("/")[-1].split(".")[0]
        
        result = cloudinary.uploader.destroy(
            public_id,
            invalidate=True,
            resource_type="image"
        )
        return result
    except Exception as e:
        return {'result':'fail'}
def parse_args():
    p = argparse.ArgumentParser(description="Step 2: process sale/sold property pages")
    p.add_argument("-b", "--batch-order", type=int, default=0,
                   help="Batch index (used to pick directory data/batch_<b>)")
    return p.parse_args()
MAX_CONCURRENCY  = 1

db = MongoDatabase()
async def update_history_sale_profile(prodata, prohissaleprof_indb, historysale):
    historyProfile = {}
    prohissaleprof_in = {}
    prohissaleprof_in['id'] = prodata.id
    prodata = {
            **prodata.model_dump(),
        }
    to_save_history_profile = ['propertyType','slug','title','contactInfo','soldDateInfo','stakeHolder','description',
                               'bath', 'bed','images','features','area','agencyid', 'agentid', 'imageid']  # to save in history property profile, check schema for more information
    for history in to_save_history_profile:
        historyProfile[history] = prodata[history]  # track all renovations and new features
    historyProfile['historySale'] = [historysale]

    if prohissaleprof_indb is not None:
        prohissaleprof_indb = {
            **prohissaleprof_indb.model_dump(),
        }
        historyProfiles = prohissaleprof_indb['historyprofile'] + [historyProfile] # update profile from second sold
        prohissaleprof_in['historyprofile'] = historyProfiles
    else:
        prohissaleprof_in['historyprofile'] = [historyProfile] # this is first time update profile
    return prohissaleprof_in

async def upload_profile(prodata, historysale, mode, option):
    if option == 'sold': # only sold property, then update sale profile
        # prodata = await getattr(getattr(crud, 'pro' + option), 'update')(db, db_obj=new_prosold_indb, obj_in=prosold_data_in)  # upload sold property
        current_prohissaleprof = await crud.prohissaleprof.get_by_id(db, id=prodata.id)
        if current_prohissaleprof is not None:  # update history profile
            print("Profile Updating")
            prohissaleprof = await update_history_sale_profile(prodata, current_prohissaleprof, historysale)
            prohissaleprof['updatedAt'] = datetime.now().replace(microsecond=0)
            prohissaleprof_data_in = schemas.ProhissaleprofUpdate(**prohissaleprof)
            prohisprof = await crud.prohissaleprof.update(db, db_obj=current_prohissaleprof, obj_in=prohissaleprof_data_in)  # upload sold property
        else:  # create new one
            print("Profile Creating")
            prohissaleprof = await update_history_sale_profile(prodata, current_prohissaleprof, historysale)
            prohissaleprof_data_in = schemas.ProhissaleprofCreate(**prohissaleprof)
            prohisprof = await crud.prohissaleprof.create(db, obj_in=prohissaleprof_data_in)  # upload sold property
    else:
        prohisprof = None
    return prohisprof

async def upload_agency(prosale, agency, db: Database):
    new_agency = await crud.agency.get_by_agencyId(db, agencyId=agency['agencyId'])  # found property in mongodb
    if new_agency is not None:
        print("No document to upload")
        prosale['agencyid'] = new_agency.id
    else:
        agency_in = schemas.AgencyCreate(**agency)
        new_agency = await crud.agency.create(db, obj_in=agency_in)
        prosale['agencyid'] = new_agency.id
    return prosale

async def upload_documents(prosale, documents: List[dict], coll: str, option: str, db: AgnosticDatabase, is_new_property: bool):
    """
    Upload multiple schools/agents/images.
    Uses Motor find_one (with projection) + timeouts to avoid hangs.
    """

    # ---- Resolve schema & collection names (same as before) ----
    sche = coll.title() + "Create"
    if coll == "image" and option == "sold" and is_new_property:
        sche = coll.title() + option + "Create"   # ImageSoldCreate
        coll = coll + option                       # "imagesold"
    coll = sche.replace("Create", "")              # "Agent" | "School" | "Image" | "ImageSold"
    name = coll.lower()
    print("coll", coll)
    collection = db.get_collection(coll)

    # ---- Helper to create new docs and set IDs back on prosale ----
    async def _upload(doc_list, existing_ids):
        url_mapping = {}
        if "image" in name and is_new_property:
            for doc in doc_list:
                try:
                    old_url = doc['url']
                    new_url = await uploadcloudinary(old_url)
                    print(new_url)
                    doc['url'] = new_url
                    url_mapping[old_url] = new_url
                except:
                    continue
            if url_mapping and 'images' in prosale:
                    updated_images = []
                    for img in prosale['images']:
                        if isinstance(img, dict) and 'url' in img and img['url'] in url_mapping:
                            img_copy = img.copy()
                            img_copy['url'] = url_mapping[img['url']]
                            updated_images.append(img_copy)
                        else:
                            updated_images.append(img)
                    prosale['images'] = updated_images   
        if "agent" in name:
            for doc in doc_list:
                try:
                    slug = convert_agent_slug(doc['profileUrl'])
                    doc['slug'] = slug
                except Exception as e:
                    print(e)
                    continue
        docs_in = [getattr(schemas, sche)(**d) for d in doc_list]
        new_docs = await getattr(getattr(crud, coll.lower()), "create_many")(db, objs_in=docs_in)
        key = name.replace("sold", "") + "id"      # ImageSold -> imageid, Agent -> agentid, School -> schoolid
        prosale[key] = [nd.id for nd in new_docs] + existing_ids

    # Images: no dedupe — just upload
    if "image" in name and is_new_property:
        await _upload(documents, existing_ids=[])
        return prosale

    # ---- Agents/Schools: existence check via find_one ----
    found_ids = []
    to_upload = []

    FIND_TIMEOUT_SEC   = 15        # client-side guard
    SERVER_MAX_TIME_MS = 12_000    # server-side cap

    for d in documents:
        # Build filter
        
        if name == "agent":
            filt = {"agentId": d["agentId"]}
        elif name == "school":
            filt = {"address": d["address"], "name": d["name"], "url": d["url"]}
        else:
            # should not happen here
            filt = {"_id": None}
        try:
            # Only fetch _id; cap server & client time
            async with asyncio.timeout(FIND_TIMEOUT_SEC):
                doc = await collection.find_one(filt, {"_id": 1}, max_time_ms=SERVER_MAX_TIME_MS)

            if doc:
                found_ids.append(doc["_id"])
            else:
                to_upload.append(d)

        except (asyncio.TimeoutError, ExecutionTimeout, ServerSelectionTimeoutError, AutoReconnect) as e:
            # Optimistic policy: treat as not found so we keep moving
            print(f"[WARN] {name} find_one timeout/err for doc={d}: {e}")
            to_upload.append(d)
    # ---- Apply changes ----
    if not found_ids and is_new_property:
        await _upload(documents, existing_ids=[])

    elif not to_upload:
        prosale[name + "id"] = found_ids

    else:   
        if name != "image":
            await _upload(to_upload, existing_ids=found_ids)

    return prosale

async def scraping_data(html, collections,found_url, slug, option, data_type, db: Database, is_new_property: bool):
    pro_meta = Get_pro_meta(html, found_url, slug, data_type)
    # mapping = Mapping(pro_meta, suburb_csv, data_type, cls_emb = False, semantic = False)
    mapping = Mapping(pro_meta, data_type, cls_emb = False, semantic = False)
    final_meta = await mapping.mapping()
    if final_meta:
        prodata = final_meta['propertyforsale']
        for col in collections:
            if col == "agency":
                documents = final_meta[col]
                #prodata = asyncio.run(upload_agency(prodata, documents, db)) # for debug
                prodata = await upload_agency(prodata, documents, db)
            else:
                documents = final_meta[col+'s']
                #prodata = asyncio.run(upload_documents(prodata, documents, col, option, db)) # for debug
                prodata = await upload_documents(prodata, documents, col, option, db,is_new_property=is_new_property)
        #if new_pro is not None:
            #prodata['id'] = new_pro['_id']  # keep sold property id rather than create new id
    else:
        prodata = None
    return prodata

async def _sync_agents_and_contactinfo(pro_meta, new_pro_indb, collsale):
    """
    1) Reconcile pro_meta['agentProfiles'] with new_pro_indb.agentid:
       - pull out stale agents
       - add new agents (create if needed)
    2) Update each Agent's contact fields (email, phoneNumber, photo, profileUrl)
    3) Return a new contactInfo array for the property if it changed, else None.
    """
    agentProfiles   = pro_meta.get("agentProfiles", [])
    agents = [{k: v for k, v in agent_raw.items() if k != 'landlineNumber'} for agent_raw in agentProfiles] 
    meta_agentIds   = [p["agentId"] for p in agents]

    db_agentids     = getattr(new_pro_indb, "agentid", []) or []
    agent_docs      = await db.get_collection("Agent").find({"_id": {"$in": db_agentids}}, {"agentId": 1, "_id": 1}).to_list(length=None)
    db_agentIds     = [d["agentId"] for d in agent_docs]
    # Make a dictionary to map agentID to agentid
    agent_dict = {}
    for d in agent_docs:
        agent_dict[d["agentId"]] = d["_id"]

    if set(meta_agentIds) != set(db_agentIds):
        to_remove     = set(db_agentIds) - set(meta_agentIds)
        to_add        = set(meta_agentIds) - set(db_agentIds)
        now           = datetime.now().replace(microsecond=0)

        # 1a) Remove stale agent links
        for agentId in to_remove:
            print(f'Unlink agent {agentId}')
            # remove from property
            await collsale.update_one(
                {'_id': new_pro_indb.id},
                {'$pull': {'agentid': agent_dict[agentId]}}
            )
            # remove property from agent
            await db.get_collection("Agent").update_one(
                {'_id': agent_dict[agentId]},
                {
                    '$pull': {'propertysaleids': new_pro_indb.id},
                    '$set':  {'updatedAt': now}
                }
            )

        # 1b) Add new agents (and update/create their contact info)
        for agentId in to_add:
            print(f'Link agent {agentId}')
            # find or create Agent
            existing_doc = await db.get_collection("Agent").find_one({"agentId": agentId})            
            if existing_doc:
                agent_db_id = existing_doc["_id"]
            else:
                # create new Agent
                for agent in agentProfiles:
                    if agent['agentId']==agentId:
                        agent_new = agent
                        agent_new['slug'] = convert_agent_slug(agent_new['profileUrl'])
                agent_in    = schemas.AgentCreate(**agent_new)
                new_agent   = await crud.agent.create(db, obj_in=agent_in)
                agent_db_id = new_agent.id

            # link agent ↔ property
            await collsale.update_one(
                {'_id': new_pro_indb.id},
                {'$addToSet': {'agentid': agent_db_id}}
            )
            await db.get_collection("Agent").update_one(
                {'_id': agent_db_id},
                {
                    '$addToSet': {'propertysaleids': new_pro_indb.id},
                    '$set':      {'updatedAt': now}
                }
            )

        # 2) Check if the property's contactInfo array has changed
        new_contact = []
        for agent in agents:                    
            new_contact.append(
                {'firstName': agent['firstName'], 'lastName': agent['lastName'], 'email': agent['email'],'phoneNumber': agent['phoneNumber'], 'photo': agent['photo'] }
            )
        if new_contact != getattr(new_pro_indb, 'contactInfo', None):
            return new_contact

        return None

async def update_sale_profile(html, found_url, slug, saleInfo, new_pro_indb, data_type, collsale):
    pro_meta = Get_pro_meta(html, found_url, slug, data_type)    
    to_updates = ['status', 'pricing', 'listingOption', 'expectedPrice']
    changes = {}
    # only copy across the fields that really changed
    for field in to_updates:
        new_val = saleInfo[field]
        # get the old value (adapt this to however you access your existing record)
        old_obj = getattr(new_pro_indb, field, None)

        # convert embedded Pydantic model → dict, else leave primitives alone
        def to_dict(val):
            if hasattr(val, "model_dump"):   # Pydantic v2
                return val.model_dump(by_alias=True)
            elif hasattr(val, "dict"):         # Pydantic v1
                return val.dict(by_alias=True)
            return val
        
        old_val = to_dict(old_obj)

        if new_val != old_val:
            changes[field] = new_val
    # Update title if changes
    if pro_meta['headline']['headline'] != getattr(new_pro_indb, 'title', None):
        changes['title'] = pro_meta['headline']['headline']
    # Update description if changes
    if pro_meta['description']['description'] != getattr(new_pro_indb, 'description', None):
        changes['description'] = pro_meta['description']['description']

    # Update agent and contactInfo if changes, for now only check agentid
    # ── agent & contactInfo sync ────────────────────
    new_contact = await _sync_agents_and_contactinfo(pro_meta, new_pro_indb, collsale)
    if new_contact is not None:
        changes['contactInfo'] = new_contact

    if changes:
        # stamp it
        print(f'Update: {changes}')
        changes['updatedAt'] = datetime.now().replace(microsecond=0)
        # and only now do the database update
        updated_prosale_indb = await do_update(collsale, new_pro_indb.id, changes)
        print(f"Updated fields: {list(changes.keys())}")
    else:
        print("No fields changed; skipping update.")
    if changes:
        # stamp it
        print(f'Update: {changes}')
        changes['updatedAt'] = datetime.now().replace(microsecond=0)
        # and only now do the database update
        updated_prosale_indb = await do_update(collsale, new_pro_indb.id, changes)
        print(f"Updated fields: {list(changes.keys())}")
    else:
        print("No fields changed; skipping update.")


async def _sync_agent_ids(agent_ids, add_field=None, pull_field=None, prop_id=None):
    now = datetime.now().replace(microsecond=0)
    for aid in agent_ids:
        update = {"$set": {"updatedAt": now}}
        if add_field and prop_id:
            update["$addToSet"] = { add_field: prop_id }
        if pull_field and prop_id:
            update["$pull"]    = { pull_field: prop_id }
        await db.Agent.update_one({"_id": aid}, update)

async def upload_soldsale_profile(html, collections, found_url, slug, saleInfo, new_pro_indb, historysale, mode, createOrupdate, data_type, imageids, images,changeStatus, proid,db: Database, is_new_property: bool):
    option = saleInfo['status']
    if option == 'for-sale':
        option = 'sale'
    #prodata = asyncio.run(scraping_data(html, collections, found_url, suburb_csv, option, data_type, db)) # for debug
    print(historysale)
    prodata = await scraping_data(html, collections, found_url, slug, option, data_type, db, is_new_property)
    """
    historysale = {"soldDate": soldDate, "soldPrice": soldPrice, "agencyId": agencyId}
    saleInfo = {"expectedPrice":expectedPrice, "listingOption": listingOption, 'pricing': pricing, 'status':status, 'soldDateInfo': soldDateInfo[0]}
    """
    
    prohisprof = None
    if prodata is not None:
        prodata['historySale'] = [historysale]
        if is_new_property == False:
            print(1)
            results = await do_move_images(imageids, images,changeStatus, proid, db) 
            if results:
                prodata['imageid'] = imageids
                if images and hasattr(images[0], 'model_dump'):
                    prodata['images'] = [img.model_dump() for img in images]
                elif images and hasattr(images[0], 'dict'):  # For older Pydantic versions
                    prodata['images'] = [img.dict() for img in images]
                else:
                    prodata['images'] = images
            print(2)
        sche = 'Pro' + option + createOrupdate.title()  if option == 'sold' else 'Pro' + 'sale' + createOrupdate.title()  # sche is to update sold property
        print('sche',sche)
        if option == 'under-offer':
            option_ = 'sale'
        else:
            option_ = option

        if createOrupdate == 'update':
            prodata['updatedAt'] = datetime.now().replace(microsecond=0)
            prodata_in = getattr(schemas, sche)(**prodata)
            print('Pro updating')
            prodata = await getattr(getattr(crud, 'pro' + option_), createOrupdate)(db, db_obj=new_pro_indb,
                                                                         obj_in=prodata_in)  # upload sold property

        else:
            if new_pro_indb is not None:
                createOrupdate = 'update' # we want to keep the same property id when moving data from sale coll to sol or vice verse
                sche = 'Pro' + option + createOrupdate.title() if option == 'sold' else 'Pro' + 'sale' + createOrupdate.title()  # sche is to update sold property
                prodata['id'] = new_pro_indb.id
                prodata['expectedPrice'] = getattr(new_pro_indb, 'expectedPrice')
            prodata_in = getattr(schemas, sche)(**prodata)
            prodata = await getattr(getattr(crud, 'pro' + option_), 'create')(db, obj_in=prodata_in)  # upload property

            # If we’re uploading/updating a *sale* property:
            if option != 'sold':
                await _sync_agent_ids(
                    prodata.agentid,
                    add_field='propertysaleids',
                    pull_field='propertysoldids',
                    prop_id=prodata.id
                )

            # If we’re uploading/updating a *sold* property:
            else:
                await _sync_agent_ids(
                    prodata.agentid,
                    add_field='propertysoldids',
                    pull_field='propertysaleids',
                    prop_id=prodata.id
                )

            if option != 'sold':
                try:
                    # Append the new _id to the NewSales collection
                    await db.NewSales.insert_one({"_id": prodata.id})
                except Exception as e:
                    print("Error:",e)
                image_collection = "Image"
            else:
                image_collection = "ImageSold"
            # Update all images whose _id is in prodata['imageid']
            # await db[image_collection].update_many(
            #     {"_id": {"$in": prodata.imageid}},
            #     {"$set": {"proid": prodata.id}}
            # )
            print('Pro {} creating'.format(option))

            prohisprof = await upload_profile(prodata, historysale, mode, option)
    return prohisprof

async def do_delete(coll, key, conds):# oneOrMany can be one, many
    if conds: # do nothing if no images found
        dict_conds = {key:conds} if len(conds) == 1 else {key:{'$in':conds}}
        oneOrMany = 'one' if len(conds) == 1 else 'many'
        # n = await coll.count_documents({})
        # print("%s documents before calling delete_many()" % n)
        result = await getattr(coll, 'delete_'+oneOrMany)(dict_conds)
        # print("%s documents after" % (await coll.count_documents({})))

async def do_move_images(imageids, images,status_change, proid,db: Database):
    """
    There are two cases:
    sale2sold: move images from image sale coll to sold image coll
    sold2sale: delete images from image sold coll since re-sale property will have new images
    """
    results = []
    fromStatus = status_change.split('2')[0]
    toStatus = status_change.split('2')[1]
    if 'sale' in fromStatus:
        fromStatus = '' # since Image coll is to store sale image while ImageSold is to store sold images
    if 'sale' in toStatus:
        toStatus = ''
    for id, image in zip(imageids,images) :
        image_dict = {}
        image_dict = {
            '_id': ObjectId(id),
            'url': image.url,
            'category': image.category,
            'createdAt': datetime.now(),
            'star': image.star,
            'updatedAt': datetime.now(),
            'proid': proid
        }
        results.append(image)
        if image.category == "Living Room":
            try:
                await db.get_collection(convert_text(image.category)  + toStatus.title()).insert_one(image_dict)
                await db.get_collection(convert_text(image.category)  + fromStatus.title()).delete_one({'url':image.url})
            except:
                continue
        elif image.category == "Bath Room":

            try:
                await db.get_collection(convert_text(image.category)  + toStatus.title()).insert_one(image_dict)
                await db.get_collection(convert_text(image.category)  + fromStatus.title()).delete_one({'url':image.url})
            except:
                continue
        elif image.category == "Bed Room":

            try:
                await db.get_collection(convert_text(image.category)  + toStatus.title()).insert_one(image_dict)
                await db.get_collection(convert_text(image.category)  + fromStatus.title()).delete_one({'url':image.url})
            except:
                continue
        elif image.category == "Kitchen":

            try:
                await db.get_collection(convert_text(image.category)  + toStatus.title()).insert_one(image_dict)
                await db.get_collection(convert_text(image.category)  + fromStatus.title()).delete_one({'url':image.url})
            except:
                continue
        elif image.category == "Swimming Pool":

            try:
                await db.get_collection(convert_text(image.category)  + toStatus.title()).insert_one(image_dict)
                await db.get_collection(convert_text(image.category)  + fromStatus.title()).delete_one({'url':image.url})
            except:
                continue
        else:
            try:
                await db.get_collection("OthersImage" + toStatus.title()).insert_one(image_dict)
                
                await db.get_collection("OthersImage" + fromStatus.title()).delete_one({'url':image.url})
            except:
                continue
        print(2)
    # collimgFromStatus = db.get_collection('Image' + fromStatus.title())
    # collimgToStatus = db.get_collection('Image' + toStatus.title())
    """
    if status_change == 'sale2sold':
        foundids = []
        async for imageid in collimgToStatus.find({"_id": {'$in': imageids}}):
            foundids.append(imageid['_id'])
        to_move_imagesids = [imageid for imageid in imageids if imageid not in foundids] # only move imageids not found in sold image coll
        images = []
        async for imageid in collimgFromStatus.find({"_id": {'$in': to_move_imagesids}}):  # similar to collimgsale.find_one(...)
            images.append(imageid)
        if images:
            resutls = await collimgToStatus.insert_many(images)
        results = await do_delete(collimgFromStatus, '_id', imageids)
    """
    # results = None
    # if status_change == 'sold2sale' or status_change == 'sale2sold': # delete images since already upload images to sale/sold table during scraping data
    #     results = await do_delete(collimgFromStatus, '_id', imageids)
    return results
async def do_update(coll, id, fields_to_update):
    result = await coll.update_one({"_id": id}, {"$set": fields_to_update})
    print("updated %s document" % result.modified_count)
    new_document = await coll.find_one({"_id": id})
    return new_document

async def scrape_pro_salesold(html, collsale, collsold, new_prosale_indb, new_prosold_indb, historysale, saleInfo, found_url, slug, mode, data_type, db: Database):
    """
    new_pro is property
    historySale = {"soldDate": soldDate, "soldPrice": soldPrice, "agencyId": agencyId}
    #saleInfo = {"expectedPrice": expectedPrice, "listingOption": listingOption, 'pricing': pricing, 'status': status,
                'soldDateInfo': soldDateInfo[0]}
    """
    collections = ['agency', 'agent', 'school', 'image']

    async def do_change_status(changeStatus, imageids, images,new_pro_indb, proid,crud, is_new_property):
        prohisprof = await upload_soldsale_profile(html, collections, found_url, slug, saleInfo, new_pro_indb, historysale, mode, crud, data_type, imageids,images,changeStatus,proid,db,is_new_property)
        
        if changeStatus == 'sold2sale' or changeStatus == 'sale2sold':
            if 'sale' == changeStatus.split('2')[0]:
                coll = collsale
            elif 'sold' == changeStatus.split('2')[0]:
                coll = collsold
            result = await coll.delete_one({"_id": new_pro_indb.id})  # delete sold property
    if new_prosale_indb is None and new_prosold_indb is not None: # found in sold coll
        if saleInfo['status'] == 'sold':
            found = False
            for historysale_indb in new_prosold_indb.historySale:
                historysale_indb = {**historysale_indb.model_dump(),}
                if historysale['soldDate'] == historysale_indb['soldDate']: # sold property already in PropertySold coll
                    print("This sold property already exists in Mongodb")
                    found = True
                    break
            if not found: # from second sold
                changeStatus = 'sold2sold'
                imageids = new_prosold_indb.imageid
                images = new_prosold_indb.images
                await do_change_status(changeStatus, imageids, images,new_prosold_indb, new_prosold_indb.id,'update',False)
        else: # property re-sale
            changeStatus = 'sold2sale'
            imageids = new_prosold_indb.imageid
            images = new_prosold_indb.images
            await do_change_status(changeStatus, imageids, images,new_prosold_indb, new_prosold_indb.id,'create',True)
        # complete this flow
    if new_prosale_indb is not None and new_prosold_indb is None: # found in sale coll

        if saleInfo['status'] != 'sold':
            await update_sale_profile(html, found_url, slug, saleInfo, new_prosale_indb, data_type, collsale)
        if saleInfo['status'] == 'sold':
            changeStatus = 'sale2sold'
            imageids = new_prosale_indb.imageid
            images = new_prosale_indb.images
            await do_change_status(changeStatus, imageids, images,new_prosale_indb,new_prosale_indb.id,'create',False)
            print("Property has been changed status")
        # complete this flow

    if new_prosale_indb is None and new_prosold_indb is None: # no found in both sale and sold coll
        """
        no found sold property in both sale and sold coll then scape sold property and update property history profile
        """

        prohisprof = await upload_soldsale_profile(html, collections, found_url, slug, saleInfo, new_prosold_indb,
                                                   historysale, mode, 'create', data_type, None, None, None, None, db, True)

def convert_text(name: str) -> str:
    camel_case = "".join(word.capitalize() for word in name.split())
    return camel_case + "Image"
async def remove_sale_property(slug: str, url: str):
    # 1) find the sale‐property
    prosale = await crud.prosale.get_by_slug(db, slug=slug)
    if not prosale:
        print(f"[remove_sale_property] no PropertyForSale for url={url}")
        return

    prop_id   = prosale.id
    agent_ids = prosale.agentid    # e.g. [ObjectId(...), ...]
    images = prosale.images

    # Collect data for batch operations
    cloudinary_urls = []
    category_deletes = {
        "LivingRoomImage": [],
        "BathRoomImage": [],
        "BedRoomImage": [],
        "SwimmingPoolImage": [],
        "KitchenImage": [],
        "OthersImage": []
    }
    image_urls = []

    for image in images:
        image_urls.append(image.url)
        if 'res.cloudinary.com' in image.url:
            cloudinary_urls.append(image.url)
        
        category = convert_text(image.category)
        if category not in category_deletes:
            category = "OthersImage"
        category_deletes[category].append(image.url)

    # Batch delete from Image collection
    if image_urls:
        await db.get_collection("Image").delete_many({'url': {'$in': image_urls}})

    # Batch delete from category collections
    for cat, urls in category_deletes.items():
        if urls:
            await db.get_collection(cat).delete_many({'url': {'$in': urls}})

    # Parallel delete from Cloudinary
    if cloudinary_urls:
        tasks = [deleteImageCloudinary(u) for u in cloudinary_urls]
        await asyncio.gather(*tasks)

    # 2) delete from PropertyForSale
    await db.get_collection("PropertyForSale").delete_one({"_id": prop_id})
    print(f"[remove_sale_property] deleted PropertyForSale for url={url}")

    # 2b) log this removal in the progress log
    from datetime import datetime
    timestamp = datetime.now().replace(microsecond=0)
    with open(progress_log, 'a') as log_file:
        log_file.write(f"[{timestamp}] REMOVED slug={slug}  url={url}\n")

    # 3) unlink it from each Agent.propertysaleids
    await _sync_agent_ids(
        prosale.agentid,
        add_field=None,
        pull_field="propertysaleids",
        prop_id=prosale.id
    )

    print(f"[remove_sale_property] cleaned up {len(agent_ids)} agent(s)")

async def extract_prosale_sold_rent_lease_commercial(
    found_url: str,
    slug: str,
    html: str,
    historysale: dict,
    saleInfo: dict,
    mode: str,
    data_type: str,
):
    collsale = db.get_collection('PropertyForSale')
    collsold = db.get_collection('PropertySold')
    # Under‐sale / sold flow
    if data_type == 'salesold':
        existing_sale = await crud.prosale.get_by_slug(db, slug=slug)
        existing_sold = await crud.prosold.get_by_slug(db, slug=slug)

        await scrape_pro_salesold(
            html, collsale, collsold,
            existing_sale, existing_sold,
            historysale, saleInfo,
            found_url, slug,
            mode, data_type, db
        )
              
    elif data_type == 'rent':
        pass

    elif data_type == 'commercial':
        pass
            

EXECUTOR = ThreadPoolExecutor(max_workers=MAX_CONCURRENCY)

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

    # If still no usable result → mark as failure (do NOT treat as redirect)
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
    except Exception :
        # no pricing info -> treat as “no history”
        historysale, saleInfo = None, None

    return ("ok", slug, html, soup, historysale, saleInfo)

async def change_status_property(url):
    start = time()
    print("Start time: ", start)

    # Fetch the property from the "PropertyForSale" collection
    pro = await db.get_collection("PropertyForSale").find_one({'url': url})
    if not pro:
        print(f"Property with URL {url} not found in PropertyForSale.")
        return

    # Initialize categories for deletion and sold
    category_deletes = {
        "LivingRoomImage": [],
        "BathRoomImage": [],
        "BedRoomImage": [],
        "SwimmingPoolImage": [],
        "KitchenImage": [],
        "OthersImage": []
    }

    # Categorize images for deletion and sold
    for id, image in zip(pro["imageid"],pro['images']):
        category = convert_text(image['category'])
        if category not in category_deletes:
            category = "OthersImage"
        result = await db.get_collection(category).find_one({'_id':ObjectId(id)})
        if result:
            result = dict(result)
            sold_category = category + "Sold"
            print(sold_category)
            await db.get_collection(sold_category).insert_one(result)
            await db.get_collection(category).delete_one({'_id':ObjectId(id)})
        else:
            result = await db.get_collection("Image").find_one({'_id':ObjectId(id)})
            result = dict(result)
            sold_category = category + "Sold"
            await db.get_collection(sold_category).insert_one(result)
            await db.get_collection("Image").delete_one({'_id':ObjectId(id)})
    

    # Move the property from "PropertyForSale" to "PropertySold"
    if pro:
        pro = dict(pro)
        pro['status'] = 'sold'
        pro['updatedAt'] = datetime.now()
        await db.get_collection("PropertySold").insert_one(pro)
        await db.get_collection("PropertyForSale").delete_one({"_id": pro["_id"]})

    end = time()
    print("Elapsed time: ", end - start)
    print(f"Property {url} has been moved from sale to sold.")
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
            status, slug, html, soup, historysale, saleInfo = await _sync_crawl_prep(url, "salesold")
            print("History for sale:",historysale)
            if status == "redirect":
                # await remove_sale_property(slug, url)
                await change_status_property(url)
                return

            if status != "ok":
                # failure (flaky connection etc.) → just skip, don’t delete
                print(f"[WARN] Skipping (crawl failure) {url}")
                return

            # ── Quick validity check ──
            if not soup.find_all("h1", class_="css-hkh81z"):
                print(f"Invalid URL")
                # await remove_sale_property(slug, url)
                await change_status_property(url)
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
            status, slug, html, soup, historysale, saleInfo = await _sync_crawl_prep(url, "salesold")
            print("History for sale:",historysale)
            if status == "redirect":
                # await remove_sale_property(slug, url)
                await change_status_property(url)
                return

            if status != "ok":
                # failure (flaky connection etc.) → just skip, don’t delete
                print(f"[WARN] Skipping (crawl failure) {url}")
                return

            # ── Quick validity check ──
            if not soup.find_all("h1", class_="css-hkh81z"):
                print(f"Invalid URL")
                # await remove_sale_property(slug, url)
                await change_status_property(url)
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
            asyncio.create_task(process_url(sem, u, i+1, len(urls),client))
            for i, u in enumerate(urls)
        ]
    await asyncio.gather(*tasks)
    EXECUTOR.shutdown(wait=True)

if __name__ == "__main__":
    asyncio.run(main())
