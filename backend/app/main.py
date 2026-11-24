from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.api import analytics, auth, blueprints, datasets, sources
from app.core.config import get_settings
from app.core.errors import PrismError
from app.core.logging import configure_logging, log
from app.core.security import hash_password
from app.db.models import Base, Membership, User, Workspace
from app.db.session import get_engine, get_sessionmaker
from app.analytics import service as _analytics_handlers  # noqa: F401  (registers job handlers)
from app.blueprint import service as _blueprint_handlers  # noqa: F401  (registers job handlers)
from app.services import ingest as _ingest_handlers  # noqa: F401  (registers job handlers)
from app.services.jobs import worker_loop


def bootstrap() -> None:
    """Create schema in dev/test (prod uses Alembic) and the initial admin user."""
    s = get_settings()
    if s.env != "production":
        Base.metadata.create_all(get_engine())
    with get_sessionmaker()() as db:
        if s.admin_email and s.admin_password and db.execute(select(User).limit(1)).first() is None:
            user = User(email=s.admin_email.lower(), name="Administrator", password_hash=hash_password(s.admin_password), is_superadmin=True)
            ws = Workspace(name="Main workspace", slug="main", timezone=s.timezone)
            db.add_all([user, ws])
            db.flush()
            db.add(Membership(user_id=user.id, workspace_id=ws.id, role="admin"))
            try:
                db.commit()
                log.info("bootstrap_admin_created", email=user.email)
            except IntegrityError:  # another API worker process bootstrapped concurrently
                db.rollback()


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(json=s.env == "production")
    bootstrap()
    stop = threading.Event()
    thread = None
    if s.jobs_mode == "embedded":
        thread = threading.Thread(target=worker_loop, args=(stop,), name="prism-embedded-worker", daemon=True)
        thread.start()
    yield
    stop.set()
    if thread:
        thread.join(timeout=5)


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="PRISM API",
        version="0.1.0",
        description="Universal AI data intelligence and automation platform.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )

    @app.exception_handler(PrismError)
    async def prism_error(_: Request, exc: PrismError):
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message, **({"details": exc.details} if exc.details else {})}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        errs = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": "Invalid request.", "details": {"errors": errs}}})

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    @app.get("/api/health", tags=["system"])
    def health():
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
        return {"status": "ok", "version": app.version}

    for r in (auth.router, sources.router, datasets.router, blueprints.router, analytics.router):
        app.include_router(r, prefix="/api/v1")
    return app


app = create_app()
