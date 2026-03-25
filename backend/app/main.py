from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.debug import router as debug_router
from app.api.documents import router as documents_router
from app.core.config import get_settings
from app.services.storage import init_storage


settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_storage()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(documents_router)
app.include_router(debug_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "app_name": settings.app_name,
        "schema_version": settings.schema_version,
    }
