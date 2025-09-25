# services/admin_ops.py
from __future__ import annotations
import os, io, tarfile, shutil, subprocess as sp, tempfile, re
from collections import deque
from pathlib import Path
from typing import Tuple, List, Optional
from urllib import request as urlreq

REPO_SLUG = "accunettech/firepi_zero"
RAW_VERSION_URL = f"https://raw.githubusercontent.com/{REPO_SLUG}/main/VERSION"

def app_dir(app) -> Path:
    return Path(app.root_path).parent  # your project root (contains app.py)

def log_file_path(app) -> Path:
    # Honor config override, else instance/logs/firepi.log
    p = Path(app.config.get("LOG_FILE_PATH", Path(app.instance_path) / "logs" / "firepi.log"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def backup_path(app) -> Path:
    p = Path(app.instance_path) / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p / "firepi-backup.tar.gz"

EXCLUDES = {".venv", ".git", "__pycache__", "instance/backups", "instance/logs"}

def _should_exclude(rel: str) -> bool:
    for x in EXCLUDES:
        if rel == x or rel.startswith(x + os.sep):
            return True
    return False

def make_backup(app) -> Tuple[bool, str]:
    root = app_dir(app)
    out = backup_path(app)
    try:
        with tarfile.open(out, "w:gz") as tf:
            for path in root.rglob("*"):
                rel = str(path.relative_to(root))
                if _should_exclude(rel):
                    continue
                tf.add(path, arcname=rel)
        return True, f"Backup written: {out}"
    except Exception as e:
        return False, f"Backup failed: {e}"

def rollback_from_backup(app) -> Tuple[bool, List[str]]:
    root = app_dir(app)
    b = backup_path(app)
    logs: List[str] = []
    if not b.exists():
        return False, ["No backup archive found."]
    try:
        logs.append(f"Restoring from {b} to {root}")
        with tarfile.open(b, "r:gz") as tf:
            # Remove everything except our excludes, then extract
            for p in list(root.iterdir()):
                rel = p.name
                if _should_exclude(rel):
                    continue
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
            tf.extractall(root)
        logs.append("Restore complete.")
        return True, logs
    except Exception as e:
        return False, logs + [f"Restore failed: {e}"]

def get_log_tail_text(app, lines: int = 50) -> str:
    p = log_file_path(app)
    if not p.exists():
        return "(log file not found)"
    dq: deque[str] = deque(maxlen=max(1, min(lines, 2000)))
    with p.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            dq.append(line.rstrip("\n"))
    return "\n".join(dq)

def get_current_version(app) -> str:
    # Prefer explicit config override; else read VERSION file; else "dev"
    v = str(app.config.get("APP_VERSION", "")).strip()
    if v:
        return v
    # VERSION file at repo root
    vf = app_dir(app) / "VERSION"
    if vf.exists():
        try:
            return vf.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return "dev"

def get_latest_version_online(timeout: float = 4.0) -> Tuple[Optional[str], Optional[str]]:
    try:
        with urlreq.urlopen(RAW_VERSION_URL, timeout=timeout) as resp:
            if resp.status == 200:
                txt = resp.read().decode("utf-8", errors="replace").strip()
                return txt, None
            return None, f"HTTP {resp.status}"
    except Exception as e:
        return None, str(e)

def _run(cmd: List[str], cwd: Path, logs: List[str]) -> int:
    logs.append("$ " + " ".join(cmd))
    try:
        proc = sp.run(cmd, cwd=str(cwd), text=True, stdout=sp.PIPE, stderr=sp.STDOUT)
        if proc.stdout:
            for ln in proc.stdout.splitlines():
                logs.append(ln)
        return proc.returncode
    except Exception as e:
        logs.append(f"ERROR: {e}")
        return 1

def update_from_github(app) -> Tuple[bool, List[str]]:
    root = app_dir(app)
    logs: List[str] = []

    ok, msg = make_backup(app)
    logs.append(msg)
    if not ok:
        return False, logs

    # If it's a git repo, hard reset to origin/main. Else do a shallow fetch.
    git = shutil.which("git") or "/usr/bin/git"
    if not Path(git).exists():
        logs.append("git not found.")
        return False, logs

    if (root / ".git").exists():
        logs.append("Detected git repo – resetting to origin/main")
        rc = _run([git, "fetch", "origin", "main"], root, logs)
        if rc != 0: return False, logs
        _run([git, "checkout", "-f", "main"], root, logs)  # allow non-zero
        rc = _run([git, "reset", "--hard", "origin/main"], root, logs)
        if rc != 0: return False, logs
        _run([git, "clean", "-fd"], root, logs)
    else:
        logs.append("Not a git repo – doing shallow clone into temp then overlay")
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            rc = _run([git, "clone", "--depth", "1", "--branch", "main",
                       f"https://github.com/{REPO_SLUG}.git", str(tdp / "repo")], root, logs)
            if rc != 0: return False, logs
            src = tdp / "repo"
            # Remove current files (except excludes), then copy in fresh tree
            for p in list(root.iterdir()):
                rel = p.name
                if _should_exclude(rel):
                    continue
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
            for p in src.rglob("*"):
                rel = p.relative_to(src)
                dest = root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if p.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    shutil.copy2(p, dest)
            logs.append("Copied new code from shallow clone.")

    # If requirements.txt exists, try to install into venv
    req = root / "requirements.txt"
    pip = root / ".venv" / "bin" / "pip"
    if req.exists() and pip.exists():
        logs.append("Installing/updating Python packages…")
        _run([str(pip), "install", "-r", str(req)], root, logs)
    else:
        logs.append("No venv or no requirements.txt – skipping pip install.")

    logs.append("Update complete. Reboot recommended.")
    return True, logs

def reboot_system() -> Tuple[bool, str]:
    # Requires passwordless sudo for /sbin/reboot (see note in routes)
    reboot_bin = shutil.which("reboot") or "/sbin/reboot"
    try:
        sp.Popen(["sudo", reboot_bin])
        return True, "Rebooting…"
    except Exception as e:
        return False, f"Failed to reboot: {e}"