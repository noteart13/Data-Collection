from typing import Any, List

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic.networks import EmailStr
from motor.core import AgnosticDatabase

from app import crud, models, schemas
from app.api import deps
from app.core.config import settings
from app.core import security
from app.utilities.email import (
    generate_new_account_email,
    send_email
)

router = APIRouter()

import os
from typing import Optional, List

from fastapi import FastAPI, Body, HTTPException, status
from fastapi.responses import Response
from pydantic import ConfigDict, BaseModel, Field, EmailStr
from pydantic.functional_validators import BeforeValidator

from typing_extensions import Annotated

from bson import ObjectId
import motor.motor_asyncio
from pymongo import ReturnDocument


router = APIRouter()

#MONGODB_URI=os.getenv("MONGODB_URI", "mongodb://localhost:27017/property_master")
#client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
#db = client.property_master

from pydantic.networks import UrlConstraints
# Represents an ObjectId field in the database.
# It will be represented as a `str` on the model so that it can be serialized to JSON.


@router.post(
    "/properties/",
    response_description="Add new property",
    #response_model=StudentCollection,
    response_model=schemas.PropertyForSaleModel,
    status_code=status.HTTP_201_CREATED,
    response_model_by_alias=False,
)
async def create_property(
        db: AgnosticDatabase = Depends(deps.get_db),
        property: schemas.PropertyForSaleModel = Body(...)
    ):
    """
    Insert a new property record.

    A unique `id` will be created and provided in the response.
    """
    #cursor = student_collection.find({ "gpa": {'$lt': 5}})
    #result = [document for document in await cursor.to_list(length=100)]
    #pro = property
    #user_in = schemas.UserCreate(password='password', email='dungnt81983@gmail.com', full_name='Dave Nguyen')
    #user = await crud.user.create(db, obj_in=user_in)
    #pro = property.model_dump(by_alias=True, exclude=["id"])
    #user_in = schemas.PropertyForSaleModel(property.model_dump(by_alias=True, exclude=["id"]))
    #user = await crud.user.create(db, obj_in=user_in)
    #return user
    property_collection = db.get_collection("PropertyForSale")
    output_json = {
        'pipeline': [{'$match': {'state': 'New South Wales'}},
                     {'$limit': 10}]}
    result = property_collection.aggregate(output_json['pipeline'])
    data = [item for item in result]
    new_property = await property_collection.insert_one(
        property.model_dump(by_alias=True, exclude=["id"])
    )
    created_property = await property_collection.find_one(
        {"_id": new_property.inserted_id}
    )
    return created_property
    #return StudentCollection(students=await student_collection.find({ "gpa": {'$lt': 5}}).to_list(1000))


@router.post(
    "/properties/",
    response_description="Add new property",
    #response_model=StudentCollection,
    response_model=schemas.PropertyForSaleModel,
    status_code=status.HTTP_201_CREATED,
    response_model_by_alias=False,
)
async def create_property(
        db: AgnosticDatabase = Depends(deps.get_db),
        property: schemas.PropertyForSaleModel = Body(...)
    ):
    """
    Insert a new property record.

    A unique `id` will be created and provided in the response.
    """
    #cursor = student_collection.find({ "gpa": {'$lt': 5}})
    #result = [document for document in await cursor.to_list(length=100)]
    #pro = property
    #user_in = schemas.UserCreate(password='password', email='dungnt81983@gmail.com', full_name='Dave Nguyen')
    #user = await crud.user.create(db, obj_in=user_in)
    #pro = property.model_dump(by_alias=True, exclude=["id"])
    #user_in = schemas.PropertyForSaleModel(property.model_dump(by_alias=True, exclude=["id"]))
    #user = await crud.user.create(db, obj_in=user_in)
    #return user
    property_collection = db.get_collection("PropertyForSale")
    output_json = {
        'pipeline': [{'$match': {'state': 'New South Wales'}},
                     {'$limit': 10}]}
    result = property_collection.aggregate(output_json['pipeline'])
    data = [item for item in result]
    new_property = await property_collection.insert_one(
        property.model_dump(by_alias=True, exclude=["id"])
    )
    created_property = await property_collection.find_one(
        {"_id": new_property.inserted_id}
    )
    return created_property
    #return StudentCollection(students=await student_collection.find({ "gpa": {'$lt': 5}}).to_list(1000))
"""

@router.get(
    "/properties/",
    response_description="List all properties",
    response_model=schemas.PropertyForSaleModel,
    response_model_by_alias=False,
)
async def list_properties():
    
    #List all of the propertied data in the database.

    #The response is unpaginated and limited to 1000 results.
   
    #return schemas.PropertyForSaleCollection(students=await property_collection.find().to_list(1000))


@router.get(
    "/properties/{id}",
    response_description="Get a single student",
    response_model=PropertyForSaleModel,
    response_model_by_alias=False,
)
async def show_property(id: str):

    #Get the record for a specific property, looked up by `id`.
 
    if (
        property := await property_collection.find_one({"_id": ObjectId(id)})
    ) is not None:
        return property

    raise HTTPException(status_code=404, detail=f"Student {id} not found")


@router.put(
    "/properties/{id}",
    response_description="Update a student",
    response_model=PropertyForSaleModel,
    response_model_by_alias=False,
)
async def update_property(id: str, property: UpdateStudentModel = Body(...)):
    
    #Update individual fields of an existing property record.

    #Only the provided fields will be updated.
    #Any missing or `null` fields will be ignored.
    
    property = {
        k: v for k, v in property.model_dump(by_alias=True).items() if v is not None
    }

    if len(property) >= 1:
        update_result = await property_collection.find_one_and_update(
            {"_id": ObjectId(id)},
            {"$set": property},
            return_document=ReturnDocument.AFTER,
        )
        if update_result is not None:
            return update_result
        else:
            raise HTTPException(status_code=404, detail=f"Student {id} not found")

    # The update is empty, but we should still return the matching document:
    if (existing_student := await property_collection.find_one({"_id": id})) is not None:
        return existing_student

    raise HTTPException(status_code=404, detail=f"Student {id} not found")


@router.delete("/properties/{id}", response_description="Delete a student")
async def delete_student(id: str):

    #Remove a single student record from the database.
  
    delete_result = await property_collection.delete_one({"_id": ObjectId(id)})

    if delete_result.deleted_count == 1:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    raise HTTPException(status_code=404, detail=f"Student {id} not found")
"""