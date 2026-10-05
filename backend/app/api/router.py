"""Root API router assembled from bounded feature routers."""

from fastapi import APIRouter

from app.api.health import router as health_router
from app.api.routes import router as chat_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(chat_router)
