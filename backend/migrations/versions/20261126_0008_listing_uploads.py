"""Support posting many listings from a zip file: coordinate lookups and re-upload detection.

geocoding_status records what the scheduled lookup of a listing's coordinates found ("found",
"not_found", "outside_ward", "no_address", "unknown_ward"; NULL when not looked up yet), so an
address that cannot be found is not sent to the geocoder on every run
(src/services/listing_submission/scheduled_geocoding.py).

content_fingerprint is a SHA-256 of the listing as validated, without its posting date. A user who
fixes the failed listings of a zip and uploads the whole zip again would otherwise post every
listing that already went through a second time; the posting route answers 409 instead. The index
is not unique: rows posted before this migration may already repeat.

Revision ID: 20261126_0008
Revises: 20261126_0007
Create Date: 2026-11-26
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20261126_0008"
down_revision: Union[str, None] = "20261126_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE properties ADD COLUMN IF NOT EXISTS geocoding_status VARCHAR(16)")
    op.execute("ALTER TABLE properties ADD COLUMN IF NOT EXISTS content_fingerprint CHAR(64)")
    op.execute("CREATE INDEX IF NOT EXISTS properties_owner_fingerprint_idx ON properties (owner_user_id, content_fingerprint) "
               "WHERE content_fingerprint IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS properties_owner_fingerprint_idx")
    op.execute("ALTER TABLE properties DROP COLUMN IF EXISTS content_fingerprint")
    op.execute("ALTER TABLE properties DROP COLUMN IF EXISTS geocoding_status")
