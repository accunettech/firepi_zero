from __future__ import annotations
import json, logging
from datetime import datetime
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt


CLIENT_ID = "firepi"


def publish_event(cfg: dict, *, sensor: str, value: str, logger: logging.Logger | None = None, tz: str = "America/New_York") -> None:
    """
    Publish a simple sensor event. cfg keys:
      host: "hostname[:port]"
      username, password (optional)
      topic_base
    """
    log = logger or logging.getLogger(__name__)
    if not cfg:
        return

    host = (cfg.get("host") or "").strip()
    base = (cfg.get("topic_base") or "").strip()
    if not host or not base:
        return

    topic = f"{base.rstrip('/')}/events/{sensor}"

    parts = host.split(":", 1)
    hst = parts[0].strip()
    try:
        port = int(parts[1]) if len(parts) == 2 else 1883
    except ValueError:
        port = 1883

    try:
        client = mqtt.Client(client_id=CLIENT_ID)
        if cfg.get("username"):
            client.username_pw_set(cfg.get("username"), cfg.get("password") or None)
        client.connect(hst, port, 60)

        local_ts = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M:%S")
        payload = {"ts": local_ts, "type": "Sensor", "sensor": sensor, "value": value}
        client.publish(topic, json.dumps(payload), qos=0, retain=False)
        client.disconnect()
    except Exception as e:
        log.info("[MQTT] Failed to publish event: %s", e)
