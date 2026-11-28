"""Record retrieval evaluation runs for the /du-lieu dashboard.

retrieval_eval_runs stores each evaluation run started by a signed-in user pressing
"Chạy đánh giá" on /du-lieu: running (with live queries_done / queries_total progress),
completed (with per-mode constraint satisfaction, category breakdowns and latency
percentiles in summary), or failed. The partial unique index on status = 'running' lets
at most one evaluation run execute at a time across backend processes.

Revision ID: 20261128_0011
Revises: 20261126_0010
Create Date: 2026-11-28
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20261128_0011"
down_revision: Union[str, None] = "20261126_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS retrieval_eval_runs (
            id SERIAL PRIMARY KEY,
            retrieval_mode VARCHAR(32) NOT NULL,
            requested_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
            requested_by_name VARCHAR(255),
            status VARCHAR(16) NOT NULL,
            queries_total INTEGER NOT NULL DEFAULT 0,
            queries_done INTEGER NOT NULL DEFAULT 0,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ,
            summary JSONB,
            error TEXT
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS retrieval_eval_runs_one_running_idx "
        "ON retrieval_eval_runs (status) WHERE status = 'running'"
    )
    op.execute("CREATE INDEX IF NOT EXISTS retrieval_eval_runs_status_idx ON retrieval_eval_runs (status, id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS retrieval_eval_runs")
