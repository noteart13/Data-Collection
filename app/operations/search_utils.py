
from app.utils import get_json_files, get_all_suburb_city, change_key, remove_keys
from app.core.config import settings
from openai import OpenAI
import json
import torch
import requests
#from operations.feature_extraction import AUS_MULTIMODAL_MATCHING_CLIP
from random import choice

equals_ranger_filter = ['bed', 'bath', 'features.garage', 'features.floorno', 'area.totalarea', 'expectedPrice']
text_place_filter = ['suburb', 'state']
range_filter = ['area.totalarea', 'expectedPrice']
street_filter = ['street']
description_filter = ['description']
text_filter = ['propertyType', 'listingOption', 'status','city'] # since city is not included in
hybrid_filter = ['features.floorCovering', 'features.parking', 'features.roof', 'features.view', 'features.rooms',
                 'features.indoorFeatures', 'features.coolingTypes', 'features.heatingTypes',
                 'features.heatingFuels', 'features.appliances', 'features.buildingAmenities',
                 'features.outdoorAmenities']
possible_vector_filters = equals_ranger_filter + text_place_filter + range_filter + text_filter
def get_vector_filters(filters):
    vector_filters = {}
    for fil in possible_vector_filters:
        if fil in filters:
            vector_filters[fil] = filters[fil]
    return vector_filters
def get_compound(filters):
    search_filter = []
    must = []
    text_image_match = []
    if '$and' in filters:
        filters = filters['$and']
    for fil in filters:
        if isinstance(fil, dict):
            k, v = fil.items()
        elif isinstance(fil, str):
            k, v = fil, filters[fil]
        if k in equals_ranger_filter:
            if isinstance(v, (int, float)):
                search_filter.append({'equals': {'value': v, 'path': k, }})
            elif isinstance(v, dict):
                if '$gte' in v and '$lte' not in v:
                    search_filter.append({'range': {'gte': v['$gte'], 'path': k, }})
                elif '$gte' not in v and '$lte' in v:
                    search_filter.append({'ranger': {'lte': v['$lte'], 'path': k, }})
                elif '$gte' in v and '$lte' in v:
                    search_filter.append({'range': {'gte': v['$gte'], 'lte': v['$lte'], 'path': k, }})
        elif k in text_filter:
            if isinstance(v, str):
                search_filter.append(
                    {'text': {'query': v, 'path': k}})
            elif isinstance(v, dict):
                if '$in' in v:
                    search_filter.append({'text': {'query': v['$in'], 'path': k, }})
                elif '$all' in v:
                    for term in v['$all']:
                        search_filter.append({'text': {'query': term, 'path': k, }})
        elif k in text_place_filter:
            if isinstance(v, str):
                must.append({'autocomplete': {'query': v, 'path': 'street','tokenOrder': 'any',
                                              'fuzzy': {'maxEdits': 2, 'prefixLength': 1, 'maxExpansions': 256}}})
            elif isinstance(v, dict) and '$in' in v:
                must.append({'autocomplete': {'query': v['$in'], 'path': 'street','tokenOrder': 'any',
                                              'fuzzy': {'maxEdits': 2, 'prefixLength': 1, 'maxExpansions': 256}}})
        elif k in hybrid_filter and v is not None:
            if isinstance(v, str):
                if k == 'features.view' and 'View' not in v:
                    v = v + ' View'
                must.append({'autocomplete': {'query': v, 'path': 'description', 'tokenOrder': 'any',
                                              'fuzzy': {'maxEdits': 2, 'prefixLength': 1, 'maxExpansions': 256}}})
                text_image_match.append(v)
            elif isinstance(v, dict):
                if '$in' in v:
                    if k == 'features.view' and 'view' not in v['$in']:
                        v['$in'] = [item + ' view' for item in v['$in']]

                    must.append({'autocomplete': {'query': v['$in'], 'path': 'description', 'tokenOrder': 'any',
                                                  'fuzzy': {'maxEdits': 2, 'prefixLength': 1, 'maxExpansions': 256}}})
                    text_image_match.extend([item for item in v['$in']])
                elif isinstance(v, dict) and '$regex' in v:
                    if k == 'features.view' and 'view' not in v['$regex'].title():
                        v['$regex'] = v['$regex'] + ' view'
                    must.append({'autocomplete': {'query': v['$regex'] + ' view', 'path': 'description', 'tokenOrder': 'sequential',
                                                  'fuzzy': {'maxEdits': 2, 'prefixLength': 1, 'maxExpansions': 256}}})
                    text_image_match.append(v['$regex'])
                elif '$all' in v:
                    for term in v['$all']:
                        must.append({'autocomplete': {'query': term, 'path': 'description', 'tokenOrder': 'any',
                                                      'fuzzy': {'maxEdits': 2, 'prefixLength': 1,
                                                                'maxExpansions': 256}}})
                        text_image_match.append(term)
        elif k in street_filter + description_filter:
            if isinstance(v, str):
                must.append({'autocomplete': {'query': v, 'path': k, 'tokenOrder': 'sequential',
                                              'fuzzy': {'maxEdits': 2, 'prefixLength': 1, 'maxExpansions': 256}}})
                if k == 'description':
                    text_image_match.append(v)
            elif isinstance(v, dict) and '$regex' in v:

                must.append({'autocomplete': {'query': v['$regex'], 'path': k, 'tokenOrder': 'sequential',
                                              'fuzzy': {'maxEdits': 2, 'prefixLength': 1, 'maxExpansions': 256}}})
                if k == 'description':
                    text_image_match.append(v['$regex'])
    #must = [{'autocomplete': {'query': ['City view','River View'], 'path': 'description', 'tokenOrder': 'any', 'fuzzy': {'maxEdits': 2, 'prefixLength': 1, 'maxExpansions': 256}}}, {'autocomplete': {'query': 'QLD', 'path': 'street', 'tokenOrder': 'any', 'fuzzy': {'maxEdits': 2, 'prefixLength': 1, 'maxExpansions': 256}}}]
    return must, search_filter, text_image_match
def nomic_embedding_v1(query):
    url = "https://api-atlas.nomic.ai/v1/embedding/text"

    payload = json.dumps({
        "texts": [query
                  ],
        "task_type": "search_query",
        "max_tokens_per_text": 8192,
        "dimensionality": 768
    })
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': 'Bearer nk-jX6NQnpcBKPcu-n4fL9_UYWVQPHzbgQSF_k5GJFMS9k'
    }
    response = requests.request("POST", url, headers=headers, data=payload)
    query_vector = response.text
    query_vector = json.loads(query_vector)
    query_vector = query_vector["embeddings"][0]
    return query_vector

def match_text_images(clip_text_features, images):
    """
        This function is to find most similar image based on text query
        input: text feature, list of images (images is list of dict: url, emb, category, star)
    """
    image_features = []
    for img in images:
        if 'emb' in img and img['emb'] is not None:
            image_features.append(img['emb'])
    images = [remove_keys(img, ['emb']) for img in images]
    if image_features and len(image_features) == len(images):
        image_features = torch.tensor(image_features)
        probs = (100.0 * clip_text_features @ image_features.T).softmax(dim=-1)
        y_preds = ([item.item() for item in probs.argmax(dim=-1).cpu()])
        temp = images[0]
        images[0] = images[y_preds[0]]
        images[y_preds[0]] = temp
        index = y_preds[0]
        return images, index
    return images, -1
def match_text_pro(features, results):
    indexes = []
    for result in results:
        result['images'], index = match_text_images(features, result['images'])
        indexes.append(index)
    return results, indexes
def map_suburb_city_state(match, output_data):
    """
    query = "apartment at least 3 bed 2 bath, two floor, one garage in St Lucia, Brisbane and Southport, Gold coast with Siwmming pool, tennis court, and timber floor between 900k to 1.2M"
    {'bath': {'$gte': 2}, 'bed': {'$gte': 3}, 'expectedPrice':
    {'$gte': 900000, '$lte': 1200000},
    'features.floorCovering': {'$in': ['Hardwood']},
    'features.floorno': {'$gte': 2},
    'features.garage': {'$gte': 1},
    'outdoorAmenities': {'$all': ['Swimming Pool', 'Tennis Court']},
    'propertyType': 'apartment', or 'propertyType': {'$in': ['house','apartment']},
    'state': {'$in': ['Queensland']},
    'suburb': {'$in': ['St Lucia', 'Southport']}} or 'suburb': {'$in': ['St Lucia', 'Brisbane', 'Gold Coast']}
    """
    #match = {'bath': {'$gte': 2}, 'bed': {'$gte': 3}, 'city': {'$in': ['Brisbane', 'Gold Coast']}, 'expectedPrice': {'$gte': 900000, '$lte': 1200000}, 'features.buildingAmenities': 'Tennis Court', 'features.floorCovering': 'Timber', 'features.floorno': {'$gte': 2}, 'features.garage': {'$gte': 1}, 'features.outdoorAmenities': 'Swimming Pool', 'propertyType': 'apartment', 'suburb': 'St Lucia'}
    map_state_name = settings.map_state_name
    rvt_map = {}
    for k, v in map_state_name.items():
        rvt_map[v] = k

    check_places = ['suburb', 'city', 'state']
    found_place = False # found suburb, or city or state in match
    # extract places from match
    for nl_place in match.copy():
        if nl_place == 'state': # map standard state name such as map Queensland to QLD
            if isinstance(match[nl_place], dict)  and '$in' in match['state']:
                state_names = []
                for name in match[nl_place]['$in']:
                    if name in rvt_map:
                        name = rvt_map[name] # convert Queensland to QLD
                    state_names.append(name)
                match[nl_place]['$in'] = state_names # update
            elif isinstance(match[nl_place], str):
                state = match['state']
                if state in map_state_name:
                    match[nl_place] = state
                else:
                    match[nl_place] = rvt_map[state]  # make short name of state
        if nl_place == 'propertyType': # map house to House
            if isinstance(match[nl_place], dict) and '$in' in match[nl_place]:
                proTypes = []
                for proType in match[nl_place]['$in']:
                    proTypes.append(proType.title())
                match[nl_place]['$in'] = proTypes # update
            elif isinstance(match[nl_place], str):
                match[nl_place] = match[nl_place].title()

    for nl_place in match.copy():
        temp = nl_place  # {'$match': {'state':'Brisbane','bath': 3, 'bed': 2, 'city': 'Port Melbourne', 'features.floorno': 2, 'features.garage': 2, 'listingOption': 'sale', 'propertyType': 'townhouse/villa'}
        if temp in check_places:  # check if AI grab correct suburb and city and state name
            found_place = True
            found = False
            collect = []
            for check_place in check_places:  # if suburb = St Lucia, City = Kelvin Grove, State = Ashrow, collect all
                for item in output_data:
                    if isinstance(match[temp],str) and match[temp].lower().title() == item[check_place].lower().title():  # for example match[city] = item[suburb] or match[city] = item[state]
                        match[check_place] = match.pop(temp)  # replace city by suburb key
                        temp = check_place
                        found = True  # found first nl place found in all suburb city
            if not found:  # remove nl_place from match
                match = remove_keys(match, [nl_place])

    # get postcode to prepare for the case if no property found in this suburb, then search surrounded suburbs
    for item in output_data:
        if 'suburb' in match and 'city' in match:
            if match['suburb'] == item['suburb'].lower().title() and match['city'] == item['city'].lower().title():
                match['postcode'] = {'$in':[item['postcode']]}
                break
        if 'suburb' in match and 'state' in match: # get postcode
            if match['suburb'] == item['suburb'].lower().title() and match['state'].lower().title() == item['state'].lower().title():
                match['postcode'] = {'$in':[item['postcode']]}
                break
    return match
def get_pipeline(body):
    allsuburbcity = get_all_suburb_city()
    output_data = change_key('lga', 'city', allsuburbcity)
    user_message = body['query']
    table_schema = settings.table_schema
    schema_description = settings.table_schema
    list_features = ['features.parking', 'features.roof', 'features.view', 'features.rooms',
                   'features.indoorFeatures', 'features.coolingTypes', 'features.heatingTypes',
                   'features.heatingFuels', 'features.appliances', 'features.buildingAmenities', 'features.outdoorAmenities']
    system_temp = f"""
                You are a MongoDb expert. Create a syntactically correct raw aggregation query for the user question:

                This is the table schema : "{table_schema}"
                The following is an the example of one mongodb document: {schema_description}
                Return the raw aggregation pipeline in JSON.
                Output should be like  "pipeline": "json_object" and replace default value in {schema_description}
                Unless the user specifies in the question a specific number of examples to obtain, limit your query to at most 10 results using the $limit stage in an aggregation pipeline.
                Always use the field names as they appear in the collection. Be careful not to query for fields that do not exist.
                When dealing with dates, and if the question involves "today", use MongoDB's $currentDate, $dateFromString, or similar operators to work with the current date.
                DO NOT USE $TRIM etc to convert the price in raw aggregation, data is already in double.
                DO NOT to use $lookup
                If query is property address, should query in $street 
                When have feature in the pipeline, should access feature by using dot rather than using dict, only use following list of features in {list_features}
                If users search for query nearby or close to, pipeline should include features into features and should grab phase for features rather only one word
                Please avoid use '$elemMatch' and '$regex' in features
                ###{user_message}###
                """

    client = OpenAI(
        api_key=settings.OPENAI_API_KEY
    )
    response = client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_temp}
        ]
    )
    output_str = response.choices[0].message.content
    ori_output_json = json.loads(output_str)
    #ori_output_json = {'pipeline': [{'$match': {'city': 'Sydney', 'state': 'New South Wales'}}]}
    output_json = {"pipeline": [ori_output_json['pipeline'][0]]}
    print('output_json', output_json)
    pipeline = output_json['pipeline'][0]
    #if "$match" in pipeline: # nl query is successfully converted into pipeline mongodb
    match = pipeline["$match"]
    #match = {'bed': 3, 'suburb': 'St Lucia', 'city':'Brisbane','features.view': 'City', 'state': 'Queensland'}
    match = map_suburb_city_state(match, output_data) # will check this later
    output_json = {"pipeline": [{"$match":match}]} # update output_json
    return output_json
