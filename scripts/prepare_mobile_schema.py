"""Create the two additive mobile API tables on an existing deployment.

Run from the project root: python -m scripts.prepare_mobile_schema
"""
from __future__ import annotations

import os

from sqlalchemy import inspect

from app import create_app
from app.extensions import db
from app.models import ChatMessage, MobileAuthToken


def main() -> None:
    os.environ['APP_ENABLE_RECURRING_WORKER'] = 'false'
    app = create_app()
    tables = [MobileAuthToken.__table__, ChatMessage.__table__]
    with app.app_context(), db.engine.begin() as connection:
        inspector = inspect(connection)
        for table in tables:
            if inspector.has_table(table.name):
                existing = {column['name'] for column in inspector.get_columns(table.name)}
                missing = set(table.columns.keys()) - existing
                if missing:
                    raise RuntimeError(f'{table.name}: missing columns {sorted(missing)}; manual migration required')
        db.metadata.create_all(bind=connection, tables=tables, checkfirst=True)
    print('Mobile API tables ready: mobile_auth_token, chat_message')


if __name__ == '__main__':
    main()
