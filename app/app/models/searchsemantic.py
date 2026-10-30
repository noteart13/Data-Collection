from __future__ import annotations
from typing import TYPE_CHECKING, Any, Optional
from odmantic import ObjectId, Field
from datetime import datetime
from app.db.base_class import Base
from typing_extensions import Annotated
from pydantic import BaseModel, StrictInt, ConfigDict, Field, EmailStr, StringConstraints, field_validator, SecretStr
from datetime import datetime
from typing import Optional, List
from pydantic.functional_validators import BeforeValidator
PyObjectId = Annotated[str, BeforeValidator(str)]
def datetime_now_sec():
    return datetime.now().replace(microsecond=0)

def datetime_now_sec():
    return datetime.now().replace(microsecond=0)

class Searchsemantic(Base):
    """
    Container for a single semantic search.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    id: Optional[PyObjectId] = Field(alias="_id", default=None) # make customize _id
    name: str = Field(default="") # can be title, address, chunk of description
    emb: Optional[list[float]] = Field(default=None)
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    model_config = {
        "collection": "Image"
    }
    model_config = {
        "collection": "Searchsemantic"
    }


