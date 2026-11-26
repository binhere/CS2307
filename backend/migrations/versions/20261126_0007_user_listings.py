"""Let signed-in users post listings next to the crawled catalog.

properties.source tells catalog rows (loaded by the seed, replaced whenever the catalog changes)
from rows users posted, which the seed must never delete. Posted rows take id and listing_id from
user_listing_id_seq, far above the catalog's ids (exported ids under 10,000, crawl ids under
100 million), so a larger catalog can never collide with them. submission keeps the listing
exactly as validated, including fields the table has no column for (nearby amenities).

Revision ID: 20261126_0007
Revises: 20261125_0006
Create Date: 2026-11-26
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20261126_0007"
down_revision: Union[str, None] = "20261125_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE properties ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'catalog'")
    op.execute("ALTER TABLE properties ADD COLUMN IF NOT EXISTS owner_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL")
    op.execute("ALTER TABLE properties ADD COLUMN IF NOT EXISTS submission JSONB")
    op.execute("CREATE INDEX IF NOT EXISTS properties_owner_user_id_idx ON properties (owner_user_id) WHERE owner_user_id IS NOT NULL")
    op.execute("CREATE SEQUENCE IF NOT EXISTS user_listing_id_seq START WITH 9000000000")


def downgrade() -> None:
    op.execute("DELETE FROM properties WHERE source <> 'catalog'")
    op.execute("DROP SEQUENCE IF EXISTS user_listing_id_seq")
    op.execute("DROP INDEX IF EXISTS properties_owner_user_id_idx")
    op.execute("ALTER TABLE properties DROP COLUMN IF EXISTS submission")
    op.execute("ALTER TABLE properties DROP COLUMN IF EXISTS owner_user_id")
    op.execute("ALTER TABLE properties DROP COLUMN IF EXISTS source")
