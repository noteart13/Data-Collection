from typing import Any, Dict, Union, List
from motor.core import AgnosticDatabase
from app.crud.base import CRUDBase
from app.models import Image
from app.schemas.Image import ImageCreate, ImageInDB, ImageUpdate



# ODM, Schema, Schema
class CRUDImage(CRUDBase[Image, ImageCreate, ImageUpdate]):
    async  def create_many(self, db: AgnosticDatabase, *, objs_in: List[ImageCreate]) -> List[Image]:  # noqa
        async with self.engine.session() as session:
            return await session.save_all(
                [Image(**obj_in.model_dump()) for obj_in in objs_in])

image = CRUDImage(Image)
