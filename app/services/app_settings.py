"""Studio Settings page: read, validate and save the non-secret options in app/settings_spec.py.

A saved value overrides .env (see app.config.get_settings); reset deletes it. Secrets never pass through here.
Every change is written to the setting_changes history.
"""

import json

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import _env_settings, clear_settings_cache, get_settings
from app.db import utcnow
from app.models import AppSetting, SettingChange
from app.services import AppError
from app.settings_spec import BY_KEY, FIELDS, parse

PAUSED_FROM = "PAUSED_FROM"  # not a setting: remembers the mode to go back to after 'Pause posting'


def rows(db: Session) -> list[dict]:
    """Every field with its effective value and where it comes from ('studio' = saved here, else '.env')."""
    saved = {r.key for r in db.execute(select(AppSetting)).scalars()}
    live, base = get_settings(), _env_settings()
    out = []
    for f in FIELDS:
        value = getattr(live, f["key"])
        if f["kind"] == "choice" and value not in [c[0] for c in f["choices"]]:
            f = {**f, "choices": [*f["choices"], (value, f"{value} (set on the server)")]}  # never silently replace it
        out.append({**f, "value": value, "selected": [v for v in str(value).split(",") if v] if f["kind"] == "multi" else [],
                    "source": "studio" if f["key"] in saved else ".env", "env_value": getattr(base, f["key"])})
    return out


def changes(db: Session, limit: int = 20) -> list[SettingChange]:
    return list(db.execute(select(SettingChange).order_by(desc(SettingChange.at), desc(SettingChange.id)).limit(limit)).scalars())


def paused(db: Session) -> bool:
    return db.get(AppSetting, PAUSED_FROM) is not None


def _log(db: Session, key: str, old, new) -> None:
    db.add(SettingChange(key=key, old=str(old)[:200], new=str(new)[:200]))


def _write(db: Session, key: str, value, old) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=json.dumps(value)))
    else:
        row.value, row.updated_at = json.dumps(value), utcnow()
    _log(db, key, old, value)


def save(db: Session, form: dict) -> int:
    """Validate the whole form first; save only values that differ from what is in effect. Returns how many changed."""
    live = get_settings()
    new: dict = {}
    for f in FIELDS:
        key = f["key"]
        try:
            if f["kind"] == "bool":
                value = parse(f, form.get(key, "off"))  # an unticked checkbox sends nothing
            elif f["kind"] == "multi":
                value = parse(f, form.get(key, []))
            elif key in form:
                value = form[key] if (f["kind"] == "choice" and form[key] == getattr(live, key)) else parse(f, form[key])
            else:
                continue
        except ValueError as e:
            raise AppError("validation_error", str(e), status=422) from e
        new[key] = value
    if new.get("FANVUE_PPV_MIN_CENTS", live.FANVUE_PPV_MIN_CENTS) > new.get("FANVUE_PPV_MAX_CENTS", live.FANVUE_PPV_MAX_CENTS):
        raise AppError("validation_error", "Lowest paid-message price cannot be above the highest.", status=422)
    if new.get("PUBLISHING_MODE") == "live" and not live.live_publishing_ready:
        raise AppError("validation_error", "Live needs PUBLIC_BASE_URL, TOKEN_ENCRYPTION_KEY and MEDIA_URL_SECRET in .env first.", status=422)
    changed = 0
    for key, value in new.items():
        if value != getattr(live, key):
            _write(db, key, value, getattr(live, key))
            changed += 1
    if new.get("PUBLISHING_MODE", live.PUBLISHING_MODE) != "off":
        row = db.get(AppSetting, PAUSED_FROM)  # switching posting on by hand ends a pause
        if row is not None:
            db.delete(row)
    db.commit()
    clear_settings_cache()
    return changed


def reset(db: Session, key: str | None = None) -> None:
    """Delete one saved value (or all): .env / the default applies again."""
    if key is not None and key not in BY_KEY:
        raise AppError("validation_error", "Unknown setting.", status=422)
    base = _env_settings()
    for row in db.execute(select(AppSetting)).scalars().all():
        if key is None or row.key == key:
            if row.key in BY_KEY:
                _log(db, row.key, row.value, f"{getattr(base, row.key)} (server value)")
            db.delete(row)
    db.commit()
    clear_settings_cache()


def pause(db: Session) -> None:
    """One click: publishing off, remembering the mode so Resume can put it back."""
    live = get_settings()
    if live.PUBLISHING_MODE == "off":
        raise AppError("invalid_transition", "Posting is already off.", status=409)
    db.merge(AppSetting(key=PAUSED_FROM, value=json.dumps(live.PUBLISHING_MODE)))
    _write(db, "PUBLISHING_MODE", "off", live.PUBLISHING_MODE)
    db.commit()
    clear_settings_cache()


def resume(db: Session) -> None:
    row = db.get(AppSetting, PAUSED_FROM)
    if row is None:
        raise AppError("invalid_transition", "Posting is not paused.", status=409)
    mode = json.loads(row.value)
    _write(db, "PUBLISHING_MODE", mode, get_settings().PUBLISHING_MODE)
    db.delete(row)
    db.commit()
    clear_settings_cache()
