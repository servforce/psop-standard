from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api import config, standard_library, standards
from app.core.standard_config import standard_settings as settings
from app.db.session import SessionLocal, init_db
from app.db.standard_library import StandardLibrarySessionLocal, init_standard_library_db
from app.jobs.standard_library_processing_worker import StandardLibraryProcessingWorker
from app.jobs.standard_update_scheduler import StandardUpdateScheduler
from app.services.standard_library_atlas import fail_interrupted_standard_library_atlas_jobs
from app.services.standard_library_index import fail_interrupted_standard_library_index_jobs
from app.services.standard_library_materialize import fail_interrupted_standard_library_materialize_jobs
from app.services.standard_job_recovery import fail_interrupted_workbench_jobs
from app.services.storage import storage_service


logger = logging.getLogger(__name__)
logging.getLogger("app").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_standard_library_db()
    with SessionLocal() as session:
        counts = fail_interrupted_workbench_jobs(session)
    with StandardLibrarySessionLocal() as session:
        counts.update(fail_interrupted_standard_library_materialize_jobs(session))
    with StandardLibrarySessionLocal() as session:
        counts.update(fail_interrupted_standard_library_index_jobs(session))
    with StandardLibrarySessionLocal() as session:
        counts.update(fail_interrupted_standard_library_atlas_jobs(session))
    if any(counts.values()):
        logger.warning("marked interrupted standard jobs as failed: %s", counts)

    scheduler = None
    scheduler_task = None
    if settings.standard_update_scheduler_enabled:
        scheduler = StandardUpdateScheduler()
        scheduler_task = asyncio.create_task(scheduler.run_forever(), name="standard-update-scheduler")
        app.state.standard_update_scheduler = scheduler
        app.state.standard_update_scheduler_task = scheduler_task
        logger.info("standard update scheduler enabled")
        print("standard update scheduler enabled", flush=True)
    else:
        logger.info("standard update scheduler disabled")
        print("standard update scheduler disabled", flush=True)

    processing_worker = None
    processing_worker_task = None
    if settings.standard_library_processing_worker_enabled:
        processing_worker = StandardLibraryProcessingWorker()
        processing_worker_task = asyncio.create_task(
            processing_worker.run_forever(),
            name="standard-library-processing-worker",
        )
        app.state.standard_library_processing_worker = processing_worker
        app.state.standard_library_processing_worker_task = processing_worker_task
        logger.info("standard library processing worker enabled")
        print("standard library processing worker enabled", flush=True)
    else:
        logger.info("standard library processing worker disabled")
        print("standard library processing worker disabled", flush=True)

    yield

    if scheduler is not None:
        scheduler.stop()
    if scheduler_task is not None:
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("standard update scheduler stopped with error")

    if processing_worker is not None:
        processing_worker.stop()
    if processing_worker_task is not None:
        try:
            await processing_worker_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("standard library processing worker stopped with error")


def create_app() -> FastAPI:
    init_db()
    init_standard_library_db()
    app = FastAPI(title="Servforce Standard Library Service", version="0.1.0", lifespan=lifespan)
    app.include_router(standards.router)
    app.include_router(standard_library.router)
    app.include_router(config.router)
    app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.get("/")
    def index():
        return FileResponse(
            "static/standard.html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )

    @app.get("/api/objects/{object_key:path}")
    def object_proxy(object_key: str):
        content = storage_service.get_bytes(bucket=settings.object_store_bucket, object_key=object_key)
        return Response(content=content, media_type=media_type_for_object_key(object_key))

    @app.get("/health")
    def health():
        return {"ok": True, "app": "servforce-standard-library-service"}

    return app


def media_type_for_object_key(object_key: str) -> str:
    if object_key.endswith(".jpg") or object_key.endswith(".jpeg"):
        return "image/jpeg"
    if object_key.endswith(".svg"):
        return "image/svg+xml"
    if object_key.endswith(".png"):
        return "image/png"
    if object_key.endswith(".md"):
        return "text/markdown; charset=utf-8"
    if object_key.endswith(".pdf"):
        return "application/pdf"
    return "application/octet-stream"


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.standard_main:app", host="127.0.0.1", port=8091)
