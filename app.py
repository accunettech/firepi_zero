import os, atexit
import logging
from flask import Flask, jsonify
from db import db, init_db
from blueprints.config_ui import bp as config_ui_bp
from services.solenoid_monitor import SolenoidMonitor
from services.panel_monitor import PanelMonitor

def create_app() -> Flask:
    app = Flask(__name__)
    app.logger.setLevel(logging.INFO)

    # Core config
    os.makedirs(app.instance_path, exist_ok=True)
    db_path = os.path.join(app.instance_path, "alerting.db")
    rois_path = os.path.join(app.instance_path, "panel_rois.yaml")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

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

        # Start only in the actual serving process (not the reloader parent)
        if not sm.started and (os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug):
            app.logger.info("Starting solenoid monitor...")
            sm.start()
            atexit.register(sm.stop)
        
        if not pm.started and (os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug):
            app.logger.info("Starting panel monitor...")
            pm.start()
            atexit.register(pm.stop)
    except Exception as e:
        logging.exception("Solenoid monitor not started: %s", e)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, threaded=False, use_reloader=False)
