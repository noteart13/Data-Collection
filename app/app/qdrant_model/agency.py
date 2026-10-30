import uuid
from typing import TYPE_CHECKING, Any, Optional
from datetime import datetime
from pydantic import BaseModel, StrictInt, ConfigDict, Field, EmailStr, StringConstraints, field_validator, SecretStr
from odmantic import ObjectId, Field

class Agency(BaseModel):
    agencyId: Optional[int] = Field(default=None)
    banner: Optional[str] = Field(default=None)
    contactDetails: Optional[str] = Field(default=None)
    createdAt: datetime = Field(default=datetime.now())
    logo: Optional[str] = Field(default=None)
    logoSmall: Optional[str] = Field(default=None)
    name: Optional[str] = Field(default=None)
    profileUrl: Optional[str] = Field(default=None)
    updatedAt: datetime = Field(default=datetime.now())
    website: Optional[str] = Field(default=None)
    model_config = {
        "collection": "Agency"
    }
    def to_dict(self):
        data_dict = self.dict(by_alias=True, exclude_none=True)
        return data_dict