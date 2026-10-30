from fastapi import APIRouter
from fastapi_pagination import LimitOffsetPage, Page, add_pagination
from app.api.api_v1.endpoints import (
    #cls_mat,
    login,
    users,
    pro_meta,
    searchnl,
    searchimg
)

api_router = APIRouter()
#api_router.include_router(cls_mat.router, prefix="/aus", tags=["Text, Image Embedding"])
api_router.include_router(pro_meta.router, prefix="/aus", tags=["Property Meta Data"])
#api_router.include_router(prosales.router, prefix="/prosales", tags=["CRUD Property for Sale"])
api_router.include_router(searchnl.router, prefix="/aus", tags=["Search by Natural Language"])
api_router.include_router(searchimg.router, prefix="/aus", tags=["Search by Image and Visual Property Recognition"])
api_router.include_router(login.router, prefix="/login", tags=["Login"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
#api_router.include_router(healthy.router, prefix="")
add_pagination(api_router)