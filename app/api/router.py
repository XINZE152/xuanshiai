"""Top-level API router."""

from fastapi import APIRouter

from app.api.routes import health
from app.api.routes import auth, identity, users


api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(users.router, tags=["users"])
api_router.include_router(identity.router, tags=["identity"])
