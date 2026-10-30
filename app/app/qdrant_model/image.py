import uuid
from typing import TYPE_CHECKING, Any, Optional
from datetime import datetime
from pydantic import BaseModel, StrictInt, ConfigDict, Field, EmailStr, StringConstraints, field_validator, SecretStr
from odmantic import ObjectId, Field

class Image(BaseModel):
    category: Optional[str] = Field(default=None)
    createdAt: datetime = Field(default=datetime.now())
    emb: Optional[list] = Field(default=None)
    star: Optional[bool] = Field(default=None)
    updatedAt: datetime = Field(default=datetime.now())
    url: Optional[str] = Field(default=None)
    model_config = {
        "collection": "Image"
    }
    def to_dict(self):
        data_dict = self.dict(by_alias=True, exclude_none=True)
        return data_dict