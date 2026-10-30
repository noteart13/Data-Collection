from typing import Any, Dict, Union, List

from motor.core import AgnosticDatabase

from app.core.security import get_password_hash, verify_password
from app.crud.base import CRUDBase
from app.models import School
from app.schemas.School import SchoolCreate, SchoolInDB, SchoolUpdate
from app.schemas.totp import NewTOTP


# ODM, Schema, Schema
class CRUDSchool(CRUDBase[School, SchoolCreate, SchoolUpdate]):
    async def get_by_email(self, db: AgnosticDatabase, *, email: str) -> School | None: # noqa
        return await self.engine.find_one(User, User.email == email)

    async  def create_many(self, db: AgnosticDatabase, *, objs_in: List[SchoolCreate]) -> List[School]:  # noqa
        async with self.engine.session() as session:
            return await session.save_all(
                [School(**obj_in.model_dump()) for obj_in in objs_in])



school = CRUDSchool(School)
