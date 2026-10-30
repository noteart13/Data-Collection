import os, time
os.environ["TOKENIZERS_PARALLELISM"] = "false"
import io
from .models import UrlModel, CrawlResult
# from .database import init_db, get_cached_url, cache_url, DB_PATH, flush_db
from .chunking_strategy import *
from .extraction_strategy import *
from .crawler_strategy import *
from concurrent.futures import ThreadPoolExecutor
from .config import *
import cloudinary.uploader
import warnings
import cloudinary
from operations.crawl4ai.google import GoogleSearch
from typing import List, Union

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", ""),
    api_key=os.getenv("CLOUDINARY_API_KEY", ""),
    api_secret=os.getenv("CLOUDINARY_API_SECRET", ""),
    secure=True
)
import cloudinary.uploader
import cloudinary.api
warnings.filterwarnings("ignore", message='Field "model_name" has conflict with protected namespace "model_".')


class WebCrawler:
    def __init__(
        self,
        # db_path: str = None,
        crawler_strategy: CrawlerStrategy = None,
        always_by_pass_cache: bool = False,
        verbose: bool = False,
    ):
        # self.db_path = db_path
        self.crawler_strategy = crawler_strategy or LocalSeleniumCrawlerStrategy(verbose=verbose)
        self.always_by_pass_cache = always_by_pass_cache

        # Create the .crawl4ai folder in the user's home directory if it doesn't exist
        # self.crawl4ai_folder = os.path.join(Path.home(), ".crawl4ai")
        # os.makedirs(self.crawl4ai_folder, exist_ok=True)
        # os.makedirs(f"{self.crawl4ai_folder}/cache", exist_ok=True)
        #for root, dirnames, filenames in os.walk(f"{self.crawl4ai_folder}"):
        #    for filename in filenames:
        #        #if filename.endswith(extensions):
        #        os.remove(filename)  # Notice here how we are using os.remove() instead of appending to a list!
        # If db_path is not provided, use the default path
        #os.rmdir(f"{self.crawl4ai_folder}")
        
        # flush_db()
        # init_db()
        
        self.ready = False
        
    def warmup(self):
        print("[LOG] 🌤️  Warming up the WebCrawler")
        result = self.run(
            url='https://google.com/',
            word_count_threshold=5,
            extraction_strategy= NoExtractionStrategy(),
            bypass_cache=False,
            verbose = False,
            # warmup=True
        )
        self.ready = True
        print("[LOG] 🌞 WebCrawler is ready to crawl")
        
    def fetch_page(
        self,
        url_model: UrlModel,
        provider: str = DEFAULT_PROVIDER,
        api_token: str = None,
        extract_blocks_flag: bool = True,
        word_count_threshold=MIN_WORD_THRESHOLD,
        css_selector: str = None,
        screenshot: bool = False,
        use_cached_html: bool = False,
        extraction_strategy: ExtractionStrategy = None,
        chunking_strategy: ChunkingStrategy = RegexChunking(),
        **kwargs,
    ) -> CrawlResult:
        return self.run(
            url_model.url,
            word_count_threshold,
            extraction_strategy or NoExtractionStrategy(),
            chunking_strategy,
            bypass_cache=url_model.forced,
            css_selector=css_selector,
            screenshot=screenshot,
            **kwargs,
        )
        pass

    def fetch_pages(
        self,
        url_models: List[UrlModel],
        provider: str = DEFAULT_PROVIDER,
        api_token: str = None,
        extract_blocks_flag: bool = True,
        word_count_threshold=MIN_WORD_THRESHOLD,
        use_cached_html: bool = False,
        css_selector: str = None,
        screenshot: bool = False,
        extraction_strategy: ExtractionStrategy = None,
        chunking_strategy: ChunkingStrategy = RegexChunking(),
        **kwargs,
    ) -> List[CrawlResult]:
        extraction_strategy = extraction_strategy or NoExtractionStrategy()
        def fetch_page_wrapper(url_model, *args, **kwargs):
            return self.fetch_page(url_model, *args, **kwargs)

        with ThreadPoolExecutor() as executor:
            results = list(
                executor.map(
                    fetch_page_wrapper,
                    url_models,
                    [provider] * len(url_models),
                    [api_token] * len(url_models),
                    [extract_blocks_flag] * len(url_models),
                    [word_count_threshold] * len(url_models),
                    [css_selector] * len(url_models),
                    [screenshot] * len(url_models),
                    [use_cached_html] * len(url_models),
                    [extraction_strategy] * len(url_models),
                    [chunking_strategy] * len(url_models),
                    *[kwargs] * len(url_models),
                )
            )

        return results

    def run(
            self,
            url: str,
            word_count_threshold=MIN_WORD_THRESHOLD,
            extraction_strategy: ExtractionStrategy = None,
            chunking_strategy: ChunkingStrategy = RegexChunking(),
            bypass_cache: bool = False,
            css_selector: str = None,
            screenshot: bool = False,
            user_agent: str = None,
            verbose=True,
            **kwargs,
        ) -> CrawlResult:
            try:
                extraction_strategy = extraction_strategy or NoExtractionStrategy()
                extraction_strategy.verbose = verbose
                if not isinstance(extraction_strategy, ExtractionStrategy):
                    raise ValueError("Unsupported extraction strategy")
                if not isinstance(chunking_strategy, ChunkingStrategy):
                    raise ValueError("Unsupported chunking strategy")
                
                # if word_count_threshold < MIN_WORD_THRESHOLD:
                #     word_count_threshold = MIN_WORD_THRESHOLD
                    
                word_count_threshold = max(word_count_threshold, 0)

                # Check cache first
                cached = None
                screenshot_data = None
                extracted_content = None
                # if not bypass_cache and not self.always_by_pass_cache:
                #     cached = get_cached_url(url)
                
                if kwargs.get("warmup", True) and not self.ready:
                    return None
                
                # if cached:
                #     html = sanitize_input_encode(cached[1])
                #     extracted_content = sanitize_input_encode(cached[4])
                #     if screenshot:
                #         screenshot_data = cached[9]
                #         if not screenshot_data:
                #             cached = None
                
                if not cached or not html:
                    if user_agent:
                        self.crawler_strategy.update_user_agent(user_agent)
                    t1 = time.time()
                    html = sanitize_input_encode(self.crawler_strategy.crawl(url, **kwargs))
                    t2 = time.time()
                    if verbose:
                        print(f"[LOG] 🚀 Crawling done for {url}, success: {bool(html)}, time taken: {t2 - t1} seconds")
                    # if screenshot:
                    #     screenshot_data = self.crawler_strategy.take_screenshot()

                
                crawl_result = self.process_html(url, html, extracted_content, word_count_threshold, extraction_strategy, chunking_strategy, css_selector, screenshot_data, verbose, bool(cached), **kwargs)
                crawl_result.success = bool(html)
                self.crawler_strategy.quit()
                return crawl_result
            except Exception as e:
                if not hasattr(e, "msg"):
                    e.msg = str(e)
                print(f"[ERROR] 🚫 Failed to crawl {url}, error: {e.msg}")    
                return CrawlResult(url=url, html="", success=False, error_message=e.msg)

    def process_html(
            self,
            url: str,
            html: str,
            extracted_content: str,
            word_count_threshold: int,
            extraction_strategy: ExtractionStrategy,
            chunking_strategy: ChunkingStrategy,
            css_selector: str,
            screenshot: bool,
            verbose: bool,
            is_cached: bool = False,
            **kwargs,
        ) -> CrawlResult:
            t = time.time()
            # Extract content from HTML
            try:
                # t1 = time.time()
                # result = get_content_of_website(url, html, word_count_threshold, css_selector=css_selector, only_text=kwargs.get("only_text", False))
                # print(f"[LOG] 🚀 Crawling done for {url}, success: True, time taken: {time.time() - t1} seconds")
                t1 = time.time()
                result = get_content_of_website_optimized(url, html, word_count_threshold, css_selector=css_selector, only_text=kwargs.get("only_text", False))
                if verbose:
                    print(f"[LOG] 🚀 Content extracted for {url}, success: True, time taken: {time.time() - t1} seconds")
                
                if result is None:
                    raise ValueError(f"Failed to extract content from the website: {url}")
            except InvalidCSSSelectorError as e:
                raise ValueError(str(e))
            
            cleaned_html = sanitize_input_encode(result.get("cleaned_html", ""))
            markdown = sanitize_input_encode(result.get("markdown", ""))
            media = result.get("media", [])
            links = result.get("links", [])
            metadata = result.get("metadata", {})
                        
            if extracted_content is None:
                if verbose:
                    print(f"[LOG] 🔥 Extracting semantic blocks for {url}, Strategy: {extraction_strategy.name}")

                sections = chunking_strategy.chunk(markdown)
                extracted_content = extraction_strategy.run(url, sections)
                extracted_content = json.dumps(extracted_content, indent=4, default=str)

                if verbose:
                    print(f"[LOG] 🚀 Extraction done for {url}, time taken: {time.time() - t} seconds.")
                
            screenshot = None if not screenshot else screenshot
            
            # if not is_cached:
            #     cache_url(
            #         url,
            #         html,
            #         cleaned_html,
            #         markdown,
            #         extracted_content,
            #         True,
            #         json.dumps(media),
            #         json.dumps(links),
            #         json.dumps(metadata),
            #         screenshot=screenshot,
            #     )                
            
            return CrawlResult(
                url=url,
                html=html,
                cleaned_html=format_html(cleaned_html),
                markdown=markdown,
                media=media,
                links=links,
                metadata=metadata,
                screenshot=screenshot,
                extracted_content=extracted_content,
                success=True,
                error_message="",
            )
class PropertyMeta:
    '''
    this class is to extract all metadata from html for one property address
    '''
    def __init__(self, html):
        self.html = html
    def property_metadata(self):
        srt_pos = self.html.find('{"saleMethod":')
        rm = self.html[0:srt_pos]
        meta = self.html.replace(rm, '')
        end_pos = meta.find('"}')
        l = len(meta)
        meta = meta.replace(meta[end_pos + 2:l], '')
        json_meta = json.loads(meta)
        return json_meta

    def property_meta(self):
        key = '{"saleMethod":'
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
        schools_json = self.extract_meta(key)
        return schools_json
    def displayAddress(self):
        key = '{"displayAddress":'
        displayAddress_json = self.extract_meta(key)
        return displayAddress_json
    def propertyType(self):
        return {"propertyType": self.extract_str('"propertyType":')}
    def headline(self):
        return {"headline": self.extract_str('"headline":')}
    def description(self):
        return {"description":self.extract_str('"},"description":')}
    def extract_str(self, key):
        srt_pos = self.html.find(key)
        rm = self.html[0:srt_pos + len(key)]
        meta_json = self.html.replace(rm, '')
        end_pos = meta_json[1:].find('"')  # ignore '"'
        str = meta_json[1:end_pos - 1]  # extract only text and ignore '"' at the begin and at the end
        return str
    def extract_meta(self, key):
        '''
           idea: to extract json or array format ,e.g., {} or []
           find first close bracket } or ] at lst_open_pos then find last { or [ from schools[1:lst_open_pos]
           if unable to find last { or [ then complete algorithm
           replace { and } or [ and ] by # then repeat procedure
        '''
        if '{' == key[0]:
            open = '{'
            close = '}'
        elif '[' == key[0]:
            open = '['
            close = ']'
        srt_pos = self.html.find(key)
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
        return meta_json


    def file_path(self, request, response=None, info=None, *, item=None):
        import hashlib
        image_url_hash = hashlib.shake_256(request.url.encode()).hexdigest(5)
        image_perspective = request.url.split("/")[-2]
        image_filename = f"{image_url_hash}_{image_perspective}.jpg"
        return image_filename
    def raw_image_urls(self, image_urls):
        import hashlib
        #for image_url in image_urls:
        #    try:

        for b in image_urls:
            try:
                image_content = requests.get(b).content
                image_file = io.BytesIO(image_content)
                image = Image.open(image_file).convert("RGB")
                test_dir = Path(os.getenv("TEST_IMAGE_DIR", "scratch/test_images"))
                test_dir.mkdir(parents=True, exist_ok=True)
                file_path = test_dir / (hashlib.sha1(image_content).hexdigest()[:10] + ".png")
                image.save(file_path)
                #self.download_image(full_image_url, image_path)

                # Create the raw GitHubusercontent URL
                #raw_image_url = f"{base_raw_url}{image_filename}"
            except Exception as e:
                raw_image_url = None
    def contactInfo(self, agent):
        fullName = agent['fullName'].split(' ')
        firstName = fullName[0]
        lastName = fullName[1]
        email = agent['email']
        phoneNumber = agent['mobileNumber']
        profileUrl = agent['profileUrl']
        photo = agent['photo']
        contactInfor = {"firstName": firstName, "lastName": lastName,
        "email": email, "phoneNumber": phoneNumber, "profileUrl": profileUrl, "photo": photo}
        return contactInfor
    def agentProfiles(self, agents):
        if isinstance(agents, dict):
            contactInfo = self.contactInfo(agents)
        elif isinstance(agents, list):
            contactInfo = [self.contactInfo(agent) for agent in agents]
        return contactInfo
cat_dict_rvt_anno = {0:'Bed Room',1: 'Bath Room',2: 'Living Room',3: 'Laundry',4: 'Garage',5: 'Garden',6:'Kitchen',7: 'Exterior',8:'Deck',9: 'Study Room',10: 'Swimming Pool',
                     11:'Yard',12: 'Media Room',13:'Floor Plan',14:'Front View',15: 'Balcony',16:'Top View',17:'Gym', 18:'Formal Dining', 19:'Dining Room'}
class AUS_MULTIMODAL_DEDUP_CLS_EMBED_V1:
    '''
    this class is to extract all metadata from html for one property address
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
        for img, emb in zip(body["images"], emb_norm):  # add embedding to dict
            img["emb"] = emb
        return body

    def cls_mat_embe(self):
        """
        Siwin is used to classify while there are such two options for image embedding as Clip, Siwin.
        Clip and ada-002 are used for text embedding
        :param modal:
        :param info:
        :return:
        """

        descriptions = [v for k,v in cat_dict_rvt_anno.items()] # category labels
        if 'description' in self.body:
            modal = "text"
        else:
            modal = "image"
        try:
            if modal == "text": # only get text embedding from clip model
                body_clip = get_payload(self.body, 'ada-002', modal)  # to be used for ada-002 embedding
                #if model == 'clip':
                #    emb_norm = AUS_MULTIMODAL_MATCHING_CLIP.embedding(x=[body_clip["text"]], modal=modal)
                # for now since clip only handle max length of 77 so ada-002 will be used instead
                emb_norm = get_embedding(body_clip["text"], "text-embedding-ada-002")# currently using ada-002
                self.body["text_embed"] = emb_norm
                self.body["para"] = body_clip["text"]
            elif modal == "image": # do classification and embedding
                model = 'swin' # clip is used for now
                body_model = get_payload(self.body, model, modal)
                y_preds = []
                #emb_norm_list = []
                image_tensors = self.GET_IMAGE_TENSORS(body_model, model)
                if model == 'swin':
                    y_preds = AUS_IMAGE_CLASSIFICATION.IMAGE_CLS(image_tensors=image_tensors)
                    emb = AUS_IMAGE_MATCHING.forward(x=image_tensors)
                    emb_norm = emb[0]
                    #emb_norm = output[0]  # output[0] is emb_norm, output[1] is emb, output[2[ is logic
                elif model == 'clip':
                    # classification
                    y_preds = AUS_MULTIMODAL_MATCHING_CLIP.zero_shot_cls(images=image_tensors, texts=descriptions)
                    # feature extraction for searching
                    emb_norm = AUS_MULTIMODAL_MATCHING_CLIP.embedding(x=image_tensors, modal=modal)
                categories = [cat_dict_rvt_anno[y_pred] for y_pred in y_preds]
                for img, cat in zip(self.body["images"], categories):
                    img["category"] = cat  # update category
                body = self.REMOVE_DUP(emb_norm, self.body)
            return body
        except Exception as e:
            return {'status': False, 'msg': e}, 400
cls_mat_emb = AUS_MULTIMODAL_DEDUP_CLS_EMBED_V1('image')
class Mapping:
    '''
    this class is to map property meta data into right format of property for sale
    '''

    def __init__(self, pro_meta):
        self.pro_meta = pro_meta

    def get_feature(self, structuredFeature):
        category = structuredFeature['category']
        if category == 'Indoor':
            indoorFeatures = structuredFeature['name']
        elif category == 'Outdoor':
            outdoorAmenities = structuredFeature['name']
        return indoorFeatures, outdoorAmenities
    def get_all_features(self,structuredFeatures):
        indoorFeatures = []
        outdoorAmenities = []
        if isinstance(structuredFeatures, dict):
            indoorFeature, outdoorAmenity = self.get_feature(structuredFeatures)
            indoorFeatures.append(indoorFeature)
            outdoorAmenities.append(outdoorAmenity)
        elif isinstance(structuredFeatures, list):
            for structuredFeature in structuredFeatures:
                indoorFeature, outdoorAmenity = self.get_features(structuredFeature)
                indoorFeatures.append(indoorFeature)
                outdoorAmenities.append(outdoorAmenity)
        return indoorFeatures, outdoorAmenities
    def mapping(self, pro_meta):
        """
        pro_meta = {'agents': [{'fullName': 'Toby Shipway', 'email': 'toby.shipway@raywhite.com', 'mobileNumber': '0413600919', 'landlineNumber': None, 'isActiveProfilePage': True,
                                'profileUrl': 'https://www.domain.com.au/real-estate-agent/toby-shipway-1952136',
                                'photo': {'url': 'https://rimh2.domainstatic.com.au/J5A8yNvwcYnJm0308bZzOgTs5Ww=/filters:format(png):quality(80):no_upscale()/https://images.domain.com.au/img/34615/contact_1952136.jpeg?date=638608995138430000', '__typename': 'Image'}, '__typename': 'Agent', 'agentId': '1952136'}, {'fullName': 'Andrew Downing', 'email': 'andrew.downing@raywhite.com', 'mobileNumber': '0404882311', 'landlineNumber': '0404 882 311', 'isActiveProfilePage': True,                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'profileUrl': 'https://www.domain.com.au/real-estate-agent/andrew-downing-1794009', 'photo': {'url': 'https://rimh2.domainstatic.com.au/RFEc8PKbSvGZFJ_SgZxZBaoC8GY=/filters:format(png):quality(80):no_upscale()/https://images.domain.com.au/img/34615/contact_1794009.jpeg?date=638608995138170000', '__typename': 'Image'}, '__typename': 'Agent', 'agentId': '1794009'}], 'agency': {'branding': {'banner': {'url': 'https://rimh2.domainstatic.com.au/YAG-3NxrBLXpW5Jw4T1xJtSW0vw=/filters:format(png):quality(80):no_upscale()/https://images.domain.com.au/img/Agencys/34615/banner_34615.png?date=638609501388217362', '__typename': 'Image'}, 'logo': {'url': 'https://rimh2.domainstatic.com.au/qGFrXIzgnnmBH_x2oIA9LwsnoGk=/filters:format(png):quality(80):no_upscale()/https://images.domain.com.au/img/Agencys/34615/logo_34615.jpg?date=133646295115415879?date=638609501388217395', '__typename': 'Image'}, 'logoSmall': {'url': 'https://rimh2.domainstatic.com.au/XkDmOKLxtnfCbtkKxUXSJbAskKw=/filters:format(png):quality(80):no_upscale()/https://images.domain.com.au/img/Agencys/34615/searchlogo_34615.jpg?date=133646295402956072?date=638609501388217390', '__typename': 'Image'}, 'backgroundColour': '#FEE536', 'logoColour': '#FEE536', '__typename': 'AgencyBranding'}, 'contactDetails': {'general': {'phone': '0404882311', '__typename': 'AgencyContactDetailsGeneral'}, '__typename': 'AgencyContactDetails'}, 'isArchived': False, 'name': 'Ray White Adelaide City', 'profileUrl': 'raywhiteadelaidecity-34615', 'website': '', '__typename': 'Agency', 'agencyId': 34615},
                    'pro_meta': {'saleMethod': 'privateTreaty', 'soldAction': 'privateTreaty',
                             'address': '25/42 Charlick Circuit, Adelaide SA 5000', 'adtype': 'premiumPlus', 'agency': 'Ray White Adelaide City', 'agencyId': 34615, 'agentNames': 'Toby Shipway, Andrew Downing', 'bathrooms': 2, 'bedrooms': 3, 'buildingsize': 135, 'daysListed': 19, 'dateListed': '2024-08-14T12:01:51.000', 'inspectionsCount': 0, 'hasPhoto': True, 'hasFloorplan': True, 'hasDisplayPrice': True, 'hasDescription': True, 'descriptionHasEmail': False, 'descriptionHasPhone': True, 'hasSOI': False, 'internalArea': 135, 'virtualTour': False, 'floorPlansCount': 1, 'images': ['https://bucket-api.domain.com.au/v1/bucket/image/2019423580_1_1_240814_023928-w5062-h3114', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_2_1_240814_020146-w4671-h3114', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_3_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_4_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_5_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_6_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_7_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_8_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_10_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_11_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_12_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_13_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_14_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_15_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_16_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_17_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_18_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_19_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_20_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_21_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_22_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_23_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_24_1_240814_020146-w6000-h4000', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_25_1_240814_020146-w5470-h3657', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_26_1_240814_020146-w3658-h2446', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_27_1_240814_020146-w5470-h3657', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_28_1_240814_020146-w6016-h4016', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_29_1_240814_020146-w6016-h4016', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_30_1_240814_020146-w6016-h4016', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_31_1_240814_020146-w6016-h4016', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_32_1_240814_020146-w5157-h3448', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_33_1_240814_020146-w5276-h3528', 'https://bucket-api.domain.com.au/v1/bucket/image/2019423580_34_1_240814_020146-w3658-h2446'], 'onlineAuctionAvailable': 'None', 'parking': 2, 'photoCount': 33, 'postcode': '5000', 'price': 'UNDER OFFER 1ST WEEK - CONTACT TOBY SHIPWAY', 'primaryPropertyType': 'Apartment', 'propertyFeatures': 'Alarm System, Intercom, Built in wardrobes, Dishwasher, Secure Parking, Balcony / Deck', 'structuredFeatures': [{'name': 'Alarm System', 'category': 'Indoor', 'source': 'advertiser'}, {'name': 'Intercom', 'category': 'Indoor', 'source': 'advertiser'}, {'name': 'Built in wardrobes', 'category': 'Indoor', 'source': 'advertiser'}, {'name': 'Dishwasher', 'category': 'Indoor', 'source': 'advertiser'}, {'name': 'Secure Parking', 'category': 'Outdoor', 'source': 'advertiser'}, {'name': 'Balcony / Deck', 'category': 'Outdoor', 'source': 'advertiser'}], 'propertyId': 'MA-9462-YS', 'secondaryPropertyType': 'Apartment / Unit / Flat', 'state': 'SA', 'suburb': 'Adelaide', 'videoCount': 1, 'x-message': 'domain-propertyinfo'}, 'schools': {'schools': [{'id': '', 'educationLevel': 'secondary', 'name': 'Adelaide International School', 'distance': 499.98472678935195, 'state': 'SA', 'postCode': '5000', 'year': 'Unknown', 'gender': '', 'type': 'Private', 'address': 'Adelaide, SA 5000', 'url': 'https://ais.edu.au/', 'domainSeoUrlSlug': 'adelaide-international-school-sa-5000-52254'}, {'id': '', 'educationLevel': 'combined', 'name': 'Christian Brothers College - Junior Campus', 'distance': 553.3408379842192, 'state': 'SA', 'postCode': '5000', 'year': '', 'gender': '', 'type': 'Catholic', 'address': 'Adelaide, SA 5000', 'url': '', 'domainSeoUrlSlug': 'christian-brothers-college-junior-campus-sa-5000-50347'}, {'id': '11107', 'educationLevel': 'combined', 'name': 'Christian Brothers College', 'distance': 576.3752185563361, 'state': 'SA', 'postCode': '5000', 'year': 'R-12', 'gender': 'Boys', 'type': 'Catholic', 'address': 'Adelaide, SA 5000', 'url': 'http://www.cbc.sa.edu.au', 'domainSeoUrlSlug': 'christian-brothers-college-sa-5000-11107'}, {'id': '', 'educationLevel': 'secondary', 'name': 'Specialised Assistance School For Youth (SASY)', 'distance': 612.8194901317762, 'state': 'SA', 'postCode': '5000', 'year': '8-12', 'gender': 'CoEd', 'type': 'Private', 'address': 'Adelaide, SA 5000', 'url': 'https://sasy.sa.edu.au/', 'domainSeoUrlSlug': 'specialised-assistance-school-for-youth-sasy-sa-5000-52253'}, {'id': '', 'educationLevel': 'secondary', 'name': 'South Australian International School', 'distance': 683.4803283440215, 'state': 'SA', 'postCode': '5000', 'year': '', 'gender': '', 'type': 'Private', 'address': 'Adelaide, SA 5000', 'url': '', 'domainSeoUrlSlug': 'south-australian-international-school-sa-5000-52606'}, {'id': '', 'educationLevel': 'secondary', 'name': 'University Senior College at Adelaide University - Rundle Mall Campus', 'distance': 695.6584523787786, 'state': 'SA', 'postCode': '5000', 'year': '', 'gender': '', 'type': 'Private', 'address': 'Adelaide, SA 5000', 'url': '', 'domainSeoUrlSlug': 'university-senior-college-at-adelaide-university-rundle-mall-campus-sa-5000-52571'}, {'id': '7311', 'educationLevel': 'combined', 'name': 'Prince Alfred College', 'distance': 727.2845970305195, 'state': 'SA', 'postCode': '5067', 'year': 'R-12', 'gender': 'Boys', 'type': 'Private', 'address': 'Kent Town, SA 5067', 'url': 'http://www.pac.edu.au', 'domainSeoUrlSlug': 'prince-alfred-college-sa-5067-7311'}, {'id': '', 'educationLevel': 'secondary', 'name': 'University Senior College at Adelaide University - Gawler Place Campus', 'distance': 755.7130173016885, 'state': 'SA', 'postCode': '5000', 'year': '', 'gender': '', 'type': 'Private', 'address': 'Gawler Place, SA 5000', 'url': '', 'domainSeoUrlSlug': 'university-senior-college-at-adelaide-university-gawler-place-campus-sa-5000-52799'}, {'id': '', 'educationLevel': 'secondary', 'name': 'Adelaide Botanic High School', 'distance': 768.7103628522017, 'state': 'SA', 'postCode': '5000', 'year': '7-12', 'gender': 'CoEd', 'type': 'Government', 'address': 'Adelaide, SA 5000', 'url': 'http://www.abhs.sa.edu.au', 'domainSeoUrlSlug': 'adelaide-botanic-high-school-sa-5000-52576'}, {'id': '', 'educationLevel': 'combined', 'name': 'St Aloysius College', 'distance': 843.6733243626262, 'state': 'SA', 'postCode': '5000', 'year': 'R-12', 'gender': 'Girls', 'type': 'Catholic', 'address': 'Adelaide, SA 5000', 'url': 'https://www.sac.sa.edu.au', 'domainSeoUrlSlug': 'st-aloysius-college-sa-5000-49706'}, {'id': '7452', 'educationLevel': 'secondary', 'name': 'University Senior College at Adelaide University', 'distance': 940.4607219277208, 'state': 'SA', 'postCode': '5005', 'year': '10-12', 'gender': 'CoEd', 'type': 'Private', 'address': 'Adelaide University, SA 5005', 'url': 'http://www.usc.adelaide.edu.au', 'domainSeoUrlSlug': 'university-senior-college-sa-5000-7452'}, {'id': '7051', 'educationLevel': 'primary', 'name': 'Gilles Street Primary School', 'distance': 1321.7635383687993, 'state': 'SA', 'postCode': '5000', 'year': 'U, R-6', 'gender': 'CoEd', 'type': 'Government', 'address': 'Adelaide, SA 5000', 'url': 'http://www.gillesstps.sa.edu.au', 'domainSeoUrlSlug': 'gilles-street-primary-school-sa-5000-7051'}, {'id': '6804', 'educationLevel': 'secondary', 'name': 'Adelaide High School', 'distance': 2114.68797912481, 'state': 'SA', 'postCode': '5000', 'year': 'U, 7-12', 'gender': 'CoEd', 'type': 'Government', 'address': 'Adelaide, SA 5000', 'url': 'http://www.adelaidehs.sa.edu.au', 'domainSeoUrlSlug': 'adelaide-high-school-sa-5000-6804'}], 'numberOfVisibleSchools': 3, 'enableSchoolProfileLink': True, 'ads': {'endpoint': 'https://ads-api.domain.com.au/v2/ads/list', 'params': {'numberOfAds': 2, 'environment': 'www.domain.com.au', 'brand': 'Domain', 'platform': 'Website', 'liveRailEnabled': True, 'cat': 'sold', 'cat1': 'details', 'userType': 'sold', 'adType': 'schoolsponsorship', 'adWidth': 1, 'adHeight': 1, 'locations': [{'state': 'SA', 'region': 'Adelaide Region', 'area': 'Adelaide', 'suburb': 'Adelaide', 'postCode': '5000'}], 'deviceType': 'Desktop'}}, 'addressParts': {'streetNumber': '42', 'unitNumber': '25', 'street': 'Charlick Circuit', 'suburb': 'Adelaide', 'stateAbbreviation': 'sa', 'postcode': '5000', 'displayAddress': '25/42 Charlick Circuit, Adelaide SA 5000', 'displayType': 'fullAddress'}, 'adId': '2019423580', 'feedbackUrl': '/phoenix/api/school-data/report-error'}, 'description': {'description': "Discover urban elegance at its best with this stunning 3 bedroom apartment securely located in the architecturally acclaimed Brooker Apartments. Offering over 135 sqm of refined living space, this residence offers an exceptional 'lock up and leave' lifestyle in the highly desirable East End Precinct.\\r\\n\\r\\nLocated on the picturesque Charlick Circuit, you'll find Adelaide's finest cafes, restaurants, and bars just a short stroll away. Enjoy an array of annual events and festivals, and explore nearby attractions such as Adelaide University, Rymill Park, the Botanic Gardens, Rundle Street, Rundle Mall and much more. Zoned for Adelaide High School and Adelaide Botanic High School plus many private schools nearby, you can be rest assured your children will have the best education possible.\\r\\n\\r\\nEnter via the secure ground floor foyer and into the lift with direct access straight to your floor.  Inside the apartment welcomes you an inviting sense of space complimented with a wide entrance hallway, 9-foot ceilings and an abundance of natural light.\\r\\n\\r\\nThe open plan living and dining area is comfortable and spacious extending through to the generous terrace with a picturesque outlook over the leafy Charlick Circuit lined with charming Parisian style homes and surrounding character buildings. Such a perfect environment for both everyday living and entertaining. The centrally located kitchen is beautifully appointed with stone benchtops, extensive solid timber cabinetry and quality appliances. \\r\\n\\r\\nThe 3 bedrooms are great size, all with an outlook over Charlick Circuit. The master suite comprises of a walk-in robe with plenty of hanging space and through to the large ensuite. Bedroom 2 has built in robes and bedroom 3 with your own private balcony and floor to ceiling built-in cabinetry with shelving, perfect space for a home office. The main bathroom is certainly big enough with a separate bath, shower and large vanity, catering to both family living and guests.\\r\\n\\r\\nAdditional features include:\\r\\n\\r\\n-\\t2 allocated garaged car parks\\r\\n-\\tStorage cage  \\r\\n-\\tYear-round comfort with a geothermal ducted reverse cycle air conditioning system\\r\\n-\\tGenerous separate laundry \\r\\n-\\tVideo intercom\\r\\n-\\tSecurity system\\r\\n-\\tLinen press \\u0026 ample storage\\r\\n-\\tNew tapware in all wet areas\\r\\n-\\tNew light fittings throughout \\r\\n\\r\\nIt's hard to look past such an amazing lifestyle apartment in the tightly held East End! Apartments like this are few and far between so be quick to look through at one of the open inspections and submit your offer. \\r\\n\\r\\nFor any further information please contact Toby Shipway on 0413 600 919. \\r\\n\\r\\nAll information provided has been obtained from sources we believe to be accurate, however, we cannot guarantee the information is accurate and we accept no liability for any errors or omissions (including but not limited to a property's land size, floor plans and size, building age and condition) Interested parties should make their own inquiries and obtain their own legal advic"}}
        :param pro_meta:
        :return:
        """
        mapped_proSale = {}
        structuredFeatures = self.pro_meta['structuredFeatures']
        indoorFeatures, outdoorAmenities = self.get_all_features(structuredFeatures)
        mapped_proSale['stakeholder'] = 'agent'
        mapped_proSale['contactInfo'] = self.pro_meta['contactInfo']
        mapped_proSale['listingOption'] = 'sale'
        mapped_proSale['title'] = self.pro_meta['headline']
        mapped_proSale['propertyType'] = self.pro_meta['propertyType']
        mapped_proSale['description'] = self.pro_meta['description']
        mapped_proSale["constructionYear"] = "N/A"
        mapped_proSale["structuralRemodelYear"] = "N/A"
        mapped_proSale["architecturalStyle"] = "None"
        mapped_proSale["status"] = "for-sale"
        mapped_proSale["state"] = self.pro_meta['pro_meta']['addressParts']['stateAbbreviation']
        mapped_proSale["city"] = self.pro_meta['pro_meta']['addressParts']['suburb']
        mapped_proSale["street"] = self.pro_meta['pro_meta']['addressParts']['displayAddress']
        mapped_proSale["coordinates"] = self.pro_meta['displayAddress']['geolocation']
        mapped_proSale["bed"] = self.pro_meta['pro_meta']['bathrooms']
        mapped_proSale["bath"] = self.pro_meta['pro_meta']['bedrooms']
        mapped_proSale["features"] = {"garage": self.pro_meta['pro_meta']['parking'],
                     "floorno": self.pro_meta['pro_meta']['floorPlansCount'],"basement": "None","floorCovering": ["None"],
                    "parking": ["Carport"],
                     "roof": ["Other"],"view": ["None"],"rooms": ["None"],
                    "indoorFeatures": indoorFeatures,
                    "coolingTypes": ["None"],"heatingTypes": ["None"],
                     "heatingFuels": ["None"],"appliances": ["None"],"buildingAmenities": ["None"],
                     "outdoorAmenities": outdoorAmenities,
                    "displayAddress": "fullAddress"}
        mapped_proSale["area"] = {"totalarea": self.pro_meta['pro_meta']['buildingsize'], "unit": "sqM"}
        mapped_proSale["expectedPrice"]: ""
        mapped_proSale["pricing"] = {"waterBillPeriod": "monthly", "councilBill": "",
                    "pricingOptions": "actual",
                    "authority": "for-sale",
                    "priceIncludes": [""]}
        # more filds added: schools and agency
        mapped_proSale['agency'] = self.pro_meta['agency']
        mapped_proSale['schools'] = self.pro_meta['schools']

        urls = self.pro_meta['pro_meta']['images']
        images = []

        for url in urls:
            response = cloudinary.uploader.upload(url)
            temp = {}
            temp["url"] = response.url
            temp['category'] = 'kitchen'
            temp['star'] = False
            images.append(url)
        cls_mat_emb = AUS_MULTIMODAL_DEDUP_CLS_EMBED_V1(urls) # labelling, embedding, and removing dubplicate images
        images = cls_mat_emb(images)
        mapped_proSale["images"] = images
class scraping():
    def __init__(self, address):
        self.address = address
    def get_html(self):
        # step 1 Search address from custome google search
        retriever = GoogleSearch(self.address)
        search_results = retriever.search()
        # step 2 Collect new URLs from search results
        search_urls = []
        temp_urls = []
        for url in search_results:
            if 'property-profile' not in url.get("href"):
                temp = url.get("href").replace('https://www.domain.com.au/','')
                if temp.rfind('-')>0:
                    temp = temp[:temp.rfind('-')].replace('-',', ')
                search_urls.append(url.get("href"))
                temp_urls.append(temp)
        # step 3 Get exact match between address and corresponding url
        query_features = AUS_MULTIMODAL_MATCHING_CLIP.embedding(self.address, modal='text')
        text_features = AUS_MULTIMODAL_MATCHING_CLIP.embedding(x=temp_urls, modal='text')
        text_probs = (100.0 * query_features @ text_features.T).softmax(dim=-1)
        y_preds = ([item.item() for item in text_probs.argmax(dim=-1).cpu()])
        url = search_urls[y_preds[0]]
        # Step 4 scraping html
        crawler = WebCrawler()
        # Step 4.1 Warm up the crawler (load necessary models)
        crawler.warmup()
        # Step 4.2 Run the crawler on a URL
        result = crawler.run(url=url)
        html = result.html
        return html

if __name__ == '__main__':
    #fine_tune_clip()
    html = open("/home/dung/Downloads/htmp_example_input.txt", "r").read()
    meta = PropertyMeta(html)
    description = meta.description()
    agents = meta.agents()
    agency = meta.agency()
    pro_meta = meta.property_meta()
    schools = meta.schools()
    displayAddress = meta.displayAddress()
    propertyType = meta.propertyType()
    headline = meta.headline()
    pro_meta= {"agents": agents, "agency": agency, "pro_meta": pro_meta, "schools": schools,
            "description": description, "headline": headline, "propertyType": propertyType, "displayAddress": displayAddress}
    mapping = Mapping(pro_meta)
    mapped_meta = mapping.mapping()



