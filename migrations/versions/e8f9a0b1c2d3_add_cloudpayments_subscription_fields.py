"""add cloudpayments subscription fields

Revision ID: e8f9a0b1c2d3
Revises: d4e5f6a7b8c9
Create Date: 2026-04-10 13:20:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e8f9a0b1c2d3"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("subscription_purchase")}

    with op.batch_alter_table("subscription_purchase", schema=None) as batch_op:
        if "cloudpayments_subscription_id" not in columns:
            batch_op.add_column(sa.Column("cloudpayments_subscription_id", sa.String(length=64), nullable=True))
        if "subscription_status" not in columns:
            batch_op.add_column(sa.Column("subscription_status", sa.String(length=32), nullable=True))
        if "recurring_interval" not in columns:
            batch_op.add_column(sa.Column("recurring_interval", sa.String(length=16), nullable=True))
        if "recurring_period" not in columns:
            batch_op.add_column(sa.Column("recurring_period", sa.Integer(), nullable=True))
        if "next_transaction_at" not in columns:
            batch_op.add_column(sa.Column("next_transaction_at", sa.DateTime(), nullable=True))
        if "canceled_at" not in columns:
            batch_op.add_column(sa.Column("canceled_at", sa.DateTime(), nullable=True))

    inspector = sa.inspect(bind)
    existing_indexes = {index["name"] for index in inspector.get_indexes("subscription_purchase")}
    with op.batch_alter_table("subscription_purchase", schema=None) as batch_op:
        if "ix_subscription_purchase_cloudpayments_subscription_id" not in existing_indexes:
            batch_op.create_index("ix_subscription_purchase_cloudpayments_subscription_id", ["cloudpayments_subscription_id"], unique=False)
        if "ix_subscription_purchase_subscription_status" not in existing_indexes:
            batch_op.create_index("ix_subscription_purchase_subscription_status", ["subscription_status"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("subscription_purchase")}
    existing_indexes = {index["name"] for index in inspector.get_indexes("subscription_purchase")}

    with op.batch_alter_table("subscription_purchase", schema=None) as batch_op:
        if "ix_subscription_purchase_subscription_status" in existing_indexes:
            batch_op.drop_index("ix_subscription_purchase_subscription_status")
        if "ix_subscription_purchase_cloudpayments_subscription_id" in existing_indexes:
            batch_op.drop_index("ix_subscription_purchase_cloudpayments_subscription_id")
        if "canceled_at" in columns:
            batch_op.drop_column("canceled_at")
        if "next_transaction_at" in columns:
            batch_op.drop_column("next_transaction_at")
        if "recurring_period" in columns:
            batch_op.drop_column("recurring_period")
        if "recurring_interval" in columns:
            batch_op.drop_column("recurring_interval")
        if "subscription_status" in columns:
            batch_op.drop_column("subscription_status")
        if "cloudpayments_subscription_id" in columns:
            batch_op.drop_column("cloudpayments_subscription_id")
