__all__ = ["paginate"]

from typing import TYPE_CHECKING, Any, Dict, List, Optional
import json
from motor.core import AgnosticCollection
from typing_extensions import TypeAlias
from ..api import apply_items_transformer, create_page
from ..bases import AbstractParams
from ..types import AdditionalData, AsyncItemsTransformer
from ..utils import verify_params
from operations.searchnl import get_pipeline
from operations.searchimg import search_img
from operations.searchnl import search_nl
if TYPE_CHECKING:
    _AgnosticCollection: TypeAlias = AgnosticCollection[Any]
else:
    _AgnosticCollection = AgnosticCollection


async def paginate(
    collection: _AgnosticCollection,
    query_filter: Optional[Dict[Any, Any]] = None,
    params: Optional[AbstractParams] = None,
    sort: Optional[Any] = None,
    *,
    transformer: Optional[AsyncItemsTransformer] = None,
    additional_data: Optional[AdditionalData] = None,
    **kwargs: Any,
) -> Any:
    params, raw_params = verify_params(params, "limit-offset")
    query_filter = query_filter or {}

    total = await collection.count_documents(query_filter) if raw_params.include_total else None
    cursor = collection.find(query_filter, skip=raw_params.offset, limit=raw_params.limit, **kwargs)
    if sort is not None:
        cursor = cursor.sort(*sort) if isinstance(sort, tuple) else cursor.sort(sort)

    items = await cursor.to_list(length=raw_params.limit)
    t_items = await apply_items_transformer(items, transformer, async_=True)

    return create_page(
        t_items,
        total=total,
        params=params,
        **(additional_data or {}),
    )

async def paginate_aggregate(
    collection: _AgnosticCollection,
    aggregate_pipeline: Optional[List[Dict[Any, Any]]] = None,
    params: Optional[AbstractParams] = None,
    *,
    transformer: Optional[AsyncItemsTransformer] = None,
    additional_data: Optional[AdditionalData] = None,
) -> Any:
    # semantic text search
    params, raw_params = verify_params(params, "limit-offset")
    query = raw_params.query
    limit = raw_params.limit

    data = await search_nl(query, limit, collection)
    items = data
    try:
        #total = data["metadata"][0]["total"]
        total = limit
    except IndexError:
        total = 0
    t_items = await apply_items_transformer(items, transformer, async_=True)
    return create_page(
        t_items,
        total=total,
        params=params,
        **(additional_data or {}),
    )

"""
# from old version
params, raw_params = verify_params(params, "limit-offset")
#if not aggregate_pipeline:
#aggregate_pipeline = get_pipeline({"query":raw_params.query})['pipeline'] or []
#aggregate_pipeline = [{'$match': {'city': 'Sydney', 'status': 'for-sale'}}]
aggregate_pipeline = json.loads(raw_params.query)
#aggregate_pipeline = {'$match': {'bath': 2, 'bed': 2, 'city': 'Sydney', 'listingOption': 'sale', 'propertyType': 'House'}}

paginate_data = []
if raw_params.limit is not None:
    paginate_data.append({"$limit": raw_params.limit + (raw_params.offset or 0)})
if raw_params.offset is not None:
    paginate_data.append({"$skip": raw_params.offset})
cursor = collection.aggregate(
    [
        *aggregate_pipeline,
        {
            "$facet": {
                "metadata": [{"$count": "total"}],
                "data": paginate_data,
            },
        },

    ],
)
data, *_ = await cursor.to_list(length=None)

items = data["data"]
try:
    total = data["metadata"][0]["total"]
except IndexError:
    total = 0

t_items = await apply_items_transformer(items, transformer, async_=True)
temp = create_page(
    t_items,
    total=total,
    params=params,
    **(additional_data or {}),
)

return create_page(
    t_items,
    total=total,
    params=params,
    **(additional_data or {}),
)
"""
async def paginate_aggregate_img(
    collections: List[_AgnosticCollection],  # iamge col and prosale col
    aggregate_pipeline: Optional[List[Dict[Any, Any]]] = None,
    params: Optional[AbstractParams] = None,
    *,
    transformer: Optional[AsyncItemsTransformer] = None,
    additional_data: Optional[AdditionalData] = None,
) -> Any:
    params, raw_params = verify_params(params, "limit-offset")
    image = raw_params.query
    limit = raw_params.limit
    """
    paginate_data = []
    if raw_params.limit is not None:
        paginate_data.append({"$limit": raw_params.limit + (raw_params.offset or 0)})
    if raw_params.offset is not None:
        paginate_data.append({"$skip": raw_params.offset})
    """
    data = await search_img(image, limit, collections[0], collections[1])
    items = data
    try:
        #total = data["metadata"][0]["total"]
        total = limit
    except IndexError:
        total = 0
    t_items = await apply_items_transformer(items, transformer, async_=True)
    return create_page(
        t_items,
        total=total,
        params=params,
        **(additional_data or {}),
    )
