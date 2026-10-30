from typing import Any, Dict, Union
from motor.core import AgnosticDatabase
from app.crud.base import CRUDBase
from app.models import Agency
from app.schemas.Agency import AgencyCreate, AgencyInDB, AgencyUpdate
from app.schemas.totp import NewTOTP


# ODM, Schema, Schema
class CRUDAgency(CRUDBase[Agency, AgencyCreate, AgencyUpdate]):
    async def get_by_agencyId(self, db: AgnosticDatabase, *, agencyId: str) -> Agency | None: # noqa
        return await self.engine.find_one(Agency, Agency.agencyId == agencyId)
    async def create(self, db: AgnosticDatabase, *, obj_in: AgencyCreate) -> Agency: # noqa
        # TODO: Figure out what happens when you have a unique key like 'email'
        agency = {
            **obj_in.model_dump(),
        }
        return await self.engine.save(Agency(**agency))
agency = CRUDAgency(Agency)
