from __future__ import annotations

from odmantic import ObjectId, Field
from datetime import datetime
from typing import Optional
from app.db.base_class import Base

def datetime_now_sec():
    return datetime.now().replace(microsecond=0)


class Agency(Base):
    """
    Container for a single student record.
    """
    # The primary key for the StudentModel, stored as a `str` on the instance.
    # This will be aliased to `_id` when sent to MongoDB,
    # but provided as `id` in the API requests and responses.
    banner: str = Field(default="")
    logo: str = Field(default="")
    logoSmall: str = Field(default="")
    contactDetails: Optional[str] = Field(default=None)
    isArchived: bool = Field(default=False)
    name: str = Field(default="")
    profileUrl: str = Field(default="")
    website: Optional[str] = Field(default=None)
    agencyId: int = Field(default=None)
    createdAt: datetime = Field(default_factory=datetime_now_sec)
    updatedAt: datetime = Field(default_factory=datetime_now_sec)
    model_config = {
        "collection": "Agency"
    }

