from fastapi import APIRouter, Depends
try:
    from operations.model_load import AUS_IMAGE_MATCHING, AUS_IMAGE_CLASSIFICATION, AUS_MULTIMODAL_MATCHING_CLIP
except ImportError:
    AUS_IMAGE_MATCHING = None
    AUS_IMAGE_CLASSIFICATION = None
    AUS_MULTIMODAL_MATCHING_CLIP = None
from operations.feature_extraction import get_embedding
from config_fas import cat_dict_rvt_anno
from operations.utils import get_payload
from fastapi import Request
from typing import Optional
from typing import List, Union
import torch
from torch.nn import functional as F
from pydantic import BaseModel
from app.api import deps
from app import crud, models

environment = "dev"
router = APIRouter()

class JsonPath(BaseModel):
    Table: Optional[str] = None
    jsonpath: str

class image(BaseModel):
    url: str
class list_images(BaseModel):
    images: Union[List[image], None] = None

class paths(BaseModel):
    path: str

class Item(BaseModel):
    Table: Optional[str] = None
    Paths: Union[List[paths], None] = None

@router.post('/image/mat')
async def AUS_IMAGE_MATCHING_V1(
        info: Request,
        current_user: models.User = Depends(deps.get_current_active_user),):
    body = await info.json()
    emb_norm_list = []
    try:
        for image_byte in body['instances']:
            image_b64 = image_byte['image_bytes']['b64']
            image_tensor = AUS_IMAGE_MATCHING.pre_processing_image(image_b64=image_b64)
            output = AUS_IMAGE_MATCHING.forward(x=image_tensor)
            emb_norm = output[0] # output[0] is emb_norm, output[1] is emb, output[2[ is logic
            emb_norm = emb_norm.tolist()
            emb_norm_list.append(emb_norm[0])
        #return emb_norm_list
        return await crud.user.get_multi(db=db, page=page)
    except Exception as e:
        return {'status': False, 'msg': e}, 400
@router.post('/image/cls')
async def AUS_IMAGE_CLASSIFICATION_V1(info: Request,
                                      current_user: models.User = Depends(deps.get_current_active_user),
                                      ):
    body = await info.json()
    y_preds = []
    try:
        for image_byte in body['instances']:
            image_b64 = image_byte['image_bytes']['b64']
            image_tensor = AUS_IMAGE_CLASSIFICATION.pre_processing_image(image_b64=image_b64)
            output = AUS_IMAGE_CLASSIFICATION.forward(x=image_tensor)
            preds = output[2]# output[0] is emb_norm, output[1] is emb, output[2[ is logic
            y_pred = ([item.item() for item in preds.argmax(dim=-1).cpu()])
            y_preds.append(y_pred[0])
        return y_preds
    except Exception as e:
        return {'status': False, 'msg': e}, 400


def REMOVE_DUP(feats, body):
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
cat_dict_rvt_anno = {0:'Bed Room',1: 'Bath Room',2: 'Living Room',3: 'Laundry',4: 'Garage',5: 'Garden',6:'Kitchen',7: 'Exterior',8:'Deck',9: 'Study Room',10: 'Swimming Pool',
                     11:'Yard',12: 'Media Room',13:'Floor Plan',14:'Front View',15: 'Balcony',16:'Top View',17:'Gym', 18:'Formal Dining', 19:'Dining Room'}
@router.post('/multimodal/dedup_cls_embed')
async def AUS_MULTIMODAL_DEDUP_CLS_EMBED_V1(
        info: Request,
        current_user: models.User = Depends(deps.get_current_active_user),
        ):
    """
    Siwin is used to classify while there are such two options for image embedding as Clip, Siwin.
    Clip and ada-002 are used for text embedding
    :param modal:
    :param info:
    :return:
    """
    body = await info.json() # original input
    descriptions = [v for k,v in cat_dict_rvt_anno.items()] # category labels
    if 'description' in body:
        modal = "text"
    else:
        modal = "image"
    try:
        if modal == "text": # only get text embedding from clip model
            body_clip = get_payload(body, 'ada-002', modal)  # to be used for ada-002 embedding
            #if model == 'clip':
            #    emb_norm = AUS_MULTIMODAL_MATCHING_CLIP.embedding(x=[body_clip["text"]], modal=modal)
            # for now since clip only handle max length of 77 so ada-002 will be used instead
            emb_norm = get_embedding(body_clip["text"], "text-embedding-ada-002")# currently using ada-002
            body["text_embed"] = emb_norm
            body["para"] = body_clip["text"]
        elif modal == "image": # do classification and embedding
            model = 'swin' # clip is used for now
            body_model = get_payload(body, model, modal)
            y_preds = []
            #emb_norm_list = []
            image_tensors = GET_IMAGE_TENSORS(body_model, model)
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
            for img, cat in zip(body["images"], categories):
                img["category"] = cat  # update category
            body = REMOVE_DUP(emb_norm, body)
        return body
    except Exception as e:
        return {'status': False, 'msg': e}, 400
