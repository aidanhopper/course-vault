from fastapi import APIRouter

from vault.api.imports import router as imports_router
from vault.api.resources import router as resources_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(imports_router)
api_v1_router.include_router(resources_router)
