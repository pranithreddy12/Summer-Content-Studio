"""Summer AI Content Automation - FastAPI application package."""

# Health endpoint is defined inline in app/main.py (create_app).
# Do not remount app.api.health.router alongside it, or /health will be duplicated.