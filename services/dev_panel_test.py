# services/dev_panel_test.py
from __future__ import annotations
import os, io, base64, uuid
from pathlib import Path
from typing import Dict, Any

import numpy as np
import cv2
from flask import Blueprint, current_app, render_template, request, jsonify, send_file, send_from_directory

# Reuse image/ROI logic from panel monitor
from .panel_monitor import _crop, _read_lcd, _led_on  # <-- these exist in your file

dev_panel_test_bp = Blueprint("dev_panel_test", __name__, url_prefix="/dev/panel-test")

def _uploads_dir() -> Path:
    d = Path(current_app.instance_path) / "dev_panel_test"
    d.mkdir(parents=True, exist_ok=True)
    return d

@dev_panel_test_bp.get("/")
def page():
    # Your template name (you showed it as panel_test.html)
    return render_template("dev/panel_test.html")

@dev_panel_test_bp.post("/upload")
def upload():
    """
    Optional helper: accept multipart file upload, save it, return image_id + URL.
    Your JS can keep using FileReader; this route is just here if you want it.
    """
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file"}), 400

    img_bytes = f.read()
    try:
        arr = np.frombuffer(img_bytes, dtype=np.uint8)
        im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if im is None:
            return jsonify({"error": "decode failed"}), 400
    except Exception:
        return jsonify({"error": "decode failed"}), 400

    image_id = uuid.uuid4().hex
    out_path = _uploads_dir() / f"{image_id}.jpg"
    # Re-encode to jpg so we know the content-type for static serving
    ok, enc = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        return jsonify({"error": "encode failed"}), 500
    out_path.write_bytes(enc.tobytes())

    h, w = im.shape[:2]
    return jsonify({
        "image_id": image_id,
        "width":  int(w),
        "height": int(h),
        "url":    f"/dev/panel-test/uploads/{image_id}.jpg"
    })

@dev_panel_test_bp.get("/uploads/<image_id>.jpg")
def serve_upload(image_id: str):
    p = _uploads_dir() / f"{image_id}.jpg"
    if not p.exists():
        return jsonify({"error": "not found"}), 404
    return send_from_directory(_uploads_dir(), f"{image_id}.jpg")

def _decode_image_from_data_url(data_url: str):
    """
    Accepts 'data:image/...;base64,....' and returns BGR image for OpenCV.
    """
    try:
        header, b64 = data_url.split(",", 1)
    except ValueError:
        return None
    try:
        raw = base64.b64decode(b64, validate=True)
        arr = np.frombuffer(raw, dtype=np.uint8)
        im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return im
    except Exception:
        return None

def _load_image_from_image_id(image_id: str):
    p = _uploads_dir() / f"{image_id}.jpg"
    if not p.exists():
        return None
    data = p.read_bytes()
    arr = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)

@dev_panel_test_bp.post("/parse")
def parse():
    """
    Body JSON:
    {
      "rois": { "<key>": {x1,y1,x2,y2}, ... },
      "options": {
        "digits": int,
        "seg_threshold": float,
        "lcd_inverted": bool,
        "led_sat": int,
        "led_val": int
      },
      // Supply either of these:
      "image_data_url": "data:image/jpeg;base64,...",
      "image_id": "abc123"
    }
    """
    data = request.get_json(silent=True) or {}

    rois: Dict[str, Any] = data.get("rois") or {}
    opts: Dict[str, Any] = data.get("options") or {}

    # --- get image (data URL or saved image_id) ---
    im = None
    if isinstance(data.get("image_data_url"), str) and data["image_data_url"].startswith("data:"):
        im = _decode_image_from_data_url(data["image_data_url"])
        if im is None:
            return jsonify({"error": "invalid image_data_url"}), 400
    elif isinstance(data.get("image_id"), str) and data["image_id"]:
        im = _load_image_from_image_id(data["image_id"])
        if im is None:
            return jsonify({"error": "image_id not found"}), 404
    else:
        # Old behavior complained about image_id; now we accept either, so return a clearer error
        return jsonify({"error": "missing image (provide image_data_url or image_id)"}), 400

    # --- options ---
    digits       = int(opts.get("digits", 4))
    seg_thr      = float(opts.get("seg_threshold", 0.55))
    lcd_inverted = bool(opts.get("lcd_inverted", True))
    led_sat      = int(opts.get("led_sat", 110))
    led_val      = int(opts.get("led_val", 120))

    gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)

    # --- parse LCDs ---
    lcds = {}
    for key in ("lcd1", "lcd2", "lcd3", "lcd4"):
        r = rois.get(key)
        if not r:
            lcds[key] = ""
            continue
        try:
            roi_gray = cv2.cvtColor(_crop(im, r), cv2.COLOR_BGR2GRAY)
            lcds[key] = _read_lcd(roi_gray, digits=digits, inverted=lcd_inverted, thr=seg_thr) or ""
        except Exception:
            lcds[key] = ""

    # --- parse LEDs ---
    leds = {}
    for key in ("opr_ctrl", "interlck", "ptfi", "flame", "alarm"):
        r = rois.get(key)
        if not r:
            leds[key] = False
            continue
        try:
            leds[key] = bool(_led_on(_crop(im, r), sat_thr=led_sat, val_thr=led_val))
        except Exception:
            leds[key] = False

    return jsonify({
        "ok": True,
        "lcds": lcds,
        "leds": leds,
        "width":  int(im.shape[1]),
        "height": int(im.shape[0]),
    })
