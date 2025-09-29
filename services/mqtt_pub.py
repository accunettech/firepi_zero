# services/mqtt_pub.py
from __future__ import annotations
import json
import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

import paho.mqtt.client as mqtt

_LOG = logging.getLogger(__name__)

class MqttPublisher:
    """
    Self-contained persistent MQTT client.
    - Connects synchronously in connect().
    - Runs network loop in background (auto-reconnects).
    - Simple publish helpers (text/json).
    """

    def __init__(
        self,
        *,
        host: str,
        port: int = 1883,
        username: Optional[str] = None,
        password: Optional[str] = None,
        client_id: str = "firepi",
        keepalive: int = 60,
        tls: Optional[Dict[str, Any]] = None,
        will: Optional[Tuple[str, str, int, bool]] = None,  # (topic, payload, qos, retain)
        logger: Optional[logging.Logger] = None,
    ):
        self.host = host
        self.port = int(port or 1883)
        self.username = username
        self.password = password
        self.client_id = client_id
        self.keepalive = keepalive
        self.tls = tls or {}
        self.will = will
        self.log = logger or _LOG

        self._client = mqtt.Client(client_id=self.client_id, clean_session=True)
        self._connected = threading.Event()
        self._stop = threading.Event()

        # Callbacks
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

        # Auth
        if self.username:
            self._client.username_pw_set(self.username, self.password or None)

        # TLS (optional)
        if self.tls.get("enabled"):
            # You can expand this based on your needs
            self._client.tls_set()  # default certs

        # LWT (optional)
        if self.will:
            topic, payload, qos, retain = self.will
            self._client.will_set(topic, payload=payload, qos=qos, retain=retain)

        # Backoff for reconnects
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)

        # Use paho's internal logging if desired
        # self._client.enable_logger(self.log)

    # ---- callbacks ----
    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.log.info("[MQTT] connected to %s:%s", self.host, self.port)
            self._connected.set()
        else:
            self.log.error("[MQTT] connect failed rc=%s", rc)

    def _on_disconnect(self, client, userdata, rc):
        self._connected.clear()
        if rc != mqtt.MQTT_ERR_SUCCESS:
            self.log.warning("[MQTT] disconnected rc=%s (will auto-reconnect if loop is running)", rc)

    # ---- lifecycle ----
    def connect(self, *, timeout_s: int = 10) -> None:
        """
        Synchronous connect. Raises RuntimeError if broker not reachable
        within timeout. Starts background loop on success.
        """
        self._stop.clear()
        try:
            self._client.connect(self.host, self.port, self.keepalive)
        except Exception as e:
            raise RuntimeError(f"MQTT connect error: {e}") from e

        self._client.loop_start()
        if not self._connected.wait(timeout=timeout_s):
            # Stop loop if we didn't complete connect handshake in time
            self._client.loop_stop()
            raise RuntimeError("MQTT connect timeout")

    def close(self) -> None:
        try:
            self._stop.set()
            self._client.disconnect()
        except Exception:
            pass
        finally:
            try:
                self._client.loop_stop()
            except Exception:
                pass

    # ---- publish ----
    def publish(self, topic: str, payload: str | bytes, qos: int = 0, retain: bool = False):
        if not self._connected.is_set():
            # Caller expects MQTT to be up if publisher exists.
            # Raise to surface mis-ordering (start monitors before MQTT).
            raise RuntimeError("MQTT not connected")
        res = self._client.publish(topic, payload=payload, qos=qos, retain=retain)
        # Optionally block for mid result; here we just log failures
        if res.rc not in (mqtt.MQTT_ERR_SUCCESS, mqtt.MQTT_ERR_NO_CONN):
            self.log.warning("[MQTT] publish rc=%s topic=%s", res.rc, topic)
        return res

    def publish_json(self, topic: str, data: Dict[str, Any], qos: int = 0, retain: bool = False):
        return self.publish(topic, json.dumps(data), qos=qos, retain=retain)

# --------- App-level helpers ---------

def _parse_host_port(host: str) -> Tuple[str, int]:
    host = (host or "").strip()
    if ":" in host:
        h, p = host.split(":", 1)
        try:
            return h.strip(), int(p)
        except ValueError:
            return h.strip(), 1883
    return host, 1883

def init_global_publisher(app, cfg: Dict[str, Any], *, client_id: str = "firepi-app", timeout_s: int = 10) -> MqttPublisher:
    """
    Create+connect a single global publisher and store it under app.extensions['mqtt_publisher'].
    Raises RuntimeError on failure (so the app/monitors won't start).
    """
    if not cfg:
        raise RuntimeError("MQTT config missing")

    raw_host = (cfg.get("host") or "").strip()
    topic_base = (cfg.get("topic_base") or "").strip()
    if not raw_host or not topic_base:
        raise RuntimeError("MQTT host/topic_base missing")

    host, port = _parse_host_port(raw_host)
    username = (cfg.get("username") or "").strip() or None
    password = (cfg.get("password") or "").strip() or None

    # Optional retained service status topic (shared pattern)
    status_topic = f"{topic_base.rstrip('/')}/service/status"

    pub = MqttPublisher(
        host=host,
        port=port,
        username=username,
        password=password,
        client_id=client_id,
        will=(status_topic, '{"service":"app","status":"offline"}', 0, True),
        logger=app.logger if hasattr(app, "logger") else None,
    )
    pub.connect(timeout_s=timeout_s)

    # On successful connect, publish "online" app status (retained)
    try:
        pub.publish_json(status_topic, {"service": "app", "status": "online", "ts": int(time.time())}, qos=0, retain=True)
    except Exception:
        app.logger.exception("Failed to publish initial app status")

    app.extensions["mqtt_publisher"] = pub
    app.config["MQTT_TOPIC_BASE"] = topic_base.rstrip("/")
    app.logger.info("MQTT initialized (host=%s:%s, base=%s)", host, port, app.config["MQTT_TOPIC_BASE"])
    return pub

def get_publisher(app) -> MqttPublisher:
    pub = app.extensions.get("mqtt_publisher")
    if not pub:
        raise RuntimeError("Global MQTT publisher not initialized")
    return pub
