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
    email: EmailStr = Field(...)
    phoneNumber: str = Field(...)
class Coordinate(BaseModel):
    lat: float
    lng: float

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
    priceIncludes: list[str]
class Area(BaseModel):
    totalarea: Optional[int] | None = None
    unit: Optional[str] | None = None
class HistoryOneSale(BaseModel):
    solddate: Optional[datetime] | None = None
    agancyid: Optional[str] | None = None
    soldprice: Optional[int] | None = None
    dayslisted: Optional[int] | None = None



class Searchapi(BaseModel):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    #id: ObjectId | None = None
    title: Optional[str] | None = None
    slug: Optional[str] | None = None
    contactInfo: Optional[list[Contact]] | None = None
    propertyType: Optional[str] | None = None
    status: Optional[str] | None = None
    street: Optional[str] | None = None
    coordinates: Optional[Coordinate] | None = None
    features: Optional[Features] | None = None
    expectedPrice: Optional[int] | None = None
    pricing: Optional[Pricing]
    published: bool = Field(default=False)
    bed: Optional[int] | None = None
    bath: Optional[int] | None = None
    area: Optional[Area] | None = None
    images: Optional[list[Image]] | None = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)