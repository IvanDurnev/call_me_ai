"""Add a call instruction specific to each corporate page."""
from alembic import op
import sqlalchemy as sa

revision = "4c5d6e7f8a9b"
down_revision = "3b4c5d6e7f8a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("corporate_page", sa.Column("call_instructions", sa.Text(), nullable=False, server_default=""))


def downgrade():
    with op.batch_alter_table("corporate_page") as batch:
        batch.drop_column("call_instructions")
