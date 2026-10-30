from typing import TYPE_CHECKING, Any, Optional
from datetime import datetime
from pydantic import BaseModel, StrictInt, ConfigDict, Field, EmailStr, StringConstraints, field_validator, SecretStr
from odmantic import ObjectId, Field

from app.db.base_class import Base

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
    photo: Optional[str] = Field(default=None)
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
    priceIncludes: Optional[str] = None


class Prorented(Base):
    """
    Container for a single student record.
    """

    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    stakeHolder: str = Field(default="")
    contactInfo: list[Contact] = Field(default=[])
    listingOption: str = Field(default="")
    title: str = Field(default="")
    slug: str = Field(default="")
    url: str = Field(default="")
    propertyType: str = Field(default="")
    description: str = Field(default="")
    constructionYear: str = Field(default="")
    structuralRemodelYear: str = Field(default="")
    architecturalStyle: str = Field(default="")
    status: str = Field(default="")
    state: str = Field(default="")
    suburb: str = Field(default="")
    postcode: Optional[int] = Field(default=None)
    city: str = Field(default="")
    street: str = Field(default="")
    coordinates: Optional[Coordinate] = Field(default=None)
    bed: Optional[int] = Field(default=None)
    bath: Optional[int] = Field(default=None)
    images: Optional[list[Image]] = Field(default=None)
    features: Optional[Features] = Field(default=None)
    area: Optional[dict] = Field(default=None)
    rentPrice: Optional[float] = Field(default=None)
    pricing: Optional[Pricing] = Field(default=None)
    published: Optional[bool] = Field(default=False)
    recommended: Optional[bool] = Field(default=False)
    userId: Optional[ObjectId] = Field(default=None)
    agencyid: Optional[ObjectId] = Field(default=None)
    agentid: Optional[list[ObjectId]] = Field(default=None)
    schoolid: Optional[list[ObjectId]] = Field(default=None)
    imageid: Optional[list[ObjectId]] = Field(default=None)
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    model_config = {
        "collection": "PropertyRented"
    }
