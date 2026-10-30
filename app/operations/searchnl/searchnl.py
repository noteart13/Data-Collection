import torch
from random import choice
from openai import OpenAI
import json
from app.core.config import settings
import pandas as pd
from app.utils import get_json_files, get_all_suburb_city, change_key, remove_keys
from operations.feature_extraction import AUS_MULTIMODAL_MATCHING_CLIP
#from sentence_transformers import SentenceTransformer
import requests
import json
# Load the embedding model (https://huggingface.co/nomic-ai/nomic-embed-text-v1")
#model = SentenceTransformer("nomic-ai/nomic-embed-text-v1", trust_remote_code=True)
def get_embedding(data):
#   Generates vector embeddings for the given data.
   embedding = model.encode(data)
   return embedding.tolist()


def dict_sweep(input_dict, key):
    if isinstance(input_dict, dict):
        return {k: dict_sweep(v, key) for k, v in input_dict.items() if k != key}
    elif isinstance(input_dict, list):
        return [dict_sweep(element, key) for element in input_dict]
    else:
        return input_dict


def remove_keys(input_dict, keys):
    temp = input_dict
    for rem_key in keys:
        new_dict = dict_sweep(temp, rem_key)
        temp = new_dict
    return new_dict


def map_suburb_city_state(match, output_data):
    map_state_name = settings.map_state_name
    rvt_map = {}
    for k, v in map_state_name.items():
        rvt_map[v] = k
    # map standard state name such as map Queensland to QLD
    for k2 in match:  # get key and value in match dic
        if k2 == 'state' and match['state'] in rvt_map:
            state = match['state']
            match[k2] = rvt_map[state]  # make short name of state
    check_places = ['suburb', 'city', 'state']
    found_place = False # found suburb, or city or state in match
    for nl_place in match.copy():
        if 'propertyType' == nl_place and isinstance(match[nl_place], str):
            match[nl_place] = match[nl_place].title()
        temp = nl_place # {'$match': {'state':'Brisbane','bath': 3, 'bed': 2, 'city': 'Port Melbourne', 'features.floorno': 2, 'features.garage': 2, 'listingOption': 'sale', 'propertyType': 'townhouse/villa'}
        if temp in check_places:  # check if AI grab correct suburb and city and state name
            found_place = True
            found = False
            for check_place in check_places: # if suburb = St Lucia, City = Kelvin Grove, State = Ashrow, collect all
                #places = places.pop(index)
                #lsts = lsts.pop(index)
                for item in output_data:
                    if match[temp] == item[check_place].lower().title():  # for example match[city] = item[suburb] or match[city] = item[state]
                        match[check_place] = match.pop(temp) # replace city by suburb key
                        temp = check_place
                        found = True # found first nl place found in all suburb city, if there are several suburb
            new_match = match
            if not found:  # remove nl_place from match
                new_match = remove_keys(match, [nl_place])
    if not found_place:
        new_match = match
    # get postcode to prepare for the case if no property found in this suburb, then search surrounded suburbs
    for item in output_data:
        if 'suburb' in new_match and 'city' in new_match:
            if new_match['suburb'] == item['suburb'].lower().title() and new_match['city'] == item['city'].lower().title():
                new_match['postcode'] = {'$in':[item['postcode']]}
                break
        if 'suburb' in new_match and 'state' in new_match: # get postcode
            if new_match['suburb'] == item['suburb'].lower().title() and new_match['state'] == item['state'].lower().title():
                new_match['postcode'] = {'$in':[item['postcode']]}
                break
    return new_match

def get_pipeline(body):
    allsuburbcity = get_all_suburb_city()
    output_data = change_key('lga', 'city', allsuburbcity)
    user_message = body['query']
    table_schema = settings.table_schema
    schema_description = settings.table_schema
    system_temp = f"""
                You are a MongoDb expert. Create a syntactically correct raw aggregation query for the user question:

                This is the table schema : "{table_schema}"
                The following is an the example of one mongodb document: {schema_description}
                Return the raw aggregration pipeline in JSON.
                Output should be like  "pipeline": "json_object" and replace default value in {schema_description}
                Unless the user specifies in the question a specific number of examples to obtain, limit your query to at most 10 results using the $limit stage in an aggregation pipeline.
                Always use the field names as they appear in the collection. Be careful not to query for fields that do not exist.
                When dealing with dates, and if the question involves "today", use MongoDB's $currentDate, $dateFromString, or similar operators to work with the current date.
                DO NOT USE $TRIM etc to convert the price in raw aggregation, data is already in double.
                DO NOT to use $lookup
                ###{user_message}###
                """
    client = OpenAI(
        api_key=settings.OPENAI_API_KEY
    )
    response = client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        messages = [
            {"role": "system", "content": system_temp}
        ]
    )
    output_str = response.choices[0].message.content
    ori_output_json = json.loads(output_str)
    #ori_output_json = {'pipeline': [{'$match': {'city': 'Sydney', 'state': 'New South Wales'}}]}
    output_json = {"pipeline": [ori_output_json['pipeline'][0]]}
    pipeline = output_json['pipeline'][0]
    #if "$match" in pipeline: # nl query is successfully converted into pipeline mongodb
    match = pipeline["$match"]
    match = map_suburb_city_state(match, output_data)
    output_json = {"pipeline": [{"$match":match}]} # update output_json
    return output_json

def match_text_image(text_features, images):
    """
    This function is to find most similar image based on text query
    input: text feature, list of images (images is list of dict: url, emb, category, star)
    """
    image_features = []
    for image in images:
        image_features.append(image['emb'])
    image_features = torch.tensor(image_features)
    probs = (100.0 * text_features @ image_features.T).softmax(dim=-1)
    y_preds = ([item.item() for item in probs.argmax(dim=-1).cpu()])
    temp = images[0]
    images[0] = images[y_preds[0]]
    images[y_preds[0]] = temp
    images = remove_keys(images,['emb'])
    return images, y_preds

def match_text_prosale(query, prosales):
    text_features = AUS_MULTIMODAL_MATCHING_CLIP.embedding(x=[query], modal='text')
    for prosale in prosales:# should implement parallel here
        prosale['images'], y_preds = match_text_image(text_features, prosale['images'])
        prosale['bestMatchIndex'] = y_preds[0]
    return prosales

async def search_nl(query,limit, prosale_col):
    """
    Natural language and semantic search.
    """
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

    queryVector = response.text
    queryVector = json.loads(queryVector)
    queryVector = queryVector["embeddings"][0]

    filer_keys = ['propertyType', 'bed', 'bath', 'features.garage', 'features.floorno', 'area.totalarea',
                  'listingOption', 'expectedPrice', 'suburb', 'city', 'state','status']
    match_pipeline = get_pipeline({'query':query})
    filter = match_pipeline["pipeline"][0]['$match'] # get match json
    for key in filter:
        if key not in filer_keys:
            filter = remove_keys(filter, [key]) # remove features.outdoorfeatures or features.view etc, will handle this next versions
        elif key == 'suburb' and filter[
            key] == 'Camp Hill' and 'Camp Hill'.lower() not in query.lower():  # if get default info from prompt then filter is {}
            filter = {}
            break
    pipeline = [
        {
            '$vectorSearch': {
                'index': 'semantic_vector_index',
                'path': 'embSemanticNomicTextV1',
                "filter":{
                "$and": [filter]
                },
            'queryVector': queryVector,
            'numCandidates': 10000,
            'limit': limit,
            }},
            {'$project': settings.project},
    ]
    prosales = []
    async for prosale in prosale_col.aggregate(pipeline):
        prosales.append(prosale)
    if not prosales: # no result found with original query, then try to find sorrounded suburbs, if still unable to find results then by using semantic search
        if 'suburb' in filter: # to search sorrounded suburbs
            revised_filter = remove_keys(filter, [filter['suburb']]) # remove original suburb name
            exclude_this = filter['postcode']['$in']
            postcode = exclude_this[0]
            if postcode >= 4:
                sorroundedPostcode = choice(list(set(range(postcode - 3, postcode + 3)) - set(exclude_this)))
            elif postcode == 1:
                sorroundedPostcode = choice(list(set(range(postcode, postcode + 6)) - set(exclude_this)))
            elif postcode >=2 and postcode <= 4:
                sorroundedPostcode = choice(list(set(range(postcode - 1, postcode + 5)) - set(exclude_this)))
            revised_filter['postcode'] = {'$in': sorroundedPostcode}
            pipeline[0]['$vectorSearch']['filter']['$and'] = [revised_filter]
            async for prosale in prosale_col.aggregate(pipeline):
                prosales.append(prosale)
        else:
            pipeline[0]['$vectorSearch']['filter']['$and'] = [{}]
            async for prosale in prosale_col.aggregate(pipeline):
                prosales.append(prosale)
    prosales = match_text_prosale(query, prosales) # get most similar image to text query in each property so that users can see results visually
    return prosales
if __name__ == '__main__':
    # suburb_name = input("Enter the suburb name: ")
    #result = map_city("Lower Longley")
    #print(result)
    body = {"query": "1104/15 Young Street, Sydney NSW 2000"}
    pipeline = get_pipeline(body)
    print(pipeline)
    print("ok")
