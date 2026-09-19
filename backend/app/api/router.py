from fastapi import APIRouter

from app.api.routes import auth, config, onboarding, profile, users
from app.api.routes import cv_ingestion
from app.api.routes import candidate_adviser
from app.api.routes import applications
from app.api.routes.demo import router as demo_router
from app.api.routes.jobs import router as jobs_router

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(profile.router)
api_router.include_router(onboarding.router)
api_router.include_router(config.router)
api_router.include_router(cv_ingestion.router)
api_router.include_router(candidate_adviser.router)
api_router.include_router(applications.router)
api_router.include_router(jobs_router)
api_router.include_router(demo_router)
