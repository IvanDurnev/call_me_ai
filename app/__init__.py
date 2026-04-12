from __future__ import annotations

import os
import sys
from pathlib import Path

import click
from flask import Flask

from config import Config

from .extensions import db, mail, migrate, sock


def create_app(config_class: type[Config] = Config) -> Flask:
    app_dir = Path(__file__).resolve().parent
    project_dir = app_dir.parent

    app = Flask(
        __name__,
        template_folder=str(project_dir / "templates"),
        static_folder=str(project_dir / "static"),
        static_url_path="/static",
    )
    app.config.from_object(config_class)

    db.init_app(app)
    mail.init_app(app)
    migrate.init_app(app, db)
    sock.init_app(app)

    from . import models  # noqa: F401
    from .characters import ensure_default_heroes
    from .max_bot import max_bp
    from .routes import main_bp
    from .telegram_bot import telegram_bp
    from .ws import register_ws_routes

    app.register_blueprint(main_bp)
    app.register_blueprint(telegram_bp, url_prefix="/telegram")
    app.register_blueprint(max_bp, url_prefix="/max")
    register_ws_routes()
    _register_cli_commands(app)
    with app.app_context():
        ensure_default_heroes()
    _maybe_start_background_services(app)
    _maybe_start_recurring_worker(app)

    return app


def start_background_services(app: Flask) -> None:
    from .max_bot import start_polling_bot_once as start_max_polling_bot_once
    from .services.recurring import start_recurring_worker_once
    from .telegram_bot import start_polling_bot_once as start_telegram_polling_bot_once

    start_telegram_polling_bot_once(app)
    start_max_polling_bot_once(app)
    start_recurring_worker_once(app)


def _maybe_start_background_services(app: Flask) -> None:
    if app.extensions.get("background_services_started"):
        return
    if not _should_autostart_background_services():
        return
    start_background_services(app)
    app.extensions["background_services_started"] = True


def _should_autostart_background_services() -> bool:
    argv = [part.strip().lower() for part in sys.argv]
    if len(argv) >= 2 and argv[1] == "run":
        return True
    if os.environ.get("FLASK_RUN_FROM_CLI") == "true" and "run" in argv:
        return True
    return False


def _maybe_start_recurring_worker(app: Flask) -> None:
    if app.extensions.get("recurring_worker_started"):
        return
    if os.environ.get("APP_ENABLE_RECURRING_WORKER", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    from .services.recurring import start_recurring_worker_once

    start_recurring_worker_once(app)
    app.extensions["recurring_worker_started"] = True


def _register_cli_commands(app: Flask) -> None:
    @app.cli.command("create-admin")
    @click.option("--username", prompt=True, help="Admin username")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True, help="Admin password")
    def create_admin(username: str, password: str) -> None:
        from .models import AdminUser

        with app.app_context():
            db.metadata.create_all(bind=db.engine, tables=[AdminUser.__table__])

            existing = AdminUser.query.filter_by(username=username).first()
            if existing:
                raise click.ClickException(f"Admin '{username}' already exists.")

            admin = AdminUser(username=username.strip(), is_active=True)
            admin.set_password(password)
            db.session.add(admin)
            db.session.commit()
            click.echo(f"Admin '{admin.username}' created.")
