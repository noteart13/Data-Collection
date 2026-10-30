from typing import TYPE_CHECKING, Any, Optional
from datetime import datetime

from odmantic import ObjectId, Field

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.aus_models import Token  # noqa: F401

def datetime_now_sec():
    return datetime.now().replace(microsecond=0)

# Properties to return via API, id is always required
def datetime_now_sec():
    return datetime.now().replace(microsecond=0)

class School(Base):
    """
    Container for a single student record.
    """

    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    educationLevel: str = Field(default='')
    name: str = Field(default='')
    distance: float = Field(default=None)
    state: str = Field(default='')
    postCode: str = Field(default='')
    year: str = Field(default='')
    gender: str = Field(default='')
    type: str = Field(default='')
    address: str = Field(default='')
    url: str = Field(default='')
    domainSeoUrlSlug: str = Field(default='')
    createdAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    updatedAt: Optional[datetime] = Field(default_factory=datetime_now_sec)
    model_config = {
        "collection": "School"
    }
