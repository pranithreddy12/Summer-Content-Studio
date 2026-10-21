"""CLI commands for database initialization and media sync."""

import sys
from pathlib import Path

import click
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from app.config import get_settings
from app.db import Base, get_engine, init_db, session_scope
from app.seed import seed_all


@click.group()
def cli():
    """Summer AI Content Automation CLI."""
    pass


@cli.command()
def init_db() -> None:
    """Initialize database: create tables, run migrations, seed data."""
    settings = get_settings()

    # Ensure data directory exists
    data_dir = Path(settings.DATABASE_URL.replace("sqlite:///", "")).parent
    data_dir.mkdir(parents=True, exist_ok=True)

    # Ensure media directories exist
    settings.MEDIA_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if settings.MEDIA_SYNC_DIR:
        settings.MEDIA_SYNC_DIR.mkdir(parents=True, exist_ok=True)

    # Run Alembic migrations (creates all tables). Only falls back to
    # create_all+stamp for legacy DBs that were built without Alembic.
    alembic_cfg = AlembicConfig("alembic.ini")
    try:
        alembic_command.upgrade(alembic_cfg, "head")
    except Exception:
        engine = get_engine()
        Base.metadata.create_all(engine)
        alembic_command.stamp(alembic_cfg, "head")

    # Seed data
    with session_scope() as db:
        result = seed_all(db)
        click.echo(f"Seeded: {result['categories']} categories, {result['profiles']} profiles")

    click.echo("Database initialized successfully!")


@cli.command()
def sync_media() -> None:
    """Sync media from MEDIA_SYNC_DIR."""
    from app.services.assets import sync_media_dir

    settings = get_settings()
    if not settings.MEDIA_SYNC_DIR:
        click.echo("MEDIA_SYNC_DIR not configured", err=True)
        sys.exit(1)

    with session_scope() as db:
        result = sync_media_dir(db)
        click.echo(f"Sync complete: {result['added']} added, {result['skipped']} skipped")


@cli.command()
@click.option("--profile", "profile_id", default=1, type=int, show_default=True)
@click.option("--platform", default="instagram", show_default=True)
@click.option("--seed", "seed", default=None, type=int,
              help="RNG seed for deterministic runs (e.g. reviews)")
def plan_week(profile_id: int, platform: str, seed) -> None:
    """Plan the next 7 days for one (profile, platform) rule (PLAN §9a)."""
    import random as _random
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from sqlalchemy import select as _select

    from app.models import PostingRule
    from app.db import utcnow
    from app.services.planner import plan_week as _plan_week

    with session_scope() as db:
        rule = db.execute(
            _select(PostingRule).where(PostingRule.model_profile_id == profile_id,
                                       PostingRule.platform == platform)
        ).scalar_one_or_none()
        if not rule:
            click.echo(f"No posting rule for profile {profile_id} / {platform}", err=True)
            sys.exit(1)
        rng = _random.Random(seed) if seed is not None else None
        result = _plan_week(db, rule, utcnow(), rng=rng)
        click.echo(
            f"[{platform}] slots={len(result.slots)} filled={len(result.filled)} "
            f"unfilled={len(result.unfilled)} topup_drafts={len(result.topup_drafts)}"
        )
        tz = ZoneInfo(rule.timezone)
        day = (utcnow().astimezone(tz) + timedelta(days=1)).date()
        for _i in range(7):
            times = [s.start_utc.astimezone(tz).strftime("%H:%M")
                     for s in sorted(result.slots, key=lambda s: s.start_utc) if s.local_day == day]
            click.echo(f"  {day.isoformat()}: {', '.join(times) or '-'} ({'filled' if times else 'unfilled'})")
            day = day + timedelta(days=1)


if __name__ == "__main__":
    cli()