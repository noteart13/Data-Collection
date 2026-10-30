from typing import TYPE_CHECKING, Any, Optional
from datetime import datetime
from pydantic import BaseModel, StrictInt, ConfigDict, Field, EmailStr, StringConstraints, field_validator, SecretStr
from odmantic import ObjectId, Field
from typing import Optional, List
from app.db.base_class import Base
from typing_extensions import Annotated
from pydantic.functional_validators import BeforeValidator
PyObjectId = Annotated[str, BeforeValidator(str)]
if TYPE_CHECKING:
    from app.aus_models import Token  # noqa: F401

def datetime_now_sec():
    return datetime.now().replace(microsecond=0)

# Properties to return via API, id is always required
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
    email: Optional[EmailStr] = Field(default=None)
    phoneNumber: Optional[str] = Field(default=None)
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
    totalarea: int
    unit: str
class HistoryOneSale(BaseModel):
    soldDate: Optional[datetime] = None
    agencyId: Optional[int] = None
    soldPrice: Optional[float] = None # if = 0 price withheld
    daysListed: Optional[int] = None # if first sold: dayslisted = sold date - crated date, if second sold: dayslisted = modified date - sold date

class Prosoldshort(BaseModel):
    """
    Container for a property profile.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    stakeHolder: str = Field(default="")
    soldDateInfo: str = Field(default="")
    contactInfo: list[Contact] = Field(default=[])
    #listingOption: str = Field(default="")
    title: str = Field(default="")
    slug: str = Field(default="")
    propertyType: str = Field(default="")
    description: str = Field(default="")
    bed: Optional[int] = Field(default=None)
    bath: Optional[int] = Field(default=None)
    images: Optional[list[Image]] = Field(default=None)
    features: Optional[Features] = Field(default=None)
    area: Optional[Area] = Field(default=None)
    historySale: Optional[list[HistoryOneSale]] = Field(default=None)
    agencyid: Optional[ObjectId] = Field(default=None)
    agentid: Optional[list[ObjectId]] = Field(default=None)
    imageid: Optional[list[ObjectId]] = Field(default=None)

class Prohissaleprof(Base):
    """
    A container holding a list of `PropertyForSaleCollection` instances.

    This exists because providing a top-level array in a JSON response can be a [vulnerability](https://haacked.com/archive/2009/06/25/json-hijacking.aspx/)
    """
    #id: Optional[PyObjectId] = Field(alias="_id", default=None)
    #id: Optional[PyObjectId] = Field(default=None)
    historyprofile: List[Prosoldshort]
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    model_config = {
        "collection": "PropertySaleProfile"
    }
