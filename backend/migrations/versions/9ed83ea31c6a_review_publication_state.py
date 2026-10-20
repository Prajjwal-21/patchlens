"""review publication state

Revision ID: 9ed83ea31c6a
Revises: 476ea5d76211
Create Date: 2026-10-20 12:09:10.413760

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9ed83ea31c6a"
down_revision: str | None = "476ea5d76211"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("reviews", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "publish_state",
                sa.String(length=32),
                nullable=False,
                server_default="not_published",
            )
        )
        batch_op.add_column(sa.Column("publish_detail", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("published_comment_url", sa.String(length=1024), nullable=True)
        )
        batch_op.add_column(sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("reviews", schema=None) as batch_op:
        batch_op.drop_column("published_at")
        batch_op.drop_column("published_comment_url")
        batch_op.drop_column("publish_detail")
        batch_op.drop_column("publish_state")
