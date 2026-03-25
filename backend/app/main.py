from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.debug import router as debug_router
from app.api.documents import router as documents_router
from app.api.logs import router as logs_router
from app.core.config import PROJECT_ROOT, get_settings
from app.services.log_store import init_log_store
from app.services.storage import init_storage


settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_storage()
    init_log_store()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(documents_router)
app.include_router(logs_router)
app.include_router(debug_router)
app.mount(
    "/static/rendered-pages",
    StaticFiles(directory=PROJECT_ROOT / "data" / "rendered_pages", check_dir=False),
    name="rendered-pages",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "app_name": settings.app_name,
        "schema_version": settings.schema_version,
    }
