from __future__ import annotations

from typing_extensions import Annotated
from pydantic import StrictInt, ConfigDict, EmailStr, StringConstraints, field_validator, SecretStr
from odmantic import ObjectId, Field
from typing import TYPE_CHECKING, Any
from datetime import datetime
from typing import Optional
from app.db.base_class import Base
from pydantic.functional_validators import BeforeValidator
def datetime_now_sec():
    return datetime.now().replace(microsecond=0)

class Agent(Base):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    firstName: str = Field(default="")
    lastName: str = Field(default="")
    # email: EmailStr
    # phoneNumber: str = Field(default="")
    email: Optional[EmailStr] = Field(default=None)  # Allow None
    phoneNumber: Optional[str] = Field(default=None)  # Allow None
    isActiveProfilePage:  bool = Field(default=False)
    profileUrl: str = Field(default="")
    photo: str = Field(default="")
    # agentId: Optional[str] = Field(default='')
    agentId: Optional[str] = Field(default=None)
    createdAt: datetime = Field(default_factory=datetime_now_sec)
    updatedAt: datetime = Field(default_factory=datetime_now_sec)
    slug: Optional[str] = Field(default=None)
    model_config = {
        "collection": "Agent"
    }