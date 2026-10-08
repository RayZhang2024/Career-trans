from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import configure_langsmith_environment, get_settings, log_langsmith_configuration
from app.core.database import engine
from app.services.candidate_compatibility_runtime import run_candidate_compatibility_startup
from app.services.semantic_runtime_attribution import RuntimeAttributionIntegrityError
import app.models  # noqa: F401  # Ensures SQLAlchemy models are registered.

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_langsmith_environment(settings)
    log_langsmith_configuration(settings)
    result = run_candidate_compatibility_startup(engine)
    if result is not None:
        logger.info(
            "Candidate compatibility startup complete: users=%d changed=%d unresolved=%d not_yet_confirmed=%d",
            result.user_count,
            result.changed_count,
            result.unresolved_count,
            result.not_yet_confirmed_count,
        )
        if result.unresolved_count:
            logger.warning(
                "Candidate compatibility left %d user record(s) unresolved.",
                result.unresolved_count,
            )
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.exception_handler(RuntimeAttributionIntegrityError)
async def invalid_runtime_attribution_handler(_, __: RuntimeAttributionIntegrityError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": "Persisted runtime attribution is invalid."})

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
