"""Migrations (PLAN_PHASE2 §16 test_app extension):
alembic upgrade head on an empty DB, and a Phase-1 DB upgrades without data loss.
"""

import os
import sqlite3

from alembic import command
from alembic.config import Config

import pytest


def _run_upgrade(db_path, to):
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    try:
        from app.config import clear_settings_cache

        clear_settings_cache()
        cfg = Config("alembic.ini")
        command.upgrade(cfg, to)
    finally:
        clear_settings_cache()
        if old is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old


def test_upgrade_head_on_empty_db(tmp_path):
    db = str(tmp_path / "empty.db")
    _run_upgrade(db, "head")

    con = sqlite3.connect(db)
    try:
        # posting_rules seeds are per-profile: an empty DB has none yet,
        # but the table + new columns + Phase 2 tables must exist.
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for t in ["platform_accounts", "oauth_states", "publish_attempts", "post_metrics",
                  "comments", "worker_heartbeats", "notifications", "posting_rules"]:
            assert t in tables
        assert con.execute("SELECT COUNT(*) FROM posting_rules").fetchone()[0] == 0
        cols = {r[1] for r in con.execute("PRAGMA table_info(content_posts)").fetchall()}
        for c in ["published_via", "external_post_id", "planned_by", "group_key"]:
            assert c in cols
    finally:
        con.close()


def test_upgrade_from_phase1_db_keeps_data(tmp_path):
    db = str(tmp_path / "phase1.db")
    # Land on the Phase-1 head, put real-looking data in, then upgrade.
    _run_upgrade(db, "6fe077f2856a")

    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO model_profiles (slug, name, handles, persona_description, tone_style,"
        " communication_rules, restricted_topics, style_notes, cta_guidelines, is_active,"
        " created_at, updated_at)"
        " VALUES ('summer', 'Summer', '{}', 'desc', 'tone', '[]', '[]', '', '', 1,"
        " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    cur = con.execute(
        "INSERT INTO content_posts (model_profile_id, platform, content_type, campaign, hook,"
        " caption, hashtags, call_to_action, content_notes, status, source, created_at, updated_at)"
        " SELECT id, 'instagram', 'feed_post', 'lifestyle', 'hook', 'caption', '[]', '', '',"
        " 'draft', 'ai', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP FROM model_profiles WHERE slug='summer'"
    )
    con.commit()
    con.close()

    _run_upgrade(db, "head")

    con = sqlite3.connect(db)
    try:
        post = con.execute(
            "SELECT status, platform, caption, publish_attempt_count, planned_by, published_via FROM content_posts"
        ).fetchone()
        assert post == ("draft", "instagram", "caption", 0, None, None)
        profile = con.execute("SELECT slug, name FROM model_profiles").fetchone()
        assert profile == ("summer", "Summer")
        # New infrastructure present and seeded
        rules = con.execute("SELECT COUNT(*) FROM posting_rules WHERE is_active=0").fetchone()[0]
        assert rules == 2
    finally:
        con.close()
