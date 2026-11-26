"""Remember which coordinates a posted listing was enriched with (amenities and graph node).

Listings come only from users now, so the server computes their nearest amenities and adds them
to the Neo4j graph itself (src/services/listing_enrichment). enriched_coordinates holds the
latitude_longitude the listing had when that last succeeded ("" when it had none): a listing is
due again whenever its coordinates differ, for instance after the geocoding job found them.
NULL means never enriched.

Revision ID: 20261126_0010
Revises: 20261126_0009
Create Date: 2026-11-26
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20261126_0010"
down_revision: Union[str, None] = "20261126_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE properties ADD COLUMN IF NOT EXISTS enriched_coordinates TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE properties DROP COLUMN IF EXISTS enriched_coordinates")
