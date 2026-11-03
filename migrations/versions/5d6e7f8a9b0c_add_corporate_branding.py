"""Add corporate logo corner radius and uploaded font."""
from alembic import op
import sqlalchemy as sa

revision = "5d6e7f8a9b0c"
down_revision = "4c5d6e7f8a9b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("corporate_page", sa.Column("logo_border_radius", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("corporate_page", sa.Column("font_path", sa.String(512), nullable=True))
    op.add_column("corporate_page", sa.Column("font_original_name", sa.String(255), nullable=True))


def downgrade():
    with op.batch_alter_table("corporate_page") as batch:
        batch.drop_column("font_original_name")
        batch.drop_column("font_path")
        batch.drop_column("logo_border_radius")
