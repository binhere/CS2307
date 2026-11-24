from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.sql import text

from src.settings.config import config_value


# Postgres defaults to max_connections=100, shared with the LangGraph checkpointer
# pool, so keep this pool small. Overridable via DB_POOL_SIZE / DB_MAX_OVERFLOW.
DEFAULT_POOL_SIZE = 10
DEFAULT_MAX_OVERFLOW = 10
# A stuck query must not hold a pooled connection (and a worker thread) forever.
# 0 disables the limit, as in PostgreSQL itself.
DEFAULT_STATEMENT_TIMEOUT_SECONDS = 15.0


def statement_timeout_milliseconds() -> int:
    """Read the per-statement time limit applied to every pooled connection.

    Returns:
        int: DB_STATEMENT_TIMEOUT_SECONDS converted to milliseconds; the default
        (15 s) when the setting is missing or not a non-negative number, and 0
        (no limit) when the setting is 0.
    """
    try:
        seconds = float(config_value("DB_STATEMENT_TIMEOUT_SECONDS", DEFAULT_STATEMENT_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        seconds = DEFAULT_STATEMENT_TIMEOUT_SECONDS
    if seconds < 0:
        seconds = DEFAULT_STATEMENT_TIMEOUT_SECONDS
    return int(seconds * 1000)


class PostgresConnector:
    def __init__(self, config: dict):
        self.config = config
        self.engine = None
        self.connection_string = None
        self.dbname = None
        self.port = None
        self.host = None
        self.password = None
        self.username = None

    def start(self):
        self.username = self.config["username_db"]
        self.password = self.config["password_db"]
        self.host = self.config["host_db"]
        self.port = self.config["port_db"]
        self.dbname = self.config["database"]
        # URL.create escapes special characters (@, /, :) in the password.
        url = URL.create(
            "postgresql",
            username=self.username,
            password=self.password,
            host=self.host,
            port=int(self.port) if self.port else None,
            database=self.dbname,
        )
        self.connection_string = url.render_as_string(hide_password=True)
        self.engine = create_engine(
            url,
            pool_size=int(config_value("DB_POOL_SIZE", DEFAULT_POOL_SIZE)),
            max_overflow=int(config_value("DB_MAX_OVERFLOW", DEFAULT_MAX_OVERFLOW)),
            pool_pre_ping=True,
            connect_args={"options": f"-c statement_timeout={statement_timeout_milliseconds()}"},
        )

    def fetch_mappings(self, raw_query: str, **kwargs):
        """Execute a read query and preserve column names.

        Errors are raised, never converted to an empty result: callers must be able
        to tell an unavailable database from a valid no-result response.
        """
        query = text(raw_query)
        with self.engine.connect() as connection:
            result = connection.execute(query, kwargs)
            return [dict(row) for row in result.mappings().all()]

    def execute_write(self, raw_query: str, **kwargs):
        query = text(raw_query)
        with self.engine.begin() as connection:
            return connection.execute(query, kwargs)

    def fetch_write(self, raw_query: str, **kwargs):
        """Execute a write inside a transaction and return any produced rows.

        Use for parameterized ``INSERT ... RETURNING``/``UPDATE ... RETURNING``
        statements where the caller needs the committed row back.
        """
        query = text(raw_query)
        with self.engine.begin() as connection:
            result = connection.execute(query, kwargs)
            if not result.returns_rows:
                return []
            return [dict(row) for row in result.mappings().all()]

    def execute_write_many(self, raw_query: str, rows: list):
        """Execute one parameterized write per row inside a single transaction."""
        if not rows:
            return 0
        query = text(raw_query)
        with self.engine.begin() as connection:
            connection.execute(query, rows)
        return len(rows)

    def stop(self):
        self.engine.dispose()
