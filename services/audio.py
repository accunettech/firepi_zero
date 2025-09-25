from __future__ import annotations
import os
import re
import shutil
import subprocess as sp
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from werkzeug.utils import secure_filename
from flask import current_app, send_from_directory, url_for

ALLOWED_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".oga"}

def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def get_audio_dir(is_stock_audio: bool = False) -> str:
    # Try Flask app context first
    try:
        app = current_app._get_current_object()  # raises outside of app context
        base = app.config.get("AUDIO_DIR")
        if not base:
            base = os.path.join(app.root_path, "audio")
            if is_stock_audio:
                base = os.path.join(base, "stock")
    except Exception as e:
        base = str((_project_root() / "audio").resolve())
        if is_stock_audio: base = base + '/stock'

    return base


def resolve_audio_path(name: Optional[str], is_stock_audio: bool = False) -> Optional[Path]:
    if not name:
        return None

    p = Path(name)
    if p.is_absolute():
        return p if (p.is_file() and is_allowed(p.name)) else None

    base = Path(get_audio_dir(is_stock_audio=is_stock_audio))

    candidate = base / p.name
    return candidate if (candidate.is_file() and is_allowed(candidate.name)) else None


def is_allowed(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    return ext in ALLOWED_EXTS


def list_audio_files() -> list[dict]:
    root = Path(get_audio_dir())
    out: list[dict] = []
    for p in sorted(root.iterdir()):
        if not p.is_file():
            continue
        if not is_allowed(p.name):
            continue
        stat = p.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        out.append({
            "filename": p.name,
            "size": stat.st_size,
            "mtime": mtime,
            "url": url_for("config_ui.audio_file", filename=p.name),
        })
    return out


def save_upload(file_storage) -> dict:
    if not file_storage or not getattr(file_storage, "filename", ""):
        raise ValueError("No file provided")

    name = secure_filename(file_storage.filename)
    if not name:
        raise ValueError("Invalid filename")

    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise ValueError(f"Unsupported file type: {ext}")

    root = Path(get_audio_dir())
    dest = root / name

    # Ensure unique name if collision
    if dest.exists():
        stem = dest.stem
        n = 1
        while True:
            candidate = root / f"{stem}-{n}{ext}"
            if not candidate.exists():
                dest = candidate
                break
            n += 1

    file_storage.save(str(dest))
    return {"filename": dest.name, "url": url_for("config_ui.audio_file", filename=dest.name)}


def ensure_exists(name: str) -> bool:
    if not name:
        return False
    p = Path(get_audio_dir()) / name
    return p.is_file()


def serve_file(filename: str):
    # Safe directory-bound send; filename must be the exact basename we stored
    return send_from_directory(get_audio_dir(), filename, as_attachment=False)


def _amixer_path() -> Optional[str]:
    return shutil.which("amixer")


def _candidate_controls() -> list[str]:
    names: list[str] = []

    # Try Flask config
    try:
        app = current_app._get_current_object()
        ctl = app.config.get("ALSA_CONTROL")
        if ctl:
            names.append(str(ctl))
    except Exception:
        pass

    # Env override if present
    env_ctl = os.environ.get("ALSA_CONTROL")
    if env_ctl:
        names.append(env_ctl)

    names += ["FirePiVolume", "Master", "PCM", "Digital", "Speaker", "Headphone"]

    # De-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _list_all_controls() -> list[str]:
    amixer = _amixer_path()
    if not amixer:
        return []
    try:
        txt = sp.check_output([amixer, "-M", "scontrols"], text=True, timeout=3)
    except Exception:
        return []
    # Lines look like: "Simple mixer control 'Speaker',0"
    names = re.findall(r"'([^']+)'", txt)
    # De-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def get_system_volume() -> Optional[int]:
    amixer = _amixer_path()
    if not amixer:
        return None

    for ctl in [*_candidate_controls(), *_list_all_controls()]:
        try:
            out = sp.check_output([amixer, "-M", "sget", ctl], stderr=sp.DEVNULL, text=True, timeout=3)
        except Exception:
            continue

        m = re.findall(r"\[(\d{1,3})%\]", out)
        if not m:
            m = re.findall(r"(\d{1,3})%", out)

        if m:
            try:
                vals = [max(0, min(100, int(x))) for x in m]
                return max(vals)
            except ValueError:
                pass

    return None



def set_system_volume(percent: int) -> None:
    amixer = _amixer_path()
    if not amixer:
        raise RuntimeError("amixer not found (install 'alsa-utils').")

    val = max(0, min(100, int(percent)))
    last_err: Optional[Exception] = None
    tried: list[str] = []

    for ctl in [*_candidate_controls(), *_list_all_controls()]:
        tried.append(ctl)
        try:
            sp.run([amixer, "-M", "sset", ctl, f"{val}%", "unmute"],
                   check=True, stdout=sp.DEVNULL, stderr=sp.DEVNULL, timeout=3)
            return
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(
        f"Failed to set ALSA volume on any control ({', '.join(tried) or 'none'}); "
        f"your device may not expose a mixer. Last error: {last_err}"
    )