import os, atexit, signal, logging, threading
from flask import Flask, jsonify
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from db import db, init_db
from blueprints.config_ui import bp as config_ui_bp
from services.solenoid_monitor import SolenoidMonitor
from services.panel_monitor import PanelMonitor


def configure_logging():
    level = os.getenv("FIREPI_LOG_LEVEL", "INFO").upper()
    log_dir = os.getenv("FIREPI_LOG_DIR") or os.path.join(os.path.dirname(__file__), "logs")
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
        when="midnight",       # rotate every day
        backupCount=30,        # keep 30 files
        encoding="utf-8",
        delay=True,            # create file lazily on first write
        utc=False,             # rotate at local midnight
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

def create_app(log_dir: str) -> Flask:
    app = Flask(__name__)
    app.logger.setLevel(logging.INFO)

    # Core config
    vf = Path(app.root_path) / "VERSION"
    if vf.exists():
        app.config["APP_VERSION"] = vf.read_text(encoding="utf-8").strip()
    else:
        app.config["APP_VERSION"] = "dev"
    
    logging.info(f"Starting PiFire v{app.config['APP_VERSION']}")

    os.makedirs(app.instance_path, exist_ok=True)
    db_path = os.path.join(app.instance_path, "alerting.db")
    rois_path = os.path.join(app.instance_path, "panel_rois.yaml")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["LOG_DIR"] = log_dir

    # DB
    init_db(app)

    # Blueprints (routes live here)
    app.register_blueprint(config_ui_bp)

    # Health
    @app.get("/healthz")
    def health():
        return jsonify({"ok": True})

    # CLI
    @app.cli.command("init-db")
    def init_db_command():
        from db import get_or_create_settings
        with app.app_context():
            db.create_all()
            get_or_create_settings()
        print("Database initialized.")

    try:    
        app.extensions = getattr(app, "extensions", {})

        # Reuse if already present (e.g., hot reload)
        sm = app.extensions.get("solenoid_monitor")
        if sm is None:
            sm = SolenoidMonitor(
                app=app,
                pin=int(os.getenv("SOLENOID_GPIO", "25")),
                bounce_time=0.05,
            )
            app.extensions["solenoid_monitor"] = sm
        
        pm = app.extensions.get("panel_monitor")
        if pm is None:
            pm = PanelMonitor(
                app=app,
                rois_path=rois_path,
                use_picamera2=bool(int(os.getenv("USE_PICAMERA2", "1"))),
                fps=float(os.getenv("PANEL_FPS", "2.0")),
                mqtt={
                    "enabled": bool(int(os.getenv("PANEL_MQTT_ENABLED", "0"))),
                    "host": os.getenv("PANEL_MQTT_HOST", "localhost"),
                    "topic": os.getenv("PANEL_MQTT_TOPIC", "furnace/panel"),
                }
            )
            app.extensions["panel_monitor"] = pm

        def _cleanup_monitors():
            app.logger.info("Cleanup: stopping monitors")
            for key in ("panel_monitor", "solenoid_monitor"):
                mon = app.extensions.get(key)
                if mon and hasattr(mon, "stop"):
                    try:
                        mon.stop()
                    except Exception:
                        app.logger.exception("Error stopping %s", key)

        app.cleanup_monitors = _cleanup_monitors

        # Start only in the actual serving process (not the reloader parent)
        if not sm.started and (os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug):
            app.logger.info("Starting solenoid monitor...")
            sm.start()
            atexit.register(_cleanup_monitors)
        
        if not pm.started and (os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug):
            app.logger.info("Starting panel monitor...")
            pm.start()
            atexit.register(_cleanup_monitors)
    except Exception as e:
        logging.exception("Solenoid monitor not started: %s", e)

    return app


if __name__ == "__main__":
    log_dir = configure_logging()
    app = create_app(log_dir)
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT,  _graceful_shutdown)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, threaded=False, use_reloader=False)
