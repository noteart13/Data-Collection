import os
from dateutil.parser import parse
os.environ["TOKENIZERS_PARALLELISM"] = "false"
import json
from pathlib import Path
import warnings
import numpy as np
import requests
from bs4 import BeautifulSoup
import uuid
from dotenv import load_dotenv
from qdrant_client import AsyncQdrantClient as QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue

load_dotenv()
QDRANT_URL = os.getenv("QDRANT_URI", "http://localhost:6333")
QDRANT_KEY = (os.getenv("QDRANT_SCRAPE_API") or os.getenv("QDRANT_API", "")).strip('"').strip("'") or None
client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_KEY)

from app.utils import get_json_files, get_all_suburb_city, cal_expected_price, is_valid_date
PROJECT_ROOT = Path(__file__).parent
from operations.storage import gcs
import re

_map_candidates = [
    PROJECT_ROOT.parent / "data" / "suburb_city_mapping_v1.json",
    PROJECT_ROOT.parent.parent / "suburb_city_mapping_v1.json",
    PROJECT_ROOT.parent / "utils" / "suburb_city_mapping_v1_NSW_ACT.json",
]
mapping_suburb = next((p for p in _map_candidates if p.exists()), _map_candidates[0])
lookup = {}
if mapping_suburb.exists():
    try:
        with open(mapping_suburb, 'r', encoding='utf-8') as f:
            data = json.load(f)
        lookup = {
            (item["state"].lower(), item["suburb"].lower()): item.get("city", item.get("suburb", ""))
            for item in data if "state" in item and "suburb" in item
        }
    except Exception as e:
        warnings.warn(f"Failed to load suburb mapping: {e}")
from operations.scraping_by_suburb import search_url, filter_sale_urls
import re
from datetime import datetime
async def qdrant_find_one(key: str, value: str, coll: str, client: QdrantClient):
    filtered_points, _ = await client.scroll(
        collection_name=coll,
        limit=1,
        with_payload=True,
        with_vectors=False,  
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
def datetime_now_sec():
    return datetime.now().replace(microsecond=0)


warnings.filterwarnings("ignore", message='Field "model_name" has conflict with protected namespace "model_".')

def validate_email(email):
        """
        Validates the email address by ensuring the domain (after the '@') contains a period.
        
        Parameters:
            email (str): The email address to validate.
        
        Returns:
            str or None: Returns the original email if valid, otherwise returns None.
        """
        # Ensure the input is a string and contains an '@'
        if not isinstance(email, str) or '@' not in email:
            return None

        try:
            # Split the email into local and domain parts
            local_part, domain_part = email.split('@', 1)
        except ValueError:
            # If splitting fails, the email is not valid
            return None

        # Check if the domain part contains at least one period
        if '.' not in domain_part:
            return None

        # Email passes the basic validation check
        return email

class PropertyMeta:
    '''
    this class is to extract all metadata from html for one property address
    '''

    def __init__(self, html, status):
        self.html = html
        self.status = status
    def property_meta(self):
        if self.status == 'salesold':
            key = '{"saleMethod":'
            property_meta = self.extract_meta(key)
        elif self.status == 'rentleased':
            key = '{"rentalMethod":'
            property_meta = self.extract_meta(key)
        return property_meta
    def agency(self):
        key = '{"branding":'
        agency_json = self.extract_meta(key)
        return agency_json
    def agents(self):
        key = '[{"fullName":'
        agents_json = self.extract_meta(key)
        return agents_json
    def schools(self):
        key = '{"schools":'
        schools = self.extract_meta(key)
        if schools:
            schools = schools['schools']
            return {'schools':schools}
        else:
            return {'schools':schools}
    def displayAddress(self):
        key = '{"displayAddress":'
        displayAddress_json = self.extract_meta(key)
        return displayAddress_json
    def structuredFeatures(self):
        key = '"structuredFeatures":'
        structuredFeatures = self.extract_meta(key)
        return structuredFeatures
    def propertyType(self):
        return {"propertyType": self.extract_str('"propertyType":')}
    def headline(self):
        return {"headline": self.extract_str('"headline":')}
    def propertyProfileUrlSlug(self):
        return {"slug": self.extract_str('"propertyProfileUrlSlug":')}
    def description(self):
        return {"description": self.extract_str('"},"description":')}
    def extract_str(self, key):
        try:
            srt_pos = self.html.find(key)
            rm = self.html[0:srt_pos + len(key)]
            meta_json = self.html.replace(rm, '')
            end_pos = meta_json[1:].find('",')  # ignore '"'
            str = meta_json[1:end_pos + 1]  # extract only text and ignore '"' at the begin and at the end
        except:
            str = ''
        return str

    def extract_meta(self, key):
        '''
           idea: to extract json or array format ,e.g., {} or []
           find first close bracket } or ] at lst_open_pos then find last { or [ from schools[1:lst_open_pos]
           if unable to find last { or [ then complete algorithm
           replace { and } or [ and ] by # then repeat procedure
        '''
        try:
            if '{' == key[0]:
                open = '{'
                close = '}'
            elif '[' == key[0] or '"structuredFeatures":' in key:
                open = '['
                close = ']'
            srt_pos = self.html.find(key)
            if '"structuredFeatures":' in key:
                rm = self.html[0:srt_pos+len('"structuredFeatures":')]
            else:
                rm = self.html[0:srt_pos]
            meta_json = self.html.replace(rm, '')
            temp = meta_json
            fst_close_pos = temp[1:].find(close)
            sub_temp = temp[1:fst_close_pos]
            lst_open_pos = sub_temp.rfind(open)
            while lst_open_pos > 0:  # while still found
                temp = temp.replace(close, "#", 1)
                new_sub_temp = sub_temp[:lst_open_pos] + "#" + sub_temp[lst_open_pos + 1:]
                temp = temp.replace(sub_temp, new_sub_temp, 1)
                fst_close_pos = temp[1:].find(close)
                lst_open_pos = temp[1:fst_close_pos].rfind(open)
                sub_temp = temp[1:fst_close_pos]
            end_pos = fst_close_pos
            l = len(meta_json)
            meta_json = meta_json.replace(meta_json[end_pos + 2:l], '')  # remove all text after last }
            meta_json = json.loads(meta_json)
        except:
            if '{' == key[0]:
                meta_json = {}
            elif '[' == key[0] or '"structuredFeatures":' in key:
                meta_json = []
        return meta_json

    
    def contactInfo(self, agent):
        fullName = agent['fullName'].split(' ')
        firstName = fullName[0]
        lastName = fullName[1]

        email = agent['email']
        def clean_email(email: str) -> str | None:
            # Step 1: If full name + email, keep only the email part
            if ' ' in email:
                email = email.split()[-1]
            # Step 2: Remove trailing noise like '-1' after the domain
            match = re.match(r'^(.+@[^@\s]+?)(-[0-9]+)?$', email)
            if match:
                email = match.group(1)
            # Step 2.5: Remove '.' immediately before '@'
            email = re.sub(r'\.(?=@)', '', email)
            # Step 3: Validate using Pydantic
            if not validate_email(email):
                email = None
            return email
        email = clean_email(email)

        phoneNumber = agent['mobileNumber']
        landlineNumber = agent['landlineNumber']
        isActiveProfilePage = agent['isActiveProfilePage']
        profileUrl = agent['profileUrl']
        photo = agent['photo']['url']
        agentId = agent['agentId']
        contactInfor = {"firstName": firstName, "lastName": lastName,
                        "email": email, "phoneNumber": phoneNumber, 'landlineNumber':landlineNumber,
                        'isActiveProfilePage': isActiveProfilePage,
                        "profileUrl": profileUrl, "photo": photo, 'agentId': agentId}
        return contactInfor

    def agentProfiles(self, agents):
        try:
            if isinstance(agents, dict):
                contactInfo = self.contactInfo(agents)
            elif isinstance(agents, list):
                contactInfo = [self.contactInfo(agent) for agent in agents]
        except:
            contactInfo = []
        return contactInfo

class ProRentMeta(object):
    '''
    HtmlTable
    '''
    def __init__(self, html):
        '''
        Constructor
        '''
        self.html = html
        self.soup = BeautifulSoup(self.html, "html.parser")
        self.data = lookup

    def getText(self, tags):
        results = []
        for tag in tags:
            results.append(tag.text)
        return results
    def get_city(self,state_input, suburb_input, lookup_dict):
        key = (state_input.lower(), suburb_input.lower())
        return lookup_dict.get(key)
    def strings(self, key, strings):
        pos = strings.find(key)
        srt_st_pos = strings[pos + len(key):].find('"')
        temp = strings[srt_st_pos+1:]
        end_st_pos = temp.find('"')
        return strings[srt_st_pos:end_st_pos]
    
    def split_full_name(self,full_name):
        if not full_name:
            return None, None
        name_parts = full_name.strip().split()
        return name_parts[0], " ".join(name_parts[1:]) if len(name_parts) > 1 else None
    def agents(self):
        key = '{"fullName":'
        agents_json = self.extract_metas(key=key)
        agents = []
        for agent in agents_json:
            firstname, lastname = self.split_full_name(agent.get('fullName',''))
            agents.append(
                {
                    'agentId'               :agent.get('agentId',''),
                    'email'                 :agent.get('email',''),
                    'firstname'             :firstname,
                    'isActiveProfilePage'   :True,
                    'lastname'              :lastname,
                    'phonenumber'           :agent.get('mobileNumber',''),
                    'photo'                 :agent['photo'].get('url',''),
                    'profileUrl'            :agent.get('profileUrl','') 
                }
            )
        return agents
    def coordinates(self):
        key = '{"latitude":'
        coordinates = self.extract_metas(key=key)
        if len(coordinates) == 1:
            return coordinates[0]
        return coordinates
    def images(self):
        key = '{"rentalMethod"'
        rent_method = self.extract_metas(key=key)
        return rent_method[0]['images']
    def agency(self):
        key = '{"branding"'
        agency_json = self.extract_metas(key=key)
        if len(agency_json) == 1:
            return {
                'agencyId'      :agency_json[0].get('agencyId',''),
                'banner'        :agency_json[0]['branding']['banner'].get('url',''),
                'contactDetails':agency_json[0]['contactDetails']['general'].get('phone',''),
                'isArchived'    :agency_json[0].get('isArchived',''),
                'logo'          :agency_json[0]['branding']['logo'].get('url',''),
                'logosmall'     :agency_json[0]['branding']['logoSmall'].get('url',''),
                'name'          :agency_json[0].get('name',''),
                'profileUrl'    :agency_json[0].get('profileUrl',''),
                'website'       :agency_json[0].get('website','') 
            }
        else:
            return [{
                'agencyId'      :agency.get('agencyId',''),
                'banner'        :agency['branding']['banner'].get('url',''),
                'contactDetails':agency['contactDetails']['general'].get('phone',''),
                'isArchived'    :agency.get('isArchived',''),
                'logo'          :agency['branding']['logo'].get('url',''),
                'logosmall'     :agency['branding']['logoSmall'].get('url',''),
                'name'          :agency.get('name',''),
                'profileUrl'    :agency.get('profileUrl',''),
                'website'       :agency.get('website','') 
            }
                for agency in agency_json
                    ]
    def schools(self):
        key = '{"id":'
        schools = self.extract_metas(key=key)
        filtered_schools = []
        

        for school in schools:

            if not isinstance(school, dict):
                continue

            if 'name' in school and any([
                'educationLevel' in school,
                'type' in school, 
                'state' in school,
                'distance' in school
            ]):
                clean_school = {
                    "id": school.get('id', ''),
                    "educationLevel": school.get('educationLevel', ''),
                    "name": school.get('name', ''),
                    "distance": school.get('distance', 0),
                    "state": school.get('state', ''),
                    "postCode": school.get('postCode', ''),
                    "year": school.get('year', ''),
                    "gender": school.get('gender', ''),
                    "type": school.get('type', ''),
                    "address": school.get('address', ''),
                    "url": school.get('url', ''),
                    "domainSeoUrlSlug": school.get('domainSeoUrlSlug', ''),
                    "status": school.get('status', 'Open')
                }
                filtered_schools.append(clean_school)
        
        return filtered_schools
    def get_features(self):
        key = '{"name'
        features = self.extract_metas(key=key)
        indoor_features_set = set()
        outdoor_amenities_set = set()
        for feature in features:

            if not isinstance(feature, dict) or 'category' not in feature or 'name' not in feature:
                continue
                

            if feature.get('category') == 'Indoor':
                indoor_features_set.add(feature['name'])
            elif feature.get('category') == 'Outdoor':
                outdoor_amenities_set.add(feature['name'])
        

        indoor_features = list(indoor_features_set) if indoor_features_set else ['None']
        outdoor_amenities = list(outdoor_amenities_set) if outdoor_amenities_set else ['None']
        
        return indoor_features, outdoor_amenities
    def pro_meta(self):
        def split_and_format(text):
            words = text.upper().split('_')
            return ' '.join(word.capitalize() for word in words)
        pro_meta = {}
        status = self.soup.find('span', class_='css-4ux2mv')
        if status:
            if status.text.strip() == "Listing leased by advertiser":
                status = "rented"
            else:
                status = "for-rent"
        else:
            status = "for-rent"
        title = self.soup.find_all('div', class_="css-1tpe8dy")  # these are right bellow for sale
        title = self.getText(title)
        address = title[0]
        street = ' '.join(filter(None, address.split(' ')))
        sub_state_postcode = street.split(' ')[-3:]
        suburb = sub_state_postcode[0]
        state = sub_state_postcode[1]
        postcode = sub_state_postcode[2]
        city = self.get_city(state_input=state,suburb_input=suburb,lookup_dict=self.data)
        area = self.extract_metas(key='{"size')
        
        agents = self.agents()
        coordinates = self.coordinates()
        text = self.extract_metas(key='{"project":')
        totalarea = text[0].get('landAreaSqm','')
        priceOptions = self.getText(self.soup.find_all('div',class_='css-twgrok'))
        indoorFeatures, outdoorAmenities = self.get_features()
        pro_meta['area'] = {"totalArea": totalarea, "unit": "sqM"}
        pro_meta['bed'], pro_meta['bath'] = text[0].get('bedrooms',''), text[0].get('bathrooms','')
        pro_meta['constructionYear'] = 'N/A'
        pro_meta['city'] = city
        pro_meta['contractInfo'] = [
            {
                
                "email": agent["email"],
                "firstName": agent["firstname"],
                "lastName": agent["lastname"],
                "photo": agent["photo"],
                "startDate": None,
                "status": "current",
            }
            for agent in agents
        ]
        pro_meta['coordinates'] = {
            'lat' : coordinates.get('latitude',''),
            'lng' : coordinates.get('longitude','')
        }
        pro_meta['description'] = text[0].get('description','')
        pro_meta['rentPrice'] = self.extract_rent_price(priceOptions[0])
        pro_meta['features'] = {
            "appliances": [], "basement": None, "buildingAmenities": [],
            "coolingTypes": [], "displayAddress": "fullAddress", "floorCovering": [],
            "floorNo": None, "garage": text[0].get('carspaces',''), "heatingFuels": [], "heatingTypes": [],
            "indoorFeatures": indoorFeatures, "outdoorAmenities": outdoorAmenities, "parking": ["Carport"],
            "roof": [], "rooms": [], "view": []
        }
        images = self.images()
        pro_meta['images'] = [
            {
                'category':'Kitchen',
                'star':False,
                'url': image
            }
            for image in images
        ]
        pro_meta['listingOption'] = text[0].get('listingMode','')
        pro_meta['postcode'] = postcode
        pro_meta['pricing'] = {
            "authority": status,
            "priceIncludes": text[0].get('priceIncludes',[]),
            "pricingOptions": priceOptions[0],
        }
        pro_meta['propertyType'] = split_and_format(text[0].get('propertyTypes','')[0])
        if self.extract_metas(key='{"listingSlug') == []:
            slug = self.extract_metas(key='{"topspot')[0]['listingSlug']
        else:
            slug = self.extract_metas(key='{"listingSlug')[0]['listingSlug']
        pro_meta['slug'] = slug
        pro_meta['state'] = state
        pro_meta['status'] = status
        pro_meta['street'] = street
        pro_meta['suburb'] = suburb
        pro_meta['title'] = title[0]
        return pro_meta
    def find_all_keys(self, key, html):
        import re
        srt_poss = []
        for match in re.finditer(key, html):
            print(match.start(), match.end())
            srt_poss.append(match.start())
        return srt_poss
    def extract_meta(self, srt_pos, key, open, close):
        if '"structuredFeatures":' in key:
            rm = self.html[0:srt_pos + len('"structuredFeatures":')]
        else:
            rm = self.html[0:srt_pos]
        meta_json = self.html.replace(rm, '')
        temp = meta_json
        fst_close_pos = temp[1:].find(close)
        sub_temp = temp[1:fst_close_pos]
        lst_open_pos = sub_temp.rfind(open)
        while lst_open_pos > 0:  # while still found
            temp = temp.replace(close, "#", 1)
            new_sub_temp = sub_temp[:lst_open_pos] + "#" + sub_temp[lst_open_pos + 1:]
            temp = temp.replace(sub_temp, new_sub_temp, 1)
            fst_close_pos = temp[1:].find(close)
            lst_open_pos = temp[1:fst_close_pos].rfind(open)
            sub_temp = temp[1:fst_close_pos]
        end_pos = fst_close_pos
        l = len(meta_json)
        meta_json = meta_json.replace(meta_json[end_pos + 2:l], '')  # remove all text after last }
        if '<img src' in meta_json:
            https_pos = self.find_all_keys('"', meta_json)
            if https_pos:
                meta_json = meta_json[https_pos[0]+1:https_pos[1]]
            return meta_json
        else:
            return json.loads(meta_json)
    
    def extract_metas(self, key):
        """
           idea: to extract json or array format ,e.g., {} or []
           find first close bracket } or ] at lst_open_pos then find last { or [ from schools[1:lst_open_pos]
           if unable to find last { or [ then complete algorithm
           replace { and } or [ and ] by # then repeat procedure
        """

        try:
            meta_jsons = []
            open = '<'
            close = '>'
            if '{' == key[0]:
                open = '{'
                close = '}'
            elif '[' == key[0] or '"structuredFeatures":' in key:
                open = '['
                close = ']'
            elif '<' == key[0]:
                open = '<'
                close = '>'
            srt_poss = self.find_all_keys(key, self.html)
            for srt_pos in srt_poss:
                meta_json = self.extract_meta(srt_pos, key, open, close)
                meta_jsons.append(meta_json)
            return meta_jsons
        except:
            if '{' == key[0]:
                meta_jsons = [{}]
                return meta_jsons
            elif '[' == key[0] or '"structuredFeatures":' in key:
                meta_jsons = [[]]
                return meta_jsons
    def get_tables(self, header_tag: str = None) -> dict:
        """
        get all tables from my soup as a list of list of dicts
        Args:
        header_tag(str): if set search the table name from the given header tag
        Return:
            dict: the list of list of dicts for all tables
        """
        tables = {}
        self.soup.find_all("table")
        for i, table in enumerate(self.soup.find_all("table", class_="css-5a1t3x")):
            fields = []
            table_data = []
            for tr in table.find_all('tr', recursive=True):
                for th in tr.find_all('th', recursive=True):
                    fields.append(th.text)
                record = {}
                for i, td in enumerate(tr.find_all('td', recursive=True)):
                    record[th.text] = td.text
                if record:
                    table_data.append(record)
            if header_tag is not None:
                header = table.find_previous_sibling(header_tag)
                if header is not None:
                    table_name = header.text
                else:
                    table_name = f"table{i}"
            else:
                table_name = f"table{i}"
            tables[table_name] = (table_data)
        return tables
    def extract_rent_price(self,text):
        match = re.search(r'\$\s*[\d,]+(?:\.\d+)?', text)  
        if match:
            price = match.group().replace("$", "").replace(",", "").strip()  
            return float(price)  
        return None 
    
def all_pro_meta(html):
    extract_data = ProRentMeta(html=html)
    return{
        'agency': extract_data.agency(),
        'agent' : extract_data.agents(),
        'images' : extract_data.images(),
        'school': extract_data.schools(),
        'pro_meta': extract_data.pro_meta()
    }

cat_dict_rvt_anno = {0: 'Bed Room', 1: 'Bath Room', 2: 'Living Room', 3: 'Laundry', 4: 'Garage', 5: 'Garden',
                     6: 'Kitchen', 7: 'Exterior', 8: 'Deck', 9: 'Study Room', 10: 'Swimming Pool',
                     11: 'Yard', 12: 'Media Room', 13: 'Floor Plan', 14: 'Front View', 15: 'Balcony', 16: 'Top View',
                     17: 'Gym', 18: 'Formal Dining', 19: 'Dining Room'}


class AUS_MULTIMODAL_DEDUP_CLS_EMBED_V1:
    '''
    this class is to remove duplicate images, embedding, classification,
    '''

    def __init__(self, body):
        self.body = body

    def GET_IMAGE_TENSORS(self, body_model, model):
        image_tensors = []
        for image in body_model['images']:
            # classify image using Swin model
            image_b64 = image['image_bytes']['b64']
            if model == 'swin':
                image_tensor = AUS_IMAGE_MATCHING.pre_processing_image(image_b64=image_b64)
            elif model == 'clip':
                image_tensor = image_b64  # since tensor is handled insight Multimodal
            image_tensors.append(image_tensor)
        if model == 'swin':
            image_tensors = torch.cat(image_tensors, dim=0)
        return image_tensors

    def REMOVE_DUP(self, feats, body):
        cos_sim = F.cosine_similarity(feats[:, None, :], feats[None, :, :], dim=-1)  # calculated cos sim for all pairs
        cos_sim = torch.where(cos_sim > 0.95, 1.0, 0.0)  # convert cos into 1, 0 based on thres
        indices = cos_sim.nonzero()
        dup_lst = []
        for index, cor in enumerate(indices):
            if cor[0] < cor[1]:
                dup_lst.append(cor[1].item())
        if dup_lst:
            dedup_lst = [index for index in range(0, (len(body["images"]))) if index not in dup_lst]
            res_img = [body["images"][index] for index in dedup_lst]
            res_emb_norm = [feats[index].tolist() for index in dedup_lst]
            body["images"] = res_img  #
            emb_norm = res_emb_norm  # update emb list
        else:
            emb_norm = feats.tolist()
        for img, emb in zip(body["images"], emb_norm):  # add embedding to dict
            img["emb"] = emb
        return body['images']

    def cls_mat_embe(self):
        """
        Siwin is used to classify while there are such two options for image embedding as Clip, Siwin.
        Clip and ada-002 are used for text embedding
        :param modal:
        :param info:
        :return:
        """

        descriptions = [v for k, v in cat_dict_rvt_anno.items()]  # category labels
        modal = "image"
        #try:

        if modal == "text":  # only get text embedding from clip model
            model = 'openai'
            body_clip = get_payload(self.body, 'ada-002', modal)  # to be used for ada-002 embedding
            if model == 'clip':
                emb_norm = AUS_MULTIMODAL_MATCHING_CLIP.embedding(x=[body_clip["text"]], modal=modal)
            else:
            # for now since clip only handle max length of 77 so ada-002 will be used instead
                emb_norm = get_embedding(body_clip["text"], "text-embedding-ada-002")  # currently using ada-002
            self.body["text_embed"] = emb_norm
            self.body["para"] = body_clip["text"]
        elif modal == "image":  # do classification and embedding
            model = 'clip'  # clip is used for now
            body_model = get_payload(self.body, model, modal)
            y_preds = []
            # emb_norm_list = []
            image_tensors = self.GET_IMAGE_TENSORS(body_model, model)
            if model == 'swin':
                y_preds = AUS_IMAGE_CLASSIFICATION.IMAGE_CLS(image_tensors=image_tensors)
                emb = AUS_IMAGE_MATCHING.forward(x=image_tensors)
                emb_norm = emb[0]
                # emb_norm = output[0]  # output[0] is emb_norm, output[1] is emb, output[2[ is logic
            elif model == 'clip':
                # classification and feature extraction
                y_preds, emb_norm = AUS_MULTIMODAL_MATCHING_CLIP.zero_shot_cls(images=image_tensors, texts=descriptions)

            categories = [cat_dict_rvt_anno[y_pred] for y_pred in y_preds]
            emb_norm = emb_norm.tolist()
            for img, emb, cat in zip(self.body["images"], emb_norm, categories):
                img["category"] = cat  # update category
                img["emb"] = emb
            #body = self.REMOVE_DUP(emb_norm, self.body) # not nesscessary to call this function when scraping but used it when users upload images to remove dupplicate
        return self.body["images"]

# from sentence_transformers import SentenceTransformer
# Load the embedding model (https://huggingface.co/nomic-ai/nomic-embed-text-v1")
# model = SentenceTransformer("nomic-ai/nomic-embed-text-v1", trust_remote_code=True)
# Define a function to generate embeddings
def get_embedding(data):
#   Generates vector embeddings for the given data.
   embedding = model.encode(data)
   return embedding.tolist()

def embed_fields(item):
    def featureInfo(item, inoutfeatures):
        features = item['features'][inoutfeatures]
        if features is not None or not features:
            desFeatures = ' '.join([feature for feature in features])
        else:
            desFeatures = ''
        return desFeatures

    def metaInfo(item, meta): # meta can be bed, bath, floor, garage
        if meta == 'garage' or meta == 'floorno':
            count = item['features'][meta]
        else:
            count = item[meta]
        if count <= 1:
            tag = ' '+meta+', '
        else:
            tag = ' ' + meta + 's, '
        if meta == 'garage' or meta == 'floorno':
            desMeta = str(item['features'][meta]) + tag
        else:
            desMeta = str(item[meta]) + tag
        return desMeta

    emb_field = 'embSemanticNomicTextV1' # nomic-ai/nomic-embed-text-v1
    def meta_emb(item):
        description = item['description']
        propertyType = item['propertyType']
        metas = ['bed','bath','garage','floorno']
        desMeta = ''
        for meta in metas:
            desMeta = desMeta + metaInfo(item, meta)
        features = (propertyType +'with ' + desMeta + featureInfo(item, 'indoorFeatures') + ' ' +
                    featureInfo(item, 'outdoorAmenities'))
        title = item['title']
        street = item['street']
        street = ' '.join(filter(None, street.split(' ')))
        if ',' in street:
            substate = street.split(',')[1]
            substate = ' '.join(filter(None, substate.split(' ')))
            newsubstate = []
            if len(substate.split(' ')) >= 3: # add city into street if have suburb, state, postcode in substate
                newsubstate.extend(substate.split(' ')[:-2]) # suburb name
                newsubstate.extend([item['city']])
                newsubstate.extend(substate.split(' ')[-2:])
                substate = ' '.join(newsubstate)
            street = street.split(',')[0] + ', ' + substate
        description = re.sub(r'(\\r\\n)+', r'', description)
        doc = title + ', ' + features + ' at ' + street + ', ' + description
        emb_norm = get_embedding(doc)
        return emb_norm
    emb_norm = meta_emb(item)
    item[emb_field] = emb_norm
    return item

class AgentScrapping(object):
    '''
    HtmlTable
    '''
    def __init__(self, html):
        '''
        Constructor
        '''
        self.html = html
        self.soup = BeautifulSoup(html, "html.parser")

    def getText(self, tags):
        results = []
        for tag in tags:
            results.append(tag.text)
        return results
    def strings(self, key, strings):
        pos = strings.find(key)
        srt_st_pos = strings[pos + len(key):].find('"')
        temp = strings[srt_st_pos+1:]
        end_st_pos = temp.find('"')
        return strings[srt_st_pos:end_st_pos]
    def slug(self):
        key = '{"agentSlug'
        slug = self.extract_metas(key=key,html=self.html)
        return slug[0]
    def agent(self):
        key = '{"__typename":"Contact"'
        agent = self.extract_metas(key=key,html=self.html)
        if len(agent) == 1:
            return agent[0] 
        return agent
    def getRating(self):
        key = '{"__typename":"ContactRecommendations"'
        info_json = self.extract_metas(key=key,html=self.html)
        if len(info_json) == 1:
            return info_json[0]["overallStarRating"]
        return None
    def getagentSocial(self):
        company = {}
        agentInfo = {}
        pattern = r'"facebookUrl"\s*:\s*"[^"]*",\s*"twitterUrl"\s*:\s*"[^"]*",\s*"linkedInUrl"\s*:\s*[^,]*,\s*"instagramUrl"\s*:\s*[^,]*,\s*"youTubeChannelUrl"\s*:\s*[^,]*,\s*"agentVideoUrl"\s*:\s*"[^"]*",\s*"personalWebsiteUrl"\s*:\s*[^,\n]*'
        match = re.search(pattern, self.html)
        print(match)
        if match:
            json_like_str = "{" + match.group(0) + "}"
            json_like_str = json_like_str.replace("null", "null")  
            data = json.loads(json_like_str)
            keys = ["facebookUrl", "twitterUrl", "linkedInUrl", "instagramUrl", "youTubeChannelUrl"]
            for k in keys:
                company[k] = data.get(k)
            agentInfo["agentVideoUrl"] = data.get("agentVideoUrl")
            agentInfo["personalWebsiteUrl"] = data.get("personalWebsiteUrl") 
        else:
            keys = ["facebookUrl", "twitterUrl", "linkedInUrl", "instagramUrl", "youTubeChannelUrl"]
            for k in keys:
                company[k] = None
            agentInfo["agentVideoUrl"] = None
            agentInfo["personalWebsiteUrl"] = None
        element = self.soup.find("div", class_="css-1eqy3e0")
        agentInfo['profile'] = element.text
        return company, agentInfo
    def getTotalSalevalue(self):
        total_sales = None
        total_value = None
        average_value = None
        divs = self.soup.find_all("div", class_="css-m9n7ms")
        for div in divs:
            dd = div.find("dd")
            dt = div.find("dt")
            if dt and dd:
                label = dt.get_text(strip=True).lower()
                if "total sales" in label and "value" not in label:
                    total_sales = dd.get_text(strip=True)
                elif "total sales value" in label:
                    total_value = dd.get_text(strip=True)
                elif "average sales price" in label:
                    average_value = dd.get_text(strip=True)
                
        return {
                    "totalSale":total_sales,
                    "totalsalesValue":total_value,
                    "averageValue":average_value
                }
    def getSlug(self):
        key = '{"agentSlug":'
        slug = self.extract_metas(key=key,html=self.html)
        if len(slug) == 1:
            return slug[0]['agentSlug']
        return None
    def getAgencyId(self):
        match = re.search(r'"agencyId"\s*:\s*"?(?P<id>\d+)"?', self.html)
        if match:
            agency_id = match.group("id")
            return agency_id
        return None
    
    def getMemberUrls(self):
        """
        Extract all member URLs with pattern https://rebaa.com.au/members/[slug]/
        Returns a list of unique member URLs
        """
        member_urls = []
        
        # Method 1: Find all <a> tags with href containing /members/
        links = self.soup.find_all('a', href=True)
        for link in links:
            href = link['href']
            if '/members/' in href and href.startswith('https://rebaa.com.au/members/'):
                # Ensure URL ends with / if it doesn't already
                if not href.endswith('/'):
                    href += '/'
                member_urls.append(href)
        
        # Method 2: Use regex to find URLs in the HTML content
        pattern = r'https://rebaa\.com\.au/members/[^"\s/>]+/?'
        regex_urls = re.findall(pattern, self.html)
        for url in regex_urls:
            # Ensure URL ends with / if it doesn't already
            if not url.endswith('/'):
                url += '/'
            member_urls.append(url)
        
        # Remove duplicates and return sorted list
        unique_urls = list(set(member_urls))
        return sorted(unique_urls)
    
    def find_all_keys(self, key, html):
        import re
        srt_poss = []
        for match in re.finditer(key, html):
            print(match.start(), match.end())
            srt_poss.append(match.start())
        return srt_poss
    def getTitle(self):
        """
        Extract title from elements with class css-qovusy or css-1w2kflg
        Returns the first non-empty title found
        """
        title_classes = ['css-qovusy', 'css-1w2kflg']
        
        # First try: search by class directly
        for class_name in title_classes:
            elements = self.soup.find_all(class_=class_name)
            for element in elements:
                title_text = element.get_text(strip=True)
                if title_text:  # Return first non-empty title
                    return title_text
        
        # Second try: search in heading tags with these classes
        for tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            for class_name in title_classes:
                element = self.soup.find(tag, class_=class_name)
                if element:
                    title_text = element.get_text(strip=True)
                    if title_text:
                        return title_text
        
        # Third try: search by partial class match (in case of dynamic classes)
        for class_name in title_classes:
            elements = self.soup.find_all(attrs={'class': lambda x: x and class_name in ' '.join(x)})
            for element in elements:
                title_text = element.get_text(strip=True)
                if title_text:
                    return title_text
        
        return None
    def extract_meta(self, srt_pos, key, open, close):
        if '"structuredFeatures":' in key:
            rm = self.html[0:srt_pos + len('"structuredFeatures":')]
        else:
            rm = self.html[0:srt_pos]
        meta_json = self.html.replace(rm, '')
        temp = meta_json
        fst_close_pos = temp[1:].find(close)
        sub_temp = temp[1:fst_close_pos]
        lst_open_pos = sub_temp.rfind(open)
        while lst_open_pos > 0:  # while still found
            temp = temp.replace(close, "#", 1)
            new_sub_temp = sub_temp[:lst_open_pos] + "#" + sub_temp[lst_open_pos + 1:]
            temp = temp.replace(sub_temp, new_sub_temp, 1)
            fst_close_pos = temp[1:].find(close)
            lst_open_pos = temp[1:fst_close_pos].rfind(open)
            sub_temp = temp[1:fst_close_pos]
        end_pos = fst_close_pos
        l = len(meta_json)
        meta_json = meta_json.replace(meta_json[end_pos + 2:l], '')  # remove all text after last }
        if '<img src' in meta_json:
            https_pos = self.find_all_keys('"', meta_json)
            if https_pos:
                meta_json = meta_json[https_pos[0]+1:https_pos[1]]
            return meta_json
        else:
            return json.loads(meta_json)
    
    def extract_metas(self, key,html):
        """
           idea: to extract json or array format ,e.g., {} or []
           find first close bracket } or ] at lst_open_pos then find last { or [ from schools[1:lst_open_pos]
           if unable to find last { or [ then complete algorithm
           replace { and } or [ and ] by # then repeat procedure
        """

        try:
            meta_jsons = []
            open = '<'
            close = '>'
            if '{' == key[0]:
                open = '{'
                close = '}'
            elif '[' == key[0] or '"structuredFeatures":' in key:
                open = '['
                close = ']'
            elif '<' == key[0]:
                open = '<'
                close = '>'
            srt_poss = self.find_all_keys(key, html)
            for srt_pos in srt_poss:
                meta_json = self.extract_meta(srt_pos, key, open, close)
                meta_jsons.append(meta_json)
            return meta_jsons
        except:
            if '{' == key[0]:
                meta_jsons = [{}]
                return meta_jsons
            elif '[' == key[0] or '"structuredFeatures":' in key:
                meta_jsons = [[]]
                return meta_jsons
            
    def get_tables(self, header_tag: str = None) -> dict:
        """
        get all tables from my soup as a list of list of dicts
        Args:
        header_tag(str): if set search the table name from the given header tag
        Return:
            dict: the list of list of dicts for all tables
        """
        tables = {}
        self.soup.find_all("table")
        for i, table in enumerate(self.soup.find_all("table", class_="css-5a1t3x")):
            fields = []
            table_data = []
            for tr in table.find_all('tr', recursive=True):
                for th in tr.find_all('th', recursive=True):
                    fields.append(th.text)
                record = {}
                for i, td in enumerate(tr.find_all('td', recursive=True)):
                    record[th.text] = td.text
                if record:
                    table_data.append(record)
            if header_tag is not None:
                header = table.find_previous_sibling(header_tag)
                if header is not None:
                    table_name = header.text
                else:
                    table_name = f"table{i}"
            else:
                table_name = f"table{i}"
            tables[table_name] = (table_data)
        return tables
    def extract_all_data(self):
        pro_meta = {}
        rating = self.getRating()
        company,agentInfo = self.getagentSocial()
        agencyId = self.getAgencyId()
        totalSaleValue = self.getTotalSalevalue()
        slug = self.getSlug()
        title = self.getTitle() 
        pro_meta['starRating'] = rating
        pro_meta['aboutAgent'] = agentInfo
        pro_meta['salesStatistics'] = totalSaleValue
        pro_meta['slug'] = slug
        pro_meta['title'] = title 
        return pro_meta
class Mapping:
    '''
    this class is to map property meta data into right format of property for sale
    '''

    def __init__(self, pro_meta, data_type, cls_emb, semantic):
        self.pro_meta = pro_meta
        self.data_type = data_type
        self.cls_emb = cls_emb
        self.semantic = semantic
    def get_all_features(self, structuredFeatures):

        features = {}
        if isinstance(structuredFeatures, dict):
            structuredFeatures = [structuredFeatures]
        for structuredFeature in structuredFeatures:
            if 'category' in structuredFeature:
                features[structuredFeature['category']] = []
        if isinstance(structuredFeatures, list):
            for structuredFeature in structuredFeatures:
                if 'category' in structuredFeature:
                    features[structuredFeature['category']].append(structuredFeature['name'])
        return features

    def dict_sweep(self, input_dict, key):
        if isinstance(input_dict, dict):
            return {k: self.dict_sweep(v, key) for k, v in input_dict.items() if k != key}
        elif isinstance(input_dict, list):
            return [self.dict_sweep(element, key) for element in input_dict]
        else:
            return input_dict
    def remove_keys(self, input_dict, keys):
        temp = input_dict
        for rem_key in keys:
            new_dict = self.dict_sweep(temp, rem_key)
            temp = new_dict
        return new_dict
    def uploadcloudinary(self, url):
        response = cloudinary.uploader.upload(url)
        cloudinary_url = response['secure_url']
        return cloudinary_url
    def get_sub_city_state_pos(self, street):
        # street = "Lots 2, 3, 4 & 5/117 Samuel Way, Lagoon NSW 2795"
        pos = street.rfind(',')
        street = street[pos + 1:]
        street = " ".join(street.split())
        street = street.split(' ')
        suburb = " ".join(street[:-2])
        state = street[-2]
        postcode = street[-1]
        return suburb, state, postcode

    def map_city(self, suburb, state, postcode, allsuburbcity):
        city = ''
        for item in allsuburbcity:
            if item['postcode'] == postcode and item['suburb'].lower().title() == suburb and item['state'] == state:
                city = item['lga'].lower().title()
                break
        return city

    def city_mapping(self, street, allsuburbcity):
        suburb, state, postcode = self.get_sub_city_state_pos(street)
        city = self.map_city(suburb, state, postcode, allsuburbcity)
        return suburb, postcode, city, state

    async def mapping(self):
        """
        pro_meta = {'agents': [{'fullName': 'Toby Shipway', 'email': 'toby.shipway@raywhite.com', 'mobileNumber': '0413600919', 'landlineNumber': None, 'isActiveProfilePage': True,
                                'profileUrl': 'https://www.domain.com.au/real-estate-agent/toby-shipway-1952136',
                                'photo': {'url': 'https://rimh2.domainstatic.com.au/J5A8yNvwcYnJm0308bZzOgTs5Ww=/filters:format(png):quality(80):no_upscale()/https://images.domain.com.au/img/34615/contact_1952136.jpeg?date=638608995138430000', '__typename': 'Image'}, '__typename': 'Agent', 'agentId': '1952136'}, {'fullName': 'Andrew Downing', 'email': 'andrew.downing@raywhite.com', 'mobileNumber': '0404882311', 'landlineNumber': '0404 882 311', 'isActiveProfilePage': True,                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'profileUrl': 'https://www.domain.com.au/real-estate-agent/andrew-downing-1794009', 'photo': {'url': 'https://rimh2.domainstatic.com.au/RFEc8PKbSvGZFJ_SgZxZBaoC8GY=/filters:format(png):quality(80):no_upscale()/https://images.domain.com.au/img/34615/contact_1794009.jpeg?date=638608995138170000', '__typename': 'Image'}, '__typename': 'Agent', 'agentId': '1794009'}], 'agency': {'branding': {'banner': {'url': 'https://rimh2.domainstatic.com.au/YAG-3NxrBLXpW5Jw4T1xJtSW0vw=/filters:format(png):quality(80):no_upscale()/https://images.domain.com.au/img/Agencys/34615/banner_34615.png?date=638609501388217362', '__typename': 'Image'}, 'logo': {'url': 'https://rimh2.domainstatic.com.au/qGFrXIzgnnmBH_x2oIA9LwsnoGk=/filters:format(png):quality(80):no_upscale()/https://images.domain.com.au/img/Agencys/34615/logo_34615.jpg?date=133646295115415879?date=638609501388217395', '__typename': 'Image'}, 'logoSmall': {'url': 'https://rimh2.domainstatic.com.au/XkDmOKLxtnfCbtkKxUXSJbAskKw=/filters:format(png):quality(80):no_upscale()/https://images.domain.com.au/img/Agencys/34615/searchlogo_34615.jpg?date=133646295402956072?date=638609501388217390', '__typename': 'Image'}, 'backgroundColour': '#FEE536', 'logoColour': '#FEE536', '__typename': 'AgencyBranding'}, 'contactDetails': {'general': {'phone': '0404882311', '__typename': 'AgencyContactDetailsGeneral'}, '__typename': 'AgencyContactDetails'}, 'isArchived': False, 'name': 'Ray White Adelaide City', 'profileUrl': 'raywhiteadelaidecity-34615', 'website': '', '__typename': 'Agency', 'agencyId': 34615},
                    'pro_meta': {'saleMethod': 'privateTreaty', 'soldAction': 'privateTreaty',
                             'address': '25/42 Charlick Circuit, Adelaide SA 5000', 'adtype': 'premiumPlus', 'agency': 'Ray White Adelaide City', 'agencyId': 34615, 'agentNames': 'Toby Shipway, Andrew Downing', 'bathrooms': 2, 'bedrooms': 3, 'buildingsize': 135, 'daysListed': 19, 'dateListed': '2024-08-14T12:01:51.000', 'inspectionsCount': 0, 'hasPhoto': True, 'hasFloorplan': True, 'hasDisplayPrice': True, 'hasDescription': True, 'descriptionHasEmail': False, 'descriptionHasPhone': True, 'hasSOI': False, 'internalArea': 135, 'virtualTour': False, 'floorPlansCount': 1, 'images': ['https://bucket-api.domain.com.au/v1/bucket/image/2019423580_1_1_240814_023928-w5062-h3114', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_2_1_240814_020146-w4671-h3114', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_3_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_4_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_5_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_6_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_7_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_8_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_10_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_11_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_12_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_13_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_14_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_15_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_16_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_17_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_18_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_19_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_20_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_21_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_22_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_23_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_24_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_25_1_240814_020146-w5470-h3657', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_26_1_240814_020146-w3658-h2446', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_27_1_240814_020146-w5470-h3657', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_28_1_240814_020146-w6016-h4016', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_29_1_240814_020146-w6016-h4016', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_30_1_240814_020146-w6016-h4016', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_31_1_240814_020146-w6016-h4016', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_32_1_240814_020146-w5157-h3448', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_33_1_240814_020146-w5276-h3528', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_34_1_240814_020146-w3658-h2446'], 'onlineAuctionAvailable': 'None', 'parking': 2, 'photoCount': 33, 'postcode': '5000', 'price': 'UNDER OFFER 1ST WEEK - CONTACT TOBY SHIPWAY', 'primaryPropertyType': 'Apartment', 'propertyFeatures': 'Alarm System, Intercom, Built in wardrobes, Dishwasher, Secure Parking, Balcony / Deck', 'structuredFeatures': [{'name': 'Alarm System', 'category': 'Indoor', 'source': 'advertiser'}, {'name': 'Intercom', 'category': 'Indoor', 'source': 'advertiser'}, {'name': 'Built in wardrobes', 'category': 'Indoor', 'source': 'advertiser'}, {'name': 'Dishwasher', 'category': 'Indoor', 'source': 'advertiser'}, {'name': 'Secure Parking', 'category': 'Outdoor', 'source': 'advertiser'}, {'name': 'Balcony / Deck', 'category': 'Outdoor', 'source': 'advertiser'}], 'propertyId': 'MA-9462-YS', 'secondaryPropertyType': 'Apartment / Unit / Flat', 'state': 'SA', 'suburb': 'Adelaide', 'videoCount': 1, 'x-message': 'domain-propertyinfo'}, 'schools': {'schools': [{'id': '', 'educationLevel': 'secondary', 'name': 'Adelaide International School', 'distance': 499.98472678935195, 'state': 'SA', 'postCode': '5000', 'year': 'Unknown', 'gender': '', 'type': 'Private', 'address': 'Adelaide, SA 5000', 'url': 'https://ais.edu.au/', 'domainSeoUrlSlug': 'adelaide-international-school-sa-5000-52254'}, {'id': '', 'educationLevel': 'combined', 'name': 'Christian Brothers College - Junior Campus', 'distance': 553.3408379842192, 'state': 'SA', 'postCode': '5000', 'year': '', 'gender': '', 'type': 'Catholic', 'address': 'Adelaide, SA 5000', 'url': '', 'domainSeoUrlSlug': 'christian-brothers-college-junior-campus-sa-5000-50347'}, {'id': '11107', 'educationLevel': 'combined', 'name': 'Christian Brothers College', 'distance': 576.3752185563361, 'state': 'SA', 'postCode': '5000', 'year': 'R-12', 'gender': 'Boys', 'type': 'Catholic', 'address': 'Adelaide, SA 5000', 'url': 'http://www.cbc.sa.edu.au', 'domainSeoUrlSlug': 'christian-brothers-college-sa-5000-11107'}, {'id': '', 'educationLevel': 'secondary', 'name': 'Specialised Assistance School For Youth (SASY)', 'distance': 612.8194901317762, 'state': 'SA', 'postCode': '5000', 'year': '8-12', 'gender': 'CoEd', 'type': 'Private', 'address': 'Adelaide, SA 5000', 'url': 'https://sasy.sa.edu.au/', 'domainSeoUrlSlug': 'specialised-assistance-school-for-youth-sasy-sa-5000-52253'}, {'id': '', 'educationLevel': 'secondary', 'name': 'South Australian International School', 'distance': 683.4803283440215, 'state': 'SA', 'postCode': '5000', 'year': '', 'gender': '', 'type': 'Private', 'address': 'Adelaide, SA 5000', 'url': '', 'domainSeoUrlSlug': 'south-australian-international-school-sa-5000-52606'}, {'id': '', 'educationLevel': 'secondary', 'name': 'University Senior College at Adelaide University - Rundle Mall Campus', 'distance': 695.6584523787786, 'state': 'SA', 'postCode': '5000', 'year': '', 'gender': '', 'type': 'Private', 'address': 'Adelaide, SA 5000', 'url': '', 'domainSeoUrlSlug': 'university-senior-college-at-adelaide-university-rundle-mall-campus-sa-5000-52571'}, {'id': '7311', 'educationLevel': 'combined', 'name': 'Prince Alfred College', 'distance': 727.2845970305195, 'state': 'SA', 'postCode': '5067', 'year': 'R-12', 'gender': 'Boys', 'type': 'Private', 'address': 'Kent Town, SA 5067', 'url': 'http://www.pac.edu.au', 'domainSeoUrlSlug': 'prince-alfred-college-sa-5067-7311'}, {'id': '', 'educationLevel': 'secondary', 'name': 'University Senior College at Adelaide University - Gawler Place Campus', 'distance': 755.7130173016885, 'state': 'SA', 'postCode': '5000', 'year': '', 'gender': '', 'type': 'Private', 'address': 'Gawler Place, SA 5000', 'url': '', 'domainSeoUrlSlug': 'university-senior-college-at-adelaide-university-gawler-place-campus-sa-5000-52799'}, {'id': '', 'educationLevel': 'secondary', 'name': 'Adelaide Botanic High School', 'distance': 768.7103628522017, 'state': 'SA', 'postCode': '5000', 'year': '7-12', 'gender': 'CoEd', 'type': 'Government', 'address': 'Adelaide, SA 5000', 'url': 'http://www.abhs.sa.edu.au', 'domainSeoUrlSlug': 'adelaide-botanic-high-school-sa-5000-52576'}, {'id': '', 'educationLevel': 'combined', 'name': 'St Aloysius College', 'distance': 843.6733243626262, 'state': 'SA', 'postCode': '5000', 'year': 'R-12', 'gender': 'Girls', 'type': 'Catholic', 'address': 'Adelaide, SA 5000', 'url': 'https://www.sac.sa.edu.au', 'domainSeoUrlSlug': 'st-aloysius-college-sa-5000-49706'}, {'id': '7452', 'educationLevel': 'secondary', 'name': 'University Senior College at Adelaide University', 'distance': 940.4607219277208, 'state': 'SA', 'postCode': '5005', 'year': '10-12', 'gender': 'CoEd', 'type': 'Private', 'address': 'Adelaide University, SA 5005', 'url': 'http://www.usc.adelaide.edu.au', 'domainSeoUrlSlug': 'university-senior-college-sa-5000-7452'}, {'id': '7051', 'educationLevel': 'primary', 'name': 'Gilles Street Primary School', 'distance': 1321.7635383687993, 'state': 'SA', 'postCode': '5000', 'year': 'U, R-6', 'gender': 'CoEd', 'type': 'Government', 'address': 'Adelaide, SA 5000', 'url': 'http://www.gillesstps.sa.edu.au', 'domainSeoUrlSlug': 'gilles-street-primary-school-sa-5000-7051'}, {'id': '6804', 'educationLevel': 'secondary', 'name': 'Adelaide High School', 'distance': 2114.68797912481, 'state': 'SA', 'postCode': '5000', 'year': 'U, 7-12', 'gender': 'CoEd', 'type': 'Government', 'address': 'Adelaide, SA 5000', 'url': 'http://www.adelaidehs.sa.edu.au', 'domainSeoUrlSlug': 'adelaide-high-school-sa-5000-6804'}], 'numberOfVisibleSchools': 3, 'enableSchoolProfileLink': True, 'ads': {'endpoint': 'https://ads-api.domain.com.au/v2/ads/list', 'params': {'numberOfAds': 2, 'environment': 'www.domain.com.au', 'brand': 'Domain', 'platform': 'Website', 'liveRailEnabled': True, 'cat': 'sold', 'cat1': 'details', 'userType': 'sold', 'adType': 'schoolsponsorship', 'adWidth': 1, 'adHeight': 1, 'locations': [{'state': 'SA', 'region': 'Adelaide Region', 'area': 'Adelaide', 'suburb': 'Adelaide', 'postCode': '5000'}], 'deviceType': 'Desktop'}}, 'addressParts': {'streetNumber': '42', 'unitNumber': '25', 'street': 'Charlick Circuit', 'suburb': 'Adelaide', 'stateAbbreviation': 'sa', 'postcode': '5000', 'displayAddress': '25/42 Charlick Circuit, Adelaide SA 5000', 'displayType': 'fullAddress'}, 'adId': '2019423580', 'feedbackUrl': '/phoenix/api/school-data/report-error'}, 'description': {'description': "Discover urban elegance at its best with this stunning 3 bedroom apartment securely located in the architecturally acclaimed Brooker Apartments. Offering over 135 sqm of refined living space, this residence offers an exceptional 'lock up and leave' lifestyle in the highly desirable East End Precinct.\\r\\n\\r\\nLocated on the picturesque Charlick Circuit, you'll find Adelaide's finest cafes, restaurants, and bars just a short stroll away. Enjoy an array of annual events and festivals, and explore nearby attractions such as Adelaide University, Rymill Park, the Botanic Gardens, Rundle Street, Rundle Mall and much more. Zoned for Adelaide High School and Adelaide Botanic High School plus many private schools nearby, you can be rest assured your children will have the best education possible.\\r\\n\\r\\nEnter via the secure ground floor foyer and into the lift with direct access straight to your floor.  Inside the apartment welcomes you an inviting sense of space complimented with a wide entrance hallway, 9-foot ceilings and an abundance of natural light.\\r\\n\\r\\nThe open plan living and dining area is comfortable and spacious extending through to the generous terrace with a picturesque outlook over the leafy Charlick Circuit lined with charming Parisian style homes and surrounding character buildings. Such a perfect environment for both everyday living and entertaining. The centrally located kitchen is beautifully appointed with stone benchtops, extensive solid timber cabinetry and quality appliances. \\r\\n\\r\\nThe 3 bedrooms are great size, all with an outlook over Charlick Circuit. The master suite comprises of a walk-in robe with plenty of hanging space and through to the large ensuite. Bedroom 2 has built in robes and bedroom 3 with your own private balcony and floor to ceiling built-in cabinetry with shelving, perfect space for a home office. The main bathroom is certainly big enough with a separate bath, shower and large vanity, catering to both family living and guests.\\r\\n\\r\\nAdditional features include:\\r\\n\\r\\n-\\t2 allocated garaged car parks\\r\\n-\\tStorage cage  \\r\\n-\\tYear-round comfort with a geothermal ducted reverse cycle air conditioning system\\r\\n-\\tGenerous separate laundry \\r\\n-\\tVideo intercom\\r\\n-\\tSecurity system\\r\\n-\\tLinen press \\u0026 ample storage\\r\\n-\\tNew tapware in all wet areas\\r\\n-\\tNew light fittings throughout \\r\\n\\r\\nIt's hard to look past such an amazing lifestyle apartment in the tightly held East End! Apartments like this are few and far between so be quick to look through at one of the open inspections and submit your offer. \\r\\n\\r\\nFor any further information please contact Toby Shipway on 0413 600 919. \\r\\n\\r\\nAll information provided has been obtained from sources we believe to be accurate, however, we cannot guarantee the information is accurate and we accept no liability for any errors or omissions (including but not limited to a property's land size, floor plans and size, building age and condition) Interested parties should make their own inquiries and obtain their own legal advic"}}
        :param pro_meta:
        :return:
        """
        #try:
        mapped_proSale = {}
        if self.pro_meta['structuredFeatures']:
            structuredFeatures = self.pro_meta['structuredFeatures']
            features_json = self.get_all_features(structuredFeatures)
            for fea in ['Indoor','Outdoor']:
                if fea not in features_json:
                    features_json[fea] = ["None"]
        else:
            features_json = {}
            features_json['Indoor'] = ["None"]
            features_json['Outdoor'] = ["None"]
        mapped_proSale['stakeHolder'] = 'agent'
        agents_raw = self.pro_meta['agentProfiles'] # list of agent profile and used to create agent table in mongodb
        agents = self.remove_keys(agents_raw, keys=['landlineNumber'])
        contactInfo = []
        async def qdrant_get_by_agentId(agentId: str, client: QdrantClient):
            return await qdrant_find_one("agentId", agentId, "Agent", client)
        
        for agent in agents:
            photo_url = agent['photo']
            try:
                result = await qdrant_get_by_agentId(agent['agentId'],client) 
                if result:
                    agent["photo"] = result.payload.photo
                else:
                    agent["photo"] = await gcs.upload_images_to_gcs(photo_url,agent['agentId'],'properties/agent')

                
            except:
                agent["photo"] = photo_url
            contactInfo.append({'firstName': agent['firstName'], 
                                'lastName': agent['lastName'], 
                                'email': agent['email'],
                                'phoneNumber': agent['phoneNumber'], 
                                'photo':agent['photo']})
        mapped_proSale['contactInfo'] = contactInfo
        mapped_proSale['title'] = self.pro_meta['headline']['headline']
        mapped_proSale['slug'] = self.pro_meta['slug']['slug']
        # try:
        #     mapped_proSale['propertyType'] = self.pro_meta['pro_meta']['primaryPropertyType']
        # except:
        #     mapped_proSale['propertyType'] = ''
        mapped_proSale['propertyType'] = self.pro_meta.get('pro_meta', {}).get('primaryPropertyType', '')

        mapped_proSale['description'] = self.pro_meta['description']['description']
        mapped_proSale["constructionYear"] = "N/A"
        mapped_proSale["structuralRemodelYear"] = "N/A"
        mapped_proSale["architecturalStyle"] = "None"
        mapped_proSale["state"] = self.pro_meta['pro_meta']['state']
        mapped_proSale["postcode"] = int(self.pro_meta['pro_meta']['postcode'])
        # mapping city and suburb, check
        allsuburbcity = get_all_suburb_city()
        mapped_proSale["street"] = self.pro_meta['pro_meta']['address']
        suburb, postcode, city, state = self.city_mapping(self.pro_meta['pro_meta']['address'], allsuburbcity)
        assert state == self.pro_meta['pro_meta']['state']
        assert int(postcode) == int(self.pro_meta['pro_meta']['postcode'])
        mapped_proSale["suburb"] = suburb
        mapped_proSale["postcode"] = postcode
        mapped_proSale["city"] = city
        mapped_proSale["state"] = state

        mapped_proSale["coordinates"] = {}
        try:
            mapped_proSale["coordinates"]['lat'] = self.pro_meta['displayAddress']['geolocation']['latitude']
            mapped_proSale["coordinates"]['lng'] = self.pro_meta['displayAddress']['geolocation']['longitude']
        except:
            mapped_proSale["coordinates"]['lat'] = None
            mapped_proSale["coordinates"]['lng'] = None
        mapped_proSale["bed"] = self.pro_meta['pro_meta']['bedrooms']
        mapped_proSale["bath"] = self.pro_meta['pro_meta']['bathrooms']
        mapped_proSale["features"] = {"garage": self.pro_meta['pro_meta']['parking'],
                                      "floorno": self.pro_meta['pro_meta']['floorPlansCount'],
                                      "basement": "None",
                                      "floorCovering": ["None"],
                                      "parking": ["Carport"],
                                      "roof": ["Other"],
                                      "view": ["None"],
                                      "rooms": ["None"],
                                      "indoorFeatures": features_json['Indoor'],
                                      "coolingTypes": ["None"],
                                      "heatingTypes": ["None"],
                                      "heatingFuels": ["None"],
                                      "appliances": ["None"],
                                      "buildingAmenities": ["None"],
                                      "outdoorAmenities": features_json['Outdoor'],
                                      "displayAddress": "fullAddress"
                                      }
        mapped_proSale["area"] = {"totalarea": self.pro_meta['totalarea'], "unit": "sqM"}
        """
        saleInfo = {'expectedPrice': None, 'listingOption': 'sold',
         'pricing': {'authority': 'sold', 'pricingOptions': 'SOLD - Price Withheld'},
         'soldDateInfo': 'Sold by private treaty 15 Nov 2024', 'status': 'sold'}
        """
        if isinstance(self.pro_meta['saleInfo'], tuple):
            self.pro_meta['saleInfo'] = {**self.pro_meta['saleInfo'][0], **self.pro_meta['saleInfo'][1]}
            if self.data_type == 'salesold':            
                mapped_proSale["soldDateInfo"] = self.pro_meta['saleInfo']['soldDateInfo'] # to display a message" 'Sold by private treaty 15 Nov 2024', 'status': 'sold'
            elif self.data_type == 'rentleased':
                mapped_proSale["soldDateInfo"] = self.pro_meta['saleInfo']['leasedDateInfo']
        mapped_proSale['listingOption'] = self.pro_meta['saleInfo']['listingOption']
        # mapped_proSale["historySale"] = self.pro_meta['historySale']
        mapped_proSale["expectedPrice"] = self.pro_meta['saleInfo']['expectedPrice']
        mapped_proSale["status"] = self.pro_meta['saleInfo']['status']
        mapped_proSale["pricing"] = {"waterBillPeriod": "monthly", "councilBill": "",
                                     "pricingOptions": self.pro_meta['saleInfo']['pricing']["pricingOptions"],
                                     "authority": self.pro_meta['saleInfo']['pricing']['authority'],
                                     "priceIncludes": [""]}
        mapped_proSale["published"] = False
        mapped_proSale["recommended"] = False
        urls = self.pro_meta['pro_meta']['images']
        images = []

        # res = await db['PropertyForSale'].find_one({'url':self.pro_meta['url']},{'_id':1}) 
        # print(res)

        for url in urls:
            temp = {}
            size = requests.get(url, stream=True).headers['Content-length']
            try:
                
                if self.pro_meta['saleInfo']['listingOption'] == 'sold':
                    temp['url'] = await gcs.upload_images_to_gcs(url,mapped_proSale['street'],"properties/prosold")
                elif self.pro_meta['saleInfo']['listingOption'] == 'rent':
                    temp['url'] = await gcs.upload_images_to_gcs(url,mapped_proSale['street'],"properties/prorent")
                elif self.pro_meta['saleInfo']['listingOption'] == 'leased':
                    temp['url'] = await gcs.upload_images_to_gcs(url,mapped_proSale['street'],"properties/proleased")  
                else:
                    temp['url'] = await gcs.upload_images_to_gcs(url,mapped_proSale['street'],"properties/prosale")  
                
            except:
                temp['url'] = url
            # if int(size) >= 10485760:
            #     temp["url"] = url
            # else:
            #     #temp["url"] = self.uploadcloudinary(url)
            #     temp["url"] = url
            #     #temp["url"] = url
            temp['category'] = 'photo'
            temp['star'] = False
            images.append(temp)
        if self.cls_emb:
            aus_cls_mat = AUS_MULTIMODAL_DEDUP_CLS_EMBED_V1(
                {'images': images})  # labelling, embedding, and removing dubplicate images
            images = aus_cls_mat.cls_mat_embe()  # get embedding for image search
        if self.semantic:
            mapped_proSale = embed_fields(mapped_proSale)
        # for image in images:
        imagesforsale = self.remove_keys(images,
                                         keys=['emb'])  # remove embedding from images in property for sale Table
        mapped_proSale["images"] = imagesforsale
        print(mapped_proSale["images"])
        mapped_proSale["url"] = self.pro_meta['url']
        propertyforsale = self.remove_keys(mapped_proSale,keys = ['backgroundColour', '__typename', 'logoColour'])
        # more fields added: schools and agency
        # agency table
        agency = {}
        branding = self.pro_meta['agency']['branding']
        """
        agency['banner'] = self.uploadcloudinary(branding['banner']['url'])
        agency['logo'] = self.uploadcloudinary(branding['logo']['url'])
        agency['logoSmall'] = self.uploadcloudinary(branding['logoSmall']['url'])
        """
        for item in ['banner', 'logo', 'logoSmall']:
            if branding[item] is not None:
                agency[item] = branding[item]['url']
            else:
                agency[item] = ''
        agency['contactDetails'] = self.pro_meta['agency']['contactDetails']['general']['phone']
        agency['isArchived'] = self.pro_meta['agency']['isArchived']
        agency['name'] = self.pro_meta['agency']['name']
        agency['profileUrl'] = self.pro_meta['agency']['profileUrl']
        agency['website'] = self.pro_meta['agency']['website']
        agency['agencyId'] = self.pro_meta['agency']['agencyId']
        mapped_schools = self.pro_meta['schools']['schools']
        schools = self.remove_keys(mapped_schools, keys=['id'])
        prosale = {'propertyforsale': propertyforsale, 'agency': agency, 'agents': agents, 'schools': schools, 'images': images}
        #except:
        #prosale = {}
        return prosale


def remove_price(result):
    title = result['title']
    price_str = title.find("$")
    if price_str != -1:
        subtitle = title[price_str:]
        price_end = subtitle.find(" ")
        price = title[price_str:price_str+price_end]
        new_title = title.replace(price, '')
        title = new_title
        result['title'] = titlef
    return result
def find_correct_url(search_results, target_address):
    fst_results = [result for result in search_results if 'domain.com.au' in result['href']]
    results = []
    # remove incorrect link and clean title
    for result in fst_results:
        if 'domain.com.au/property-profile' not in result['href'] and 'domain.com.au/building-profile' not in result['href']:
            result['title'] = result['title'].replace('Domain.com.au','')
            result['title'] = result['title'].replace('Domain', '')
            result['title'] = result['title'].replace('|', '')
            result['title'] = result['title'].replace('-', '')
            results.append(result)
    if results:
        final_results = [remove_price(result) for result in results]
        threshold = 80
        # Perform fuzzy matching
        match = {}
        for address, href in zip([result['title'] for result in final_resutls], [result['href'] for result in final_results]):
            similarity_score = fuzz.partial_ratio(address, target_address)
            if similarity_score >= threshold:
                threshold = similarity_score
                match['title'] = address
                match['href'] = href
    else:
        final_results = []
        match = {}
    return final_results, match

def Get_html(address):
    search_url_sale = "https://www.google.com/search?q=" + address
    raw_urls = search_url(search_url_sale)
    prosale_urls = filter_sale_urls(raw_urls)
    if prosale_urls:
        found_url = prosale_urls[0]
    else:
        found_url = ''
    return found_url
import re

#get only price 
def extract_price(pricingOptions):
     # Find all price values in the string
     prices = re.findall(r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?", pricingOptions)
     # If there is only one price, return it
     if len(prices) == 1:
         return prices[0]
     # If there are two or more prices, format them as "$min - $max"
     return " - ".join(prices) if prices else ""

def getPriceInfo(html, data_type):
    if data_type == 'salesold':
        soup = BeautifulSoup(html, "html.parser")
        meta = PropertyMeta(html,data_type)
        def getText(tags):
            results = []
            for tag in tags:
                results.append(tag.text)
            return results
        price = soup.find('div', {'data-testid': 'listing-details__listing-summary-title-name'})
        if price == []:
            price = soup.find_all('span', class_="css-1w4p1vw")
        underOffer = soup.find("span", {"data-testid": "listing-details__listing-tag"})
        priceInfo = getText(price)
        # sold_date = soup.find_all('span', class_="css-h9g9i3") # check if sold date presented
        sold_date = soup.find_all('span', class_="css-2ermal")
        if sold_date:  # property sold
            soldDateInfo = getText(sold_date)
            soldDate = parse(soldDateInfo[0], fuzzy=True)
            if "$" in priceInfo[0]:
                soldPrice = int(priceInfo[0].split("$")[1].replace(",", ""))
            else:
                soldPrice = 0  # price with held
            expectedPrice = None
            pricingOptions = priceInfo[0]  #
            status = 'sold'
            listingOption = 'sold'
            authority = 'sold'
        else:  # property for sale
            soldDateInfo = ['']
            expectedPrice = None
            pricingOptions = priceInfo[0]  # price range $400,000 - $500,000 or From $500,000 or Offers Over $900,000  # price with held
            soldDate = None
            soldPrice = None
            status = 'for-sale'
            listingOption = 'sale'
            authority = 'for-sale'
            if len(priceInfo)>0:
                if "$" in priceInfo[0]:
                    # cleaned_pricingOption = ' '.join([w for w in pricingOptions.split() if not is_valid_date(w)]) # remove data time
                    cleaned_pricingOption = extract_price(pricingOptions)
                    try:
                        expectedPrice = cal_expected_price(cleaned_pricingOption)
                    except Exception as e:
                        print(e)
                        expectedPrice = None
            if underOffer: # update status
                status = 'under-offer' # 'under-offer' or under contract
                listingOption = 'under-offer'
                authority = 'under-offer'
        pricing = {'pricingOptions': pricingOptions, 'authority': authority, 'priceIncludes':['']}
        agency = meta.agency()
        agencyId = agency['agencyId']
        historysale = {"soldDate": soldDate, "soldPrice": soldPrice, "agencyId": agencyId}
        saleInfo = {"expectedPrice":expectedPrice, "listingOption": listingOption, 'pricing': pricing, 'status':status, 'soldDateInfo': soldDateInfo[0]}

    if data_type == 'rentleased':
        soup = BeautifulSoup(html, "html.parser")
        meta = PropertyMeta(html,data_type)
        def getText(tags):
            results = []
            for tag in tags:
                results.append(tag.text)
            return results
        price = soup.find('div', {'data-testid': 'listing-details__listing-summary-title-name'})
        if price == []:
            price = soup.find_all('span', class_="css-1w4p1vw")
        priceInfo = getText(price)
        # sold_date = soup.find_all('span', class_="css-h9g9i3") # check if sold date presented
        rented_option = soup.find_all('span', class_="css-1e51og4")
        if rented_option:  # property rented
            soldDateInfo = ['']
            soldDate = None
            if "$" in priceInfo[0]:
                price = priceInfo[0]
                match = re.search(r'\$([\d,]+)', price)
                soldPrice = int(match.group(1).replace(",", ""))
            else:
                soldPrice = 0  # price with held
            expectedPrice = None
            pricingOptions = priceInfo[0]  #
            status = 'leased'
            listingOption = 'leased'
            authority = 'leased'
        else:  # property for rent
            soldDateInfo = ['']
            price = priceInfo[0]
            match = re.search(r'\$([\d,]+)', price)
            soldPrice = int(match.group(1).replace(",", ""))
            expectedPrice = soldPrice
            pricingOptions = priceInfo[0]  # price range $400,000 - $500,000 or From $500,000 or Offers Over $900,000  # price with held
            soldDate = None
            soldPrice = None
            status = 'for-rent'
            listingOption = 'rent'
            authority = 'for-rent'
        pricing = {'pricingOptions': pricingOptions, 'authority': authority, 'priceIncludes':['']}
        agency = meta.agency()
        agencyId = agency['agencyId']
        historysale = {"leasedDate": soldDate, "leasedPrice": soldPrice, "agencyId": agencyId}
        saleInfo = {"expectedPrice": expectedPrice, "listingOption": listingOption, 'pricing': pricing, 'status':status, 'leasedDateInfo': soldDateInfo[0]}
    return historysale, saleInfo
def get_totalarea(soup):
    bedBathSqmProtype = soup.find_all('div', class_="css-scdx6o")
    info = ''
    for item in bedBathSqmProtype:
        info = item.text.strip()
    items = ["Beds", "Baths", "Parking"]
    temp = info
    totalarea = 0
    if info:
        if 'm²' in temp:
            for item in items:
                if item in info:
                    tem = temp.split(item)[1]
                    temp = tem
            totalarea = float(temp.split('m²')[0].replace(',', ''))
    return totalarea

def Get_pro_meta(html, found_url, slug, data_type):
    soup = BeautifulSoup(html, "html.parser")
    meta = PropertyMeta(html,data_type)
    saleInfo = getPriceInfo(html, data_type) # get history sale and sale price if available
    totalarea = get_totalarea(soup) # get total area
    description = meta.description()
    agents = meta.agents()
    agentProfiles = meta.agentProfiles(agents)
    agency = meta.agency()
    pro_meta = meta.property_meta()
    schools = meta.schools()
    displayAddress = meta.displayAddress()
    propertyType = meta.propertyType()
    headline = meta.headline()
    structuredFeatures = meta.structuredFeatures()
    #slug = meta.propertyProfileUrlSlug()
    slug = {"slug": slug}
    # mapping city and suburb

    # to be sued for semantic search
    searchSmt = {}
    all_pro_meta = {'url': found_url,"agentProfiles": agentProfiles,"agency": agency, "pro_meta": pro_meta, "schools": schools,
                    "description": description, "headline": headline, "propertyType": propertyType,
                    "displayAddress": displayAddress, "structuredFeatures": structuredFeatures, 'slug':slug,  "saleInfo":saleInfo, "totalarea": totalarea}
    return all_pro_meta

class NumpyEncoder(json.JSONEncoder):
    """ Special json encoder for numpy types """
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return json.JSONEncoder.default(self, obj)
def json_dumps(meta, name_json):
    save_path = os.getenv("EXPORT_JSON_DIR", "data")
    pred_file = Path(os.path.join(save_path, name_json))
    with open(pred_file, "w") as f:
        json.dump(meta, f, cls=NumpyEncoder)


def get_json_files():
    filepath = os.getenv("DATA_NSW_PATH", "data/NSW")
    jsons = []

    for subdir, dirs, files in os.walk(filepath):
        for file in files:
            filepath = subdir + os.sep + file
            if filepath.endswith(".json"):
                jsons.append(filepath)
    return jsons
def scrap_pro_meta(address, suburb_csv):
    html, search_urls = Get_html(address)
    all_pro_data = Get_pro_meta(html)
    mapping = Mapping(all_pro_data, suburb_csv)
    mapped_data = mapping.mapping()
    return all_pro_data, mapped_data
    #json_dumps(mapped_meta,'mapped_meta.json')

def scrape_all_pro_meta():
    json_files = get_json_files()
    for json_file in json_files[:1]:
        mapped_meta_properties = []
        all_pro_meta_properties = []
        name_mapped_meta = 'mapped_meta'+'_'+json_file.split('/')[-1]
        name_all_pro_meta = 'mapped_meta' + '_'+json_file.split('/')[-1]
        with open(json_file, "r") as read_f:
            addresses = json.load(read_f)
            for add_dict in addresses:
                address = add_dict['Address']
                all_pro_data, mapped_data = scrap_pro_meta(address)
                mapped_meta_properties.append(mapped_data)
                all_pro_meta_properties.append(all_pro_data)
                json_dumps(mapped_meta_properties, name_mapped_meta)
                json_dumps(all_pro_meta_properties, name_all_pro_meta)

def search(d, search_pattern, prev_datapoint_path=''):

    output = []
    current_datapoint = d
    current_datapoint_path = prev_datapoint_path
    if type(current_datapoint) is dict:
        for dkey in current_datapoint:
            if search_pattern in str(dkey):
                c = current_datapoint_path
                c+="['"+dkey+"']"
                output.append(c)
            c = current_datapoint_path
            c+="['"+dkey+"']"
            for i in search(current_datapoint[dkey], search_pattern, c):
                output.append(i)
    elif type(current_datapoint) is list:
        for i in range(0, len(current_datapoint)):
            if search_pattern in str(i):
                c = current_datapoint_path
                c += "[" + str(i) + "]"
                output.append(i)
            c = current_datapoint_path
            c+="["+ str(i) +"]"
            for i in search(current_datapoint[i], search_pattern, c):
                output.append(i)
    elif search_pattern in str(current_datapoint):
        c = current_datapoint_path
        output.append(c)
    return output
def GetPrometabyBs4(page_source):
    soup = BeautifulSoup(page_source, "html.parser")
    for elem in soup(text=re.compile(r'fullName')):
        try:
            json_meta = json.loads(elem)
            results = search(json_meta, 'agents', 'json_meta')
            for result in results:
                keys = [key for key in result.split("'") if "]" not in key and "[" not in key]
                for key in keys:
                    value =json_meta[key]
                if value[0]['email'] is not None:
                    agents = value
        except:
            agents = []
            continue
    return agents

if __name__ == '__main__':
    address = '6/54 Durham Street, St Lucia QLD 4067'
    #html, search_urls = Get_html(address)
    html = open("/home/dung/Downloads/htmp_example_input.txt", "r").read()
    html_recent_sold = open("/home/dung/Downloads/sold_example.txt", "r").read()
    #json_meta = GetPrometabyBs4(html)
    all_pro_meta = Get_pro_meta(html_recent_sold)
    #all_pro_meta['search_urls'] = search_urls
    #json_dumps(all_pro_meta, 'all_pro_meta.json')
    mapping = Mapping(all_pro_meta)
    mapped_meta = mapping.mapping()
    #json_dumps(mapped_meta,'mapped_meta.json')
    print("ok")



