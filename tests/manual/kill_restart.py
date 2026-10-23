"""Manual proof (not collected by pytest). Run from the repo root:  .venv\Scripts\python tests\manual\kill_restart.py

B6 'done when': kill the real worker process mid-publish, restart it, and show the post is neither
lost nor published twice. A local fake Instagram server stands in for the platform."""
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path.cwd()
PY = sys.executable
SCRATCH = Path(__file__).parent


class Platform:
    def __init__(self):
        self.creates = self.publishes = self.polls = 0
        self.published = set()      # containers that went public
        self.finished = set()       # containers whose media finished processing
        self.auto_finish = False    # after the restart every new container finishes processing at once
        self.lock = threading.Lock()


def make_server(state: Platform):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, code=200):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            with state.lock:
                if self.path.endswith("/media"):
                    state.creates += 1
                    return self._send({"id": f"C{state.creates}"})
                if self.path.endswith("/media_publish"):
                    state.publishes += 1
                    state.published.add(f"C{state.creates}")
                    return self._send({"id": f"M{state.publishes}"})
            self._send({"error": {"message": "no route", "code": 100}}, 404)

        def do_GET(self):
            with state.lock:
                if "content_publishing_limit" in self.path:
                    return self._send({"data": [{"quota_usage": 0, "config": {"quota_total": 100}}]})
                m = re.search(r"/(C\d+)\?", self.path)
                if m:
                    state.polls += 1
                    c = m.group(1)
                    status = "PUBLISHED" if c in state.published else "FINISHED" if (c in state.finished or state.auto_finish) else "IN_PROGRESS"
                    return self._send({"status_code": status})
                if re.search(r"/M\d+\?", self.path):
                    return self._send({"permalink": "https://www.instagram.com/reel/fake/", "timestamp": "2026-11-02T12:00:00+0000"})
            self._send({"error": {"message": "no route", "code": 100}}, 404)
    return ThreadingHTTPServer(("127.0.0.1", 0), H)


LAUNCHER = '''
import sys
from datetime import timedelta
from app.platforms import instagram
instagram.BASE = "http://127.0.0.1:%d/v26.0"
instagram.POLL_SECONDS = 1
instagram.POLL_MAX = 600
import app.services.publishing as pub
pub.STUCK_AFTER = timedelta(seconds=3)       # scratch: do not wait 15 minutes
pub.BACKOFF_MINUTES = (0, 0, 0)              # scratch: retry immediately
from app.cli import cli
cli(["worker"])
'''

SEED = '''
from datetime import timedelta
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import get_session_factory, utcnow
from app.models import ContentPost, PostingRule, PlatformAccount
from app.services import accounts
c = TestClient(create_app())
a = c.post("/api/v1/assets/upload", files={"file": ("reel.mp4", b"\\x00\\x00\\x00\\x18ftypmp42" + bytes(64), "video/mp4")}, data={"description": "d"}).json()
p = c.post("/api/v1/posts", json={"profile_id": 1, "platform": "instagram", "content_type": "reel", "campaign": "halloween",
          "caption": "Kill test", "hashtags": ["halloween"], "asset_id": a["id"]}).json()
c.post("/api/v1/posts/%d/approve" % p["id"])
db = get_session_factory()()
for r in db.query(PostingRule).all():
    r.enforce_window = False
post = db.get(ContentPost, p["id"]); post.status, post.planned_publish_at = "scheduled", utcnow() - timedelta(minutes=1)
db.add(PlatformAccount(model_profile_id=1, platform="instagram", external_user_id="uid1", username="summer",
       access_token_enc=accounts.encrypt("TOK"), token_expires_at=utcnow() + timedelta(days=40), scopes=[], status="connected"))
db.commit()
'''


def sql(db, q):
    last = None
    for _ in range(25):                      # WAL files of a freshly killed writer can be briefly locked on Windows
        try:
            con = sqlite3.connect(db, timeout=5)
            try:
                return con.execute(q).fetchall()
            finally:
                con.close()
        except sqlite3.OperationalError as e:
            last = e
            time.sleep(0.4)
    raise last


def scenario(name, went_public_before_crash):
    tmp = Path(tempfile.mkdtemp(prefix="killtest_"))
    dbfile = tmp / "app.db"
    state = Platform()
    srv = make_server(state)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    from cryptography.fernet import Fernet
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{dbfile}", MEDIA_UPLOAD_DIR=str(tmp / "media"), PUBLISHING_MODE="live",
               PUBLIC_BASE_URL="https://studio.example.com", TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
               MEDIA_URL_SECRET="s3cret", NOTIFY_PROVIDER="none", WORKER_INTERVAL_SECONDS="1", APP_TIMEZONE="UTC",
               MISTRAL_API_KEY="", ANTHROPIC_API_KEY="", DASHBOARD_USERNAME="", DASHBOARD_PASSWORD="", PYTHONPATH=str(ROOT))
    launcher = tmp / "launch.py"
    launcher.write_text(LAUNCHER % port)

    def sh(*args):
        r = subprocess.run([PY, *args], env=env, cwd=ROOT, capture_output=True, text=True)
        if r.returncode:
            print(r.stdout[-800:], r.stderr[-800:])
            raise SystemExit(1)
    sh("-m", "app.cli", "init-db")
    sh("-c", SEED)

    log1 = open(tmp / "w1.log", "w")
    w1 = subprocess.Popen([PY, str(launcher)], env=env, cwd=ROOT, stdout=log1, stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < 40:                                   # container created AND its id saved by on_ref
        if state.creates == 1 and sql(dbfile, "select platform_ref from publish_attempts")[0:1] == [("C1",)]:
            break
        time.sleep(0.2)
    else:
        w1.kill(); w1.wait(); log1.close()
        print((tmp / "w1.log").read_text()[-2500:])
        print(sql(dbfile, "select id,status,planned_publish_at,claimed_at,last_publish_error from content_posts"))
        raise SystemExit("worker never reached the platform")
    w1.kill()                                                      # hard kill mid-publish (TerminateProcess)
    w1.wait()
    before = sql(dbfile, "select status, claimed_at is not null, publish_attempt_count from content_posts")[0]
    print(f"[{name}] killed worker mid-publish -> post row: status={before[0]} claimed={bool(before[1])} attempts={before[2]}; "
          f"platform: creates={state.creates} publishes={state.publishes}")

    with state.lock:                                               # what the platform did while the worker was dead
        (state.published if went_public_before_crash else state.finished).add("C1")
        state.auto_finish = True

    w2 = subprocess.Popen([PY, str(launcher)], env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while time.time() - t0 < 60:
        if sql(dbfile, "select status from content_posts")[0][0] == "published":
            break
        time.sleep(0.5)
    time.sleep(3)                                                  # give a double publish a chance to show up
    w2.terminate()
    w2.wait()
    after = sql(dbfile, "select status, published_via, external_post_id, publish_attempt_count from content_posts")[0]
    attempts = sql(dbfile, "select attempt_no, trigger, success, error_code, platform_ref from publish_attempts order by id")
    print(f"[{name}] after restart -> post: {after}")
    print(f"[{name}] platform saw: creates={state.creates} publishes={state.publishes}")
    for a in attempts:
        print(f"[{name}]   attempt {a}")
    srv.shutdown()
    return state, after


if __name__ == "__main__":
    sa, pa = scenario("A: went public before the crash", True)
    assert pa[0] == "published" and sa.publishes == 0 and sa.creates == 1, "A: duplicated or lost"
    sb, pb = scenario("B: did NOT go public", False)
    assert pb[0] == "published" and sb.publishes == 1, "B: duplicated or lost"
    print("KILL/RESTART OK: no post lost, none published twice")
