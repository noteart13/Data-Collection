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

class Image(BaseModel):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    id: ObjectId | None = None
    url: Optional[str] | None = None
    category: Optional[str] | None = None
    star: Optional[bool] | None = None
    emb: Optional[list[float]] | None = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)

class ImageCreate(BaseModel):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    url: Optional[str] | None = None
    category: Optional[str] | None = None
    star: Optional[bool] | None = None
    emb: Optional[list[float]] | None = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)



class ImageUpdate(BaseModel):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    id: ObjectId | None = None
    url: Optional[str] | None = None
    category: Optional[str] | None = None
    star: Optional[bool] | None = None
    emb: Optional[list[float]] = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
class ImageInDB(BaseModel):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    id: ObjectId | None = None
    url: Optional[str] | None = None
    category: Optional[str] | None = None
    star: Optional[bool] | None = None
    emb: Optional[list[float]] = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)