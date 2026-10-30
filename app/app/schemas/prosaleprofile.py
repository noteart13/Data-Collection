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
    priceIncludes: list[str]
class Area(BaseModel):
    totalarea: Optional[int] | None = None
    unit: Optional[str] | None = None
class HistoryOneSale(BaseModel):
    soldDate: Optional[datetime] | None = None
    agencyId: Optional[int] | None = None
    soldPrice: Optional[int] | None = None
    daysListed: Optional[int] | None = None

class Prosoldshort(BaseModel):
    """
    Container for a single student record.
    """

    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    #['propertyType', 'slug', 'title', 'contactInfo', 'soldDateInfo', 'stakeHolder']
    stakeHolder: Optional[str] | None = None
    contactInfo: Optional[list[Contact]] | None = None
    soldDateInfo: Optional[str] | None = None
    title: Optional[str] | None = None
    slug: Optional[str] | None = None
    propertyType: Optional[str] | None = None
    description: Optional[str] | None = None
    bed: Optional[int] | None = None
    bath: Optional[int] | None = None
    images: Optional[list[Image]] | None = None
    features: Optional[Features]  | None = None
    area: Optional[Area] | None = None
    historySale: Optional[list[HistoryOneSale]] | None = None
    agencyid: Optional[ObjectId] | None = None
    agentid: Optional[list[ObjectId]] | None = None
    imageid: Optional[list[ObjectId]] | None = None


class ProhissaleprofCreate(BaseModel):
    """
    A container holding a list of `PropertyForSaleCollection` instances.

    This exists because providing a top-level array in a JSON response can be a [vulnerability](https://haacked.com/archive/2009/06/25/json-hijacking.aspx/)
    """
    #id: Optional[PyObjectId] = Field(alias="_id", default=None)
    id: ObjectId | None = None # this can be used when we have our own id
    historyprofile: Optional[list[Prosoldshort]] | None = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
class ProhissaleprofUpdate(BaseModel):
    """
    A container holding a list of `PropertyForSaleCollection` instances.

    This exists because providing a top-level array in a JSON response can be a [vulnerability](https://haacked.com/archive/2009/06/25/json-hijacking.aspx/)
    """
    historyprofile: List[Prosoldshort]
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)

class Prohissaleprof(BaseModel):
    """
    A container holding a list of `PropertyForSaleCollection` instances.

    This exists because providing a top-level array in a JSON response can be a [vulnerability](https://haacked.com/archive/2009/06/25/json-hijacking.aspx/)
    """
    #id: Optional[PyObjectId] = Field(alias="_id", default=None)
    #historyprofile: List[Prosoldshort]
    historyprofile: Optional[list[Prosoldshort]] | None = None
    #createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    #updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)




