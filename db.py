from flask_sqlalchemy import SQLAlchemy
import json
from datetime import datetime, timezone
from sqlalchemy import text

db = SQLAlchemy()

class Recipient(db.Model):
    __tablename__ = "recipients"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(50), nullable=True)
    email = db.Column(db.String(200), nullable=True)
    receive_sms = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class Settings(db.Model):
    __tablename__ = "settings"
    id = db.Column(db.Integer, primary_key=True)
    # Global toggles
    enable_speaker_alert = db.Column(db.Boolean, default=False, nullable=False)
    enable_phone_alert   = db.Column(db.Boolean, default=False, nullable=False)
    enable_email_alert   = db.Column(db.Boolean, default=False, nullable=False)
    enable_sms_alert     = db.Column(db.Boolean, default=False, nullable=False)
    # SMTP
    smtp_server     = db.Column(db.String(255), nullable=True)
    smtp_port       = db.Column(db.Integer, nullable=True)
    smtp_username   = db.Column(db.String(255), nullable=True)
    smtp_password   = db.Column(db.String(255), nullable=True)
    smtp_notify_text= db.Column(db.String(255), nullable=True)
    # Provider
    telephony_provider = db.Column(db.String(255), nullable=True)
    # Twilio
    twilio_username   = db.Column(db.String(255), nullable=True) # Account SID (AC…)
    twilio_token      = db.Column(db.String(255), nullable=True) # API Key SID (SK…)
    twilio_api_secret = db.Column(db.String(255), nullable=True) # API Key Secret
    twilio_source_number = db.Column(db.String(255), nullable=True)
    twilio_notify_text= db.Column(db.String(255), nullable=True)
    # ClickSend
    clicksend_username   = db.Column(db.String(255), nullable=True)
    clicksend_api_key      = db.Column(db.String(255), nullable=True)
    clicksend_from = db.Column(db.String(255), nullable=True)
    clicksend_voice_from = db.Column(db.String(255), nullable=True)
    clicksend_notify_text= db.Column(db.String(255), nullable=True)
    # MQTT
    mqtt_host       = db.Column(db.String(255), nullable=True)
    mqtt_user       = db.Column(db.String(255), nullable=True)
    mqtt_password   = db.Column(db.String(255), nullable=True)
    mqtt_topic_base = db.Column(db.String(255), nullable=True)
    # Audio
    solenoid_activated_audio   = db.Column(db.String(255), nullable=True)
    solenoid_deactivated_audio = db.Column(db.String(255), nullable=True)

class AlertHistory(db.Model):
    __tablename__ = "alert_history"
    id           = db.Column(db.Integer, primary_key=True)
    ts           = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    alert_type   = db.Column(db.String(50), nullable=False)
    sensor       = db.Column(db.String(50), nullable=True)
    sensor_val   = db.Column(db.String(50), nullable=True)
    channel      = db.Column(db.String(50), nullable=False)
    status       = db.Column(db.String(20), nullable=False)
    error_text   = db.Column(db.Text, nullable=True)
    payload_json = db.Column(db.Text, nullable=True)

def log_alert_history(alert_type:str, sensor:str, sensor_val:str, channel:str, status:str, error_text:str|None=None, payload:dict|None=None):
    entry = AlertHistory(
        alert_type=alert_type, sensor=sensor, sensor_val=sensor_val,
        channel=channel, status=status,
        error_text=(error_text or None),
        payload_json=(json.dumps(payload) if payload else None),
    )
    db.session.add(entry)
    db.session.commit()

def get_or_create_settings() -> Settings:
    s = Settings.query.get(1)
    if not s:
        s = Settings(id=1)
        db.session.add(s)
        db.session.commit()
    return s

def alert_history_as_dict(a) -> dict:
    ts = a.ts
    if ts is not None and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    
    ts_iso = ts.isoformat(timespec="milliseconds").replace("+00:00", "Z") if ts else None
    ts_epoch = int(ts.timestamp()) if ts else None

    return {
        "id": a.id,
        "ts": ts_iso,
        "alert_type": a.alert_type,
        "sensor": a.sensor,
        "sensor_val": a.sensor_val,
        "channel": a.channel,
        "status": a.status,
        "error_text": a.error_text,
    }

def settings_as_dict(s: Settings) -> dict:
    return {
        "enable_speaker_alert": s.enable_speaker_alert,
        "enable_phone_alert":   s.enable_phone_alert,
        "enable_email_alert":   s.enable_email_alert,
        "enable_sms_alert":     s.enable_sms_alert,
        "telephony_provider":   s.telephony_provider,
        "smtp": {
            "server": s.smtp_server,
            "port": s.smtp_port,
            "username": s.smtp_username,
            "password": s.smtp_password,
            "notify_text": s.smtp_notify_text,
        },
        "twilio": {
            "username": s.twilio_username,
            "token": s.twilio_token,
            "api_secret": s.twilio_api_secret,
            "source_number": s.twilio_source_number,
            "notify_text": s.twilio_notify_text,
        },
        "mqtt": {
            "host": s.mqtt_host,
            "username": s.mqtt_user,
            "password": s.mqtt_password,
            "topic_base": s.mqtt_topic_base,
        },
        "solenoid_activated_audio": s.solenoid_activated_audio,
        "solenoid_deactivated_audio": s.solenoid_deactivated_audio,
        "recipients": recipients_as_list(),
    }

def recipient_as_dict(r: Recipient) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "phone": r.phone,
        "email": r.email,
        "receive_sms": bool(r.receive_sms),
        "created_at": r.created_at.isoformat(),
    }

def recipients_as_list() -> list[dict]:
    recs = Recipient.query.order_by(Recipient.name.asc()).all()
    return [recipient_as_dict(r) for r in recs]

def load_settings_dict() -> dict:
    return settings_as_dict(get_or_create_settings())

def init_db(app):
    db.init_app(app)
    with app.app_context():
        db.create_all()
        get_or_create_settings()