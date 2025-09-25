# services/panel_monitor.py
from __future__ import annotations

import os
import time
import json
import threading
from typing import Optional, Dict, Any
import yaml
import numpy as np
import cv2

cv2.setNumThreads(1)
try:
    cv2.ocl.setUseOpenCL(False)
except Exception:
    pass

# Keep NumPy/BLAS from spinning up a bunch of worker threads
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
        # Try to keep only a single buffered frame
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        if not cap.isOpened():
            raise RuntimeError("Cannot open /dev/video0. Is the camera enabled and attached?")
        return cap, None
    else:
        # Lazy import to avoid Pi without Picamera2 installed
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


# ---------------- 7-seg OCR ----------------
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
}


def _segments_from_digit_roi(gray: np.ndarray, inverted: bool = True, thr: float = 0.55):
    """
    Return tuple of 7 ints indicating whether each segment is lit.
    For LCDs with dark lit segments: inverted=True means low intensity => ON.
    """
    if gray is None or gray.size == 0:
        return (0, 0, 0, 0, 0, 0, 0)

    h, w = gray.shape[:2]
    t = max(1, int(h * 0.18))  # segment thickness
    m = max(1, int(h * 0.05))  # margin

    regions = [
        (m, m, w - m, m + t),  # top
        (m, m, m + t, h // 2),  # upper-left
        (w - m - t, m, w - m, h // 2),  # upper-right
        (m, h // 2 - t // 2, w - m, h // 2 + t // 2),  # middle
        (m, h // 2, m + t, h - m),  # lower-left
        (w - m - t, h // 2, w - m, h - m),  # lower-right
        (m, h - m - t, w - m, h - m),  # bottom
    ]

    segs = []
    for (x1, y1, x2, y2) in regions:
        y1c, y2c = max(y1, 0), min(y2, h)
        x1c, x2c = max(x1, 0), min(x2, w)
        if x2c <= x1c or y2c <= y1c:
            segs.append(0)
            continue
        roi = gray[y1c:y2c, x1c:x2c]
        mean = roi.mean() / 255.0
        on = (mean < (1.0 - thr)) if inverted else (mean > thr)
        segs.append(1 if on else 0)
    return tuple(segs)


def _decode_digit(gray_digit: np.ndarray, inverted: bool = True, thr: float = 0.55) -> str:
    segs = _segments_from_digit_roi(gray_digit, inverted, thr)
    return SEG_TO_DIGIT.get(segs, "?")


def _read_lcd(gray_lcd: np.ndarray, digits: int = 4, inverted: bool = True, thr: float = 0.55) -> str:
    if gray_lcd is None or gray_lcd.size == 0:
        return ""
    h, w = gray_lcd.shape[:2]
    if w <= 0 or digits <= 0:
        return ""

    step = max(1, w // digits)
    out = []
    for i in range(digits):
        x1 = i * step
        x2 = (i + 1) * step if i < digits - 1 else w
        digit_roi = gray_lcd[:, x1:x2]
        out.append(_decode_digit(digit_roi, inverted=inverted, thr=thr))
    return "".join(out)


# ---------------- LED detection ----------------
def _led_on(bgr_roi: np.ndarray, sat_thr: int = 110, val_thr: int = 120) -> bool:
    """
    Detect bright red LED via HSV; supports red wrap-around (0..10 and 170..180).
    Returns True if a sufficient fraction of pixels are red.
    """
    if bgr_roi is None or bgr_roi.size == 0:
        return False
    hsv = cv2.cvtColor(bgr_roi, cv2.COLOR_BGR2HSV)
    lower1 = np.array([0, sat_thr, val_thr], dtype=np.uint8)
    upper1 = np.array([10, 255, 255], dtype=np.uint8)
    lower2 = np.array([170, sat_thr, val_thr], dtype=np.uint8)
    upper2 = np.array([180, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower1, upper1) | cv2.inRange(hsv, lower2, upper2)
    frac = (mask > 0).mean()
    return frac > 0.12  # tune threshold if needed


# ---------------- Monitor service ----------------
class PanelMonitor:
    """
    Background worker that reads a camera, decodes four LCDs and LED states,
    optionally publishes to MQTT, keeps a JPEG snapshot for the UI, and can
    raise alerts via SolenoidMonitor.external_alert on alarm LED transition.
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
        self.target_fps = int(getattr(app.config, "get", lambda *_: 5)("PANEL_FPS", 5))
        # how often the UI snapshot is refreshed (Hz)
        self.snapshot_hz = int(getattr(app.config, "get", lambda *_: 2)("PANEL_SNAPSHOT_HZ", 2))
        # how often we publish to MQTT (Hz)
        self.publish_hz = int(getattr(app.config, "get", lambda *_: 2)("PANEL_PUBLISH_HZ", 2))
        self.rois_path = rois_path
        self.use_picamera2 = use_picamera2
        self.period = 1.0 / float(max(1.0, fps))
        self.mqtt_cfg = mqtt or {}

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._latest: Dict[str, Any] = {"lcds": ["", "", "", ""], "leds": {}, "ts": 0.0}
        self._cfg: Dict[str, Any] = {}
        self._last_jpeg: Optional[bytes] = None

        self.started = False

    # ---------- lifecycle ----------
    def start(self):
        if self.started:
            return
        # Lower OpenCV threading to reduce CPU on small Pis
        try:
            cv2.setNumThreads(1)
        except Exception:
            pass

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
                self._cfg = yaml.safe_load(f) or {}
        except FileNotFoundError:
            self._cfg = {}

        # Provide safe defaults if keys are missing
        self._cfg.setdefault("lcd_rois", {"lcd1": None, "lcd2": None, "lcd3": None, "lcd4": None})
        self._cfg.setdefault(
            "led_rois",
            {"opr_ctrl": None, "interlck": None, "ptfi": None, "flame": None, "alarm": None},
        )
        self._cfg.setdefault("digit_count_per_lcd", 4)
        self._cfg.setdefault("seg_threshold", 0.55)
        self._cfg.setdefault("lcd_inverted", True)
        self._cfg.setdefault("led_red_thresh", {"sat": 110, "val": 120})

        self.app.logger.info("PanelMonitor: ROIs loaded from %s", self.rois_path)

    def save_rois(self, cfg: Dict[str, Any]):
        """Write new ROIs atomically; then reload."""
        tmp = f"{self.rois_path}.tmp"
        with open(tmp, "w") as f:
            yaml.safe_dump(cfg, f)
        os.replace(tmp, self.rois_path)
        self.reload_rois()
        self.app.logger.info("PanelMonitor: ROIs saved to %s", self.rois_path)

    # ---------- internals ----------
    def _run(self):
        # run the CV worker at a slightly lower scheduler priority
        try:
            os.nice(5)
        except Exception:
            pass

        # MQTT (lazy)
        mc = None
        topic = None
        if self.mqtt_cfg.get("enabled"):
            try:
                import paho.mqtt.client as mqtt  # lazy import
                mc = mqtt.Client()
                mc.connect(self.mqtt_cfg.get("host", "localhost"))
                topic = self.mqtt_cfg.get("topic", "furnace/panel")
                self.app.logger.info("PanelMonitor: MQTT connected to %s", self.mqtt_cfg.get("host", "localhost"))
            except Exception:
                self.app.logger.exception("PanelMonitor: MQTT init failed")
                mc = None
                topic = None

        # Config + camera
        self.reload_rois()
        cap, picam2 = _open_camera(self.use_picamera2)

        prev_alarm = None
        last_snap_ts = 0.0          # throttle JPEG snapshot
        SNAP_INTERVAL_S = 1.0       # one UI snapshot per second
        JPEG_QUALITY = 70           # lighter encode

        try:
            while not self._stop.is_set():
                frame = _read_frame(cap, picam2)

                now = time.time()

                # --- snapshot for UI (throttled) ---
                if now - last_snap_ts >= SNAP_INTERVAL_S:
                    try:
                        ok, enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                        if ok:
                            with self._lock:
                                self._last_jpeg = enc.tobytes()
                        last_snap_ts = now
                    except Exception:
                        pass  # non-fatal

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                cfg = self._cfg or {}

                seg_thr = float(cfg.get("seg_threshold", 0.55))
                inverted = bool(cfg.get("lcd_inverted", True))
                digits = int(cfg.get("digit_count_per_lcd", 4))
                thr_led = cfg.get("led_red_thresh", {"sat": 110, "val": 120})
                lcds_cfg = cfg.get("lcd_rois", {}) or {}
                leds_cfg = cfg.get("led_rois", {}) or {}

                # --- read LCDs ---
                lcd_vals = []
                for key in ("lcd1", "lcd2", "lcd3", "lcd4"):
                    roi = lcds_cfg.get(key)
                    if not roi:
                        lcd_vals.append("")
                        continue
                    try:
                        val = _read_lcd(
                            cv2.cvtColor(_crop(frame, roi), cv2.COLOR_BGR2GRAY),
                            digits=digits,
                            inverted=inverted,
                            thr=seg_thr,
                        )
                    except Exception:
                        val = ""
                    lcd_vals.append(val)

                # --- read LEDs ---
                led_states = {}
                for name, roi in leds_cfg.items():
                    if not roi:
                        led_states[name] = False
                        continue
                    try:
                        led_states[name] = bool(
                            _led_on(
                                _crop(frame, roi),
                                sat_thr=int(thr_led.get("sat", 110)),
                                val_thr=int(thr_led.get("val", 120)),
                            )
                        )
                    except Exception:
                        led_states[name] = False

                payload = {"lcds": lcd_vals, "leds": led_states, "ts": now}
                with self._lock:
                    self._latest = payload

                # MQTT publish (best effort)
                if mc and topic:
                    try:
                        mc.publish(topic, json.dumps(payload), qos=0, retain=True)
                    except Exception:
                        self.app.logger.exception("PanelMonitor: MQTT publish failed")

                # *** CRITICAL: yield CPU ***
                time.sleep(self.period)

        finally:
            if mc:
                try: mc.disconnect()
                except Exception: pass
            if picam2:
                try: picam2.stop()
                except Exception: pass
            if cap:
                try: cap.release()
                except Exception: pass
