from flask import Blueprint, current_app, render_template, jsonify, request
from services.seg7 import read_lcd_roi, ssocr_read_digits
import numpy as np, cv2, base64, os, json, time, yaml

ocr_bp = Blueprint("config_ui", __name__)

# --- Panel monitor API ---
@ocr_bp.get("/api/panel/status")
def api_panel_status():
    pm = current_app.extensions.get("panel_monitor")
    if not pm:
        return jsonify({"status": "error", "error": "Panel monitor not initialized"}), 503
    return jsonify(pm.latest())

@ocr_bp.post("/api/panel/reload")
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
@ocr_bp.route("/panel/calibrate")
def panel_calibrate():
    return render_template("panel_calibrate.html")

# Where to keep the YAML
def _rois_path():
    p = current_app.config.get(
        "PANEL_ROIS_PATH",
        os.path.join(current_app.instance_path, "panel_rois.yaml"),
    )
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p

# Helpers to read/write YAML
def _read_rois_file():
    p = _rois_path()
    if not os.path.exists(p):
        return {
            "lcd_rois": {"lcd1":None,"lcd2":None,},
            "led_rois": {"opr_ctrl":None,"interlck":None,"ptfi":None,"flame":None,"alarm":None},
            "digit_count_per_lcd": 4,
            "seg_threshold": 0.35,
            "lcd_inverted": True,
            "led_red_thresh": {"sat":110,"val":120},
        }
    with open(p, "r") as f:
        return yaml.safe_load(f) or {}

def _write_rois_file(data):
    with open(_rois_path(), "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)

@ocr_bp.get("/api/panel/rois")
def api_panel_rois_get():
    return jsonify(_read_rois_file())

@ocr_bp.post("/api/panel/rois")
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
        "seg_threshold": float(data.get("seg_threshold", 0.35)),
        "lcd_inverted": bool(data.get("lcd_inverted", True)),
        "led_red_thresh": {
            "sat": int(data.get("led_red_thresh",{}).get("sat",110)),
            "val": int(data.get("led_red_thresh",{}).get("val",120)),
        },
    })
    _write_rois_file(cur)
    return jsonify({"status": "ok"})

@ocr_bp.get("/api/panel/snapshot")
def api_panel_snapshot():
    pm = current_app.extensions.get("panel_monitor")
    if pm and hasattr(pm, "get_snapshot_jpeg"):
        jpg = pm.get_snapshot_jpeg()
        if jpg:
            resp = current_app.response_class(jpg, mimetype="image/jpeg")
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            return resp
    return jsonify({"error": "No snapshot available (worker not running)"}), 503

@ocr_bp.post("/api/panel/dry_run")
def api_panel_dry_run():
    """
    Debug-first dry-run OCR/LED decode for an uploaded snapshot.
    Logs everything to server logs so you can see exactly what's happening.
    """
    log = current_app.logger

    t0 = time.time()
    f = request.files.get("image")
    if not f:
        log.error("[dry_run] no image provided")
        return jsonify(error="no image provided"), 400
    data = f.read()
    if not data:
        log.error("[dry_run] empty image payload")
        return jsonify(error="empty image"), 400

    arr = np.frombuffer(data, np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        log.error("[dry_run] cv2.imdecode failed")
        return jsonify(error="decode failed"), 400
    H, W = bgr.shape[:2]
    log.info("[dry_run] image decoded: %dx%d", W, H)

    # --- Load ROIs from the configured single path
    rois_path = current_app.config.get("PANEL_ROIS_PATH", "panel_rois.yaml")
    abs_path = os.path.abspath(rois_path)
    exists = os.path.exists(rois_path)
    log.info("[dry_run] ROI path: %s (abs=%s) exists=%s", rois_path, abs_path, exists)

    cfg = {}
    if exists:
        try:
            with open(rois_path, "r") as fh:
                cfg = yaml.safe_load(fh) or {}
            log.info("[dry_run] YAML loaded with keys: %s", list(cfg.keys()))
        except Exception as e:
            log.exception("[dry_run] failed to load YAML from %s: %r", rois_path, e)
            return jsonify(error=f"failed to load YAML: {e}"), 500
    else:
        log.warning("[dry_run] ROI YAML not found at %s", abs_path)

    # --- Defaults (align with monitor)
    cfg.setdefault("lcd_rois", {"lcd1": None, "lcd2": None, })
    cfg.setdefault("lcd_sign_rois", {"lcd1": None, "lcd2": None})
    cfg.setdefault("led_rois", {"opr_ctrl": None, "interlck": None, "ptfi": None, "flame": None, "alarm": None})
    cfg.setdefault("digit_count_per_lcd", 4)
    cfg.setdefault("seg_threshold", 0.35)  # segment ON threshold (ratio)
    cfg.setdefault("lcd_color_hint", {"lcd1": "red", "lcd2": "red"})
    cfg.setdefault("led_thr", {"sat": 110, "val": 120, "frac": 0.12})
    cfg.setdefault("sign_thr", {"val": 140, "sat_min": 30, "frac": 0.08})
    lcd_method_default = (cfg.get("lcd_method") or "ratio").lower()
    lcd_method_per = {k: (v or "").lower() for k, v in (cfg.get("lcd_method_per") or {}).items()}

    def _choose_method_for(key: str) -> str:
        return lcd_method_per.get(key, lcd_method_default)

    # --- Normalize ROIs
    def _norm_roi(v):
        if not v: return None
        if isinstance(v, dict):
            try:
                r = {k:int(v.get(k,0)) for k in ("x1","y1","x2","y2")}
                return r
            except Exception:
                return None
        if isinstance(v, (list, tuple)) and len(v) == 4:
            x1,y1,x2,y2 = map(int, v)
            return {"x1":x1,"y1":y1,"x2":x2,"y2":y2}
        return None

    raw_lcd  = {k:_norm_roi(v) for k,v in (cfg.get("lcd_rois") or {}).items()}
    raw_sign = {k:_norm_roi(v) for k,v in (cfg.get("lcd_sign_rois") or {}).items()}
    raw_led  = {k:_norm_roi(v) for k,v in (cfg.get("led_rois") or {}).items()}
    seg_thr = cfg.get("seg_threshold")

    log.info("[dry_run] raw lcd_rois: %s", json.dumps(raw_lcd))
    log.info("[dry_run] raw sign_rois: %s", json.dumps(raw_sign))
    log.info("[dry_run] raw led_rois keys: %s", list(raw_led.keys()))
    log.info("[dry_run] seg_threshold=%.2f", seg_thr)
    

    # --- Scaling using roi_ref_size (if present)
    ref = cfg.get("roi_ref_size") or cfg.get("image_size")
    if isinstance(ref, dict) and int(ref.get("w",0)) and int(ref.get("h",0)):
        sx = float(W) / float(ref["w"])
        sy = float(H) / float(ref["h"])
        log.info("[dry_run] roi_ref_size: %s  scale sx=%.4f sy=%.4f", ref, sx, sy)
    else:
        sx = sy = 1.0
        log.info("[dry_run] no roi_ref_size found; using sx=sy=1.0")

    def _scale_roi(r):
        if not r: return None
        x1 = int(round(r["x1"] * sx)); y1 = int(round(r["y1"] * sy))
        x2 = int(round(r["x2"] * sx)); y2 = int(round(r["y2"] * sy))
        x1 = max(0, min(W, x1)); x2 = max(0, min(W, x2))
        y1 = max(0, min(H, y1)); y2 = max(0, min(H, y2))
        if x2 <= x1 or y2 <= y1:
            return None
        return {"x1":x1,"y1":y1,"x2":x2,"y2":y2}

    lcd_rois  = {k:_scale_roi(v) for k,v in raw_lcd.items()}
    sign_rois = {k:_scale_roi(v) for k,v in raw_sign.items()}
    led_rois  = {k:_scale_roi(v) for k,v in raw_led.items()}

    log.info("[dry_run] scaled lcd_rois: %s", json.dumps(lcd_rois))
    log.info("[dry_run] scaled sign_rois: %s", json.dumps(sign_rois))
    # Log LED ROIs individually to avoid very long lines if many
    for name, r in led_rois.items():
        log.info("[dry_run] scaled led_roi[%s]: %s", name, r)

    def _crop(img, roi):
        if img is None or roi is None:
            return None
        x1, y1, x2, y2 = int(roi["x1"]), int(roi["y1"]), int(roi["x2"]), int(roi["y2"])
        h, w = img.shape[:2]
        x1 = max(0, min(w, x1)); x2 = max(0, min(w, x2))
        y1 = max(0, min(h, y1)); y2 = max(0, min(h, y2))
        if x2 <= x1 or y2 <= y1:
            return None
        return img[y1:y2, x1:x2].copy()

    def _roi_bright_on_black(bgr_roi, val_thr=140, sat_min=30, frac_thr=0.08):
        if bgr_roi is None or bgr_roi.size == 0: return False
        hsv = cv2.cvtColor(bgr_roi, cv2.COLOR_BGR2HSV)
        v = hsv[...,2]; s = hsv[...,1]
        mask = (v > int(val_thr)) & (s >= int(sat_min))
        frac = float(mask.mean())
        return frac > float(frac_thr)

    def _led_on_any(bgr_roi, sat_thr=110, val_thr=120, frac_thr=0.12):
        if bgr_roi is None or bgr_roi.size == 0: return False
        hsv = cv2.cvtColor(bgr_roi, cv2.COLOR_BGR2HSV)
        m1 = cv2.inRange(hsv, np.array([0,   max(60,sat_thr-50), max(70,val_thr-50)], np.uint8), np.array([10, 255, 255], np.uint8))
        m2 = cv2.inRange(hsv, np.array([170, max(60,sat_thr-50), max(70,val_thr-50)], np.uint8), np.array([180,255, 255], np.uint8))
        mg = cv2.inRange(hsv, np.array([40,  max(60,sat_thr-50), max(70,val_thr-50)], np.uint8), np.array([90, 255, 255], np.uint8))
        mask = m1 | m2 | mg
        frac = float((mask > 0).mean())
        return frac > float(frac_thr)
    
    def _read_lcd_via_ssocr(img_bgr, digits: int, hint: str | None):
        """
        Call ssocr and adapt its output to (value, [per-digit confs]) just like read_lcd_roi.
        - Pads/truncates to `digits`
        - Synthesizes simple confidences (0.75 for a 0-9, 0.5 for blank/other)
        """
        if img_bgr is None or getattr(img_bgr, "size", 0) == 0:
            return "", []

        # Prefer red filtering if you pass "red" (or any truthy) in `hint`
        expect_color = "red" if (hint and str(hint).lower().startswith("r")) else None

        # Call your wrapper. If your wrapper only returns a string, the `try/except` handles it.
        try:
            text, meta = ssocr_read_digits(
                img_bgr,
                digits=digits,
                expect_color=expect_color,   # wrapper can ignore if unsupported
                invert=False,                # most red LED/LCDs render as bright-on-dark
                threshold="otsu",            # let wrapper choose default if not supported
                extra_args=None,             # room for tuning (erosion, despeckle, etc.)
            )
        except TypeError:
            # Backward compatibility with a simpler signature
            text = ssocr_read_digits(img_bgr, digits=digits)
            meta = {}

        # Normalize to exactly `digits` chars (spaces for blanks)
        text = (text or "")
        if digits and len(text) != digits:
            text = text[:digits].ljust(digits, " ")

        # Super-simple confidences; you can upgrade this using `meta` if your wrapper returns scores
        confs = [0.75 if ch.isdigit() else 0.5 for ch in text]
        return text, confs

    digits   = int(cfg.get("digit_count_per_lcd", 4))
    seg_thr  = float(cfg.get("seg_threshold", 0.35))
    hints    = cfg.get("lcd_color_hint") or {}
    led_thr  = cfg.get("led_thr", {"sat":110,"val":120,"frac":0.12})
    lcd_invert = cfg.get("ssocr_read_digits", False)
    #sign_thr = cfg.get("sign_thr", {"val":140,"sat_min":30,"frac":0.08})

    # --- Decode LCDs, signs, LEDs with detailed logs
    lcd_vals = []
    for key in ("lcd1","lcd2"):
        roi = lcd_rois.get(key)
        crop = _crop(bgr, roi)
        log.info("[dry_run] LCD %s roi=%s crop=%s", key, roi, crop.shape if crop is not None else None)
        # Per-LCD digits/threshold/hint fallbacks
        hint = (hints or {}).get(key)  # e.g. "red" to enable red gating in Method 1

        method = _choose_method_for(key)
        if method == "ssocr":
            # Method 2: “ssocr-style”
            text, meta = ssocr_read_digits(
                crop,
                digits=digits,
                invert=lcd_invert,           # flip to True if the display is dark-on-light
                threshold="otsu",       # can be int like 170, or "otsu"
                whitelist="0123456789", # keep just digits; spaces are allowed
            )
            confs = meta.get("confs", [0.5] * digits)
            log.info("[dry_run] LCD %s (ssocr) -> '%s' meta=%s", key, text, meta)
            lcd_vals.append(text or "")
        else:
            # Method 1: color-gated ratio decoder
            text, confs = read_lcd_roi(
                crop,
                digits=digits,
                hint=hint,          # "red" enables the red gate; anything else = plain gray
                seg_thr=seg_thr,    # falls back to DEFAULT_SEG_THR inside if None
            )
            log.info("[dry_run] LCD %s (ratio) -> '%s' confs=%s", key, text, [round(c, 3) for c in confs])
            lcd_vals.append(text or "")

    #signs_on = {}
    #for key in ("lcd1","lcd2"):
    #    sroi = sign_rois.get(key)
    #    scrop = _crop(bgr, sroi)
    #    on = _roi_bright_on_black(scrop,
    #                              val_thr=sign_thr.get("val",140),
    #                              sat_min=sign_thr.get("sat_min",30),
    #                              frac_thr=sign_thr.get("frac",0.08))
    #    log.info("[dry_run] SIGN %s roi=%s crop=%s -> %s", key, sroi, scrop.shape if scrop is not None else None, on)
    #    signs_on[key] = on

    #for i,key in enumerate(("lcd1","lcd2")):
    #    if signs_on.get(key, False):
    #        lcd_vals[i] = "-" + (lcd_vals[i] or "")

    leds = {}
    for name, roi in led_rois.items():
        crop = _crop(bgr, roi)
        on = _led_on_any(crop,
                         sat_thr=int(led_thr.get("sat",110)),
                         val_thr=int(led_thr.get("val",120)),
                         frac_thr=float(led_thr.get("frac",0.12)))
        log.info("[dry_run] LED %-10s roi=%s crop=%s -> %s",
                 name, roi, crop.shape if crop is not None else None, on)
        leds[name] = bool(on)

    # --- Annotated preview
    dbg = bgr.copy()
    def box(img, r, color, label=None):
        if not r: return
        cv2.rectangle(img, (r["x1"], r["y1"]), (r["x2"], r["y2"]), color, 2)
        if label:
            cv2.putText(img, str(label), (r["x1"], max(0, r["y1"]-6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)

    for i,k in enumerate(("lcd1","lcd2"), 1):
        box(dbg, lcd_rois.get(k),  (255,160,20), f"{k}:{lcd_vals[i-1] or ''}")
        box(dbg, sign_rois.get(k), ( 30,220,30), f"{k}-sign")
    for name, r in led_rois.items():
        box(dbg, r, (40,140,255), f"{name}:{'on' if leds.get(name) else 'off'}")

    ok, enc = cv2.imencode(".jpg", dbg, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
    preview_b64 = base64.b64encode(enc.tobytes()).decode("ascii") if ok else None

    dbg_out = {
        "roi_path": abs_path,
        "roi_exists": exists,
        "image_size": {"w": W, "h": H},
        "roi_ref_size": cfg.get("roi_ref_size"),
        "scale": {"sx": sx, "sy": sy},
        "lcd_rois": lcd_rois,
        "sign_rois": sign_rois,
        "led_rois_keys": list(led_rois.keys()),
        "elapsed_ms": int((time.time()-t0)*1000),
    }
    log.info("[dry_run] result lcds=%s leds=%s elapsed=%dms", lcd_vals, leds, dbg_out["elapsed_ms"])

    return jsonify({"lcds": lcd_vals, "leds": leds, "preview_jpeg_b64": preview_b64, "debug": dbg_out})