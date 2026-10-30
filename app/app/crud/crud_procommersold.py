from typing import Any, Dict, Union

from motor.core import AgnosticDatabase

from app.core.security import get_password_hash, verify_password
from app.crud.base import CRUDBase
from app.models import Procommersold
from app.schemas.procommersold import ProcommersoldCreate, ProcommersoldInDB, ProcommersoldUpdate
from app.schemas.totp import NewTOTP
import asyncio

# ODM, Schema, Schema
class CRUDProcommersold(CRUDBase[Procommersold, ProcommersoldCreate, ProcommersoldUpdate]):
    async def get_by_slug(self, db: AgnosticDatabase, *, slug: str) -> Procommersold | None: # noqa
        return await self.engine.find_one(Procommersold, Procommersold.slug == slug)
    async def get_by_id(self, db: AgnosticDatabase, *, id: Any) -> Procommersold | None: # noqa
        print(id)
        #return await super().get(db, db_obj=db_obj, obj_in=update_data)
        return await self.engine.find_one(Procommersold, Procommersold.id == id)
    async def create(self, db: AgnosticDatabase, *, obj_in: ProcommersoldCreate) -> Procommersold: # noqa
        # TODO: Figure out what happens when you have a unique key like 'email'

        procommersold = {
            **obj_in.model_dump(),
        }
        return await self.engine.save(Procommersold(**procommersold))

    async def update(self, db: AgnosticDatabase, *, db_obj: Procommersold, obj_in: Union[ProcommersoldUpdate, Dict[str, Any]]) -> Procommersold: # noqa
        if isinstance(obj_in, dict):
            update_data = obj_in
            print("update_data", update_data)
        else:
            update_data = obj_in.model_dump(exclude_unset=True)
        return await super().update(db, db_obj=db_obj, obj_in=update_data)
    """
    async def authenticate(self, db: AgnosticDatabase, *, email: str, password: str) -> User | None: # noqa
        user = await self.get_by_email(db, email=email)
        if not user:
            return None
        if not verify_password(plain_password=password, hashed_password=user.hashed_password): # noqa
            return None
        return user

    async def validate_email(self, db: AgnosticDatabase, *, db_obj: User) -> User: # noqa
        obj_in = UserUpdate(**UserInDB.model_validate(db_obj).model_dump())
        obj_in.email_validated = True
        return await self.update(db=db, db_obj=db_obj, obj_in=obj_in)

    async def activate_totp(self, db: AgnosticDatabase, *, db_obj: User, totp_in: NewTOTP) -> User: # noqa
        obj_in = UserUpdate(**UserInDB.model_validate(db_obj).model_dump())
        obj_in = obj_in.model_dump(exclude_unset=True)
        obj_in["totp_secret"] = totp_in.secret
        return await self.update(db=db, db_obj=db_obj, obj_in=obj_in)

    async def deactivate_totp(self, db: AgnosticDatabase, *, db_obj: User) -> User: # noqa
        obj_in = UserUpdate(**UserInDB.model_validate(db_obj).model_dump())
        obj_in = obj_in.model_dump(exclude_unset=True)
        obj_in["totp_secret"] = None
        obj_in["totp_counter"] = None
        return await self.update(db=db, db_obj=db_obj, obj_in=obj_in)

    async def update_totp_counter(self, db: AgnosticDatabase, *, db_obj: User, new_counter: int) -> User:  # noqa
        obj_in = UserUpdate(**UserInDB.model_validate(db_obj).model_dump())
        obj_in = obj_in.model_dump(exclude_unset=True)
        obj_in["totp_counter"] = new_counter
        return await self.update(db=db, db_obj=db_obj, obj_in=obj_in)

    async def toggle_user_state(self, db: AgnosticDatabase, *, obj_in: Union[UserUpdate, Dict[str, Any]]) -> User: # noqa
        db_obj = await self.get_by_email(db, email=obj_in.email)
        if not db_obj:
            return None
        return await self.update(db=db, db_obj=db_obj, obj_in=obj_in)

    @staticmethod
    def has_password(user: User) -> bool:
        return user.hashed_password is not None

    @staticmethod
    def is_active(user: User) -> bool:
        return user.is_active

    @staticmethod
    def is_superuser(user: User) -> bool:
        return user.is_superuser

    @staticmethod
    def is_email_validated(user: User) -> bool:
        return user.email_validated

    """
procommersold = CRUDProcommersold(Procommersold)
