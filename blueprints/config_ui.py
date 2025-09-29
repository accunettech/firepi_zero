from flask import Blueprint, current_app, render_template, jsonify, request, send_file, url_for, send_from_directory
import threading
from pathlib import Path
from db import db, Recipient, AlertHistory, alert_history_as_dict, get_or_create_settings
from services.audio import (
    list_audio_files,
    save_upload,
    ensure_exists,
    serve_file,
    get_system_volume,
    set_system_volume,
    delete_audio,
)
from services import admin_ops

bp = Blueprint("config_ui", __name__)

# ---------- UI ----------
@bp.route("/")
def index():
    return render_template("panel.html")

@bp.route("/panel")
def panel_page():
    return render_template("panel.html")

@bp.route("/config")
def config_page():
    return render_template("config.html")

# ---------- Recipients API ----------
@bp.get("/api/recipients")
def list_recipients():
    recs = Recipient.query.order_by(Recipient.created_at.desc()).all()
    return jsonify([{
        "id": r.id,
        "name": r.name,
        "phone": r.phone,
        "email": r.email,
        "receive_sms": r.receive_sms,
        "created_at": r.created_at.isoformat(),
    } for r in recs])

@bp.post("/api/recipients")
def create_recipient():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Missing required field: name"}), 400

    r = Recipient(
        name=name,
        phone=(data.get("phone") or "").strip() or None,
        email=(data.get("email") or "").strip() or None,
        receive_sms=bool(data.get("receive_sms", False)),
    )
    db.session.add(r)
    db.session.commit()
    return jsonify({"id": r.id}), 201

@bp.put("/api/recipients/<int:rid>")
def update_recipient(rid: int):
    r = Recipient.query.get_or_404(rid)
    data = request.get_json(force=True) or {}

    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Name cannot be empty"}), 400
        r.name = name
    if "phone" in data:
        r.phone = (data.get("phone") or "").strip() or None
    if "email" in data:
        r.email = (data.get("email") or "").strip() or None
    if "receive_sms" in data:
        r.receive_sms = bool(data.get("receive_sms"))

    db.session.commit()
    return jsonify({"status": "ok"})

@bp.delete("/api/recipients/<int:rid>")
def delete_recipient(rid: int):
    r = Recipient.query.get_or_404(rid)
    db.session.delete(r)
    db.session.commit()
    return jsonify({"status": "ok"})

# ---------- Settings API ----------
@bp.get("/api/settings")
def get_settings():
    s = get_or_create_settings()
    return jsonify({
        "enable_speaker_alert": s.enable_speaker_alert,
        "enable_phone_alert": s.enable_phone_alert,
        "enable_email_alert": s.enable_email_alert,
        "enable_sms_alert": s.enable_sms_alert,

        "telephony_provider": getattr(s, "telephony_provider", "twilio"),

        "smtp_server": s.smtp_server,
        "smtp_port": s.smtp_port,
        "smtp_username": s.smtp_username,
        "smtp_password": s.smtp_password,
        "smtp_notify_text": s.smtp_notify_text,

        "twilio_username": s.twilio_username,
        "twilio_token": s.twilio_token,
        "twilio_api_secret": s.twilio_api_secret,
        "twilio_source_number": s.twilio_source_number,
        "twilio_notify_text": s.twilio_notify_text,

        "clicksend_username": getattr(s, "clicksend_username", None),
        "clicksend_api_key": getattr(s, "clicksend_api_key", None),
        "clicksend_from": getattr(s, "clicksend_from", None),
        "clicksend_voice_from": getattr(s, "clicksend_voice_from", None),
        "clicksend_notify_text": getattr(s, "clicksend_notify_text", None),

        "mqtt_host": s.mqtt_host,
        "mqtt_user": s.mqtt_user,
        "mqtt_password": s.mqtt_password,
        "mqtt_topic_base": s.mqtt_topic_base,
    })

@bp.put("/api/settings")
def update_settings():
    s = get_or_create_settings()
    data = request.get_json(force=True) or {}

    # simple validator for max length 255
    def validate_text(field):
        if field in data and data[field] is not None:
            val = str(data[field]).strip()
            if len(val) > 255:
                return f"{field} must be 255 characters or fewer."
    
    for fld in ("smtp_notify_text", "twilio_notify_text", "clicksend_notify_text"):
        err = validate_text(fld)
        if err:
            return jsonify({"error": err}), 400
    
    for fld in ("solenoid_activated_audio", "solenoid_deactivated_audio"):
        if fld in data and data[fld] is not None:
            val = str(data[fld]).strip()
            if len(val) > 255:
                return jsonify({"error": f"{fld} must be 255 characters or fewer."}), 400
            setattr(s, fld, val if val else None)
        elif fld in data:
            setattr(s, fld, None)

    # assign toggles + strings
    for field in [
        "enable_speaker_alert", "enable_phone_alert", "enable_email_alert", "enable_sms_alert",
        "telephony_provider",
        "smtp_server", "smtp_username", "smtp_password", "smtp_notify_text",
        "twilio_username", "twilio_token", "twilio_api_secret", "twilio_source_number", "twilio_notify_text",
        "clicksend_username", "clicksend_api_key", "clicksend_from", "clicksend_voice_from", "clicksend_notify_text",
        "mqtt_host", "mqtt_user", "mqtt_password", "mqtt_topic_base",
    ]:
        if field in data:
            setattr(s, field, (data[field].strip() if isinstance(data[field], str) else data[field]))

    # port: normalize int/null
    if "smtp_port" in data:
        setattr(s, "smtp_port", int(data["smtp_port"]) if data["smtp_port"] not in (None, "",) else None)

    db.session.commit()
    return jsonify({"status": "ok"})

# ---------- Health ----------
@bp.get("/api/health")
def api_health():
    mon = current_app.extensions.get("solenoid_monitor")
    if not mon:
        return jsonify({"status":"error", "error":"Monitor not initialized"}), 503
    return jsonify(mon.health())

# ---------- History ----------
@bp.get("/api/history")
def api_history():
    try:
        limit = int(request.args.get("limit", "50"))
        limit = max(1, min(limit, 500))
    except ValueError:
        limit = 50

    rows = (AlertHistory.query.order_by(AlertHistory.ts.desc()).limit(limit).all())
    return jsonify([alert_history_as_dict(r) for r in rows])

# ---------- Test Notifications ----------
@bp.get("/api/notifications/test")
def test_notifications():
    mon = current_app.extensions.get("solenoid_monitor")
    if not mon:
        return jsonify({"status": "error", "error": "Monitor not initialized"}), 503

    threading.Thread(
        target=mon.test_alerts,
        kwargs={"message": "TEST: Furnace alert system check"},
        daemon=True,
        name="test-alerts",
    ).start()

    return jsonify({"status": "ok"})

# ---------- Audio API ----------
@bp.get("/api/audio/files")
def api_audio_files():
    return jsonify(list_audio_files())

@bp.get("/api/audio/settings")
def api_audio_settings_get():
    s = get_or_create_settings()
    vol = get_system_volume()
    return jsonify({
        "solenoid_activated_audio": s.solenoid_activated_audio,
        "solenoid_deactivated_audio": s.solenoid_deactivated_audio,
        "volume": vol if vol is not None else getattr(s, "audio_volume", None),
    })

@bp.put("/api/audio/settings")
def api_audio_settings_put():
    s = get_or_create_settings()
    data = request.get_json(force=True) or {}
    act = (data.get("solenoid_activated_audio") or "").strip() or None
    deact = (data.get("solenoid_deactivated_audio") or "").strip() or None

    # Validate chosen files exist
    if act and not ensure_exists(act):
        return jsonify({"error": f"File not found: {act}"}), 400
    if deact and not ensure_exists(deact):
        return jsonify({"error": f"File not found: {deact}"}), 400

    s.solenoid_activated_audio = act
    s.solenoid_deactivated_audio = deact

    if "volume" in data and data["volume"] is not None:
        try:
            vol = int(data["volume"])
            if not (0 <= vol <= 100):
                return jsonify({"error": "volume must be 0-100"}), 400
            set_system_volume(vol)           # apply to ALSA immediately
            setattr(s, "audio_volume", vol)  # persist our copy
        except Exception as e:
            return jsonify({"error": f"Failed to set system volume: {e}"}), 500

    db.session.commit()
    return jsonify({"status": "ok"})

@bp.post("/api/audio/upload")
def api_audio_upload():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "Missing form field 'file'"}), 400
    try:
        info = save_upload(f)
        return jsonify(info), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        current_app.logger.exception("Audio upload failed")
        return jsonify({"error": "Upload failed"}), 500

@bp.post("/api/audio/delete")
def api_audio_delete():
    data = request.get_json(force=True) or {}
    fn = (data.get("filename") or "").strip()
    ok, err = delete_audio(fn)
    if not ok:
        return jsonify({"error": err or "delete failed"}), 400
    return jsonify({"status": "ok"})

# Stream audio to the browser
@bp.get("/audio/<path:filename>")
def audio_file(filename: str):
    return serve_file(filename)

# --- Panel monitor API ---
@bp.get("/api/panel/status")
def api_panel_status():
    pm = current_app.extensions.get("panel_monitor")
    if not pm:
        return jsonify({"status": "error", "error": "Panel monitor not initialized"}), 503
    return jsonify(pm.latest())

@bp.post("/api/panel/reload")
def api_panel_reload():
    pm = current_app.extensions.get("panel_monitor")
    if not pm:
        return jsonify({"status": "error", "error": "Panel monitor not initialized"}), 503
    try:
        pm.reload_rois()
        return jsonify({"status": "ok"})
    except Exception as e:
        current_app.logger.exception("Panel reload failed")
        return jsonify({"status": "error", "error": str(e)}), 400

# ---- Calibrate page ----
@bp.route("/panel/calibrate")
def panel_calibrate():
    return render_template("panel_calibrate.html")

# Where to keep the YAML
def _rois_path():
    import os
    p = current_app.config.get(
        "PANEL_ROIS_PATH",
        os.path.join(current_app.instance_path, "panel_rois.yaml"),
    )
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p

# Helpers to read/write YAML
def _read_rois_file():
    import os, yaml
    p = _rois_path()
    if not os.path.exists(p):
        return {
            "lcd_rois": {"lcd1":None,"lcd2":None,"lcd3":None,"lcd4":None},
            "led_rois": {"opr_ctrl":None,"interlck":None,"ptfi":None,"flame":None,"alarm":None},
            "digit_count_per_lcd": 4,
            "seg_threshold": 0.55,
            "lcd_inverted": True,
            "led_red_thresh": {"sat":110,"val":120},
        }
    with open(p, "r") as f:
        return yaml.safe_load(f) or {}

def _write_rois_file(data):
    import yaml
    with open(_rois_path(), "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)

@bp.get("/api/panel/rois")
def api_panel_rois_get():
    return jsonify(_read_rois_file())

@bp.post("/api/panel/rois")
def api_panel_rois_set():
    data = request.get_json(force=True) or {}
    # minimal validation
    for k in ("lcd_rois", "led_rois"):
        if k not in data or not isinstance(data[k], dict):
            return jsonify({"error": f"Missing or invalid {k}"}), 400
    # write yaml
    cur = _read_rois_file()
    cur.update({
        "lcd_rois": data["lcd_rois"],
        "led_rois": data["led_rois"],
        "digit_count_per_lcd": int(data.get("digit_count_per_lcd", 4)),
        "seg_threshold": float(data.get("seg_threshold", 0.55)),
        "lcd_inverted": bool(data.get("lcd_inverted", True)),
        "led_red_thresh": {
            "sat": int(data.get("led_red_thresh",{}).get("sat",110)),
            "val": int(data.get("led_red_thresh",{}).get("val",120)),
        },
    })
    _write_rois_file(cur)
    return jsonify({"status": "ok"})

@bp.get("/api/panel/snapshot")
def api_panel_snapshot():
    pm = current_app.extensions.get("panel_monitor")
    if pm and hasattr(pm, "get_snapshot_jpeg"):
        jpg = pm.get_snapshot_jpeg()
        if jpg:
            resp = current_app.response_class(jpg, mimetype="image/jpeg")
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            return resp
    return jsonify({"error": "No snapshot available (worker not running)"}), 503

# --- Dry-run decode on an uploaded still photo ---
@bp.post("/api/panel/debug/decode")
def api_panel_debug_decode():
    """
    Decode an uploaded still photo using the current ROIs/thresholds and
    return the parsed values plus a JPEG (base64) annotated with boxes.
    """
    import base64
    import numpy as np
    import cv2
    from services.panel_monitor import _read_lcd, _led_on

    file = request.files.get("image")
    if not file:
        return jsonify({"error": "Missing file field 'image'"}), 400

    # Decode uploaded image -> BGR
    data = file.read()
    nparr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return jsonify({"error": "Could not decode image"}), 400

    # Load current ROIs/thresholds from disk
    cfg = _read_rois_file()
    seg_thr   = float(cfg.get("seg_threshold", 0.55))
    inverted  = bool(cfg.get("lcd_inverted", True))
    digits    = int(cfg.get("digit_count_per_lcd", 4))
    thr_led   = cfg.get("led_red_thresh", {"sat": 110, "val": 120})
    lcds_cfg  = cfg.get("lcd_rois", {}) or {}
    leds_cfg  = cfg.get("led_rois", {}) or {}

    # Helper: clamp ROI to image bounds
    h, w = img.shape[:2]
    def clamp_roi(roi):
        if not roi:
            return None
        x1 = max(0, min(int(roi.get("x1", 0)), w))
        y1 = max(0, min(int(roi.get("y1", 0)), h))
        x2 = max(0, min(int(roi.get("x2", 0)), w))
        y2 = max(0, min(int(roi.get("y2", 0)), h))
        if x2 <= x1 or y2 <= y1:
            return None
        return (x1, y1, x2, y2)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # ---- Read LCDs
    lcd_vals = []
    for key in ("lcd1", "lcd2", "lcd3", "lcd4"):
        r = clamp_roi(lcds_cfg.get(key))
        if r is None:
            lcd_vals.append("")
            continue
        x1, y1, x2, y2 = r
        sub = gray[y1:y2, x1:x2]
        try:
            val = _read_lcd(sub, digits=digits, inverted=inverted, frac_thr=seg_thr)
        except Exception:
            val = ""
        lcd_vals.append(val)

    # ---- Read LEDs
    led_states = {}
    for name, roi in (leds_cfg or {}).items():
        r = clamp_roi(roi)
        if r is None:
            led_states[name] = False
            continue
        x1, y1, x2, y2 = r
        sub = img[y1:y2, x1:x2]
        try:
            led_states[name] = bool(
                _led_on(
                    sub,
                    sat_thr=int(thr_led.get("sat", 110)),
                    val_thr=int(thr_led.get("val", 120)),
                )
            )
        except Exception:
            led_states[name] = False

    # ---- Build annotated preview
    annotated = img.copy()
    th = max(2, int(round(min(h, w) * 0.006)))
    font = cv2.FONT_HERSHEY_SIMPLEX
    fs   = max(0.5, min(1.2, min(h, w) / 900.0))
    shadow = max(1, th // 2)

    # LCD boxes (blue) + labels
    for key in ("lcd1", "lcd2", "lcd3", "lcd4"):
        r = clamp_roi(lcds_cfg.get(key))
        if r:
            x1, y1, x2, y2 = r
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (60, 180, 255), th)
            label = key.upper()
            org = (x1, max(0, y1 - 8))
            cv2.putText(annotated, label, org, font, fs, (0, 0, 0), shadow + 1, cv2.LINE_AA)
            cv2.putText(annotated, label, org, font, fs, (60, 180, 255), 1, cv2.LINE_AA)

    # LED boxes (green=ON, red=OFF) + labels
    for name in ("opr_ctrl", "interlck", "ptfi", "flame", "alarm"):
        r = clamp_roi(leds_cfg.get(name))
        if r:
            x1, y1, x2, y2 = r
            on = bool(led_states.get(name))
            color = (0, 255, 0) if on else (0, 0, 255)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, th)
            label = name.upper()
            org = (x1, max(0, y1 - 8))
            cv2.putText(annotated, label, org, font, fs, (0, 0, 0), shadow + 1, cv2.LINE_AA)
            cv2.putText(annotated, label, org, font, fs, color, 1, cv2.LINE_AA)

    ok, enc = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
    preview_b64 = base64.b64encode(enc.tobytes()).decode("ascii") if ok else None

    return jsonify({
        "lcds": lcd_vals,
        "leds": led_states,
        "preview_jpeg_b64": preview_b64
    })

# ---------- Admin APIs ----------
@bp.route("/admin")
def admin_page():
    return render_template("admin.html")

# --- Admin: log tail/download ---
@bp.get("/api/admin/log/tail")
def admin_log_tail():
    try:
        n = int(request.args.get("lines", "50"))
    except Exception:
        n = 50
    text = admin_ops.get_log_tail_text(current_app, n)
    p = admin_ops.get_full_log_file(current_app)
    dl_url = url_for("config_ui.admin_log_download") if p else None
    return jsonify({"tail": text, "download_url": dl_url})

@bp.get("/api/admin/log/download")
def admin_log_download():
    p = admin_ops.get_full_log_file(current_app)
    if not p:
        return jsonify({"error": "No log file found"}), 404
    return send_file(str(p), as_attachment=True, download_name=p.name, mimetype="text/plain")

# --- Admin: version/update/rollback/reboot ---
@bp.get("/api/admin/version")
def admin_version():
    cur = admin_ops.get_installed_version(current_app)
    latest, err = admin_ops.get_latest_github_version()
    return jsonify({
        "current": cur,
        "latest": latest or None,
        "error": (str(err) if err else None),
        "repo": admin_ops.REPO_SLUG,
        "has_backup": admin_ops.backup_exists(current_app),
    })

@bp.post("/api/admin/update")
def admin_update():
    resp = admin_ops.update_firepi(current_app)
    return jsonify(resp), (200 if resp.get("status") == "ok" else 500)

@bp.post("/api/admin/rollback")
def admin_rollback():
    resp = admin_ops.rollback_from_backup(current_app)
    return jsonify(resp), (200 if resp.get("status") == "ok" else 500)

@bp.post("/api/admin/reboot")
def admin_reboot():
    current_app.logger.info("Admin requested reboot")
    resp = admin_ops.reboot_system()
    if resp.get("status") != "ok":
        current_app.logger.error("Reboot request failed: %s", resp.get("error"))
    return jsonify(resp), (200 if resp.get("status") == "ok" else 500)

# --- Admin: support bundle + remote uploads ---
@bp.post("/api/admin/support/bundle")
def admin_support_bundle():
    data = request.get_json(silent=True) or {}
    include = bool(data.get("include_snapshot", True))
    ok, bundle_path, msg = admin_ops.create_support_bundle(current_app, include_snapshot=include)
    if not ok or not bundle_path:
        return jsonify({"status": "error", "error": msg}), 500
    dl_url = url_for("config_ui.support_download", filename=bundle_path.name)
    return jsonify({"status": "ok", "download_url": dl_url, "message": msg})


@bp.get("/api/admin/support/download/<path:filename>")
def support_download(filename: str):
    sup = Path(current_app.instance_path) / "support"
    return send_from_directory(sup, filename, as_attachment=True)

@bp.post("/api/admin/support/upload")
def admin_support_upload():
    data = request.get_json(force=True) or {}
    kind = (data.get("type") or "logs").strip().lower()

    # Optional flags from UI
    use_latest = bool(data.get("use_latest", True))            # default: reuse latest bundle
    include = bool(data.get("include_snapshot", True))         # only used if we must create a new bundle

    if kind == "logs":
        ok, msg = admin_ops.upload_logs_to_remote(current_app)
    elif kind == "snapshot":
        ok, msg = admin_ops.upload_snapshot_to_remote(current_app)
    elif kind == "bundle":
        ok, msg = admin_ops.upload_bundle_to_remote(current_app, use_latest=use_latest, include_snapshot=include)
    else:
        return jsonify({"status": "error", "error": "unknown upload type"}), 400

    return (jsonify({"status": "ok", "message": msg})
            if ok else
            (jsonify({"status": "error", "error": msg}), 500))


@bp.post("/api/admin/support/upload-snapshot")
def admin_support_upload_snapshot():
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Missing url"}), 400
    ok, msg = admin_ops.upload_snapshot(current_app, url)
    return (jsonify({"status":"ok","message":msg or "Uploaded"}), 200) if ok else (jsonify({"error": msg or "Upload failed"}), 500)

@bp.post("/api/admin/support/ping-remote")
def admin_support_ping_remote():
    from datetime import datetime
    from pathlib import Path
    tmpd = Path(current_app.instance_path) / "support"
    tmpd.mkdir(parents=True, exist_ok=True)
    tmpf = tmpd / f"ping_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
    tmpf.write_text("hello from firepi\n", encoding="utf-8")

    ok, msg = admin_ops._upload_path_to_remote(current_app, tmpf, "ping")
    return (jsonify({"status": "ok", "message": msg}), 200) if ok else (jsonify({"status": "error", "error": msg}), 500)
# ---------- Wi-Fi API ----------
@bp.get("/api/wifi/status")
def api_wifi_status():
    from services.wifi_nm import status
    return jsonify(status())

@bp.get("/api/wifi/scan")
def api_wifi_scan():
    from services.wifi_nm import scan
    try:
        nets = scan()
        return jsonify({"networks": nets})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.post("/api/wifi/connect")
def api_wifi_connect():
    data = request.get_json(force=True) or {}
    ssid = (data.get("ssid") or "").strip()
    psk  = (data.get("psk") or "").strip()
    from services.wifi_nm import connect
    try:
        resp = connect(ssid, psk, wait_s=20)
        code = 200 if resp.get("status") == "ok" else 400
        return jsonify(resp), code
    except Exception as e:
        return jsonify({"status":"error","error":str(e)}), 400

@bp.post("/api/wifi/forget")
def api_wifi_forget():
    data = request.get_json(force=True) or {}
    ssid = (data.get("ssid") or "").strip()
    from services.wifi_nm import forget
    try:
        resp = forget(ssid)
        code = 200 if resp.get("status") == "ok" else 400
        return jsonify(resp), code
    except Exception as e:
        return jsonify({"status":"error","error":str(e)}), 400
