from __future__ import annotations
import os, time, threading, yaml, cv2
import numpy as np
from typing import Optional, Dict, Any, List, Tuple
from .mqtt_pub import get_publisher
from services.seg7 import read_lcd_roi

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
        #from picamera2 import controls
        picam2 = Picamera2()
        cfg = picam2.create_video_configuration(main={"size": (1280, 720)})
        picam2.configure(cfg)
        picam2.start()
        # Settle/lock AWB if needed:
        # picam2.set_controls({"AwbMode": controls.AwbMode.Fluorescent})
        # time.sleep(2)
        # meta = picam2.capture_metadata()
        # gains = meta.get("ColourGains")
        # if gains: picam2.set_controls({"AwbEnable": False, "ColourGains": gains})
        return None, picam2


def _read_frame(cap, picam2):
    if picam2 is None:
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError("Camera read failed.")
        return frame  # BGR already
    else:
        rgb = picam2.capture_array()
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)  # normalize to BGR


def _crop(frame: np.ndarray, roi: Dict[str, int]) -> np.ndarray:
    """
    Crop using dict roi = {x1,y1,x2,y2}. Returns empty array if bad ROI.
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
    m2 = cv2.inRange(hsv, np.array([170, sat_thr, val_thr], np.uint8), np.array([180, 255, 255], np.uint8))
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

        # MQTT: init once, fail fast
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
        if 'led_red_thresh' in cfg and 'led_thr' not in cfg:
            t = cfg.get('led_red_thresh') or {}
            cfg['led_thr'] = {'sat': int(t.get('sat',110)), 'val': int(t.get('val',120)), 'frac': 0.12}

        # Provide safe defaults if keys are missing
        cfg.setdefault("lcd_rois", {"lcd1": None, "lcd2": None, "lcd3": None, "lcd4": None})
        cfg.setdefault("lcd_sign_rois", {"lcd1": None, "lcd2": None, "lcd3": None, "lcd4": None})
        cfg.setdefault("led_rois", {
            "opr_ctrl": None, "interlck": None, "ptfi": None, "flame": None, "alarm": None
        })
        cfg.setdefault("digit_count_per_lcd", 4)
        cfg.setdefault("lcd_inverted", False)  # retained for compatibility; not used by the new reader
        cfg.setdefault("seg_threshold", 0.35)     # segment-on threshold for seg7 reader
        cfg.setdefault("lcd_conf_hold", 0.40)  # hold previous value if avg conf below this
        cfg.setdefault("led_thr", {"sat": 110, "val": 120, "frac": 0.12})
        cfg.setdefault("sign_thr", {"val": 140, "sat_min": 30, "frac": 0.08})

        # Color hints per LCD (top row red, bottom row cyan for your case)
        cfg.setdefault("lcd_color_hint", {
            "lcd1": "red",
            "lcd2": "red",
            "lcd3": "cyan",
            "lcd4": "cyan",
        })

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

                digits = int(cfg.get("digit_count_per_lcd", 4))
                led_thr = cfg.get("led_thr", {"sat": 110, "val": 120, "frac": 0.12})
                sign_thr = cfg.get("sign_thr", {"val": 140, "sat_min": 30, "frac": 0.08})
                lcds_cfg = cfg.get("lcd_rois", {}) or {}
                sign_cfg = cfg.get("lcd_sign_rois", {}) or {}
                leds_cfg = cfg.get("led_rois", {}) or {}

                # --- read LCDs (digits only, color-aware) ---
                lcd_digits: list[str] = []
                hints = (cfg.get("lcd_color_hint") or {})
                seg_thr = float(cfg.get("seg_threshold", 0.35))
                digits = int(cfg.get("digit_count_per_lcd", 4))

                for key in ("lcd1","lcd2","lcd3","lcd4"):
                    roi = lcds_cfg.get(key)
                    if not roi:
                        lcd_digits.append(""); continue
                    tile = _crop(frame, roi)
                    val, _ = read_lcd_roi(tile, digits, hints.get(key))
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
                        if self._pub:
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