"""CLI commands for database initialization and media sync."""

import sys
from pathlib import Path

import click
from sqlalchemy import inspect as sa_inspect
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
    except Exception as e:
        engine = get_engine()
        if "alembic_version" in sa_inspect(engine).get_table_names():
            # A tracked database whose upgrade failed: stamping it "current" would hide missing columns forever.
            click.echo(f"Migration failed, database left as it was: {e}", err=True)
            sys.exit(1)
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

    from app.services import AppError

    with session_scope() as db:
        try:
            result = sync_media_dir(db)
        except AppError as e:  # e.g. disk_low: nothing was registered
            click.echo(e.message, err=True)
            sys.exit(1)
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


@cli.command()
@click.option("--once", "once", is_flag=True, help="Run one iteration and exit (for tests).")
def worker(once) -> None:
    """The one background process: poll the DB, publish/remind per PUBLISHING_MODE."""
    from app.logging_setup import configure_logging

    configure_logging()
    settings = get_settings()
    interval = settings.WORKER_INTERVAL_SECONDS

    if settings.PUBLISHING_MODE == "live" and not settings.live_publishing_ready:
        click.echo(
            "PUBLISHING_MODE=live needs PUBLIC_BASE_URL (https://), TOKEN_ENCRYPTION_KEY "
            "and MEDIA_URL_SECRET. Refusing to start (PLAN §2).",
            err=True,
        )
        sys.exit(1)
    if settings.PUBLISHING_MODE == "reminders" and not settings.notify_configured:
        click.echo(
            f"PUBLISHING_MODE=reminders needs a push channel: NOTIFY_PROVIDER={settings.NOTIFY_PROVIDER} "
            "has no credentials. Refusing to start (PLAN §9b).",
            err=True,
        )
        sys.exit(1)

    import signal
    import threading

    from app.platforms import http_client
    from app.services.scheduler import run_forever

    stop = threading.Event()

    def request_stop(signum, frame):  # finish the current iteration, then exit cleanly
        click.echo("Stop requested - finishing the current iteration...")
        stop.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):  # SIGBREAK = Ctrl+Break on Windows
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), request_stop)

    click.echo(f"Worker started: interval={interval}s mode={settings.PUBLISHING_MODE}")
    http = http_client(settings)  # one shared client for the whole run
    try:
        run_forever(stop, interval, http=http, once=once, echo=click.echo)
    finally:
        http.close()
    click.echo("Worker stopped cleanly.")


@cli.command()
def notify_test() -> None:
    """Send a test push via the configured NOTIFY_PROVIDER."""
    from app.services.notifications import configured, send_test

    settings = get_settings()
    if not configured(settings):
        click.echo(
            f"NOTIFY_PROVIDER={settings.NOTIFY_PROVIDER} has no credentials set. "
            "See .env.example (PLAN §9b).",
            err=True,
        )
        sys.exit(1)
    with session_scope() as db:
        ok = send_test(db)
    click.echo("Test notification sent." if ok else "Provider refused - check the log.")


@cli.command()
@click.option("--days", default=7, show_default=True, help="Refresh tokens that expire within this many days.")
def refresh_tokens(days) -> None:
    """Refresh platform tokens that are close to expiry (the worker does this automatically in B6)."""
    from datetime import timedelta

    from app.db import utcnow
    from app.platforms import PlatformError, http_client
    from app.services import accounts

    http = http_client()
    with session_scope() as db:
        due = accounts.accounts_expiring(db, utcnow(), timedelta(days=days))
        if not due:
            click.echo("Nothing to refresh.")
        for acct in due:
            try:
                accounts.refresh_account(db, acct, http)
                click.echo(f"{acct.platform} @{acct.username}: refreshed, now expires {acct.token_expires_at:%Y-%m-%d}")
            except PlatformError as e:
                click.echo(f"{acct.platform} @{acct.username}: {e.code} - {accounts.sanitize(e.message)}", err=True)


if __name__ == "__main__":
    cli()