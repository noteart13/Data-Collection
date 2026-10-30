from typing import Any, Dict, Union
from motor.core import AgnosticDatabase
from app.crud.base import CRUDBase
from app.models import Searchsemantic
from app.schemas.Searchsemantic import Searchsemantic



# ODM, Schema, Schema
class CRUDSearchsemantic(CRUDBase[Searchsemantic, Searchsemantic, Searchsemantic]):
    async def get_by_agencyId(self, db: AgnosticDatabase, *, id: str) -> Searchsemantic | None: # noqa
        return await self.engine.find_one(Searchsemantic, Searchsemantic.id == id)
    async def create(self, db: AgnosticDatabase, *, obj_in: Searchsemantic) -> Searchsemantic: # noqa
        # TODO: Figure out what happens when you have a unique key like 'email'
        searchsemantic = {
            **obj_in.model_dump(),
        }
        return await self.engine.save(Searchsemantic(**searchsemantic))
searchsemantic = CRUDSearchsemantic(Searchsemantic)
