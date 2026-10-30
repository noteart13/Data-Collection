from __future__ import annotations
from pydantic import BaseModel, Field
from odmantic import ObjectId
from datetime import datetime
from typing import Optional
def datetime_now_sec():
    return datetime.now().replace(microsecond=0)


class Agency(BaseModel):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    id: ObjectId | None = None
    banner: Optional[str]
    logo: Optional[str]
    logoSmall: Optional[str]
    contactDetails: Optional[str] = None
    isArchived: bool | None = False
    name: Optional[str] = None
    profileUrl: Optional[str] = None
    website: Optional[str] = None
    agencyId: int | None = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)


class AgencyCreate(BaseModel):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    banner: Optional[str]
    logo: Optional[str]
    logoSmall: Optional[str]
    contactDetails: Optional[str]
    isArchived: bool | None = False
    name: Optional[str] = None
    profileUrl: Optional[str] = None
    website: Optional[str] = None
    agencyId: int | None = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)

class AgencyUpdate(BaseModel):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    id: ObjectId | None = None
    banner: Optional[str]
    logo: Optional[str]
    logoSmall: Optional[str]
    contactDetails: Optional[str]
    isArchived: bool | None = False
    name: Optional[str] = None
    profileUrl: Optional[str] = None
    website: Optional[str] = None
    agencyId: Optional[int] = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)


class AgencyInDB(BaseModel):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    id: ObjectId | None = None
    banner: Optional[str]
    logo: Optional[str]
    logoSmall: Optional[str]
    contactDetails: Optional[str]
    isArchived: bool | None = False
    name: Optional[str] = None
    profileUrl: Optional[str] = None
    website: Optional[str] = None
    agencyId: Optional[int] = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
