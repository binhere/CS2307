"""Train the price model on the server: a history of training runs and a fixed holdout set.

price_model_runs records every run, whether started by the daily schedule or by a signed-in user
pressing "Train ngay": deployed, rejected (not better than the model being served), skipped (too
little new data) or failed. A deployed run keeps the model (joblib bytes written by the server
itself, never uploaded) and its training report; the latest deployed run is the model the
backend serves. The partial unique index on status = 'running' lets at most one run go at a
time across every backend process: a second start fails to insert and the API answers "busy".

price_training_holdout lists the catalog listings every model is judged on and none is trained
on, so a new model and the one being served are compared on the same rows.

Revision ID: 20261126_0009
Revises: 20261126_0008
Create Date: 2026-11-26
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20261126_0009"
down_revision: Union[str, None] = "20261126_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS price_model_runs (
            id SERIAL PRIMARY KEY,
            trigger VARCHAR(16) NOT NULL,
            requested_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
            requested_by_name VARCHAR(255),
            status VARCHAR(16) NOT NULL,
            reason VARCHAR(64),
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ,
            summary JSONB,
            report JSONB,
            model BYTEA
        )
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS price_model_runs_one_running_idx ON price_model_runs (status) "
               "WHERE status = 'running'")
    op.execute("CREATE INDEX IF NOT EXISTS price_model_runs_status_idx ON price_model_runs (status, id)")
    op.execute("CREATE TABLE IF NOT EXISTS price_training_holdout (listing_id BIGINT PRIMARY KEY)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS price_training_holdout")
    op.execute("DROP TABLE IF EXISTS price_model_runs")
