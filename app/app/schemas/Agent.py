from __future__ import annotations

from typing_extensions import Annotated
from pydantic import BaseModel, StrictInt, ConfigDict, Field, EmailStr, StringConstraints, field_validator, SecretStr
from odmantic import ObjectId
from typing import TYPE_CHECKING, Any, Optional
from datetime import datetime
from typing import Optional, List, Any
from pydantic.functional_validators import BeforeValidator
def datetime_now_sec():
    return datetime.now().replace(microsecond=0)

class Agent(BaseModel):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    firstName: Optional[str] | None = None
    lastName: Optional[str] | None = None
    email: EmailStr | None = None
    phoneNumber: Optional[str] = None    
    landlineNumber: Optional[str] = None
    isActiveProfilePage: bool | None = False
    profileUrl: Optional[str] | None = None
    photo: Optional[str] | None = None
    agentId: Optional[str] | None = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)

class AgentCreate(BaseModel):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.

    firstName: Optional[str] | None = None
    lastName: Optional[str] | None = None
    email: EmailStr | None = None
    phoneNumber: Optional[str] | None = None
    isActiveProfilePage: bool | None = False
    profileUrl: Optional[str] | None = None
    photo: Optional[str] | None = None
    agentId: Optional[str] | None = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    slug: Optional[str] = None

class AgentUpdate(BaseModel):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    id: ObjectId | None = None
    firstName: Optional[str] | None = None
    lastName: Optional[str] | None = None
    email: EmailStr | None = None
    phoneNumber: Optional[str] | None = None
    isActiveProfilePage: bool | None = False
    profileUrl: Optional[str] | None = None
    photo: Optional[str] | None = None
    agentId: Optional[str] | None = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)

class AgentInDB(BaseModel):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    id: ObjectId | None = None
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    email: EmailStr | None = None
    phoneNumber: Optional[str] = None
    isActiveProfilePage: bool | None = False
    profileUrl: Optional[str] = None
    photo: Optional[str] = None
    agentId: Optional[str] = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)

class AboutAgent(BaseModel):
    bio: Optional[str] = None
    videoUrl: Optional[str] = None
    personalWebsite: Optional[str] = None

class SalesStatistics(BaseModel):
    totalSale: Optional[str]
    totalsalesValue: Optional[str]
    averageValue: Optional[str]  
class AgentNewVerInDB(BaseModel):
    id: ObjectId | None = None
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    email: EmailStr | None = None
    phoneNumber: Optional[str] = None
    photo: Optional[str] = None
    profileUrl: Optional[str] = None
    agentId: Optional[str] = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    city: Optional[List] = None
    state: Optional[List] = None
    suburb: Optional[List] = None
    postcode: Optional[List] = None
    propertysaleids: Optional[List] = None
    propertysoldids: Optional[List] = None
    propertyrentids: Optional[List] = None
    propertyrentedids: Optional[List] = None
    slug: Optional[str] = None
    aboutAgent: AboutAgent | None = None
    agencyid: ObjectId | None = None
    rating: Optional[float] = None
    salesStatistics: SalesStatistics | None = None
    title: Optional[str] = None
    
class AgentGetCheck(BaseModel):
    id: ObjectId | None = None
    profileUrl: Optional[str] = None
    propertysaleids: Optional[List] = None
    propertysoldids: Optional[List] = None
    propertyrentids: Optional[List] = None
    propertyrentedids: Optional[List] = None
    aboutAgent: AboutAgent | None = None
    agencyid: ObjectId | None = None
    rating: Optional[float] = None
    salesStatistics: SalesStatistics | None = None
    title: Optional[str] = None
    model_config = {
        "collection": "Agent"
    }
class AgentNewUpdate(BaseModel):
    propertysaleids: Optional[List] = None
    propertysoldids: Optional[List] = None
    propertyrentids: Optional[List] = None
    propertyrentedids: Optional[List] = None
    aboutAgent: AboutAgent | None = None
    agencyid: ObjectId | None = None
    rating: Optional[float] = None
    salesStatistics: SalesStatistics | None = None
    title: Optional[str] = None
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
