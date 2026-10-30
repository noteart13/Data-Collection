from typing import TYPE_CHECKING, Any, Optional
from datetime import datetime
from pydantic import BaseModel, StrictInt, ConfigDict, Field, EmailStr, StringConstraints, field_validator, SecretStr
from odmantic import ObjectId, Field

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.aus_models import Token  # noqa: F401

def datetime_now_sec():
    return datetime.now().replace(microsecond=0)

# Properties to return via API, id is always required
def datetime_now_sec():
    return datetime.now().replace(microsecond=0)

class Imagesold(Base):
    url: str = Field(default="")
    category: str = Field(default="")
    star: bool = Field(default=False)
    emb: Optional[list[float]] = Field(default=None)
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    model_config = {
        "collection": "ImageSold"
    }

