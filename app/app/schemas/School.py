from __future__ import annotations

from typing_extensions import Annotated
from pydantic import BaseModel, StrictInt, ConfigDict, Field, EmailStr, StringConstraints, field_validator, SecretStr
from odmantic import ObjectId
from typing import TYPE_CHECKING, Any, Optional
from datetime import datetime
from typing import Optional, List
from pydantic.functional_validators import BeforeValidator
def datetime_now_sec():
    return datetime.now().replace(microsecond=0)

PyObjectId = Annotated[str, BeforeValidator(str)]

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
    totalarea: int
    unit: str

class School(BaseModel):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    id: ObjectId | None = None
    educationLevel: Optional[str] = None
    name: Optional[str] = None
    distance: Optional[float] = None
    state: Optional[str] = None
    postCode: Optional[str] = None
    year: Optional[str] = None
    gender: Optional[str] = None
    type: Optional[str] = None
    address: Optional[str] = None
    url: Optional[str] = None
    domainSeoUrlSlug: Optional[str] = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)

class SchoolCreate(BaseModel):
    """
    Container for a single student record.
    """

    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    educationLevel: Optional[str] = None
    name: Optional[str] = None
    distance: Optional[float] = None
    state: Optional[str] = None
    postCode: Optional[str] = None
    year: Optional[str] = None
    gender: Optional[str] = None
    type: Optional[str] = None
    address: Optional[str] = None
    url: Optional[str] = None
    domainSeoUrlSlug: Optional[str] = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)


class SchoolUpdate(BaseModel):
    """
    Container for a single student record.
    """

    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    id: ObjectId | None = None
    educationLevel: Optional[str] = None
    name: Optional[str] = None
    distance: Optional[float] = None
    state: Optional[str] = None
    postCode: Optional[str] = None
    year: Optional[str] = None
    gender: Optional[str] = None
    type: Optional[str] = None
    address: Optional[str] = None
    url: Optional[str] = None
    domainSeoUrlSlug: Optional[str] = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)

class SchoolInDB(BaseModel):
    """
    Container for a single student record.
    """

    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    id: ObjectId | None = None
    educationLevel: Optional[str] = None
    name: Optional[str] = None
    distance: Optional[float] = None
    state: Optional[str] = None
    postCode: Optional[str] = None
    year: Optional[str] = None
    gender: Optional[str] = None
    type: Optional[str] = None
    address: Optional[str] = None
    url: Optional[str] = None
    domainSeoUrlSlug: Optional[str] = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
class UpdateStudentModel(BaseModel):
    """
    A set of optional updates to be made to a document in the database.
    """

    name: Optional[str] = None
    email: Optional[EmailStr] = None
    course: Optional[str] = None
    gpa: Optional[float] = None
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str},
        json_schema_extra={
            "example": {
                "name": "Jane Doe",
                "email": "jdoe@example.com",
                "course": "Experiments, Science, and Fashion in Nanophotonics",
                "gpa": 3.0,
            }
        },
    )


class PropertyForSaleCollection(BaseModel):
    """
    A container holding a list of `PropertyForSaleCollection` instances.

    This exists because providing a top-level array in a JSON response can be a [vulnerability](https://haacked.com/archive/2009/06/25/json-hijacking.aspx/)
    """

    properties: List[PropertyForSaleModel]

#UserCreate, UserInDB, UserUpdate
class ProsaleBase(BaseModel):
    created: datetime = Field(default_factory=datetime_now_sec)
    modified: datetime = Field(default_factory=datetime_now_sec)
    stakeholder: str = Field(...)
    #full_name: str = Field(default="")
    #email: EmailStr
    #hashed_password: Any = Field(default=None)
    #totp_secret: Any = Field(default=None)
    #totp_counter: Optional[int] = Field(default=None)
    #email_validated: bool = Field(default=False)
    #is_active: bool = Field(default=False)
    #is_superuser: bool = Field(default=False)
    #refresh_tokens: list[ObjectId] = Field(default_factory=list)
    #access_tokens: list[ObjectId] = Field(default_factory=list)


class UserLogin(BaseModel):
    username: str
    password: str


# Shared properties
class UserBase(BaseModel):
    email: EmailStr | None = None
    email_validated: bool | None = False
    is_active: bool | None = True
    is_superuser: bool | None = False
    full_name: str = ""

# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: ObjectId | None = None

class ProsaleCreate(ProsaleBase):
    #email: EmailStr
    #password: Annotated[str | None, StringConstraints(min_length=8, max_length=64)] = None # noqa
    title: Optional[str] = None

# Properties to receive via API on update
class ProsaleUpdate(ProsaleBase):
    #original: Annotated[str | None, StringConstraints(min_length=8, max_length=64)] = None # noqa
    #password: Annotated[str | None, StringConstraints(min_length=8, max_length=64)] = None  # noqa
    title: Optional[str] = None

class ProsaleInDBBase(ProsaleBase):
    id: ObjectId | None = None
    model_config = ConfigDict(from_attributes=True)

"""

# Additional properties to return via API
class User(UserInDBBase):
    hashed_password: bool = Field(default=False, alias="password")
    totp_secret: bool = Field(default=False, alias="totp")
    model_config = ConfigDict(populate_by_name=True)

    @field_validator("hashed_password", mode="before")
    def evaluate_hashed_password(cls, hashed_password):
        if hashed_password:
            return True
        return False

    @field_validator("totp_secret", mode="before")
    def evaluate_totp_secret(cls, totp_secret):
        if totp_secret:
            return True
        return False


# Additional properties stored in DB
class ProsaleInDB(UserInDBBase):
    hashed_password: SecretStr | None = None
    totp_secret: SecretStr | None = None
    totp_counter: int | None = None


"""