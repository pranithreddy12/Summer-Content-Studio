r"""Manual tool (not collected by pytest). Run from the repo root:  .venv\Scripts\python tests\manual\build_delivery_zip.py

Builds the delivery zip, then verifies it: portable lock file, LF line endings in deploy/, none of the developer's
real secrets, and the full test suite run from an extracted copy.
"""
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

root = Path.cwd()
name = "summer-ai-content-phase2-v0.2.0"
out = root / "dist" / f"{name}.zip"
TOP_FILES = [".gitattributes", "README.md", "PLAN.md", "PLAN_PHASE2.md", ".env.example", ".gitignore", "alembic.ini",
             "pytest.ini", "requirements.txt", "requirements-dev.txt", "requirements.lock"]
DIRS = ["app", "migrations", "tests", "docs", "deploy"]
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".venv", ".git"}
SKIP_FILES = {"docs/PROGRESS.md"}  # internal build log
SKIP_SUFFIX = {".pyc", ".db", ".db-shm", ".db-wal", ".log"}
CRLF = bytes([13, 10])

files = [root / f for f in TOP_FILES]
for d in DIRS:
    for p in sorted((root / d).rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not (set(p.parts) & SKIP_PARTS) and rel not in SKIP_FILES and p.suffix not in SKIP_SUFFIX:
            files.append(p)

if out.exists():
    out.unlink()
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in files:
        z.write(p, f"{name}/{p.relative_to(root).as_posix()}")
    z.writestr(f"{name}/data/.gitkeep", "")
print("zip:", out.name, len(files) + 1, "files,", round(out.stat().st_size / 1024), "KB")

# 0) the lock file must install on another machine; deploy/ files must be LF (they run on Linux)
with zipfile.ZipFile(out) as z0:
    lock = z0.read(f"{name}/requirements.lock").decode()
    if "@ file:" in lock or "C:/" in lock or "C:" + chr(92) in lock:
        sys.exit("requirements.lock contains a machine-local path (replace `name @ file:///...` by `name==version`)")
    crlf = [n for n in z0.namelist() if "/deploy/" in n and CRLF in z0.read(n)]
    if crlf:
        sys.exit(f"CRLF line endings in {crlf}")
    if not any("/deploy/server-setup.sh" in n for n in z0.namelist()):
        sys.exit("deploy/ is missing from the zip")
print("lock file portable, deploy/ present and LF")

# 1) the developer's real secrets must not be in the zip
secrets_found = []
env_file = root / ".env"
real = []
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        k, _, v = line.partition("=")
        if re.search(r"KEY|TOKEN|SECRET|PASSWORD", k) and len(v.strip()) >= 8 and "REPLACE" not in v:
            real.append((k, v.strip()))
with zipfile.ZipFile(out) as z:
    blob = b"".join(z.read(n) for n in z.namelist())
    names = z.namelist()
for k, v in real:
    if v.encode() in blob:
        secrets_found.append(k)
print("real .env secrets checked:", len(real), "-> found in zip:", secrets_found or "none")
bad_names = [n for n in names if re.search(r"(^|/)\.env$|\.db$|rclone\.conf|CLAUDE\.md|PROGRESS\.md", n)]
print("forbidden file names in zip:", bad_names or "none")
pat = re.compile(rb"sk-ant-api[0-9A-Za-z_-]{10,}|\b[0-9]{8,10}:[A-Za-z0-9_-]{34,}\b")
print("key-shaped strings:", [m.group(0)[:12] for m in pat.finditer(blob)] or "none")
if secrets_found or bad_names:
    sys.exit("SECRET OR FORBIDDEN FILE IN ZIP")

# 2) extract to a temp dir and run the suite there
tmp = Path(tempfile.mkdtemp(prefix="deliverycheck_"))
with zipfile.ZipFile(out) as z:
    z.extractall(tmp)
proj = tmp / name
env = {k: v for k, v in os.environ.items()
       if not re.search(r"KEY|TOKEN|SECRET|PASSWORD|DATABASE_URL|MEDIA_|NOTIFY|PUBLISHING", k)}
r = subprocess.run([sys.executable, "-m", "pytest", "-o", "addopts=", "-q", "-p", "no:cacheprovider"],
                   cwd=proj, env=env, capture_output=True, text=True)
tail = [ln for ln in r.stdout.strip().splitlines() if "passed" in ln or "failed" in ln or "error" in ln.lower()][-3:]
print("suite in extracted copy:", tail or r.stdout[-300:])
sys.exit(r.returncode)
