"""Add corporate pages, memberships and single-use registration codes.

Revision ID: 3b4c5d6e7f8a
Revises: 2a3b4c5d6e7f
"""
from alembic import op
import sqlalchemy as sa

revision = "3b4c5d6e7f8a"
down_revision = "2a3b4c5d6e7f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("email_code", sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.create_table("corporate_page",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("endpoint", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("button_label", sa.String(120), nullable=False),
        sa.Column("hero_id", sa.Integer(), sa.ForeignKey("hero.id"), nullable=False),
        sa.Column("email_domains", sa.Text(), nullable=False),
        sa.Column("background_path", sa.String(512)),
        sa.Column("logo_path", sa.String(512)),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_table("corporate_membership",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("page_id", sa.Integer(), sa.ForeignKey("corporate_page.id"), nullable=False),
        sa.Column("app_user_id", sa.Integer(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("page_id", "app_user_id", name="uq_corporate_member"))
    op.create_table("corporate_registration_code",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("page_id", sa.Integer(), sa.ForeignKey("corporate_page.id"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("label", sa.String(32), nullable=False),
        sa.Column("used_by_id", sa.Integer(), sa.ForeignKey("app_user.id")),
        sa.Column("used_at", sa.DateTime()),
        sa.Column("revoked_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_corporate_registration_code_page_id", "corporate_registration_code", ["page_id"])
    with op.batch_alter_table("call_session") as batch:
        batch.add_column(sa.Column("corporate_page_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_call_session_corporate_page", "corporate_page", ["corporate_page_id"], ["id"])
        batch.create_index("ix_call_session_corporate_page_id", ["corporate_page_id"])


def downgrade():
    with op.batch_alter_table("call_session") as batch:
        batch.drop_index("ix_call_session_corporate_page_id")
        batch.drop_constraint("fk_call_session_corporate_page", type_="foreignkey")
        batch.drop_column("corporate_page_id")
    op.drop_table("corporate_registration_code")
    op.drop_table("corporate_membership")
    op.drop_table("corporate_page")
    with op.batch_alter_table("email_code") as batch:
        batch.drop_column("failed_attempts")
