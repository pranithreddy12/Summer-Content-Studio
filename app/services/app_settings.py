"""Studio Settings page: read, validate and save the non-secret options in app/settings_spec.py.

A saved value overrides .env (see app.config.get_settings); reset deletes it. Secrets never pass through here.
"""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import clear_settings_cache, get_settings, _env_settings
from app.db import utcnow
from app.models import AppSetting
from app.services import AppError
from app.settings_spec import BY_KEY, FIELDS, parse


def rows(db: Session) -> list[dict]:
    """Every field with its effective value and where it comes from ('studio' = saved here, else '.env')."""
    saved = {r.key for r in db.execute(select(AppSetting)).scalars()}
    live, base = get_settings(), _env_settings()
    out = []
    for f in FIELDS:
        value = getattr(live, f["key"])
        out.append({**f, "value": value, "selected": [v for v in str(value).split(",") if v] if f["kind"] == "multi" else [],
                    "source": "studio" if f["key"] in saved else ".env", "env_value": getattr(base, f["key"])})
    return out


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
                value = parse(f, form[key])
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
        if value == getattr(live, key):
            continue
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=json.dumps(value)))
        else:
            row.value, row.updated_at = json.dumps(value), utcnow()
        changed += 1
    db.commit()
    clear_settings_cache()
    return changed


def reset(db: Session, key: str | None = None) -> None:
    """Delete one saved value (or all): .env / the default applies again."""
    if key is not None and key not in BY_KEY:
        raise AppError("validation_error", "Unknown setting.", status=422)
    for row in db.execute(select(AppSetting)).scalars().all():
        if key is None or row.key == key:
            db.delete(row)
    db.commit()
    clear_settings_cache()
