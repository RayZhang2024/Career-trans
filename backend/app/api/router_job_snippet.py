# Merge these lines into backend/app/api/router.py

from app.api.routes.jobs import router as jobs_router

# Add alongside the existing include_router(...) calls:
api_router.include_router(jobs_router)
