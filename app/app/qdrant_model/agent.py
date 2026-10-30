import uuid
from typing import TYPE_CHECKING, Any, Optional
from datetime import datetime
from pydantic import BaseModel, StrictInt, ConfigDict, Field, EmailStr, StringConstraints, field_validator, SecretStr
from odmantic import ObjectId, Field

class Agent(BaseModel):
    agentId: Optional[int] = Field(default=None)
    createdAt: datetime = Field(default=datetime.now())
    email: Optional[str] = Field(default=None)
    firstName: Optional[str] = Field(default=None)
    isActiveProfilePage: Optional[bool] = Field()
    lastName: Optional[str] = Field(default=None)
    phoneNumber: Optional[str] = Field(default=None)
    photo: Optional[str] = Field(default=None)
    profileUrl: str = Field()
    updatedAt: datetime = Field(default=datetime.now())
    model_config = {
        "collection": "Agent"
    }
    def to_dict(self):
        data_dict = self.dict(by_alias=True, exclude_none=True)
        return data_dict