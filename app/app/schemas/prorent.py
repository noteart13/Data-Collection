from __future__ import annotations

from typing_extensions import Annotated
from pydantic import BaseModel, StrictInt, ConfigDict, Field, EmailStr, StringConstraints, field_validator, SecretStr
from odmantic import ObjectId
from typing import TYPE_CHECKING, Any, Optional
from datetime import datetime
from typing import Optional, List
from pydantic.functional_validators import BeforeValidator
PyObjectId = Annotated[str, BeforeValidator(str)]
def datetime_now_sec():
    return datetime.now().replace(microsecond=0)

class Image(BaseModel):
    url: str
    category: str
    star: bool
class Contact(BaseModel):
    firstName: str = Field(...)
    lastName: str = Field(...)
    # email: EmailStr = Field(...)
    # phoneNumber: str = Field(...)
    email: Optional[EmailStr] = Field(None)
    phoneNumber: Optional[str] = Field(None) # Change to Optional
    photo: Optional[str] = Field(None)
    # @field_validator('phoneNumber', mode='before')
    # def set_phone_number(cls, v):
    #     return v or ''  # Return empty string if None is provided
    
class Coordinate(BaseModel):
    lat: Optional[float] = Field(None)
    lng: Optional[float] = Field(None)

class Features(BaseModel):
    garage: int
    floorno: int
    basement: str
    floorCovering: list[str]
    parking: list[str]
    roof: list[str]
    view: list[str]
    rooms: list[str]
    indoorFeatures: list[str]
    coolingTypes: list[str]
    heatingTypes: list[str]
    heatingFuels: list[str]
    appliances: list[str]
    buildingAmenities: list[str]
    outdoorAmenities: list[str]
    displayAddress: str
class Pricing(BaseModel):
    pricingOptions: str
    authority: str
    priceIncludes: Optional[str] | None = None
class Area(BaseModel):
    totalarea: Optional[int] | None = None
    unit: Optional[str] | None = None

class ProrentInDB(BaseModel):
    """
    Container for a single student record.
    """

    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    id: ObjectId | None = None
    stakeHolder: Optional[str] = Field(...)
    contactInfo: Optional[list[Contact]] | None = None
    listingOption: Optional[str] = None
    title: Optional[str] = None
    slug: Optional[str] = None
    url: Optional[str] | None = None
    propertyType: Optional[str] = None
    description: Optional[str] = None
    constructionYear: Optional[str] = None
    structuralRemodelYear: Optional[str] = None
    architecturalStyle: Optional[str] = None
    status: Optional[str] = None
    state: Optional[str] = None
    suburb: Optional[str] = None
    postcode: Optional[int] = None
    city: Optional[str] = None
    street: Optional[str] = None
    coordinates: Optional[Coordinate] | None = None
    bed: Optional[int] = Field(...)
    bath: Optional[int] = Field(...)
    images: Optional[list[Image]] | None = None
    features: Optional[Features]
    area: Area
    rentPrice: Optional[StrictInt] = Field(default=None)
    pricing: Optional[Pricing]
    published: Optional[bool] = Field(default=False)
    recommended: Optional[bool] = Field(default=False)
    userId: Optional[ObjectId] = Field(alias="_id", default=None)
    agencyid: Optional[ObjectId] = Field(alias="_id", default=None)
    agentid: Optional[list[ObjectId]] | None = None
    schoolid: Optional[list[ObjectId]] | None = None
    imageid: Optional[list[ObjectId]] | None = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)



class Prorent(BaseModel):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    #id: Optional[PyObjectId] = Field(alias="_id", default=None)
    id: ObjectId | None = None
    stakeHolder: Optional[str] | None = None
    contactInfo: Optional[list[Contact]] | None = None
    listingOption: Optional[str] | None = None
    title: Optional[str] | None = None
    slug: Optional[str] | None = None
    url: Optional[str] | None = None
    propertyType: Optional[str] | None = None
    description: Optional[str] | None = None
    constructionYear: Optional[str] | None = None
    structuralRemodelYear: Optional[str] | None = None
    architecturalStyle: Optional[str] | None = None
    status: Optional[str] | None = None
    state: Optional[str] | None = None
    suburb: Optional[str] | None = None
    postcode: Optional[int] | None = None
    city: Optional[str] | None = None
    street: Optional[str] | None = None
    coordinates: Optional[Coordinate] | None = None
    bed: Optional[int] | None = None
    bath: Optional[int] | None = None
    images: Optional[list[Image]] | None = None
    features: Optional[Features] | None = None
    area: Optional[Area] | None = None
    rentPrice: Optional[int] | None = None
    pricing: Optional[Pricing]
    published: bool = Field(default=False)
    recommended: bool = Field(default=False)
    userId: Optional[ObjectId] | None = None
    agencyid: Optional[ObjectId] | None = None
    agentid: Optional[list[ObjectId]] | None = None
    schoolid: Optional[list[ObjectId]] | None = None
    imageid: Optional[list[ObjectId]] | None = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
class ProrentCreate(BaseModel):
    """
    Container for a single student record.
    """

    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    #id: Optional[PyObjectId] = Field(alias="_id", default=None)
    #id: ObjectId | None = None
    stakeHolder: Optional[str] | None = None
    contactInfo: Optional[list[Contact]] | None = None
    listingOption: Optional[str] | None = None
    title: Optional[str] | None = None
    slug: Optional[str] | None = None
    url: Optional[str] | None = None
    propertyType: Optional[str] | None = None
    description: Optional[str] | None = None
    constructionYear: Optional[str] | None = None
    structuralRemodelYear: Optional[str] | None = None
    architecturalStyle: Optional[str] | None = None
    status: Optional[str] | None = None
    state: Optional[str] | None = None
    suburb: Optional[str] | None = None
    postcode: Optional[int] | None = None
    city: Optional[str] | None = None
    street: Optional[str] | None = None
    coordinates: Optional[Coordinate] | None = None
    bed: Optional[int] | None = None
    bath: Optional[int] | None = None
    images: Optional[list[Image]] | None = None
    features: Optional[Features] | None = None
    area: Area | None = None
    rentPrice: Optional[int] | None = None
    pricing: Optional[Pricing]
    published: Optional[bool] | None = None
    recommended: Optional[bool] | None = None
    userId: Optional[PyObjectId] | None = None
    agencyid: Optional[PyObjectId] | None = None
    agentid: Optional[list[PyObjectId]] | None = None
    schoolid: Optional[list[PyObjectId]] | None = None
    imageid: Optional[list[PyObjectId]] | None = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
class ProrentUpdate(BaseModel):
    """
    Container for a single student record.
    """

    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    #id: Optional[PyObjectId] = Field(alias="_id", default=None)
    stakeHolder: Optional[str] | None = None
    contactInfo: Optional[list[Contact]] | None = None
    listingOption: Optional[str] | None = None
    title: Optional[str] | None = None
    slug: Optional[str] | None = None
    url: Optional[str] | None = None
    propertyType: Optional[str] | None = None
    description: Optional[str] | None = None
    constructionYear: Optional[str] | None = None
    structuralRemodelYear: Optional[str] | None = None
    architecturalStyle: Optional[str] | None = None
    status: Optional[str] | None = None
    state: Optional[str] | None = None
    suburb: Optional[str]
    postcode: Optional[int] | None = None
    city: Optional[str] | None = None
    street: Optional[str] | None = None
    coordinates: Optional[Coordinate] | None = None
    bed: Optional[int] | None = None
    bath: Optional[int] | None = None
    images: Optional[list[Image]] | None = None
    features: Optional[Features]
    area: Area
    rentPrice: Optional[StrictInt] | None = None
    pricing: Optional[Pricing]
    published: Optional[bool] | None = None
    recommended: Optional[bool] | None = None
    userId: Optional[ObjectId] | None = None
    agencyid: Optional[ObjectId] | None = None
    agentid: Optional[list[ObjectId]] | None = None
    schoolid: Optional[list[ObjectId]] | None = None
    imageid: Optional[list[ObjectId]] | None = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)

class PropertyForRentCollection(BaseModel):
    """
    A container holding a list of `PropertyForSaleCollection` instances.

    This exists because providing a top-level array in a JSON response can be a [vulnerability](https://haacked.com/archive/2009/06/25/json-hijacking.aspx/)
    """

    properties: List[PropertyForRentModel]


