from pymongo.mongo_client import MongoClient
import json
import os
from openai import OpenAI
from typing import Optional, List, Dict, Any, Union

from pydantic import BaseModel, Json, ValidationError, ConfigDict, Field, EmailStr, HttpUrl
from pydantic.functional_validators import BeforeValidator
from typing_extensions import Annotated
from bson import ObjectId
import motor.motor_asyncio
from pymongo import ReturnDocument

PyObjectId = Annotated[str, BeforeValidator(str)]


class AnyJsonModel(BaseModel):
    json_obj: Json[Any]


class ConstrainedJsonModel(BaseModel):
    json_obj: Json[List[int]]


class Contact(BaseModel):
    firstName: str = Field(...)
    lastName: str = Field(...)
    email: str = Field(...)
    phoneNumber: str = Field(...)


class InspectionSchedule(BaseModel):
    id: str
    contacts: List[Contact]
    startTime: str


class Image(BaseModel):
    url: HttpUrl
    name: str


class Item(BaseModel):
    name: str
    description: str | None = None
    price: float
    tax: float | None = None
    tags: set[str] = set()
    images: list[Image] | None = None


class PropertyFeatures(BaseModel):
    garage: Optional[str] = None
    floorno: Optional[str] = None
    basement: Optional[str] = None
    floorCovering: Optional[List[str]] = None
    parking: Optional[List[str]] = None
    roof: Optional[List[str]] = None
    view: Optional[List[str]] = None
    rooms: Optional[List[str]] = None
    indoorFeatures: Optional[List[str]] = None
    coolingTypes: Optional[List[str]] = None
    heatingTypes: Optional[List[str]] = None
    heatingFuels: Optional[List[str]] = None
    appliances: Optional[List[str]] = None
    buildingAmenities: Optional[List[str]] = None
    outdoorAmenities: Optional[List[str]] = None
    displayAddress: Optional[str] = None


class PropertyArea(BaseModel):
    totalarea: Optional[str] = ""
    unit: Optional[str] = "sqM"


class PropertyPricing(BaseModel):
    pricingOptions: Optional[str] = None
    authority: Optional[str] = None
    priceIncludes: Optional[List[str]] = None


class PropertyImage(BaseModel):
    url: str
    category: Optional[str] = None
    star: Optional[bool] = False


class PropertyForSale(BaseModel):
    """
    Container for an Australian property listing record.
    """
    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    stakeholder: str = Field(default="agent")
    contactInfo: Optional[List[Contact]] = None
    listingOption: Optional[str] = "sale"
    title: Optional[str] = None
    propertyType: Optional[str] = None
    description: Optional[str] = None
    constructionYear: Optional[str] = None
    structuralRemodelYear: Optional[str] = None
    architecturalStyle: Optional[str] = None
    status: Optional[str] = "for-sale"
    state: Optional[str] = None
    city: Optional[str] = None
    street: Optional[str] = None
    coordinates: Optional[Dict[str, Any]] = None
    bed: Optional[str] = None
    bath: Optional[str] = None
    features: Optional[PropertyFeatures] = None
    area: Optional[PropertyArea] = None
    expectedPrice: Optional[Union[str, float, int]] = None
    pricing: Optional[PropertyPricing] = None
    images: Optional[List[PropertyImage]] = None
    published: Optional[bool] = True
    recommended: Optional[bool] = False
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    userId: Optional[Dict[str, Any]] = None
    slug: Optional[str] = None


def mongo_connect(uri: str):
    """
    Connect to MongoDB deployment and test connection with ping.
    """
    from pymongo.mongo_client import MongoClient
    from pymongo.server_api import ServerApi

    try:
        client = MongoClient(uri, server_api=ServerApi('1'))
        client.admin.command('ping')
        print("Pinged your deployment. You successfully connected to MongoDB!")
        return client
    except Exception as e:
        print(f"Error connecting to MongoDB: {e}")
        return None


def read_mongodb_query(user_input: str, table_schema: dict, schema_description: str, database: str, collectionName: str):
    """Generates a MongoDB raw aggregation pipeline based on user input and retrieves data from a MongoDB collection.

    :param user_input: User's question or input that guides the query generation.
    :param table_schema: A dictionary or string representation of the table schema.
    :param schema_description: A string that describes the schema of the data.
    :param database: The name of the MongoDB database to query.
    :param collectionName: The name of the MongoDB collection to query.
    :return: A list of dictionaries containing data retrieved from the MongoDB collection, or an error message.
    """
    max_retries = 3
    retries = 0
    user_message = user_input

    system_temp = f"""
    You are a MongoDb expert. Create a syntactically correct raw aggregation query for the user question:

    This is the table schema : "{table_schema}"
    The following is an example of one mongodb document: {schema_description}
    Return the raw aggregation pipeline in JSON.
    output should be like: {{"pipeline": [...]}}
    Unless the user specifies in the question a specific number of examples to obtain, limit your query to at most 10 results using the $limit stage in an aggregation pipeline.
    Always use the field names as they appear in the collection. Be careful not to query for fields that do not exist.
    When dealing with dates, and if the question involves "today", use MongoDB's $currentDate, $dateFromString, or similar operators to work with the current date.
    DO NOT USE $TRIM etc to convert the price in raw aggregation, data is already in double.
    DO NOT use $lookup
    ###{user_message}###
    """
    
    openai_api_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_api_key:
        print("Warning: OPENAI_API_KEY not configured. Natural language query generation will not work.")
        return []

    client = OpenAI(api_key=openai_api_key)

    while retries < max_retries:
        mongo_client = None
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo-1106",
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_temp}
                ]
            )

            output_str = response.choices[0].message.content
            output_json = json.loads(output_str)
            if 'pipeline' not in output_json:
                raise ValueError("Invalid format: 'pipeline' key not found")

            uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
            mongo_client = MongoClient(uri)
            db = mongo_client[database]
            collection = db[collectionName]

            result = collection.aggregate(output_json['pipeline'])
            print(f"(EXECUTED PIPELINE: {output_json['pipeline']})")
            data = list(result)
            return data
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Error encountered: {e}. Retrying ({retries+1}/{max_retries})...")
            retries += 1
            if retries == max_retries:
                return f"Error: Could not figure the query out after maximum retries: {e}"
        except Exception as e:
            print(f"Database/Runtime error: {e}")
            return f"Error: {e}"
        finally:
            if mongo_client:
                mongo_client.close()


if __name__ == '__main__':
    schema_dict = {
        "_id": "66af3b44e74bcbfbeb2bc59b",
        "stakeholder": "agent",
        "contactInfo": [{"firstName": "KIM", "lastName": "KIRKBY", "email": "kim@kkpretsige.com", "phoneNumber": "0402400497"}],
        "listingOption": "sale",
        "title": "listing title",
        "propertyType": "house",
        "status": "for-sale",
        "state": "Queensland",
        "city": "Camp Hill",
        "street": "576 Old Cleveland Road, Camp Hill QLD, Australia",
        "coordinates": {"lat": "-27.4922227", "lng": "153.0738727"},
        "bed": "3",
        "bath": "2",
        "features": {
            "garage": "2",
            "floorno": "1",
            "basement": "None",
            "floorCovering": ["Carpet", "Hardwood"],
            "parking": ["Carport"]
        },
        "area": {"totalarea": "1157", "unit": "sqM"},
        "expectedPrice": 2000000,
        "published": True
    }

    print("Testing Pydantic schema validation...")
    try:
        validated = PropertyForSale(**schema_dict)
        print("Schema validation successful:", validated.title)
    except Exception as e:
        print("Validation error:", e)

    print("Done")
