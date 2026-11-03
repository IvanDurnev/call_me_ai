"""Add the chat operation table without changing existing tables or migration history."""
from __future__ import annotations

import os
from sqlalchemy import inspect
from app import create_app
from app.extensions import db
from app.models import ChatMessage, ChatOperation


def main():
    os.environ["APP_ENABLE_RECURRING_WORKER"] = "false"
    app = create_app()
    with app.app_context(), db.engine.begin() as connection:
        inspector = inspect(connection)
        for table in (ChatMessage.__table__, ChatOperation.__table__):
            if inspector.has_table(table.name):
                missing = set(table.columns.keys()) - {c["name"] for c in inspector.get_columns(table.name)}
                if missing:
                    raise RuntimeError(f"{table.name}: missing columns {sorted(missing)}")
        db.metadata.create_all(bind=connection, tables=[ChatMessage.__table__, ChatOperation.__table__], checkfirst=True)
    print("Chat sync tables ready")


if __name__ == "__main__":
    main()
