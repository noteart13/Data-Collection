from fastapi import APIRouter, Body, Depends, HTTPException
from motor.core import AgnosticDatabase
from app import crud, models, schemas
from app.schemas import Prosale, Searchimgs, Searchapi
from app.api import deps
from typing import Optional, List
from pydantic import ConfigDict, BaseModel, Field, EmailStr
from fastapi_pagination.ext.motor import paginate_aggregate_img
from fastapi_pagination import LimitOffsetPage, Page, add_pagination
router = APIRouter()

class PropertyForSaleCollection(BaseModel):
    """
    A container holding a list of `PropertyForSaleCollection` instances.

    This exists because providing a top-level array in a JSON response can be a [vulnerability](https://haacked.com/archive/2009/06/25/json-hijacking.aspx/)
    """

    properties: List[Prosale]

@router.get("/search/img", response_model=LimitOffsetPage[Searchapi]
)
async def AUS_SEARCH_IMG(
        db: AgnosticDatabase = Depends(deps.get_db),
        current_user: models.User = Depends(deps.get_current_active_user),
):
    image_col = db.get_collection("Image")
    prosale_col = db.get_collection("PropertyForSale")
    #results = asyncio.run(paginate_aggregate_img([image_col, prosale_col]))
    return await paginate_aggregate_img([image_col, prosale_col])



