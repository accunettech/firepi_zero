from __future__ import annotations
import os, atexit, signal, logging
from pathlib import Path
from flask import Flask
from logging.handlers import TimedRotatingFileHandler
from services.sse import SseHub
from db import db, init_db, get_or_create_settings
from services.mqtt_pub import init_global_publisher
from blueprints.config_ui import bp as config_ui_bp
from blueprints.fileops import bp as fileops_bp
from services.solenoid_monitor import SolenoidMonitor
from services.panel_snapshot import PanelSnapshot
# from services.panel_monitor import PanelMonitor

def configure_logging(log_dir: str) -> str:
    level = os.getenv("FIREPI_LOG_LEVEL", "INFO").upper()
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "app.log")

    root = logging.getLogger()
    root.setLevel(level)

    # avoid duplicate handlers if reloaded
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(threadName)s: %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    file_h = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        backupCount=30,
        encoding="utf-8",
        delay=True,
        utc=False,
    )
    file_h.setFormatter(fmt)
    file_h.setLevel(level)
    root.addHandler(file_h)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(level)
    root.addHandler(console)

    return log_dir


def _graceful_shutdown(signum, frame):
    logging.info("Received signal %s -> graceful shutdown", signum)
    try:
        if hasattr(app, "cleanup_monitors"):
            app.cleanup_monitors()
    finally:
        raise SystemExit(0)


def create_app() -> Flask:
    app = Flask(__name__)
    logDir = os.getenv("FIREPI_LOG_DIR") or os.path.join(os.path.dirname(__file__), "logs")
    configure_logging(logDir)
    app.logger.setLevel(logging.INFO)
    os.environ.setdefault("LIBCAMERA_LOG_LEVELS", "*:ERROR")

    for name in ("picamera2", "picamera2.picamera2"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.WARNING)
        lg.propagate = False

    app.config["SECRET_KEY"] = os.getenv("FIREPI_UPLOAD_TOKEN", "").strip()

    vf = Path(app.root_path) / "VERSION"
    if vf.exists():
        app.config["APP_VERSION"] = vf.read_text(encoding="utf-8").strip()
    else:
        app.config["APP_VERSION"] = "dev"

    logging.info(f"Starting PiFire v{app.config['APP_VERSION']}")

    os.makedirs(app.instance_path, exist_ok=True)
    db_path = os.path.join(app.instance_path, "alerting.db")
    rois_path = os.path.join(app.instance_path, "panel_rois.yaml")
    app.config["PANEL_ROIS_PATH"] = rois_path
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["LOG_DIR"] = logDir
    app.config["MUTE_STATUS_SOUNDS"] = os.getenv("MUTE_STATUS_SOUNDS", "false").lower() == "true"
    app.config["CAMERA_SRC"] = int(os.getenv("CAMERA_SRC", "0"))

    init_db(app)

    app.register_blueprint(config_ui_bp)
    app.register_blueprint(fileops_bp)

    app.sse_hub = SseHub(keepalive_s=25.0)

    @app.cli.command("init-db")
    def init_db_command():
        with app.app_context():
            db.create_all()
            get_or_create_settings()
        print("Database initialized.")

    try:
        app.extensions = getattr(app, "extensions", {})

        with app.app_context():
            s = get_or_create_settings()
            mqtt_cfg = { "host": s.mqtt_host, "username": s.mqtt_user, "password": s.mqtt_password, "topic_base": s.mqtt_topic_base }
            app.config['MQTT_TOPIC_BASE'] = mqtt_cfg.get("topic_base") or None

        try:
            init_global_publisher(app, mqtt_cfg, client_id="firepi-main", timeout_s=10)
        except Exception as e:
            logging.info(f"MQTT connect failed: {e}")

        # SolenoidMonitor
        sm = app.extensions.get("solenoid_monitor")
        if sm is None:
            sm = SolenoidMonitor(
                app=app,
                pin=int(os.getenv("SOLENOID_GPIO", "25")),
                bounce_time=0.05,
                mute_status_sounds=app.config.get("MUTE_STATUS_SOUNDS"),
            )
            app.extensions["solenoid_monitor"] = sm

        # PanelMonitor (disabled as requested)
        # pm = app.extensions.get("panel_monitor")
        # if pm is None:
        #     pm = PanelMonitor(
        #         app=app,
        #         rois_path=rois_path,
        #         use_picamera2=bool(int(os.getenv("USE_PICAMERA2", "1"))),
        #         fps=float(os.getenv("PANEL_FPS", "2.0")),
        #     )
        #     app.extensions["panel_monitor"] = pm

        # PanelSnapshot (new)
        ps = app.extensions.get("panel_snapshot")
        if ps is None:
            ps = PanelSnapshot(app=app, interval=5.0)
            app.extensions["panel_snapshot"] = ps

        # Cleanup hook
        def _cleanup_monitors():
            app.logger.info("Cleanup: stopping monitors")
            for key in ("panel_monitor", "solenoid_monitor", "panel_snapshot"):
                mon = app.extensions.get(key)
                if mon and hasattr(mon, "stop"):
                    try:
                        mon.stop()
                    except Exception:
                        app.logger.exception("Error stopping %s", key)

        app.cleanup_monitors = _cleanup_monitors

        # Start services only in the serving process
        is_real_runner = (os.environ.get("WERKZEUG_RUN_MAIN") == "true") or (not app.debug)

        if is_real_runner:
            if not getattr(sm, "started", False):
                sm.start()

            # if not getattr(pm, "started", False):
            #     app.logger.info("Starting panel monitor...")
            #     pm.start()

            if not getattr(ps, "started", False):
                app.logger.info("Starting panel snapshot processor...")
                ps.start()

        atexit.register(_cleanup_monitors)

    except Exception as e:
        logging.exception("Service init error: %s", e)

    app.logger.info("[boot] FirePi app initialized (instance=%s)", app.instance_path)
    return app


if __name__ == "__main__":
    app = create_app()
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT,  _graceful_shutdown)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True, use_reloader=False)
