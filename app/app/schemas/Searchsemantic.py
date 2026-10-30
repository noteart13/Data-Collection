from __future__ import annotations
from typing_extensions import Annotated
from pydantic import BaseModel, StrictInt, ConfigDict, Field, EmailStr, StringConstraints, field_validator, SecretStr
from datetime import datetime
from typing import Optional, List
from pydantic.functional_validators import BeforeValidator
# Represents an ObjectId field in the database.
# It will be represented as a `str` on the model so that it can be serialized to JSON.
PyObjectId = Annotated[str, BeforeValidator(str)]
def datetime_now_sec():
    return datetime.now().replace(microsecond=0)

class Searchsemantic(BaseModel):
    """
    Container for a single semantic search.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    id: Optional[PyObjectId] = Field(alias="_id", default=None) # make customize _id
    name: Optional[str] | None = None # can be title, address, chunk of description
    emb: Optional[list[float]] | None = None
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
