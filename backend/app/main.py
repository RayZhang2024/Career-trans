from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import configure_langsmith_environment, get_settings
from app.core.database import Base, engine
import app.models  # noqa: F401  # Ensures SQLAlchemy models are registered.

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_langsmith_environment(settings)
    # V1 convenience. Replace with Alembic migrations before production deployment.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}
