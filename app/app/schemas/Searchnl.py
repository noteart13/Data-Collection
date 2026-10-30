from __future__ import annotations

from typing_extensions import Annotated
from pydantic import BaseModel, StrictInt, ConfigDict, Field, EmailStr, StringConstraints, field_validator, SecretStr
from odmantic import ObjectId
from typing import TYPE_CHECKING, Any, Optional
from datetime import datetime
from typing import Optional, List
from pydantic.functional_validators import BeforeValidator
# Represents an ObjectId field in the database.
# It will be represented as a `str` on the model so that it can be serialized to JSON.
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
    email: EmailStr = Field(...)
    phoneNumber: str = Field(...)
class Coordinate(BaseModel):
    lat: float
    lng: float

class Features(BaseModel):
    garage: Optional[int] = None
    floorno: Optional[int] = None
    basement: Optional[str] = None
    floorCovering: Optional[list[str]] = None
    parking: Optional[list[str]] = None
    roof: Optional[list[str]] = None
    view: Optional[list[str]] = None
    rooms: Optional[list[str]] = None
    indoorFeatures: Optional[list[str]] = None
    coolingTypes: Optional[list[str]] = None
    heatingTypes: Optional[list[str]] = None
    heatingFuels: Optional[list[str]] = None
    appliances: Optional[list[str]] = None
    buildingAmenities: Optional[list[str]] = None
    outdoorAmenities: Optional[list[str]] = None
    displayAddress: Optional[str] = None
class Pricing(BaseModel):
    pricingOptions: Optional[str] = None
    authority: Optional[str] = None
    customText: Optional[str] = None
    priceIncludes: Optional[list[str]] = None
class Area(BaseModel):
    totalarea: Optional[int] = None
    unit: Optional[str] = None
class HistoryOneSale(BaseModel):
    solddate: Optional[datetime] = None
    agancyid: Optional[str] = None
    soldprice: Optional[float] = None
    dayslisted: Optional[int] = None

class Prosale(BaseModel):
    """
    Container for a single student record.
    """

    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    #id: Optional[PyObjectId] = Field(alias="_id", default=None)
    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    stakeholder: Optional[str] | None = None
    contactInfo: Optional[list[Contact]] | None = None
    listingOption: Optional[str] | None = None
    title: Optional[str] | None = None
    slug: Optional[str] | None = None
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
    features: Optional[Features]
    area: Area
    expectedPrice: Optional[StrictInt] | None = None
    pricing: Optional[Pricing]
    historysale: Optional[list[HistoryOneSale]] | None = None
    published: Optional[bool] | None = None
    recommended: Optional[bool] | None = None
    userId: Optional[ObjectId] | None = None
    agencyid: Optional[ObjectId] | None = None
    agentid: Optional[list[ObjectId]] | None = None
    schoolid: Optional[list[ObjectId]] | None = None
    imageid: Optional[list[ObjectId]] | None = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)


class Image(BaseModel):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    url: Optional[str] | None = None
    category: Optional[str] | None = None
    star: Optional[bool] | None = None
    emb: Optional[list[float]] | None = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
class Searchimg(BaseModel):
    matchingimage: Optional[Image] | None = None
    property: Optional[Prosale] | None = None
class Searchnls(BaseModel):
    """
    A container holding a list of `PropertyForSaleCollection` instances.

    This exists because providing a top-level array in a JSON response can be a [vulnerability](https://haacked.com/archive/2009/06/25/json-hijacking.aspx/)
    """

    searchnls: List[Prosale]


