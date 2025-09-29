# services/solenoid_monitor.py
from __future__ import annotations
import logging, time, signal, atexit
from typing import Optional
from gpiozero import Button
from db import log_alert_history, load_settings_dict
from .notification import (
    play_audio_pwm_async,
    send_email,
    provider_call_out,
    provider_send_sms,
)

from .mqtt_pub import get_publisher

try:
    from flask import Flask  # type: ignore
except Exception:
    Flask = object  # type: ignore


class SolenoidMonitor:
    """
    RIB dry contact:
      GPIO25 <- Orange (NO)
      GND    <- Yellow (COM)

    - Uses internal pull-up on GPIO25 (HIGH=open=OFF, LOW=closed=ON)
    - No internal worker thread; gpiozero handles edges.
    - MQTT is REQUIRED and must be initialized by the app before start().
    """

    def __init__(
        self,
        app: Optional["Flask"] = None,
        pin: int = 25,
        bounce_time: float = 0.05,
        off_delay_s: float = 2.0,
        min_alert_interval_s: int = 10,
    ):
        self.app = app
        self.pin = pin
        self.bounce_time = bounce_time
        self.off_delay_s = off_delay_s
        self.min_alert_interval_s = min_alert_interval_s

        self._btn: Optional[Button] = None
        self.started = False

        self._boot_ts = 0.0
        self._last_change_ts = 0.0
        self._last_alert_ts: float = 0.0
        self._last_state: Optional[str] = None  # "ON"/"OFF"

        self._log = (app.logger if app and hasattr(app, "logger") else logging.getLogger(__name__))

        # MQTT (provided by app)
        self._pub = None
        self._base = None
        self._topic_state = None   # base/solenoid/state   (non-retained)
        self._topic_status = None  # base/solenoid/status  (retained)

        # graceful stop hook (only registered when start() succeeds)
        self._sig_reg = False

    # ---------- public API ----------
    def external_alert(self, sensor: str, sensor_description: str, sensor_val: str, alert_text: str, force: bool = False):
        cfg = self._load_cfg()
        self._send_alert_sequence(cfg, sensor, sensor_description, sensor_val, alert_text, force)

    def start(self):
        if self.started:
            return

        if not self.app:
            raise RuntimeError("SolenoidMonitor requires Flask app")

        # MQTT must already be initialized by the app:
        # - app.extensions['mqtt_publisher'] is a connected publisher
        # - app.config['MQTT_TOPIC_BASE'] is the base topic
        try:
            self._pub = get_publisher(self.app)
            self._pub_enabled = True
            self._base = (self.app.config.get("MQTT_TOPIC_BASE") or "").rstrip("/")
            self._topic_state  = f"{self._base}/solenoid/state"
            self._topic_status = f"{self._base}/solenoid/status"
        except Exception as e:
            self._pub_enabled = False
            logging.info(f"MQTT not connected: {e}")

        # Init GPIO
        try:
            self._btn = Button(self.pin, pull_up=True, bounce_time=self.bounce_time)
        except Exception as e:
            self._log.exception("Failed to init GPIO Button on pin %s: %s", self.pin, e)
            raise

        # Baseline current state BEFORE wiring callbacks
        try:
            cur_pressed = bool(self._btn.is_pressed)
        except Exception:
            cur_pressed = False

        self._last_state = "ON" if cur_pressed else "OFF"
        self._last_change_ts = time.time()
        self._boot_ts = time.time()
        self._log.info("Fuel solenoid initial state: %s", self._last_state)

        # Publish lifecycle + initial state
        if self._pub_enabled:
            self._publish_status("started")
            self._publish_state_change(self._last_state, initial=True)

        # Wire callbacks
        self._btn.when_pressed  = self._on_change   # closed -> ON
        self._btn.when_released = self._on_change   # open   -> OFF
        self.started = True
        self._log.info("Solenoid Monitor started on GPIO %s", self.pin)

        # Ensure stop() runs on SIGTERM/SIGINT and interpreter exit
        if not self._sig_reg:
            try:
                signal.signal(signal.SIGTERM, self._on_sig_exit)
                signal.signal(signal.SIGINT, self._on_sig_exit)
            except Exception:
                pass
            atexit.register(self._atexit_stop)
            self._sig_reg = True

        # Startup audio cue
        play_audio_pwm_async(
            "startup_solenoid_activated.mp3" if cur_pressed else "startup_solenoid_deactivated.mp3",
            is_stock_audio=True,
            logger=self._log
        )

    def stop(self):
        # lifecycle event first (best effort)
        try:
            if self._pub_enabled:
                self._publish_status("stopped")
        except Exception:
            pass

        play_audio_pwm_async("shutdown.mp3", is_stock_audio=True, logger=self._log)
        try:
            if self._btn:
                self._btn.close()
        except Exception:
            pass
        self.started = False

        # DO NOT close the global MQTT publisher here (app owns it)
        self._log.info("Solenoid Monitor stopped.")

    def health(self) -> dict:
        now = time.time()
        return {
            "status": "ok",
            "uptime_s": int(now - (self._boot_ts or now)),
            "pin": self.pin,
            "state": self._last_state,
            "last_change_ts": self._last_change_ts or None,
            "last_alert_ts": self._last_alert_ts or None,
            "rate_limit_remaining_s": (
                max(0, int(self.min_alert_interval_s - (now - self._last_alert_ts)))
                if self._last_alert_ts else 0
            ),
        }

    # ---------- internals ----------
    def _on_sig_exit(self, *_):
        try:
            self.stop()
        except Exception:
            pass

    def _atexit_stop(self):
        try:
            if self.started:
                self.stop()
        except Exception:
            pass

    def _load_cfg(self) -> dict:
        try:
            if self.app is not None:
                with self.app.app_context():
                    return load_settings_dict()
            return load_settings_dict()
        except Exception as e:
            self._log.warning("Config load failed; using defaults. %s", e, exc_info=True)
            return {}

    # ---- MQTT publish wrappers ----
    def _publish_status(self, status: str):
        """Publish service lifecycle: started/stopped (retained)."""
        if self._pub and self._topic_status:
            payload = {
                "event": "service_status",
                "service": "solenoid_monitor",
                "status": status,            # "started" | "stopped"
                "ts": int(time.time()),
            }
            self._pub.publish_json(self._topic_status, payload, qos=0, retain=True)
            self._log.info("SolenoidMonitor: status -> %s", status)

    def _publish_state_change(self, state: str, initial: bool = False):
        """Publish ON/OFF change (not retained)."""
        if self._pub and self._topic_state:
            payload = {
                "event": "solenoid_state_change",
                "state": state,              # "ON" | "OFF"
                "initial": bool(initial),
                "ts": int(time.time()),
            }
            self._pub.publish_json(self._topic_state, payload, qos=0, retain=False)
            self._log.info("SolenoidMonitor: state -> %s%s", state, " (initial)" if initial else "")

    # ---------- GPIO change handling ----------
    def _on_change(self):
        sensor_description = "Fuel solenoid"
        try:
            state = "ON" if (self._btn and self._btn.is_pressed) else "OFF"
            if state == self._last_state:
                return

            prev = self._last_state
            self._last_state = state
            self._last_change_ts = time.time()
            self._log.info("%s state %s -> %s", sensor_description, prev, state)

            # MQTT state-change event
            self._publish_state_change(state)

            # Handle alert/speaker/email/sms/voice logic
            cfg = self._load_cfg()
            self._handle_state_change(cfg, sensor="fuel_solenoid", sensor_description=sensor_description, state=state)

        except Exception as e:
            self._log.exception("Error handling GPIO change: %s", e)

    # ---------- alert / notification path ----------
    def _log_alert_history(
        self,
        alert_type: str,
        sensor: str,
        sensor_val: str,
        channel: str,
        status: str,
        error_text: str | None = None,
        payload: dict | None = None,
    ):
        try:
            with self.app.app_context():
                log_alert_history(
                    alert_type=alert_type,
                    sensor=sensor,
                    sensor_val=sensor_val,
                    channel=channel,
                    status=status,
                    error_text=error_text,
                    payload=payload,
                )
        except Exception:
            self._log.warning("Alert history write failed.", exc_info=True)

    def _handle_state_change(self, cfg: dict, sensor: str, sensor_description: str, state: str):
        self._log.info(f"{sensor} ({sensor_description}) is now {state}")

        if state == "ON":
            if cfg.get("enable_speaker_alert"):
                try:
                    play_audio_pwm_async(cfg.get("solenoid_activated_audio"), logger=self._log)
                    self._log.info("[SPEAKER] activation sound queued")
                    self._log_alert_history("Notification", sensor, state, "speaker", "success")
                except Exception as e:
                    self._log_alert_history("Notification", sensor, state, "speaker", "error", str(e))
            return

        self._send_alert_sequence(
            cfg,
            sensor=sensor,
            sensor_description=sensor_description,
            sensor_val=state,
            alert_text="ALARM: Gas solenoid de-energized (burner down)",
            force=False,
        )

    def _send_alert_sequence(self, cfg: dict, sensor: str, sensor_description: str, sensor_val: str, alert_text: str, force: bool = False):
        now = time.time()
        if self._last_alert_ts and (now - self._last_alert_ts) < self.min_alert_interval_s:
            self._log.info(
                "Alert suppressed by rate-limit (%.0fs remaining)",
                self.min_alert_interval_s - (now - self._last_alert_ts),
            )
            return

        # OFF debounce (optional)
        if not force and (sensor_val == "OFF" and self.off_delay_s > 0):
            time.sleep(self.off_delay_s)
            try:
                curr = "ON" if (self._btn and self._btn.is_pressed) else "OFF"
                if curr != "OFF":
                    self._log.info("OFF debounce failed (now %s); aborting alert.", curr)
                    return
            except Exception:
                pass

        self._last_alert_ts = now

        # Speaker (non-blocking)
        if cfg.get("enable_speaker_alert"):
            try:
                play_audio_pwm_async(cfg.get("solenoid_deactivated_audio"), logger=self._log)
                self._log.info("[SPEAKER] queued")
                self._log_alert_history("Notification", sensor, sensor_val, "speaker", "success")
            except Exception as e:
                self._log.info(f"[SPEAKER] FAILED: {e}")
                self._log_alert_history("Notification", sensor, sensor_val, "speaker", "error", str(e))
        else:
            self._log.info("[SPEAKER] disabled. Skipping")
            self._log_alert_history("Notification", sensor, sensor_val, "speaker", "skipped")

        recipients = cfg.get("recipients") or []

        # PHONE
        if cfg.get("enable_phone_alert"):
            try:
                call_res = provider_call_out(cfg, message=alert_text, recipients=recipients)
                prov_log = (cfg.get("telephony_provider") or cfg.get("provider") or "twilio")
                self._log.info("[PHONE] processed with provider %s: %s", prov_log, call_res)
                self._log_alert_history("Notification", sensor, sensor_val, "phone", "success")
            except Exception as e:
                self._log.info(f"[PHONE] FAILED: {e}")
                self._log_alert_history("Notification", sensor, sensor_val, "phone", "error", str(e))
        else:
            self._log.info("[PHONE] disabled. Skipping")
            self._log_alert_history("Notification", sensor, sensor_val, "phone", "skipped")

        # EMAIL
        if cfg.get("enable_email_alert"):
            try:
                email_res = send_email(cfg.get("smtp") or {}, recipients)
                self._log.info("[EMAIL] processed: %s", email_res)
                self._log_alert_history("Notification", sensor, sensor_val, "email", "success")
            except Exception as e:
                self._log.info(f"[EMAIL] FAILED: {e}")
                self._log_alert_history("Notification", sensor, sensor_val, "email", "error", str(e))
        else:
            self._log.info("[EMAIL] disabled. Skipping")
            self._log_alert_history("Notification", sensor, sensor_val, "email", "skipped")

        # SMS
        if cfg.get("enable_sms_alert"):
            try:
                sms_res = provider_send_sms(cfg, body=alert_text, recipients=recipients)
                self._log.info("[SMS] processed: %s", sms_res)
                self._log_alert_history("Notification", sensor, sensor_val, "sms", "success")
            except Exception as e:
                self._log.info(f"[SMS] FAILED: {e}")
                self._log_alert_history("Notification", sensor, sensor_val, "sms", "error", str(e))
        else:
            self._log.info("[SMS] disabled. Skipping")
            self._log_alert_history("Notification", sensor, sensor_val, "sms", "skipped")

    # -------- Public test trigger --------
    def test_alerts(self, message: str | None = None) -> None:
        cfg = self._load_cfg()
        if message:
            try:
                if "smtp" in cfg and isinstance(cfg["smtp"], dict):
                    cfg["smtp"] = dict(cfg["smtp"], notify_text=message)
                if "twilio" in cfg and isinstance(cfg["twilio"], dict):
                    cfg["twilio"] = dict(cfg["twilio"], notify_text=message)
                if "clicksend" in cfg and isinstance(cfg["clicksend"], dict):
                    cfg["clicksend"] = dict(cfg["clicksend"], notify_text=message)
            except Exception:
                pass

        self._send_alert_sequence(
            cfg,
            sensor="TEST_SENSOR",
            sensor_description="Test Trigger",
            sensor_val="OFF",
            alert_text=message or "TEST: Furnace alert system check",
            force=True,
        )