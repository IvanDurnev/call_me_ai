"""Add corporate tables and columns to an existing deployment, without migration history.

Run from the project root: python -m scripts.prepare_corporate_schema
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app import create_app
from app.extensions import db
from app.models import CorporatePage, CorporateMembership, CorporateRegistrationCode


def prepare_schema(connection):
    inspector = sa.inspect(connection)
    required = {"app_user", "hero", "call_session", "email_code"}
    if not required.issubset(inspector.get_table_names()):
        raise RuntimeError("First prepare the existing application schema.")
    tables = [CorporatePage.__table__, CorporateMembership.__table__, CorporateRegistrationCode.__table__]
    for table in tables:
        if inspector.has_table(table.name):
            missing = set(table.columns.keys()) - {column["name"] for column in inspector.get_columns(table.name)}
            if missing:
                raise RuntimeError(f"{table.name}: missing columns {sorted(missing)}")
    db.metadata.create_all(bind=connection, tables=tables, checkfirst=True)
    operations = Operations(MigrationContext.configure(connection))
    if "failed_attempts" not in {column["name"] for column in inspector.get_columns("email_code")}:
        operations.add_column("email_code", sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"))
    if "corporate_page_id" not in {column["name"] for column in inspector.get_columns("call_session")}:
        with operations.batch_alter_table("call_session") as batch:
            batch.add_column(sa.Column("corporate_page_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_call_session_corporate_page", "corporate_page", ["corporate_page_id"], ["id"])
            batch.create_index("ix_call_session_corporate_page_id", ["corporate_page_id"])


def main():
    os.environ["APP_ENABLE_RECURRING_WORKER"] = "false"
    app = create_app()
    with app.app_context(), db.engine.begin() as connection:
        prepare_schema(connection)
    print("Corporate pages schema ready")


if __name__ == "__main__":
    main()
