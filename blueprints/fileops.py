from __future__ import annotations
import os
import shlex
import subprocess
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    jsonify,
    request,
    session,
    send_from_directory,
    render_template,
)
from werkzeug.utils import secure_filename

bp = Blueprint("fileops", __name__, url_prefix="/fileops")

# --- helpers -----------------------------------------------------------------

def uploads_dir() -> Path:
    """
    Ensure and return the instance/uploads directory.
    """
    inst = Path(current_app.instance_path)
    up = inst / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    return up

def session_cwd() -> Path:
    """
    Get or initialize the per-session working directory, confined under instance/uploads.
    """
    base = uploads_dir().resolve()
    # initialize if missing
    cwd = session.get("cwd")
    if not cwd:
        session["cwd"] = str(base)
        return base

    # sanitize: keep it inside base
    p = Path(cwd).resolve()
    if not str(p).startswith(str(base)):
        p = base
        session["cwd"] = str(p)
    return p

def set_session_cwd(new_dir: Path) -> None:
    base = uploads_dir().resolve()
    p = new_dir.resolve()
    if not str(p).startswith(str(base)):
        # ignore attempts to escape
        return
    session["cwd"] = str(p)

def safe_path_in_uploads(filename: str) -> Path:
    """
    Only allow accessing files inside uploads using a secured filename.
    """
    fn = secure_filename(filename)
    return uploads_dir() / fn

def prompt() -> str:
    return f"{session_cwd()} >"

# --- routes ------------------------------------------------------------------

@bp.route("/ui")
def ui():
    # renders templates/fileops.html (see below)
    return render_template("fileops.html")

@bp.route("/files", methods=["GET"])
def list_files():
    up = uploads_dir()
    files = []
    for entry in sorted(up.iterdir()):
        if entry.is_file():
            # size in bytes
            files.append({"name": entry.name, "size": entry.stat().st_size})
    return jsonify({"files": files})

@bp.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file part"}), 400

    f = request.files["file"]
    if f.filename == "":
        return jsonify({"ok": False, "error": "No selected file"}), 400

    target = safe_path_in_uploads(f.filename)
    f.save(str(target))
    return jsonify({"ok": True})

@bp.route("/delete", methods=["POST"])
def delete_file():
    data = request.get_json(silent=True) or {}
    name = data.get("name", "")
    if not name:
        return jsonify({"ok": False, "error": "Missing file name"}), 400

    target = safe_path_in_uploads(name)
    try:
        target.unlink(missing_ok=False)
        return jsonify({"ok": True})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Not found"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/download/<path:filename>", methods=["GET"])
def download_file(filename):
    # download strictly from uploads/
    return send_from_directory(
        directory=str(uploads_dir()),
        path=secure_filename(filename),
        as_attachment=True,
        download_name=secure_filename(filename),
    )

@bp.route("/state", methods=["GET"])
def state():
    # Return current session working dir and prompt
    p = prompt()
    return jsonify({"cwd": str(session_cwd()), "prompt": p})

@bp.route("/run", methods=["POST"])
def run_cmd():
    """
    Execute a single command within the per-session working directory.
    - Supports built-in 'cd' (e.g., 'cd ..', 'cd subdir')
    - Otherwise runs via /bin/bash -lc "<command>" so pipes/globs work
    - Captures combined stdout+stderr and return code
    """
    data = request.get_json(silent=True) or {}
    cmd_line = (data.get("cmd") or "").strip()

    if not cmd_line:
        return jsonify({"ok": False, "error": "Empty command"}), 400

    base = uploads_dir().resolve()
    cwd = session_cwd()

    # built-in: cd
    if cmd_line.startswith("cd"):
        # allow: "cd", "cd ..", "cd subdir/.."
        parts = shlex.split(cmd_line)
        target = base if len(parts) == 1 else (cwd / parts[1])

        try:
            new_dir = target if target.is_dir() else (cwd if target == base else None)
            if new_dir is None:
                return jsonify({
                    "ok": True,
                    "output": f"cd: no such directory: {parts[1]}",
                    "rc": 1,
                    "prompt": prompt(),
                    "cwd": str(cwd),
                })
            # normalize and clamp inside uploads
            new_dir = new_dir.resolve()
            if not str(new_dir).startswith(str(base)):
                # clamp
                new_dir = base
            set_session_cwd(new_dir)
            return jsonify({"ok": True, "output": "", "rc": 0, "prompt": prompt(), "cwd": str(new_dir)})
        except Exception as e:
            return jsonify({"ok": True, "output": f"cd: {e}", "rc": 1, "prompt": prompt(), "cwd": str(cwd)})

    # built-in: pwd
    if cmd_line.strip() == "pwd":
        cwd = session_cwd()
        return jsonify({"ok": True, "output": str(cwd), "rc": 0, "prompt": prompt(), "cwd": str(cwd)})

    # Everything else: run via bash (for pipes/globs). Security caution: this executes on the host.
    try:
        proc = subprocess.run(
            ["/bin/bash", "-lc", cmd_line],
            cwd=str(session_cwd()),
            capture_output=True,
            text=True,
            timeout=300,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return jsonify({"ok": True, "output": out, "rc": proc.returncode, "prompt": prompt(), "cwd": str(session_cwd())})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": True, "output": "Command timed out.", "rc": 124, "prompt": prompt(), "cwd": str(session_cwd())})
    except Exception as e:
        return jsonify({"ok": True, "output": f"Error: {e}", "rc": 1, "prompt": prompt(), "cwd": str(session_cwd())})
