from typing import Any, Dict, Union, List
from motor.core import AgnosticDatabase
from app.crud.base import CRUDBase
from app.models import Imagesold
from app.schemas.Imagesold import ImagesoldCreate, ImagesoldInDB, ImagesoldUpdate

# ODM, Schema, Schema
class CRUDImagesold(CRUDBase[Imagesold, ImagesoldCreate, ImagesoldUpdate]):
    async  def create_many(self, db: AgnosticDatabase, *, objs_in: List[ImagesoldCreate]) -> List[Imagesold]:  # noqa
        async with self.engine.session() as session:
            return await session.save_all(
                [Imagesold(**obj_in.model_dump()) for obj_in in objs_in])

imagesold = CRUDImagesold(Imagesold)
