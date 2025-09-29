from __future__ import annotations
import os
import tarfile
import tempfile
import shutil
import subprocess as sp
from pathlib import Path
from typing import Optional, Tuple

# Show this in the UI; keep it human-friendly.
REPO_SLUG = "accunettech/firepi_zero"
RAW_VERSION_URL = f"https://raw.githubusercontent.com/{REPO_SLUG}/main/VERSION"
TARBALL_URL = f"https://codeload.github.com/{REPO_SLUG}/tar.gz/refs/heads/main"

def _safe_run(cmd: list[str], *, cwd: Optional[str] = None, timeout: int = 60) -> Tuple[int, str, str]:
    try:
        p = sp.Popen(
            cmd, cwd=cwd, stdout=sp.PIPE, stderr=sp.PIPE, text=True
        )
        out, err = p.communicate(timeout=timeout)
        return p.returncode, out or "", err or ""
    except Exception as e:
        return 127, "", f"{type(e).__name__}: {e}"

def _app_root(app) -> Path:
    return Path(app.root_path).resolve()

def _log_dir(app) -> Path:
    d = Path(app.config.get("LOG_DIR") or (_app_root(app) / "logs"))
    d.mkdir(parents=True, exist_ok=True)
    return d

def _current_log_path(app) -> Optional[Path]:
    d = _log_dir(app)
    main = d / "app.log"
    if main.exists() and main.is_file():
        return main
    candidates = sorted(
        (p for p in d.glob("app.log*") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None

def _read_tail_bytes(p: Path, max_bytes: int = 32_000) -> str:
    size = p.stat().st_size
    with p.open("rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
        data = f.read()
    return data.decode("utf-8", errors="replace")

def _detect_venv_pip(app) -> Optional[Path]:
    for rel in (".venv/bin/pip", "venv/bin/pip"):
        p = _app_root(app) / rel
        if p.exists() and os.access(p, os.X_OK):
            return p
    return None

def get_log_tail_text(app, lines: int = 50) -> str:
    lf = _current_log_path(app)
    if not lf:
        return "No log file found yet."
    text = _read_tail_bytes(lf, max_bytes=64_000)
    last_lines = text.rstrip("\n").splitlines()[-max(1, int(lines)) :]
    return "\n".join(last_lines) + ("\n" if last_lines else "")

def get_full_log_file(app) -> Optional[Path]:
    return _current_log_path(app)

def get_installed_version(app) -> str:
    root = _app_root(app)
    vf = root / "VERSION"
    if vf.exists():
        return vf.read_text(encoding="utf-8").strip()

    git_dir = root / ".git"
    if git_dir.exists() and git_dir.is_dir():
        rc, out, _ = _safe_run(["git", "rev-parse", "--show-toplevel"], cwd=str(root))
        top = Path(out.strip()) if rc == 0 and out.strip() else root
        v2 = top / "VERSION"
        if v2.exists():
            return v2.read_text(encoding="utf-8").strip()

    return "dev"

def get_latest_github_version(timeout: int = 6) -> tuple[Optional[str], Optional[str]]:
    """
    Fetch VERSION from GitHub main branch (raw file).
    Returns (version or None, error or None).
    """
    import urllib.request
    ver: Optional[str] = None
    err: Optional[str] = None
    try:
        with urllib.request.urlopen(RAW_VERSION_URL, timeout=timeout) as r:
            if r.status == 200:
                ver = r.read().decode("utf-8", errors="replace").strip()
            else:
                err = f"HTTP {r.status} fetching VERSION"
    except Exception as e:
        err = str(e)
    return ver, err

def _tar_exclude(name: str) -> bool:
    base = name.strip("/")
    exclude_names = {
        ".git", ".venv", "venv", "logs",
        "__pycache__", ".mypy_cache", ".pytest_cache", ".DS_Store",
    }
    parts = set(base.split("/"))
    if parts & exclude_names:
        return True
    if base.endswith((".pyc", ".pyo", "~")):
        return True
    return False

def _safe_tar_add(tar: tarfile.TarFile, root: Path, rel: Path):
    arcname = str(rel.as_posix())
    if _tar_exclude(arcname):
        return
    tar.add(str(root / rel), arcname=arcname, recursive=True)

def _safe_extract_all(tar: tarfile.TarFile, dest: Path) -> None:
    dest = dest.resolve()
    for member in tar.getmembers():
        member_path = (dest / member.name).resolve()
        if not str(member_path).startswith(str(dest)):
            raise RuntimeError("Blocked path traversal in tar extract")
    tar.extractall(path=str(dest))

def backup_exists(app) -> bool:
    p = Path(app.instance_path) / "firepi_backup.tar.gz"
    try:
        return p.is_file() and p.stat().st_size > 0
    except Exception:
        return False

def backup_app(app) -> Path:
    inst = Path(app.instance_path)
    inst.mkdir(parents=True, exist_ok=True)
    backup_path = inst / "firepi_backup.tar.gz"

    root = _app_root(app)
    with tarfile.open(backup_path, "w:gz") as tar:
        for p in root.iterdir():
            rel = p.relative_to(root)
            _safe_tar_add(tar, root, rel)

    return backup_path

def rollback_from_backup(app) -> dict:
    root = _app_root(app)
    backup_path = Path(app.instance_path) / "firepi_backup.tar.gz"
    if not backup_path.exists():
        return {"status": "error", "error": "No backup found"}

    try:
        with tarfile.open(backup_path, "r:gz") as tar:
            _safe_extract_all(tar, root)
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

def _git_update(app) -> Tuple[bool, str]:
    root = _app_root(app)
    if not (root / ".git").exists():
        return False, "Not a git repo"

    steps = [
        (["git", "remote", "-v"], 15),
        (["git", "fetch", "--all", "--prune"], 120),
        (["git", "checkout", "-f", "main"], 30),
        (["git", "reset", "--hard", "origin/main"], 60),
    ]

    logs = []
    ok = True
    for cmd, to in steps:
        rc, out, err = _safe_run(cmd, cwd=str(root), timeout=to)
        logs.append(f"$ {' '.join(cmd)}\n{out}{err}")
        if rc != 0:
            ok = False
            break

    return ok, "\n".join(logs).strip()

def _tarball_update(app) -> Tuple[bool, str]:
    import urllib.request

    root = _app_root(app)

    with tempfile.TemporaryDirectory() as tmpd:
        tar_path = Path(tmpd) / "repo.tar.gz"
        try:
            with urllib.request.urlopen(TARBALL_URL, timeout=30) as r, open(tar_path, "wb") as f:
                shutil.copyfileobj(r, f)
        except Exception as e:
            return False, f"Download failed: {e}"

        with tarfile.open(tar_path, "r:gz") as tar:
            _safe_extract_all(tar, Path(tmpd))

        top_dirs = [p for p in Path(tmpd).iterdir() if p.is_dir()]
        if not top_dirs:
            return False, "Unexpected tarball layout"
        src = top_dirs[0]

        for item in src.iterdir():
            rel = item.name
            if _tar_exclude(rel):
                continue

            dest = root / rel
            if item.is_dir():
                if dest.exists():
                    for src_dirpath, _, files in os.walk(item):
                        rel_dir = Path(src_dirpath).relative_to(item)
                        dst_dir = dest / rel_dir
                        dst_dir.mkdir(parents=True, exist_ok=True)
                        for fn in files:
                            s = Path(src_dirpath) / fn
                            d = dst_dir / fn
                            shutil.copy2(s, d)
                else:
                    shutil.copytree(item, dest)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest)

    return True, "Updated from tarball"

def _install_requirements(app) -> Tuple[bool, str]:
    req = _app_root(app) / "requirements.txt"
    if not req.exists():
        return True, "No requirements.txt"

    pip = _detect_venv_pip(app)
    if not pip:
        return False, "Could not find venv pip (.venv/bin/pip)"

    rc, out, err = _safe_run([str(pip), "install", "-r", str(req)], cwd=str(_app_root(app)), timeout=600)
    ok = rc == 0
    return ok, (out + err).strip()

def update_firepi(app, *, make_backup: bool = True) -> dict:
    results = {}

    if make_backup:
        try:
            backup_path = backup_app(app)
            results["backup"] = str(backup_path)
        except Exception as e:
            return {"status": "error", "step": "backup", "error": str(e)}

    if (_app_root(app) / ".git").exists():
        ok, log = _git_update(app)
        results["update_method"] = "git"
        results["update_log"] = log
        if not ok:
            return {"status": "error", "step": "update", "error": "git update failed", "log": log}
    else:
        ok, log = _tarball_update(app)
        results["update_method"] = "tarball"
        results["update_log"] = log
        if not ok:
            return {"status": "error", "step": "update", "error": "tarball update failed", "log": log}

    ok, pip_log = _install_requirements(app)
    results["pip_log"] = pip_log
    if not ok:
        results["pip_warning"] = "Dependency install reported an error"

    return {"status": "ok", **results}

def reboot_system() -> dict:
    last_err = ""
    for cmd in (
        ["sudo", "-n", "systemctl", "reboot"],
        ["sudo", "-n", "/sbin/reboot"],
        ["sudo", "-n", "/usr/sbin/reboot"],
    ):
        rc, out, err = _safe_run(cmd, timeout=5)
        if rc == 0:
            return {"status": "ok"}
        last_err = (err or out or "").strip()
    return {"status": "error", "error": last_err or "reboot failed"}

def get_current_log_for_download(app):
    return _current_log_path(app)

def get_latest_support_bundle(app) -> Optional[Path]:
    sup = Path(app.instance_path) / "support"
    sup.mkdir(parents=True, exist_ok=True)
    candidates = sorted(sup.glob("support_*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None

def save_snapshot_file(app, attempts: int = 5, sleep_ms: int = 300) -> Optional[Path]:
    """
    Try a few times to fetch a fresh snapshot from panel_monitor.
    If no fresh bytes are available, fall back to any existing snapshot on disk.
    Saves to instance/support/snapshot.jpg and returns that path, or None.
    """
    from time import sleep
    outdir = Path(app.instance_path) / "support"
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / "snapshot.jpg"

    pm = app.extensions.get("panel_monitor")
    if not pm or not hasattr(pm, "get_snapshot_jpeg"):
        app.logger.warning("save_snapshot_file: panel_monitor not available")
        # Fallback to existing snapshot if present
        if out.exists() and out.stat().st_size > 0:
            app.logger.warning("save_snapshot_file: using existing snapshot %s", out)
            return out
        return None

    jpg = None
    for i in range(max(1, int(attempts))):
        try:
            b = pm.get_snapshot_jpeg()
        except Exception as e:
            app.logger.warning("save_snapshot_file: attempt %d error: %s", i + 1, e)
            b = None
        if b:
            jpg = b
            break
        sleep(max(1, int(sleep_ms)) / 1000.0)

    if jpg:
        out.write_bytes(jpg)
        app.logger.info("save_snapshot_file: wrote %d bytes to %s", len(jpg), out)
        return out

    # Fallback to previously saved snapshot
    if out.exists() and out.stat().st_size > 0:
        app.logger.warning("save_snapshot_file: no fresh snapshot; using existing file %s", out)
        return out

    app.logger.warning("save_snapshot_file: no snapshot bytes available")
    return None

def create_support_bundle(app, include_snapshot: bool = True) -> tuple[bool, Optional[Path], str]:
    """
    Tar.gz bundle with logs, VERSION, panel_rois.yaml, requirements.txt, and optional snapshot.
    """
    try:
        sup = Path(app.instance_path) / "support"
        sup.mkdir(parents=True, exist_ok=True)

        from datetime import datetime
        name = f"support_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"
        bundle = sup / name

        # Attempt fresh snapshot (with retry/fallback inside)
        snap_path = save_snapshot_file(app) if include_snapshot else None

        root = _app_root(app)
        logs_dir = _log_dir(app)
        rois = Path(app.instance_path) / "panel_rois.yaml"
        version = root / "VERSION"
        reqs = root / "requirements.txt"

        contents = []
        with tarfile.open(bundle, "w:gz") as tar:
            if logs_dir.exists():
                tar.add(str(logs_dir), arcname="logs")
                contents.append("logs/*")
            if version.exists():
                tar.add(str(version), arcname="VERSION")
                contents.append("VERSION")
            if rois.exists():
                tar.add(str(rois), arcname="panel_rois.yaml")
                contents.append("panel_rois.yaml")
            if reqs.exists():
                tar.add(str(reqs), arcname="requirements.txt")
                contents.append("requirements.txt")
            if snap_path and snap_path.exists() and snap_path.stat().st_size > 0:
                tar.add(str(snap_path), arcname="snapshot.jpg")
                contents.append("snapshot.jpg")

        app.logger.info("[bundle] created %s with: %s", bundle, ", ".join(contents) or "(empty)")
        return True, bundle, "Bundle created"
    except Exception as e:
        app.logger.exception("[bundle] creation failed")
        return False, None, str(e)

def _upload_path_to_remote(app, path: Path, kind: str = "file") -> tuple[bool, str]:
    """
    Post multipart 'file' to FIREPI_UPLOAD_URL with optional bearer token.
    Logs URL, filename, size, and remote response for easy debugging.
    """
    url = os.getenv("FIREPI_UPLOAD_URL", "").strip()
    if not url:
        app.logger.error("[upload] FIREPI_UPLOAD_URL not set; cannot upload %s", kind)
        return False, "Upload URL not configured (set FIREPI_UPLOAD_URL)"

    token = os.getenv("FIREPI_UPLOAD_TOKEN", "").strip()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    if not path or not path.exists():
        app.logger.error("[upload] path does not exist for %s: %s", kind, path)
        return False, "Local file missing"

    size = -1
    try:
        size = path.stat().st_size
    except Exception:
        pass

    app.logger.info("[upload] sending %s (%s, %d bytes) to %s", kind, path.name, size, url)

    try:
        import requests
    except Exception:
        app.logger.error("[upload] 'requests' package not available")
        return False, "The 'requests' package is required for remote upload"

    try:
        with open(path, "rb") as fh:
            files = {"file": (path.name, fh, "application/octet-stream")}
            data = {"kind": kind}
            r = requests.post(url, headers=headers, files=files, data=data, timeout=60)

        snippet = (r.text or "")[:200]
        app.logger.info("[upload] remote responded %s: %s", r.status_code, snippet)

        if 200 <= r.status_code < 300:
            return True, f"Remote {r.status_code}: {snippet}"
        return False, f"Remote {r.status_code}: {snippet}"
    except Exception as e:
        app.logger.exception("[upload] error posting %s", kind)
        return False, f"Upload error: {e}"


def upload_logs_to_remote(app) -> tuple[bool, str]:
    sup = Path(app.instance_path) / "support"
    sup.mkdir(parents=True, exist_ok=True)

    from datetime import datetime
    name = f"logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"
    tpath = sup / name

    try:
        logs_dir = _log_dir(app)
        if not logs_dir.exists():
            return False, "No logs to upload"
        with tarfile.open(tpath, "w:gz") as tar:
            tar.add(str(logs_dir), arcname="logs")
    except Exception as e:
        return False, f"Pack logs failed: {e}"

    return _upload_path_to_remote(app, tpath, "logs")

def upload_snapshot_to_remote(app) -> tuple[bool, str]:
    p = save_snapshot_file(app)
    if not p:
        return False, "No snapshot available"
    return _upload_path_to_remote(app, p, "snapshot")

def upload_bundle_to_remote(app, *, use_latest: bool = True, include_snapshot: bool = True) -> tuple[bool, str]:
    """
    Upload an existing most-recent support_*.tar.gz if available (use_latest=True),
    otherwise create a new bundle with include_snapshot and upload that.
    """
    bundle_path: Optional[Path] = None
    if use_latest:
        bundle_path = get_latest_support_bundle(app)

    created_now = False
    if not bundle_path or not bundle_path.exists():
        ok, bundle_path, msg = create_support_bundle(app, include_snapshot=include_snapshot)
        if not ok or not bundle_path:
            return False, f"Create bundle failed: {msg}"
        created_now = True

    ok, msg = _upload_path_to_remote(app, bundle_path, "bundle")
    if ok:
        suffix = " (newly created)" if created_now else " (reused latest)"
        return True, f"{msg}{suffix}: {bundle_path.name}"
    return False, msg


def upload_snapshot(app, url: str) -> tuple[bool, str]:
    """
    Upload snapshot to a specific URL (overrides FIREPI_UPLOAD_URL).
    Accepts optional FIREPI_UPLOAD_TOKEN for Bearer auth.
    """
    p = save_snapshot_file(app)
    if not p:
        return False, "No snapshot available"

    token = os.getenv("FIREPI_UPLOAD_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    try:
        import requests
    except Exception:
        return False, "The 'requests' package is required for remote upload"

    files = {"file": (p.name, open(p, "rb"), "image/jpeg")}
    try:
        r = requests.post(url, headers=headers, files=files, timeout=45)
        if 200 <= r.status_code < 300:
            return True, "Uploaded"
        return False, f"Upload failed: {r.status_code} {r.text[:200]}"
    except Exception as e:
        return False, f"Upload error: {e}"