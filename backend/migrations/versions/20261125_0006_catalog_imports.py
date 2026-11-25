"""Record which catalog the seed loaded, so a newer seed image can replace it.

The seed (deploy/seed/seed.py) writes one row named "catalog" holding the SHA-256 of the
catalog files it loaded. A seed whose files hash differently replaces the catalog tables; one
with the same hash skips them.

Revision ID: 20261125_0006
Revises: 20260805_0005
Create Date: 2026-11-25
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20261125_0006"
down_revision: Union[str, None] = "20260805_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS catalog_imports (
            name VARCHAR(64) PRIMARY KEY,
            content_sha256 CHAR(64) NOT NULL,
            seed_version VARCHAR(128),
            loaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS catalog_imports")
