"""Tests for app startup, health, and database initialization."""


def test_app_starts(client):
    """App starts and responds to health check."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "ok"
    assert data["claude_configured"] is True
    assert data["version"] == "0.2.0"


def test_health_endpoint_open(client):
    """Health endpoint is accessible without auth even when auth is enabled."""
    response = client.get("/health")
    assert response.status_code == 200


def test_db_creates_all_tables(app, settings):
    """Database init creates all expected tables."""
    engine = app.dependency_overrides.get("engine")  # May not be set
    from app.db import get_engine
    from sqlalchemy import inspect

    engine = get_engine()
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    expected = {
        "model_profiles", "tags", "asset_tags", "post_tags",
        "media_assets", "content_posts", "content_variants",
        "generation_logs", "alembic_version",
    }
    # alembic_version might not exist if using create_all
    expected_tables = expected - {"alembic_version"}
    assert expected_tables.issubset(tables), f"Missing tables: {expected_tables - tables}"


def test_init_db_idempotent(app, settings):
    """Running init-db twice is safe (idempotent)."""
    from app.db import session_scope, get_engine, Base
    from app.seed import seed_all

    engine = get_engine()
    Base.metadata.create_all(engine)

    # App fixture already seeded, so both runs should add 0
    with session_scope() as session:
        result1 = seed_all(session)

    with session_scope() as session:
        result2 = seed_all(session)

    # Both runs add 0 because app fixture already seeded
    assert result1["profiles"] == 0  # Already exists
    assert result1["categories"] == 0  # Already exist
    assert result2["profiles"] == 0  # Still exists, no duplicates
    assert result2["categories"] == 0  # Still exist

    # Verify data still present
    from sqlalchemy import select, func
    from app.models import ModelProfile, Tag
    with session_scope() as session:
        profile_count = session.execute(select(func.count(ModelProfile.id))).scalar()
        category_count = session.execute(select(func.count(Tag.id)).where(Tag.is_category == True)).scalar()
        assert profile_count == 1  # Summer
        assert category_count == 6  # 6 categories


def test_alembic_migration_on_empty_db(settings, tmp_path):
    """Alembic upgrade head succeeds on an empty temp database."""
    import os
    from alembic import command as alembic_command
    from alembic.config import Config as AlembicConfig

    test_db = tmp_path / "migrate_test.db"
    test_url = f"sqlite:///{test_db}"

    # Point settings at this DB
    os.environ["DATABASE_URL"] = test_url
    clear_settings_cache()

    try:
        cfg = AlembicConfig("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", test_url)
        alembic_command.upgrade(cfg, "head")

        # Verify tables exist
        from sqlalchemy import create_engine, inspect
        engine = create_engine(test_url)
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "model_profiles" in tables
        assert "content_posts" in tables
        engine.dispose()
    finally:
        os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}/test.db"
        clear_settings_cache()


def test_docs_available_in_dev(client):
    """OpenAPI docs are available in development mode."""
    response = client.get("/docs")
    assert response.status_code == 200


def test_api_prefix(client):
    """API routes are under /api/v1."""
    response = client.get("/api/v1/profiles")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


# Helper to clear cache (imported here to avoid circular import in test)
from app.config import clear_settings_cache