"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.db import Base, get_engine, init_db
from app.logging_setup import configure_logging, get_logger

# NOTE: app.security still owns _rate_limit_store (used at runtime by the rate limiter),
# but app/main.py no longer imports require_basic_auth / check_origin — that auth + CSRF
# logic lives inline in auth_and_csrf_middleware below to keep the composition explicit.
import app.security as _security  # noqa: F401  (kept for runtime store + future refactors)


# Configure logging early
configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info("Starting application", extra={"event": "startup"})
    init_db()

    # Verify database connection
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database connection verified")
    except Exception as e:
        logger.error("Database connection failed", extra={"event": "startup", "error": str(e)})
        raise

    yield

    # Shutdown
    logger.info("Shutting down application", extra={"event": "shutdown"})
    engine.dispose()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    # Configure docs URLs based on environment
    docs_url = "/docs" if settings.APP_ENV == "development" else None
    redoc_url = "/redoc" if settings.APP_ENV == "development" else None

    app = FastAPI(
        title="Summer AI Content Automation",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
    )

    # =====================================================================
    # Exception Handlers
    # =====================================================================

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """Handle HTTPExceptions with standardized ErrorOut format."""
        # If detail is already an error dict, keep its code.
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": {"code": exc.detail["code"], "message": exc.detail.get("message", "")}},
                headers=getattr(exc, "headers", None),
            )
        code_map = {
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            413: "payload_too_large",
            415: "unsupported_media_type",
            422: "validation_error",
            429: "rate_limited",
        }
        code = code_map.get(exc.status_code, "error")
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": str(exc.detail)}},
            headers=getattr(exc, "headers", None),
        )

    from app.services import AppError

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        """Handle AppError raised by services with the ErrorOut shape."""
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Handle Pydantic validation errors with standardized format."""
        errors = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err["loc"])
            errors.append(f"{loc}: {err['msg']}")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"error": {"code": "validation_error", "message": "; ".join(errors)}},
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        """Handle unexpected exceptions."""
        logger.exception(
            "Unhandled exception",
            extra={"event": "exception", "path": request.url.path, "method": request.method}
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": {"code": "internal_error", "message": "Unexpected error"}},
        )

    # =====================================================================
    # Middleware
    # =====================================================================

    @app.middleware("http")
    async def auth_and_csrf_middleware(request: Request, call_next):
        """Apply basic auth (all routes except /health) and dashboard origin check."""
        # Skip auth for the health endpoint and the signed public media route (/m/{token}:
        # HMAC + expiry protect it; Instagram must be able to fetch it without credentials)
        if request.url.path == "/health" or (request.url.path.startswith("/m/") and request.method in ("GET", "HEAD")):
            return await call_next(request)

        # Basic auth when enabled (secrets.compare_digest, constant-time)
        if settings.basic_auth_enabled:
            import base64
            import secrets as _secrets

            auth_header = request.headers.get("Authorization", "")
            authorized = False
            if auth_header.startswith("Basic "):
                try:
                    decoded = base64.b64decode(auth_header[6:].strip()).decode("utf-8")
                    username, _, password = decoded.partition(":")
                    ok_user = _secrets.compare_digest(username, settings.DASHBOARD_USERNAME or "")
                    ok_pass = _secrets.compare_digest(
                        password,
                        settings.DASHBOARD_PASSWORD.get_secret_value() if settings.DASHBOARD_PASSWORD else "",
                    )
                    authorized = ok_user and ok_pass
                except Exception:
                    authorized = False
            if not authorized:
                return JSONResponse(
                    status_code=401,
                    content={"error": {"code": "unauthorized", "message": "Authentication required"}},
                    headers={"WWW-Authenticate": "Basic"},
                )

        # CSRF mitigation: dashboard (non-/api) unsafe methods must be same-origin
        unsafe = request.method not in ("GET", "HEAD", "OPTIONS", "TRACE")
        if not request.url.path.startswith("/api") and unsafe:
            from urllib.parse import urlparse

            host = request.headers.get("host")
            origin = request.headers.get("origin") or request.headers.get("referer")
            if host and origin:
                origin_host = urlparse(origin).netloc
                if origin_host and origin_host != host:
                    return JSONResponse(
                        status_code=403,
                        content={"error": {"code": "forbidden", "message": "Cross-origin request rejected"}},
                    )

        return await call_next(request)

    @app.middleware("http")
    async def logging_middleware(request: Request, call_next):
        """Log requests with timing."""
        start_time = time.time()
        response = await call_next(request)
        duration_ms = int((time.time() - start_time) * 1000)

        logger.info(
            "Request completed",
            extra={
                "event": "request",
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            }
        )
        return response

    # =====================================================================
    # Routes
    # =====================================================================

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        settings = get_settings()
        engine = get_engine()

        db_ok = False
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            pass

        from sqlalchemy.orm import Session as _Session

        from app.services.scheduler import worker_status

        with _Session(bind=engine) as hb_session:
            worker_last_seen, worker_ok = worker_status(hb_session)

        return {
            "status": "ok" if db_ok else "degraded",
            "database": "ok" if db_ok else "error",
            "claude_configured": settings.claude_configured,
            "mistral_configured": settings.mistral_configured,
            "publishing_mode": settings.PUBLISHING_MODE,
            "worker_last_seen": worker_last_seen,
            "worker_ok": worker_ok,
            "version": "0.1.0",
        }

    from app.api import profiles, assets, content, posts, tags, planner, notifications
    from app.api import accounts as accounts_api, media_public, publishing as publishing_api
    from app.web import routes as web_routes

    app.include_router(profiles.router, prefix="/api/v1")
    app.include_router(assets.router, prefix="/api/v1")
    app.include_router(content.router, prefix="/api/v1")
    app.include_router(posts.router, prefix="/api/v1")
    app.include_router(tags.router, prefix="/api/v1")
    app.include_router(planner.router, prefix="/api/v1")
    app.include_router(notifications.router, prefix="/api/v1")
    app.include_router(accounts_api.router, prefix="/api/v1")
    app.include_router(publishing_api.router, prefix="/api/v1")
    app.include_router(accounts_api.oauth_router)
    app.include_router(media_public.router)
    app.include_router(web_routes.router)

    # Static files
    from fastapi.staticfiles import StaticFiles
    static_dir = Path(__file__).parent / "web" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


import time  # for logging middleware (used in logging_middleware above)


# Module-level app instance for uvicorn: `uvicorn app.main:app`
app = create_app()