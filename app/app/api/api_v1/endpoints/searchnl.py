from fastapi import APIRouter, Body, Depends, HTTPException
from motor.core import AgnosticDatabase
from fastapi import Request
from app import crud, models, schemas
from app.api import deps
from operations.searchnl import get_pipeline
from typing import Any
from fastapi_pagination import LimitOffsetPage, Page, add_pagination
from fastapi_pagination.ext.motor import paginate, paginate_aggregate
from app.schemas import Prosale, Searchimgs, Searchnls, Searchapi
import json
router = APIRouter()


@router.get("/search/nl", response_model=LimitOffsetPage[Searchapi])
async def AUS_SEARCH_NL(
    db: AgnosticDatabase = Depends(deps.get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    return await paginate_aggregate(db.get_collection("PropertyForSale"))


@router.post("/generate/nosql"
)
async def AUS_GENERATE_NOSQL(
        info: Request,
        current_user: models.User = Depends(deps.get_current_active_user),
):
    """
    Create new user (moderator function).
    """
    body = await info.json()
    pipeline = get_pipeline(body)
    pipeline = pipeline['pipeline']
    pipeline = json.dumps(pipeline)
    return pipeline