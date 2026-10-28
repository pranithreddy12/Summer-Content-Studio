r"""Manual proof (not collected by pytest). Run from the repo root:  .venv\Scripts\python tests\manual\dryrun_proof.py

Scratch proof for B5 'done when': dry_run worker on a scratch DB -> 'would publish' attempts, zero HTTP."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="b5scratch_"))
env = dict(os.environ, DATABASE_URL=f"sqlite:///{tmp / 'app.db'}", MEDIA_UPLOAD_DIR=str(tmp / "media"),
           PUBLISHING_MODE="dry_run", NOTIFY_PROVIDER="none", MISTRAL_API_KEY="", ANTHROPIC_API_KEY="",
           DASHBOARD_USERNAME="", DASHBOARD_PASSWORD="", APP_TIMEZONE="UTC")
py = sys.executable
root = Path.cwd()


def run(*args, code=None):
    r = subprocess.run([py, *args] if code is None else [py, "-c", code], env=env, cwd=root, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


print("init-db:", run("-m", "app.cli", "init-db")[0])

seed = '''
import itertools
from datetime import timedelta
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import get_session_factory, utcnow
from app.models import ContentPost, PostingRule
app = create_app()
c = TestClient(app)
for i in range(3):
    a = c.post("/api/v1/assets/upload", files={"file": (f"c{i}.mp4", b"\\x00\\x00\\x00\\x18ftypmp42" + bytes(64) + bytes([i]), "video/mp4")}, data={"description": "d"}).json()
    p = c.post("/api/v1/posts", json={"profile_id": 1, "platform": "instagram", "content_type": "reel", "campaign": "halloween",
              "caption": f"Scratch reel {i}", "hashtags": ["halloween"], "asset_id": a["id"]}).json()
    c.post(f"/api/v1/posts/{p['id']}/approve")
db = get_session_factory()()
if not db.query(PostingRule).count():   # no rules seeded: the hard 08:00-21:00 default would make this run depend on the wall clock
    db.add(PostingRule(model_profile_id=1, platform="instagram", posts_per_week=7, min_per_day=1, max_per_day=1, window_start="08:00",
                       window_end="21:00", min_gap_minutes=0, timezone="UTC", is_active=True))
    db.flush()
for r in db.query(PostingRule).all():
    r.enforce_window = False          # scratch run must not depend on the wall clock
for p in db.query(ContentPost).all():
    p.status, p.planned_publish_at = "scheduled", utcnow() - timedelta(minutes=2)
db.commit()
print("seeded", db.query(ContentPost).count())
'''
print("seed:", run(code=seed))

# any real HTTP would need DNS: make every httpx send explode so a leak is loud
guard = '''
import httpx, sys
def boom(self, request, **kw):
    print("HTTP LEAK:", request.url); sys.exit(99)
httpx.Client.send = boom
from app.cli import cli
cli(["worker", "--once"])
'''
code, out = run(code=guard)
print("worker exit:", code)
print(out.splitlines()[-1] if out else "")

check = '''
from app.db import get_session_factory
from app.models import PublishAttempt, ContentPost
db = get_session_factory()()
rows = db.query(PublishAttempt).all()
print("attempts:", len(rows), {r.trigger for r in rows}, {r.success for r in rows})
print("statuses:", {p.status for p in db.query(ContentPost).all()}, "counts:", {p.publish_attempt_count for p in db.query(ContentPost).all()})
print(rows[0].error_message)
'''
print(run(code=check)[1])
print("second loop:", run(code=guard)[1].splitlines()[-1])
print(run(code=check)[1].splitlines()[0])
