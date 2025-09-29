from __future__ import annotations
import os
import time
import json
import threading
from typing import Optional, Dict, Any, List, Tuple
import yaml
import numpy as np
import cv2

# same persistent publisher helper you use elsewhere
from .mqtt_pub import get_publisher  # must provide .publish_json(...) and .close()

# Keep OpenCV/NumPy threading modest on small devices
cv2.setNumThreads(1)
try:
    cv2.ocl.setUseOpenCL(False)
except Exception:
    pass

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")


# ---------------- camera helpers ----------------
def _open_camera(use_picamera2: bool):
    """
    Returns (cap, picam2) where one will be None depending on backend.
    """
    if not use_picamera2:
        cap = cv2.VideoCapture(0)
        # Prefer MJPG to lower CPU on Pi
        try:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        except Exception:
            pass
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_FPS, 10)
        # Keep buffer small if driver supports it
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        if not cap.isOpened():
            raise RuntimeError("Cannot open /dev/video0. Is the camera enabled and attached?")
        return cap, None
    else:
        # Lazy import for systems without Picamera2
        from picamera2 import Picamera2
        picam2 = Picamera2()
        cfg = picam2.create_video_configuration(main={"size": (1280, 720)})
        picam2.configure(cfg)
        picam2.start()
        return None, picam2


def _read_frame(cap, picam2):
    if picam2 is None:
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError("Camera read failed.")
        return frame
    else:
        return picam2.capture_array()


# ---------------- ROI + image utils ----------------
def _crop(frame: np.ndarray, roi: Dict[str, int]) -> np.ndarray:
    """
    Safely crop using dict roi = {x1,y1,x2,y2}. Returns empty array if bad ROI.
    """
    if frame is None or roi is None:
        return np.empty((0, 0, 3), dtype=np.uint8)

    h, w = frame.shape[:2]
    x1 = max(0, min(int(roi.get("x1", 0)), w))
    y1 = max(0, min(int(roi.get("y1", 0)), h))
    x2 = max(0, min(int(roi.get("x2", 0)), w))
    y2 = max(0, min(int(roi.get("y2", 0)), h))
    if x2 <= x1 or y2 <= y1:
        return np.empty((0, 0, 3), dtype=frame.dtype)
    return frame[y1:y2, x1:x2].copy()


# ---------------- 7-segment OCR ----------------
# Map of lit segments -> digit
# Order: top, upper-left, upper-right, middle, lower-left, lower-right, bottom
SEG_TO_DIGIT = {
    (1, 1, 1, 1, 1, 1, 0): "0",
    (0, 1, 1, 0, 0, 0, 0): "1",
    (1, 1, 0, 1, 1, 0, 1): "2",
    (1, 1, 1, 1, 0, 0, 1): "3",
    (0, 1, 1, 0, 0, 1, 1): "4",
    (1, 0, 1, 1, 0, 1, 1): "5",
    (1, 0, 1, 1, 1, 1, 1): "6",
    (1, 1, 1, 0, 0, 0, 0): "7",
    (1, 1, 1, 1, 1, 1, 1): "8",
    (1, 1, 1, 1, 0, 1, 1): "9",
    (0, 0, 0, 0, 0, 0, 0): " ",  # blank
}

def _segments_from_digit_roi(bgr_or_gray: np.ndarray, *, inverted: bool, frac_thr: float = 0.10):
    """
    Decide ON/OFF for each of 7 segments using Otsu on the Value (brightness) channel.
    Works well for bright (green) digits on black, and for dark-on-light if inverted=True.
    """
    if bgr_or_gray is None or bgr_or_gray.size == 0:
        return (0, 0, 0, 0, 0, 0, 0)

    # Work on brightness (V in HSV) for colored digits
    if len(bgr_or_gray.shape) == 3:
        hsv = cv2.cvtColor(bgr_or_gray, cv2.COLOR_BGR2HSV)
        chan = hsv[..., 2]
    else:
        chan = bgr_or_gray

    h, w = chan.shape[:2]
    t = max(1, int(h * 0.16))  # segment thickness
    m = max(1, int(h * 0.06))  # margin

    # Otsu to split bright vs dark in this digit
    _, bin_img = cv2.threshold(chan, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if inverted:
        bin_img = 255 - bin_img

    regions = [
        (m, m, w - m, m + t),                               # top
        (m, m, m + t, h // 2),                              # upper-left
        (w - m - t, m, w - m, h // 2),                      # upper-right
        (m, h // 2 - t // 2, w - m, h // 2 + t // 2),       # middle
        (m, h // 2, m + t, h - m),                          # lower-left
        (w - m - t, h // 2, w - m, h - m),                  # lower-right
        (m, h - m - t, w - m, h - m),                       # bottom
    ]

    segs = []
    for (x1, y1, x2, y2) in regions:
        y1c, y2c = max(y1, 0), min(y2, h)
        x1c, x2c = max(x1, 0), min(x2, w)
        if x2c <= x1c or y2c <= y1c:
            segs.append(0)
            continue
        roi = bin_img[y1c:y2c, x1c:x2c]
        on_frac = (roi == 255).mean()
        segs.append(1 if on_frac >= frac_thr else 0)
    return tuple(segs)

def _decode_digit(bgr_or_gray_digit: np.ndarray, *, inverted: bool, frac_thr: float = 0.10) -> str:
    segs = _segments_from_digit_roi(bgr_or_gray_digit, inverted=inverted, frac_thr=frac_thr)
    return SEG_TO_DIGIT.get(segs, "?")

def _read_lcd(bgr_lcd: np.ndarray, *, digits: int = 4, inverted: bool = False, frac_thr: float = 0.10) -> str:
    """Read exactly `digits` characters (no inline minus)."""
    if bgr_lcd is None or bgr_lcd.size == 0:
        return ""
    h, w = bgr_lcd.shape[:2]
    if w <= 0 or digits <= 0:
        return ""

    step = max(1, w // digits)
    out = []
    for i in range(digits):
        x1 = i * step
        x2 = (i + 1) * step if i < digits - 1 else w
        droi = bgr_lcd[:, x1:x2]
        out.append(_decode_digit(droi, inverted=inverted, frac_thr=frac_thr))
    return "".join(out)


# ---------------- LED / sign detection ----------------
def _led_on_any(bgr_roi: np.ndarray, sat_thr: int = 110, val_thr: int = 120, frac_thr: float = 0.12) -> bool:
    """
    Detect bright LED via HSV; supports RED (0..10,170..180) and GREEN (40..90).
    Returns True if a sufficient fraction of pixels match either range.
    """
    if bgr_roi is None or bgr_roi.size == 0:
        return False
    hsv = cv2.cvtColor(bgr_roi, cv2.COLOR_BGR2HSV)
    # red
    m1 = cv2.inRange(hsv, np.array([0,   sat_thr, val_thr], np.uint8), np.array([10, 255, 255], np.uint8))
    m2 = cv2.inRange(hsv, np.array([170, sat_thr, val_thr], np.uint8), np.array([180,255, 255], np.uint8))
    # green
    mg = cv2.inRange(hsv, np.array([40,  sat_thr, val_thr], np.uint8), np.array([90, 255, 255], np.uint8))
    mask = m1 | m2 | mg
    frac = (mask > 0).mean()
    return frac > frac_thr

def _led_on(bgr_roi: np.ndarray, sat_thr: int = 110, val_thr: int = 120) -> bool:
    return _led_on_any(bgr_roi, sat_thr=sat_thr, val_thr=val_thr, frac_thr=0.12)

def _roi_bright_on_black(bgr_roi: np.ndarray, val_thr: int = 140, sat_min: int = 30, frac_thr: float = 0.08) -> bool:
    """
    Generic 'is this small ROI showing a bright lit dash/indicator on black?' detector.
    Uses Value channel threshold with a minimal saturation check.
    """
    if bgr_roi is None or bgr_roi.size == 0:
        return False
    hsv = cv2.cvtColor(bgr_roi, cv2.COLOR_BGR2HSV)
    v = hsv[..., 2]
    s = hsv[..., 1]
    mask = (v > val_thr) & (s >= sat_min)
    return mask.mean() > frac_thr


# ---------------- Monitor service ----------------
class PanelMonitor:
    """
    Background worker that reads a camera, decodes four LCDs and LED states,
    publishes status (retained) and per-change events to MQTT, and keeps a
    JPEG snapshot for the UI.

    LCD negatives are handled via separate ROIs (lcd_sign_rois); if the sign ROI is "bright",
    a '-' is prefixed to the LCD value.
    """

    def __init__(
        self,
        app,
        rois_path: str = "panel_rois.yaml",
        use_picamera2: bool = False,
        fps: float = 8.0,
        mqtt: Optional[Dict[str, Any]] = None,
    ):
        self.app = app
        # Basic scheduling
        self.target_fps = int(getattr(app.config, "get", lambda *_: 5)("PANEL_FPS", 5))
        self.period = 1.0 / float(max(1.0, fps))
        # UI snapshot cadence (Hz)
        self.snapshot_hz = int(getattr(app.config, "get", lambda *_: 2)("PANEL_SNAPSHOT_HZ", 2))

        self.rois_path = rois_path
        self.use_picamera2 = use_picamera2

        # MQTT cfg (from Settings -> cfg["mqtt"])
        self.mqtt_cfg = mqtt or {}

        # Threading
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Latest snapshot for UI
        self._latest: Dict[str, Any] = {"lcds": ["", "", "", ""], "leds": {}, "ts": 0.0}
        self._last_jpeg: Optional[bytes] = None

        # Change detection caches for MQTT events
        self._last_pub_leds: Optional[Dict[str, bool]] = None
        self._last_pub_lcds: Optional[List[str]] = None

        # MQTT persistent publisher + topics
        self._pub = None
        self._topic_status = None
        self._topic_led_evt = None
        self._topic_lcd_evt = None

        self.started = False

    # ---------- lifecycle ----------
    def start(self):
        if self.started:
            return
        try:
            cv2.setNumThreads(1)
        except Exception:
            pass

        # MQTT: init once, fail fast (keep consistent with solenoid)
        self._init_mqtt_publisher()

        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="panel-monitor", daemon=True)
        self._thread.start()
        self.started = True
        self.app.logger.info("PanelMonitor started.")

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self.started = False

        if self._pub:
            try:
                self._pub.close()
            except Exception:
                pass
            self._pub = None

        self.app.logger.info("PanelMonitor stopped.")

    # ---------- public API ----------
    def latest(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._latest)

    def get_snapshot_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._last_jpeg

    def reload_rois(self):
        """(Re)load ROIs from disk; safe if file missing/empty."""
        try:
            with open(self.rois_path, "r") as f:
                cfg = yaml.safe_load(f) or {}
        except FileNotFoundError:
            cfg = {}

        # Normalize legacy keys
        if 'seg_threshold' in cfg and 'seg_frac_thr' not in cfg:
            try:
                cfg['seg_frac_thr'] = float(cfg.get('seg_threshold', 0.10))
            except Exception:
                cfg['seg_frac_thr'] = 0.10
        if 'led_red_thresh' in cfg and 'led_thr' not in cfg:
            t = cfg.get('led_red_thresh') or {}
            cfg['led_thr'] = {'sat': int(t.get('sat',110)), 'val': int(t.get('val',120)), 'frac': 0.12}
        # Provide safe defaults if keys are missing
        cfg.setdefault("lcd_rois", {"lcd1": None, "lcd2": None, "lcd3": None, "lcd4": None})
        cfg.setdefault(
            "lcd_sign_rois",
            {"lcd1": None, "lcd2": None, "lcd3": None, "lcd4": None},
        )
        cfg.setdefault(
            "led_rois",
            {"opr_ctrl": None, "interlck": None, "ptfi": None, "flame": None, "alarm": None},
        )
        cfg.setdefault("digit_count_per_lcd", 4)
        cfg.setdefault("lcd_inverted", False)  # bright-on-dark typical here
        cfg.setdefault("seg_frac_thr", 0.10)   # fraction of lit pixels per segment
        cfg.setdefault("led_thr", {"sat": 110, "val": 120, "frac": 0.12})
        cfg.setdefault("sign_thr", {"val": 140, "sat_min": 30, "frac": 0.08})

        self._cfg = cfg
        self.app.logger.info("PanelMonitor: ROIs loaded from %s", self.rois_path)

    def save_rois(self, cfg: Dict[str, Any]):
        """Write new ROIs atomically; then reload."""
        tmp = f"{self.rois_path}.tmp"
        with open(tmp, "w") as f:
            yaml.safe_dump(cfg, f)
        os.replace(tmp, self.rois_path)
        self.reload_rois()
        self.app.logger.info("PanelMonitor: ROIs saved to %s", self.rois_path)

    # ---------- change helpers ----------
    @staticmethod
    def _leds_diff(old: Optional[Dict[str, bool]], new: Dict[str, bool]) -> List[Tuple[str, Optional[bool], bool]]:
        """
        Return list of (name, old_val, new_val) for LEDs that changed (or all if old is None).
        """
        if old is None:
            return [(k, None, bool(v)) for k, v in sorted(new.items())]
        out = []
        keys = set(old.keys()) | set(new.keys())
        for k in sorted(keys):
            o = bool(old.get(k, False))
            n = bool(new.get(k, False))
            if o != n:
                out.append((k, o, n))
        return out

    @staticmethod
    def _lcds_diff(old: Optional[List[str]], new: List[str]) -> List[Tuple[str, Optional[str], str]]:
        """
        Return list of (lcd_id, old_val, new_val) for LCDs that changed (or all if old is None).
        lcd_id is 'lcd1'..'lcd4'.
        """
        if old is None:
            return [(f"lcd{i+1}", None, (new[i] or "")) for i in range(len(new))]
        out = []
        n = max(len(old), len(new))
        for i in range(n):
            o = (old[i] if i < len(old) else "") or ""
            c = (new[i] if i < len(new) else "") or ""
            if o != c:
                out.append((f"lcd{i+1}", o, c))
        return out

    # ---------- MQTT ----------
    def _init_mqtt_publisher(self):
        mqc = self.mqtt_cfg or {}
        host = (mqc.get("host") or "").strip()
        base = (mqc.get("topic_base") or "").strip().rstrip("/")
        if host and base:
            self._pub = get_publisher(mqc, client_id="firepi-panel")
            self._topic_status  = f"{base}/panel/status"
            self._topic_led_evt = f"{base}/panel/events/alert_state_change"
            self._topic_lcd_evt = f"{base}/panel/events/lcd_state_change"
            self.app.logger.info("PanelMonitor: MQTT ready (host=%s, base=%s)", host, base)
        else:
            self.app.logger.info("PanelMonitor: MQTT not enabled")

    # ---------- worker ----------
    def _run(self):
        # Lower CPU priority a touch (best-effort)
        try:
            os.nice(5)
        except Exception:
            pass

        # Config + camera
        self.reload_rois()
        cap, picam2 = _open_camera(self.use_picamera2)

        last_snap_ts = 0.0
        snap_interval = 1.0 / max(1, int(self.snapshot_hz))
        JPEG_QUALITY = 70

        try:
            while not self._stop.is_set():
                frame = _read_frame(cap, picam2)
                now = time.time()

                # --- snapshot for UI (throttled) ---
                if now - last_snap_ts >= snap_interval:
                    try:
                        ok, enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                        if ok:
                            with self._lock:
                                self._last_jpeg = enc.tobytes()
                        last_snap_ts = now
                    except Exception:
                        pass  # non-fatal

                cfg = getattr(self, "_cfg", {}) or {}

                inverted = bool(cfg.get("lcd_inverted", False))
                digits = int(cfg.get("digit_count_per_lcd", 4))
                seg_frac_thr = float(cfg.get("seg_frac_thr", 0.10))
                led_thr = cfg.get("led_thr", {"sat": 110, "val": 120, "frac": 0.12})
                sign_thr = cfg.get("sign_thr", {"val": 140, "sat_min": 30, "frac": 0.08})
                lcds_cfg = cfg.get("lcd_rois", {}) or {}
                sign_cfg = cfg.get("lcd_sign_rois", {}) or {}
                leds_cfg = cfg.get("led_rois", {}) or {}

                # --- read LCDs (digits only) ---
                lcd_digits: List[str] = []
                for key in ("lcd1", "lcd2", "lcd3", "lcd4"):
                    roi = lcds_cfg.get(key)
                    if not roi:
                        lcd_digits.append("")
                        continue
                    try:
                        val = _read_lcd(
                            _crop(frame, roi),
                            digits=digits,
                            inverted=inverted,
                            frac_thr=seg_frac_thr,
                        )
                    except Exception:
                        val = ""
                    lcd_digits.append(val)

                # --- sign ROIs (minus indicators) ---
                signs_on: Dict[str, bool] = {}
                for key in ("lcd1", "lcd2", "lcd3", "lcd4"):
                    sroi = sign_cfg.get(key)
                    if not sroi:
                        signs_on[key] = False
                        continue
                    try:
                        signs_on[key] = _roi_bright_on_black(
                            _crop(frame, sroi),
                            val_thr=int(sign_thr.get("val", 140)),
                            sat_min=int(sign_thr.get("sat_min", 30)),
                            frac_thr=float(sign_thr.get("frac", 0.08)),
                        )
                    except Exception:
                        signs_on[key] = False

                # Combine sign + digits
                lcd_vals: List[str] = []
                for i, key in enumerate(("lcd1", "lcd2", "lcd3", "lcd4")):
                    base = (lcd_digits[i] if i < len(lcd_digits) else "") or ""
                    sign = "-" if signs_on.get(key, False) else ""
                    lcd_vals.append((sign + base).strip())

                # --- read LEDs (red OR green on black) ---
                led_states: Dict[str, bool] = {}
                for name, roi in (leds_cfg or {}).items():
                    if not roi:
                        led_states[name] = False
                        continue
                    try:
                        led_states[name] = bool(
                            _led_on_any(
                                _crop(frame, roi),
                                sat_thr=int(led_thr.get("sat", 110)),
                                val_thr=int(led_thr.get("val", 120)),
                                frac_thr=float(led_thr.get("frac", 0.12)),
                            )
                        )
                    except Exception:
                        led_states[name] = False

                # update latest for UI
                payload = {"lcds": lcd_vals, "leds": led_states, "ts": now}
                with self._lock:
                    self._latest = payload

                # ----- MQTT: per-change events + retained status -----
                # LEDs
                for name, _old, newv in self._leds_diff(self._last_pub_leds, led_states):
                    try:
                        if self._pub:
                            self._pub.publish_json(
                                self._topic_led_evt,
                                {"ts": int(now), "name": name, "value": "on" if newv else "off"},
                                qos=0, retain=False
                            )
                    except Exception:
                        self.app.logger.exception("PanelMonitor: MQTT LED event publish failed")

                # LCDs
                for lcd_id, _old, newv in self._lcds_diff(self._last_pub_lcds, lcd_vals):
                    try:
                        if self._pub:
                            self._pub.publish_json(
                                self._topic_lcd_evt,
                                {"ts": int(now), "id": lcd_id, "value": newv or ""},
                                qos=0, retain=False
                            )
                    except Exception:
                        self.app.logger.exception("PanelMonitor: MQTT LCD event publish failed")

                # Retained status if anything changed (first run publishes)
                changed_any = (
                    self._last_pub_leds is None
                    or self._last_pub_lcds is None
                    or bool(self._leds_diff(self._last_pub_leds, led_states))
                    or bool(self._lcds_diff(self._last_pub_lcds, lcd_vals))
                )
                if changed_any:
                    try:
                        self._pub.publish_json(self._topic_status, payload, qos=0, retain=True)
                    except Exception:
                        self.app.logger.exception("PanelMonitor: MQTT status publish failed")
                    self._last_pub_leds = dict(led_states)
                    self._last_pub_lcds = list(lcd_vals)

                # pacing
                time.sleep(self.period)

        finally:
            if picam2:
                try:
                    picam2.stop()
                except Exception:
                    pass
            if cap:
                try:
                    cap.release()
                except Exception:
                    pass
            if self._pub:
                try:
                    self._pub.close()
                except Exception:
                    pass