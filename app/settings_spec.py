"""Options that can be changed on the studio Settings page (non-secret only).

A value saved there lives in the `app_settings` table and overrides `.env`; "Reset" deletes it and the `.env`
value (or the default) applies again. Keys, tokens, passwords and URLs stay in `.env` and are never editable
here. No imports from the app: `app.config` uses this too.
"""

import json

PLATFORMS = ("instagram", "tiktok", "fanvue")

MODES = [("off", "Off - drafts only, nothing is published"),
         ("reminders", "Reminders - I post by hand, the studio reminds me"),
         ("dry_run", "Dry run - rehearse, nothing is sent"),
         ("live", "Live - approved posts are published on schedule")]

FIELDS = [
    {"key": "PUBLISHING_MODE", "group": "Publishing", "label": "Publishing mode", "kind": "choice", "choices": MODES,
     "help": "The one switch for everything that can post. Live needs the server's public address and keys set in .env."},
    {"key": "ENABLED_PLATFORMS", "group": "Publishing", "label": "Platforms to publish to", "kind": "multi",
     "choices": [(p, p.capitalize()) for p in PLATFORMS],
     "help": "A switched-off platform gets no reminders, no automatic posts and no auto-planning. Drafts stay."},
    {"key": "TIKTOK_POST_MODE", "group": "Publishing", "label": "TikTok delivery", "kind": "choice",
     "choices": [("inbox", "Inbox - the video lands in TikTok drafts and you post it"),
                 ("direct", "Direct - the studio posts it (only after TikTok's audit approves the app)")],
     "help": "Inbox is the safe choice until TikTok approves direct posting."},
    {"key": "PUBLISH_MAX_ATTEMPTS", "group": "Publishing", "label": "Publish attempts before a post fails",
     "kind": "int", "min": 1, "max": 10, "help": "Failed publishes are retried this many times."},
    {"key": "AUTO_PLAN_ENABLED", "group": "Drafting and scheduling", "label": "Plan and draft every day",
     "kind": "bool", "help": "Once a day the studio plans the next 7 days and writes drafts for empty slots (needs 'auto top-up' on the Planner rule)."},
    {"key": "PLANNER_RUN_HOUR", "group": "Drafting and scheduling", "label": "Daily planning hour (local time, 0-23)",
     "kind": "int", "min": 0, "max": 23, "help": "The planner runs once a day from this hour."},
    {"key": "PLANNER_MAX_TOPUP_PER_DAY", "group": "Drafting and scheduling", "label": "How many drafts to write per day (per platform)",
     "kind": "int", "min": 0, "max": 50, "help": "0 stops automatic drafting."},
    {"key": "AUTO_APPROVE_PLATFORMS", "group": "Drafting and scheduling", "label": "Fully automatic: no approval needed for", "kind": "multi",
     "choices": [(p, p.capitalize()) for p in PLATFORMS],
     "help": "WARNING: new automatic drafts for the ticked platforms are approved, scheduled and posted without you seeing them first. "
             "Leave all unticked to keep approving every post yourself. Drafts you make by hand always wait for you."},
    {"key": "AUTO_SCHEDULE_ON_APPROVE", "group": "Drafting and scheduling", "label": "Schedule a post as soon as it is approved",
     "kind": "bool", "help": "Off: an approved post waits for the next daily planning run."},
    {"key": "PLANNER_LEAD_MINUTES", "group": "Drafting and scheduling", "label": "Earliest slot after planning (minutes)",
     "kind": "int", "min": 0, "max": 1440, "help": "A slot is never earlier than this many minutes from now."},
    {"key": "NOTIFY_PROVIDER", "group": "Notifications", "label": "Push channel", "kind": "choice",
     "choices": [("none", "None"), ("telegram", "Telegram"), ("ntfy", "ntfy"), ("pushover", "Pushover")],
     "help": "The bot token / chat id / topic stay in .env."},
    {"key": "NOTIFY_NEW_DRAFTS", "group": "Notifications", "label": "Tell me about every new draft", "kind": "bool",
     "help": "One message per draft, with an Approve button on Telegram. For fully automatic platforms it says the post was auto-approved."},
    {"key": "NOTIFY_PUBLISHED", "group": "Notifications", "label": "Tell me when a post goes live", "kind": "bool",
     "help": "One message with the link to the post."},
    {"key": "FANVUE_PPV_MIN_CENTS", "group": "Fanvue", "label": "Lowest paid-message price (cents)", "kind": "int",
     "min": 100, "max": 100000, "help": "300 = 3.00. Prices outside the range are refused."},
    {"key": "FANVUE_PPV_MAX_CENTS", "group": "Fanvue", "label": "Highest paid-message price (cents)", "kind": "int",
     "min": 100, "max": 100000, "help": "5000 = 50.00."},
    {"key": "FAN_DATA_RETENTION_DAYS", "group": "Fanvue", "label": "Keep fan chats for (days)", "kind": "int",
     "min": 1, "max": 3650, "help": "Older chats are deleted."},
    {"key": "FANVUE_REPORT_WEEKDAY", "group": "Fanvue", "label": "Weekly report day (0 = Monday ... 6 = Sunday)",
     "kind": "int", "min": 0, "max": 6, "help": ""},
    {"key": "FANVUE_REPORT_HOUR", "group": "Fanvue", "label": "Weekly report hour (0-23)", "kind": "int",
     "min": 0, "max": 23, "help": ""},
]
BY_KEY = {f["key"]: f for f in FIELDS}


def parse(field: dict, raw) -> object:
    """Form text (or a list for 'multi') -> the typed value. Raises ValueError with a message for the user."""
    kind, label = field["kind"], field["label"]
    if kind == "bool":
        return str(raw).lower() in ("1", "true", "on", "yes")
    if kind == "int":
        try:
            value = int(str(raw).strip())
        except ValueError:
            raise ValueError(f"{label}: enter a whole number.") from None
        if not field["min"] <= value <= field["max"]:
            raise ValueError(f"{label}: must be between {field['min']} and {field['max']}.")
        return value
    allowed = [c[0] for c in field["choices"]]
    if kind == "multi":
        picked = [raw] if isinstance(raw, str) else list(raw or [])
        if any(p not in allowed for p in picked):
            raise ValueError(f"{label}: unknown platform.")
        return ",".join(p for p in allowed if p in picked)  # fixed order; empty = none
    if raw not in allowed:
        raise ValueError(f"{label}: pick one of the listed options.")
    return raw


def decode(rows) -> dict:
    """(key, json_text) rows from app_settings -> {key: value} for known keys; a bad row is ignored."""
    out = {}
    for key, text in rows:
        if key in BY_KEY:
            try:
                out[key] = json.loads(text)
            except ValueError:
                pass
    return out
