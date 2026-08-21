from fastapi import APIRouter

from app.api.routes import auth, profile, users
from app.api.routes.jobs import router as jobs_router

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(profile.router)
api_router.include_router(jobs_router)
