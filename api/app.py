"""FastAPI application factory."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api.errors import APIError
from api.routes import router, ui
from api.settings import ROOT, Settings
from api.store import Store
from worker.cloud import AzureCloud, FakeCloud
from worker.engine import Worker


def create_app(settings: Settings, store: Store | None = None, cloud=None) -> FastAPI:
    store = store or Store(settings.database_path)
    if cloud is None:
        cloud = AzureCloud(settings.repo_root) if settings.cloud == "azure" else FakeCloud()
    templates = Jinja2Templates(directory=str(ROOT / "ui" / "templates"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = app.state.worker
        thread = None
        if app.state.settings.worker_enabled:
            thread = threading.Thread(target=worker.run_forever, name="dbaas-worker", daemon=True)
            thread.start()
        yield
        worker.stop()

    app = FastAPI(title="Scylla DBaaS", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.cloud = cloud
    app.state.worker = Worker(store, cloud, settings)
    app.state.templates = templates
    app.mount("/static", StaticFiles(directory=str(ROOT / "ui" / "static")), name="static")
    app.include_router(ui)
    app.include_router(router, prefix="/api/v1")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.exception_handler(APIError)
    async def api_error(_request: Request, exc: APIError):
        return JSONResponse(status_code=exc.status, content=exc.body())

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        message = "Request body is invalid"
        if exc.errors():
            message = exc.errors()[0].get("msg", message)
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "invalid_body", "message": message, "operation_id": None}},
        )

    return app
