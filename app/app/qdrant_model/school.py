import uuid
from typing import TYPE_CHECKING, Any, Optional
from datetime import datetime
from pydantic import BaseModel, StrictInt, ConfigDict, Field, EmailStr, StringConstraints, field_validator, SecretStr
from odmantic import ObjectId, Field

class School(BaseModel):
    address: Optional[str] = Field(default=None)
    createdAt: datetime = Field(default=datetime.now())
    distance: Optional[float] = Field(default=None)
    domainSeoUrlSlug: Optional[str] = Field(default=None)
    educationLevel: Optional[str] = Field(default=None)
    gender: Optional[str] = Field(default=None)
    name: Optional[str] = Field(default=None)
    postCode: Optional[str] = Field(default=None)
    state: Optional[str] = Field(default=None)
    type: Optional[str] = Field(default=None)
    updatedAt: datetime = Field(default=datetime.now())
    url: Optional[str] = Field(default=None)
    year: Optional[str] = Field(default=None)
    model_config = {
        "collection": "School"
    }
    def to_dict(self):
        data_dict = self.dict(by_alias=True, exclude_none=True)
        return data_dict