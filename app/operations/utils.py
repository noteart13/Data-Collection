# import sys
import torch
from io import BytesIO
import base64
import logging
import os
# import platform
from pymongo import ReplaceOne
import torch
from pymongo.mongo_client import MongoClient
from pymongo.operations import SearchIndexModel
# from google.oauth2 import service_account

#from app.operations.hybrid_search import rrf_vector_search

try:
    import thop  # for FLOPS computation
except ImportError:
    thop = None
logger = logging.getLogger(__name__)

# import time
import requests
# import random
import numpy as np
from PIL import Image
import json

def attempt_load_onnx_spec(opt, weights_onnx):
    cuda = torch.cuda.is_available()
    w = opt[weights_onnx]
    #device = ort.get_device()
    #w = "/home/dung/Downloads/Trendii/yolov7/yolov7-tiny.onnx"
    providers = ['CUDAExecutionProvider', 'CPUExecutionProvider'] if cuda else ['CPUExecutionProvider']
    session = ort.InferenceSession(w, providers=providers)
    return session
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

#save_similarity_score

def save_json(dict, save_path, json_name):
    if not os.path.exists(save_path):
        os.makedirs(save_path, exist_ok=True)
    with open(os.path.join(save_path, json_name), "w") as f:
        json.dump(dict, f, cls=NumpyEncoder)
    with open(os.path.join(save_path, json_name), "r") as read_f:
        decodedArray = json.load(read_f)
    print('ok')
def read_json(json_name):
    with open(json_name, "r") as read_f:
        dict = json.load(read_f)
    print('ok')
    return dict



def get_img_b64(img_path, model):
    print("ok")
    """
    Takes an image, returns it base64 encoded
    param img_url: str
          img:
    return img_b64: str
    """
    if 'http' in img_path:
        img_url = img_path
        if model == "swin":
            img_b64 = base64.b64encode(requests.get(img_url).content).decode("utf-8")
        else:
            img_b64 = Image.open(requests.get((img_url), stream=True).raw)
    else:
        if model == "swin":
            img = Image.open(img_path)
            buffered = BytesIO()
            img = img.convert('RGB')
            img.save(buffered, format="JPEG")
            img_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
        else:
            img_b64 = Image.open(img_path)
    return img_b64

def get_payload(body, model, modal):
    """
    Format of images as follow
    images = {
        "images": [{"url": "https://res.cloudinary.com/dldnumha3/image/upload/v1715390232/d6bxvoo8xes9jcforkez.jpg",
                    "category": "", "star": True},
                   {"url": "https://res.cloudinary.com/dldnumha3/image/upload/v1715390213/otdvy55j9nwho1clype8.jpg",
                    "category": "", "star": False},
                   {"url": "https://res.cloudinary.com/dldnumha3/image/upload/v1715390194/ucidqwaqq74xgiskl8ul.jpg",
                    "category": "", "star": False}]
        "modal": "image",
        "modal": "swin"
                    }
    """
    if modal == "image":
        payload = {}
        temps = []
        for img in body["images"]:
            temp = {}
            img_b64 = get_img_b64(img["url"], model)
            temp["image_bytes"] = {"b64": img_b64}
            temps.append(temp)
        payload["images"] = temps
    else:
        payload = {}
        metadata = get_metadata(body)
        payload["text"] = metadata
    return payload

key_to_remove = ["_id","stakeholder", "contactInfo","listingOption","coordinates","availableFrom","expectedPrice","pricing",
                     "maintenanceCharge",
               "maintenancePeriod","serviceCost","priceIncludes","hidePriceOnWebsite","images",
                "published","recommended","createdAt", "updatedAt", "userId","availableFrom"]

items_to_remove = ['"$numberLong"', '"$numberInt"', '{', '}', '[', ']', '"', ': :', 'features:']
def dict_sweep(input_dict, key):
    if isinstance(input_dict, dict):
        return {k: dict_sweep(v, key) for k, v in input_dict.items() if k not in key}

    elif isinstance(input_dict, list):
        return [dict_sweep(element, key) for element in input_dict]
    else:
        return input_dict

def get_para(doc):
    doc = dict_sweep(doc, key_to_remove)
    para = json.dumps(doc)
    for item in items_to_remove:
        if item == ': :':
            para = para.replace(item, ':')
        else:
            para = para.replace(item, '')
    para = para.replace("{", "")
    return para
def get_payload(doc, model, modal):
    import ast
    doc = json.dumps(doc)
    doc = doc.replace('false','False')
    doc = doc.replace('true','True')
    doc = ast.literal_eval(doc)
    if modal == "image":
        payload = {}
        temps = []
        for img in doc["images"]:
            temp = {}
            img_b64 = get_img_b64(img["url"], model)
            temp["image_bytes"] = {"b64": img_b64}
            temps.append(temp)
        payload["images"] = temps
    elif modal == "text":
        payload = {}
        body = get_para(doc)
        payload["text"] = body
    return payload

def add_metadata(mycollection):
    for doc in mycollection.find({'metadata': {"$exists": True}}).limit(500):
        doc['metadata'] = get_metadata(doc)
        requests.append(ReplaceOne({'_id': doc['_id']}, doc))
    print('successful')
    mycollection.bulk_write(requests)
# prints: [[0.9927937  0.00421068 0.00299572]]

def zero_shot_clip():
    import numpy as np
    import torch
    import clip
    model, preprocess = clip.load("ViT-B/32")
    model.eval()
    input_resolution = model.visual.input_resolution
    context_length = model.context_length
    vocab_size = model.vocab_size

    print("Model parameters:", f"{np.sum([int(np.prod(p.shape)) for p in model.parameters()]):,}")
    print("Input resolution:", input_resolution)
    print("Context length:", context_length)
    print("Vocab size:", vocab_size)
    clip.tokenize("Hello World!")
    import os
    import matplotlib.pyplot as plt
    from PIL import Image
    import numpy as np

    import torch
    from torchvision.datasets import CIFAR100


    # images in skimage to use and their textual descriptions
    descriptions = {
        "Balcony": "Balcony",
        "Bathroom": "Bathroom",
        "Bedroom": "Bedroom",
        "Deck": "Deck",
        "Exterior": "Exterior",
        "Floorplan": "Floorplan",
        "Frontview": "Frontview",
        "Garage": "Garage"
    }
    descriptions = [
        "Balcony",
        "Bathroom",
        "Bedroom",
        "Deck",
        "Exterior",
        "Floorplan",
        "Frontview",
        "Garage"
    ]

    original_images = []
    images = []
    texts = []
    plt.figure(figsize=(16, 5))

    sample_dir = Path(os.getenv("SAMPLE_IMAGE_DIR", "data/samples"))
    ori_images = []
    for cat in ["balcony", "bathroom", "bedroom", "deck", "exterior", "floorplan", "frontview", "garage"]:
        img_p = sample_dir / cat / "image.jpg"
        if img_p.exists():
            ori_images.append(Image.open(img_p).convert("RGB"))
        else:
            ori_images.append(Image.new("RGB", (224, 224), color=(200, 200, 200)))
    for i, image in enumerate(ori_images):
        plt.subplot(2, 4, len(images) + 1)
        plt.imshow(image)
        plt.title(f"{descriptions[i]}\n{descriptions[i]}")
        plt.xticks([])
        plt.yticks([])

        original_images.append(image)
        images.append(preprocess(image))
        texts.append(descriptions[i])

    plt.tight_layout()

    image_input = torch.tensor(np.stack(images))
    text_tokens = clip.tokenize(["This is " + desc for desc in texts])

    with torch.no_grad():
        image_features = model.encode_image(image_input).float()
        text_features = model.encode_text(text_tokens).float()

    print(clip.available_models())

    image_features /= image_features.norm(dim=-1, keepdim=True)
    text_features /= text_features.norm(dim=-1, keepdim=True)
    similarity = text_features.cpu().numpy() @ image_features.cpu().numpy().T

    count = len(descriptions)

    plt.figure(figsize=(20, 14))
    plt.imshow(similarity, vmin=0.1, vmax=0.3)
    # plt.colorbar()
    plt.yticks(range(count), texts, fontsize=18)
    plt.xticks([])
    for i, image in enumerate(original_images):
        plt.imshow(image, extent=(i - 0.5, i + 0.5, -1.6, -0.6), origin="lower")
    for x in range(similarity.shape[1]):
        for y in range(similarity.shape[0]):
            plt.text(x, y, f"{similarity[y, x]:.2f}", ha="center", va="center", size=12)

    for side in ["left", "top", "right", "bottom"]:
        plt.gca().spines[side].set_visible(False)

    plt.xlim([-0.5, count - 0.5])
    plt.ylim([count + 0.5, -2])

    plt.title("Cosine similarity between text and image features", size=20)


    cifar100 = CIFAR100(os.path.expanduser("~/.cache"), transform=preprocess, download=True)

    text_descriptions = [f"{label}" for label in descriptions]
    text_tokens = clip.tokenize(text_descriptions)

    with torch.no_grad():
        text_features = model.encode_text(text_tokens).float()
        text_features /= text_features.norm(dim=-1, keepdim=True)

    text_probs = (100.0 * image_features @ text_features.T).softmax(dim=-1)
    top_probs, top_labels = text_probs.cpu().topk(5, dim=-1)

    plt.figure(figsize=(16, 16))

    for i, image in enumerate(original_images):
        plt.subplot(4, 4, 2 * i + 1)
        plt.imshow(image)
        plt.axis("off")

        plt.subplot(4, 4, 2 * i + 2)
        y = np.arange(top_probs.shape[-1])
        plt.grid()
        plt.barh(y, top_probs[i])
        plt.gca().invert_yaxis()
        plt.gca().set_axisbelow(True)
        plt.yticks(y, [descriptions[index] for index in top_labels[i].numpy()])
        plt.xlabel("probability")

    plt.subplots_adjust(wspace=0.5)
    plt.show()


cat_dict_rvt_anno = {0:'Bed Room',1: 'Bath Room',2: 'Living Room',3: 'Laundry',4: 'Garage',5: 'Garden',6:'Kitchen',7: 'Exterior',8:'Deck',9: 'Study Room',10: 'Swimming Pool',
                     11:'Yard',12: 'Media Room',13:'Floor Plan',14:'Front View',15: 'Balcony',16:'Top View',17:'Gym', 18:'Formal Dining', 19:'Dining Room'}

cat_dict_anno = {'bedroom':0,'bathroom':1,'livingroom':2,'laundry':3,'garage':4,'garden':5,'kitchen':6,'exterior':7,'deck':8,'studyroom':9,'swimmingpool':10,
                     'yard':11,'mediaroom':12,'floorplan':13,'frontview':14,'balcony':15,'topview':16,'gym':17,'formaldining':18,'diningroom':19}

import hashlib


def file_path(self, request, response=None, info=None, *, item=None):
    image_url_hash = hashlib.shake_256(request.url.encode()).hexdigest(5)
    image_perspective = request.url.split("/")[-2]
    image_filename = f"{image_url_hash}_{image_perspective}.jpg"

    return image_filename
def zero_shot_cls_clip():
    import numpy as np
    import torch
    import clip
    model, preprocess = clip.load("ViT-B/32")
    model.eval()
    input_resolution = model.visual.input_resolution
    context_length = model.context_length
    vocab_size = model.vocab_size

    print("Model parameters:", f"{np.sum([int(np.prod(p.shape)) for p in model.parameters()]):,}")
    print("Input resolution:", input_resolution)
    print("Context length:", context_length)
    print("Vocab size:", vocab_size)
    clip.tokenize("Hello World!")
    import os
    import matplotlib.pyplot as plt
    from PIL import Image
    import numpy as np
    import torch
    from torchvision.datasets import CIFAR100


    descriptions = [v for k,v in cat_dict_rvt_anno.items()]
    original_images = []
    images = []
    texts = []
    plt.figure(figsize=(4, 5))

    sample_dir = Path(os.getenv("SAMPLE_IMAGE_DIR", "data/samples"))
    ori_images = []
    for cat in ["balcony", "bathroom"]:
        img_p = sample_dir / cat / "image.jpg"
        if img_p.exists():
            ori_images.append(Image.open(img_p).convert("RGB"))
        else:
            ori_images.append(Image.new("RGB", (224, 224), color=(200, 200, 200)))
    y_preds = []
    for i, image in enumerate(ori_images):
        plt.subplot(2, 4, len(images) + 1)
        plt.imshow(image)
        plt.title(f"{descriptions[i]}\n{descriptions[i]}")
        plt.xticks([])
        plt.yticks([])
        original_images.append(image)
        images.append(preprocess(image))
        texts.append(descriptions[i])

    plt.tight_layout()

    image_input = torch.tensor(np.stack(images))
    text_tokens = clip.tokenize([desc for desc in descriptions])

    with torch.no_grad():
        image_features = model.encode_image(image_input).float()
        text_features = model.encode_text(text_tokens).float()

    clip.available_models()

    image_features /= image_features.norm(dim=-1, keepdim=True)
    text_features /= text_features.norm(dim=-1, keepdim=True)
    similarity = text_features.cpu().numpy() @ image_features.cpu().numpy().T
    text_descriptions = [f"{label}" for label in descriptions]
    text_tokens = clip.tokenize(text_descriptions)

    with torch.no_grad():
        text_features = model.encode_text(text_tokens).float()
        text_features /= text_features.norm(dim=-1, keepdim=True)
    text_probs = (100.0 * image_features @ text_features.T)
    text_probs = (100.0 * image_features @ text_features.T).softmax(dim=-1)
    y_pred = ([item.item() for item in text_probs.argmax(dim=-1).cpu()])
    y_preds.append(y_pred[0])
    category = cat_dict_rvt_anno[y_pred[0]]
    top_probs, top_labels = text_probs.cpu().topk(5, dim=-1)
    count = len(descriptions)
    plt.figure(figsize=(20, 14))
    plt.imshow(similarity, vmin=0.1, vmax=0.3)
    # plt.colorbar()
    plt.yticks(range(count), texts, fontsize=18)
    plt.xticks([])
    for i, image in enumerate(original_images):
        plt.imshow(image, extent=(i - 0.5, i + 0.5, -1.6, -0.6), origin="lower")
    for x in range(similarity.shape[1]):
        for y in range(similarity.shape[0]):
            plt.text(x, y, f"{similarity[y, x]:.2f}", ha="center", va="center", size=12)

    for side in ["left", "top", "right", "bottom"]:
        plt.gca().spines[side].set_visible(False)

    plt.xlim([-0.5, count - 0.5])
    plt.ylim([count + 0.5, -2])

    plt.title("Cosine similarity between text and image features", size=20)


    cifar100 = CIFAR100(os.path.expanduser("~/.cache"), transform=preprocess, download=True)

    text_descriptions = [f"This is a photo of a {label}" for label in descriptions]
    text_tokens = clip.tokenize(text_descriptions)

    with torch.no_grad():
        text_features = model.encode_text(text_tokens).float()
        text_features /= text_features.norm(dim=-1, keepdim=True)

    text_probs = (100.0 * image_features @ text_features.T).softmax(dim=-1)
    top_probs, top_labels = text_probs.cpu().topk(5, dim=-1)

    plt.figure(figsize=(16, 16))

    for i, image in enumerate(original_images):
        plt.subplot(4, 4, 2 * i + 1)
        plt.imshow(image)
        plt.axis("off")

        plt.subplot(4, 4, 2 * i + 2)
        y = np.arange(top_probs.shape[-1])
        plt.grid()
        plt.barh(y, top_probs[i])
        plt.gca().invert_yaxis()
        plt.gca().set_axisbelow(True)
        plt.yticks(y, [descriptions[index] for index in top_labels[i].numpy()])
        plt.xlabel("probability")
    plt.subplots_adjust(wspace=0.5)
    plt.show()
def delete_index(coll,name):
    # Connect to your Atlas deployment
    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/property_master")
    client = MongoClient(uri)
    # Access your database and collection
    database = client[os.getenv("MONGODB_DATABASE", "property_master")]
    collection = database[coll]
    # Delete your search index
    collection.drop_search_index(name)
def create_index_():

    # Connect to your Atlas deployment
    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/property_master")
    client = MongoClient(uri)

    # Access your database and collection
    database = client[os.getenv("MONGODB_DATABASE", "property_master")]
    collection = database["Image"]
    # Create your index model, then create the search index
    search_index_model = SearchIndexModel(
        definition={
            "fields": [
                {
                    "type": "vector",
                    "path": "emb",
                    "numDimensions": 768,
                    "similarity": "cosine"
                }
            ]
        },
        name="image_vector_index",
        type="vectorSearch",
    )

    result = collection.create_search_index(model=search_index_model)
    print(result)

def hybrid_search_index():
    import motor
    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/property_master")
    client = MongoClient(uri)
    database = client[os.getenv("MONGODB_DATABASE", "property_master")]
    collection = database["embedded_movies"]
    rrf_vector_search = SearchIndexModel(
        name = "rrf-vector-search",
        type = "vectorSearch",
        definition = {
            "fields": [
                {
                    "type": "vector",
                    "numDimensions": 1536,
                    "path": "plot_embedding",
                    "similarity": "dotProduct"
                }
            ]
        }
    )
    result = collection.create_search_index(model=rrf_vector_search)
    print('rrf_vector_search',result)

    rrf_full_text_search = SearchIndexModel(
        name = "rrf-full-text-search",
        type = "search",
        definition = {
            "mappings": {
                "dynamic": False,
                "fields": {
                    "title": [{
                        "type": "string"
                    }]
                }
            }
        }
    )
    result = collection.create_search_index(model=rrf_full_text_search)
    print("rrf_full_text_search", result)
def create_index(coll,dim, name, path):

    # Connect to your Atlas deployment
    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/property_master")
    client = MongoClient(uri)

    # Access your database and collection
    database = client[os.getenv("MONGODB_DATABASE", "property_master")]
    collection = database[coll]
    # Create your index model, then create the search index
    """
    search_index_model = SearchIndexModel(
        definition={
            "fields": [
                {
                    "type": "vector",
                    "path": path,
                    "numDimensions": dim,
                    "similarity": "cosine"
                }
            ]
        },
        name=name,
        type="vectorSearch",
    )
    """
    search_index_model = SearchIndexModel(
        definition={
        "fields": [
            {
                "type": "vector",
                "path": path,
                "numDimensions": dim,
                "similarity": "cosine"
            },
            {
                "type": "filter",
                "path": "bed"
            },
            {
                "type": "filter",
                "path": "bath"
            },
            {
                "type": "filter",
                "path": "features.floorno"
            },
            {
                "type": "filter",
                "path": "features.garage"
            },
            {
                "type": "filter",
                "path": "listingOption"
            },
            {
                "type": "filter",
                "path": "propertyType"
            },
            {
                "type": "filter",
                "path": "expectedPrice"
            },
            {
                "type": "filter",
                "path": "street"
            },
            {
                "type": "filter",
                "path": "title"
            },
            {
                "type": "filter",
                "path": "description"
            },
            {
                "type": "filter",
                "path": "suburb"
            },
            {
                "type": "filter",
                "path": "city"
            },
            {
                "type": "filter",
                "path": "state"
            },
            {
                "type": "filter",
                "path": "area.totalarea"
            }
        ]
    },
    name = name,
    type = "vectorSearch",
    )
    result = collection.create_search_index(model=search_index_model)
    print(result)

def create_geo_index(coll, name):
    # Connect to your Atlas deployment
    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/property_master")
    client = MongoClient(uri)
    database = client[os.getenv("MONGODB_DATABASE", "property_master")]
    collection = database[coll]
    # Create your index model, then create the search index

    search_index_model = SearchIndexModel(
    {
        "mappings": {
            "dynamic": True,
            "fields": {
                "location": {
                    "type": "geo"
                },
                "embSemanticNomicTextV1": {
                    "dimensions": 768,
                    "similarity": "cosine",
                    "type": "knnVector"
                }
            },
        }
    },
    name = name,
    )
    result = collection.create_search_index(model=search_index_model)
    print(result)
def view_index():
    # Connect to your Atlas deployment
    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/property_master")
    client = MongoClient(uri)
    # Access your database and collection
    database = client[os.getenv("MONGODB_DATABASE", "property_master")]
    collection = database["Image"]
    # Get a list of the collection's search indexes and print them
    cursor = collection.list_search_indexes()
    for index in cursor:
        print(index)
import datetime

from google.cloud import storage


def generate_upload_signed_url_v4(bucket_name, blob_name):
    """Generates a v4 signed URL for uploading a blob using HTTP PUT.

    Note that this method requires a service account key file. You can not use
    this if you are using Application Default Credentials from Google Compute
    Engine or from the Google Cloud SDK.
    """
    # bucket_name = 'your-bucket-name'
    # blob_name = 'your-object-name'

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    url = blob.generate_signed_url(
        version="v4",
        # This URL is valid for 15 minutes
        expiration=datetime.timedelta(minutes=15),
        # Allow PUT requests using this URL.
        method="PUT",
        content_type="application/octet-stream",
    )

    print("Generated PUT signed URL:")
    print(url)
    print("You can use this URL with any user agent, for example:")
    print(
        "curl -X PUT -H 'Content-Type: application/octet-stream' "
        "--upload-file my-file '{}'".format(url)
    )
    return url


from google.cloud import storage
import requests
import urllib.parse

# ************* SET THESE VARIABLES *************
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "property-media-bucket")
ROBOFLOW_API_KEY = os.getenv("ROBOFLOW_API_KEY", "YOUR_ROBOFLOW_API_KEY")
ROBOFLOW_PROJECT_NAME = os.getenv("ROBOFLOW_PROJECT_NAME", "YOUR_ROBOFLOW_PROJECT_NAME")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json")
# ***********************************************

def get_gcs_signed_url(bucket_name: str, blob_name: str) -> str:
    """Generate a signed URL for a GCS object."""
    GCS_PROJECT = os.getenv("GCS_PROJECT", "property-project")
    try:
        credentials = service_account.Credentials.from_service_account_file(GOOGLE_APPLICATION_CREDENTIALS)
        storage_client = storage.Client(project=GCS_PROJECT, credentials=credentials)
    except Exception:
        storage_client = storage.Client()
    blob_name = os.getenv("SAMPLE_BLOB_NAME", "data/sample.tfrecord.gz")
    # Fetch list of available blobs
    available_blobs = get_gcs_objects(bucket_name) if bucket_name else []
    bucket = storage_client.bucket(bucket_name)
    for blob_name in available_blobs:
        #blob_url = get_gcs_signed_url(GCS_BUCKET_NAME, blob)

        #storage_client = storage.Client()
        #bucket = storage_client.bucket(bucket_name)
        #blob = bucket.blob(blob_name)

        #storage_client = storage.Client()
        #bucket = storage_client.bucket(bucket_name)
        #blob = bucket.blob(blob_name)
        blob = bucket.get_blob(blob_name)
        url = blob.generate_signed_url(
            version="v4",
            # This URL is valid for 15 minutes
            expiration=datetime.timedelta(minutes=15),
            # Allow GET requests using this URL.
            method="GET",
        )



    #blob = bucket.get_blob(blob_name)

    return url


def get_gcs_objects(bucket_name: str) -> list:
    """Fetch the list of object keys in the given GCS bucket."""
    #storage_client = storage.Client.from_service_account_json(GOOGLE_APPLICATION_CREDENTIALS)
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(bucket_name)
    blobs = bucket.list_blobs()

    object_names = []
    index = 0
    for blob in blobs:
        index = index + 1
        if 'image_classification' in blob.name:
            object_names.append(blob.name)
            if index > 150:
                break
    return object_names


def upload_to_roboflow(api_key: str, project_name: str, presigned_url: str, img_name='', split="train"):
    """Upload an image to Roboflow."""
    API_URL = "https://api.roboflow.com"
    if img_name == '':
        img_name = presigned_url.split("/")[-1]

    upload_url = "".join([
        API_URL + "/dataset/" + project_name + "/upload",
        "?api_key=" + api_key,
        "&name=" + img_name,
        "&split=" + split,
        "&image=" + urllib.parse.quote_plus(presigned_url),
    ])
    response = requests.post(upload_url)

    # Check response code
    if response.status_code == 200:
        print(f"Successfully uploaded {img_name} to {project_name}")
        return True
    else:
        print(f"Failed to upload {img_name}. Error: {response.content.decode('utf-8')}")
        return False

if __name__ == '__main__':

    #delete_index('PropertyForSale', "semantic_vector_index")# delete semantic vector index before updating
    #delete_index('Image', "image_vector_index", "emb")  # delete image vector index before updating
    #print("ok")
    # create search index for image search
    #create_index('Image', 768, "image_vector_index", "emb")
    #create_index('PropertyForSale', 768, "semantic_vector_index", "embSemanticNomicTextV1") # create semaantic vector index
    #create_geo_index('listingsAndReviews','proSaleGeoIndex')
    #create_geo_index('PropertyForSale', 'proSaleGeoIndex')
    hybrid_search_index()
    #view_index()

