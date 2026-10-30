
import torch
from typing import Optional, List
from pydantic import ConfigDict, BaseModel, Field, EmailStr
import concurrent.futures
import asyncio
from app.db.session import MongoDatabase
from pymongo.database import Database
from app.schemas import Prosale, Searchimgs
from operations.utils import get_payload
from operations.feature_extraction import AUS_MULTIMODAL_MATCHING_CLIP

db = MongoDatabase()

class PropertyForSaleCollection(BaseModel):
    """
    A container holding a list of `PropertyForSaleCollection` instances.

    This exists because providing a top-level array in a JSON response can be a [vulnerability](https://haacked.com/archive/2009/06/25/json-hijacking.aspx/)
    """
    properties: List[Prosale]

def GET_IMAGE_TENSORS(body_model, model):
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


async def search_img(query, limit, image_col, prosale_col):
    """
    Image search in image coll, and text search in image coll
    """
    model = 'clip'  # clip is used for now
    if 'https:' in query:
        modal = "image"
        body = {"images": [{"url": query}]}
        body_model = get_payload(body, model, modal)
        tensors = GET_IMAGE_TENSORS(body_model, model)
    else:
        modal = "text"
        tensors = [query]
    emb_norm = AUS_MULTIMODAL_MATCHING_CLIP.embedding(x=tensors, modal=modal) # feature extraction for searching
    queryVector = emb_norm.tolist()[0] # have only one query image/text for search in image coll
    pipeline = [
        {
            '$vectorSearch': {
                'index': 'image_vector_index',
                'path': 'emb',
                'queryVector': queryVector,
                'numCandidates': 10000,
                'limit': limit
            }
        }, {
            '$project': {
                '_id': 1,
                'star': 1,
                'url': 1,
                'category': 1,
                'score': {
                    '$meta': 'vectorSearchScore'
                }
            }
        }
    ]

    images = []
    queries = {}
    index = 0
    async for img in image_col.aggregate(pipeline):
        images.append(img)
        queries[str(index)] = [{'$match':{'imageid': {'$in': [img['_id']]}}}] # query matching image to find property in PropertyForSale col
        index = index + 1
    facet = [{'$facet':queries}] # create multiple queries to speed up query, number of queries is equal to number of matching images
    results = []
    cursor = prosale_col.aggregate(facet)
    data, *_ = await cursor.to_list(length=None)
    if data:
        for index in data: # loop over all results of facet, this is json result
            #print(index)
            temp = {} # save result including matched image and corresponding property
            if data[index]:
                # arranage matched image as hero by swapping based on _id
                matchedImgId = images[int(index)]['_id']
                #pos = [i for i in range(len()) if data[index]['imageid'][i] == matchedImgId]
                try:
                    value_index = data[index][0]['imageid'].index(matchedImgId)
                except:
                    value_index = -1;
                # swap images[value_index] to images[0]
                temp = data[index][0]['images'][0]
                data[index][0]['images'][0] = data[index][0]['images'][value_index]
                data[index][0]['images'][value_index] = temp
                # swap imageid
                temp = data[index][0]['imageid'][0]
                data[index][0]['imageid'][0] = data[index][0]['imageid'][value_index]
                data[index][0]['imageid'][value_index] = temp
                results.append(data[index][0])
    return results


if __name__ == '__main__':
    image = 'https://res.cloudinary.com/dldnumha3/image/upload/v1728105890/rysnayftcyz3tw2lfug7.jpg'
    pipelineImageProsales = asyncio.run(search_img(image, 30, db))
    results = multi_process(pipelineImageProsales)
    #asyncio.run(search_img(image, 20, db))
