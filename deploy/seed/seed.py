"""Load the catalog into an empty deployment: PostgreSQL first, then Neo4j.

Runs as a Kubernetes Job after the backend's migrations. Safe to run any number of times:
- PostgreSQL: skipped when `properties` has rows. Otherwise both catalog tables are loaded
  in one transaction (all or nothing) under an advisory lock, so two concurrent runs never
  load twice.
- Neo4j: skipped when the completion marker (:SeedImport {name: "catalog"}) exists. Every
  import statement MERGEs, so a run interrupted half way is simply repeated in full.

Input: environment variables HOST_DB, PORT_DB, USERNAME_DB, PASSWORD_DB, DATABASE (the
    backend's PostgreSQL settings) and NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
    NEO4J_DATABASE; optional SEED_DATA_DIR (default /seed/data) holding catalog/*.csv,
    graph/*.csv, import_search_graph.cypher and SEED_VERSION.
Output: exit code 0 when both stores hold the catalog (loaded now or before); non-zero on
    any failure, so the Job retries. Progress goes to stdout.
"""

from __future__ import annotations

import csv
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterator, List, Optional

import psycopg2
from psycopg2 import sql
from neo4j import GraphDatabase

csv.field_size_limit(sys.maxsize)

DATA_DIR = Path(os.environ.get("SEED_DATA_DIR", "/seed/data"))
# Catalog tables in load order, each read from catalog/<table>.csv.
CATALOG_TABLES = ("properties", "listing_amenity_distances")
# Any constant works; it only has to be the same for every seed run.
ADVISORY_LOCK_KEY = 2307_0001
NEO4J_BATCH_SIZE = 2000
NEO4J_CONNECT_ATTEMPTS = 60
NEO4J_CONNECT_INTERVAL_SECONDS = 5
SEED_MARKER_NAME = "catalog"

LOAD_CSV_CLAUSE = re.compile(
    r"LOAD CSV WITH HEADERS FROM 'file:///real_estate_graph_ready/(?P<file>[^']+)' AS row",
)
IN_TRANSACTIONS_CLAUSE = re.compile(r"\}\s*IN TRANSACTIONS OF \d+ ROWS")


def log(message: str) -> None:
    """Print one progress line, flushed so it reaches the Job log immediately."""
    print(f"[seed] {message}", flush=True)


def required_setting(name: str) -> str:
    """Read a required environment variable.

    Raises:
        SystemExit: The variable is missing or empty.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"[seed] missing required environment variable {name}")
    return value


def seed_version() -> str:
    """The image tag this data was built for (written by the image build), or "unknown"."""
    path = DATA_DIR / "SEED_VERSION"
    return path.read_text(encoding="utf-8").strip() if path.exists() else "unknown"


def load_postgres() -> None:
    """Load the catalog tables into an empty PostgreSQL database.

    Raises:
        SystemExit: The schema is missing (migrations have not run yet).
        psycopg2.Error: Any database failure; the transaction is rolled back.
    """
    connection = psycopg2.connect(
        host=required_setting("HOST_DB"),
        port=os.environ.get("PORT_DB", "5432"),
        user=required_setting("USERNAME_DB"),
        password=required_setting("PASSWORD_DB"),
        dbname=required_setting("DATABASE"),
        connect_timeout=10,
    )
    try:
        with connection, connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.properties') IS NOT NULL")
            if not cursor.fetchone()[0]:
                raise SystemExit("[seed] table properties does not exist; run the backend migrations first")
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))
            cursor.execute("SELECT EXISTS (SELECT 1 FROM properties)")
            if cursor.fetchone()[0]:
                log("PostgreSQL: properties already has rows; skipping")
                return
            for table in CATALOG_TABLES:
                path = DATA_DIR / "catalog" / f"{table}.csv"
                with path.open(encoding="utf-8", newline="") as stream:
                    columns = next(csv.reader(stream))
                    stream.seek(0)
                    statement = sql.SQL("COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv, HEADER true)").format(
                        table=sql.Identifier(table),
                        columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                    )
                    cursor.copy_expert(statement.as_string(cursor), stream)
                log(f"PostgreSQL: loaded {cursor.rowcount} rows into {table}")
            # Rows keep their exported ids, so the sequence must continue after them.
            cursor.execute(
                "SELECT setval(pg_get_serial_sequence('properties', 'id'), (SELECT max(id) FROM properties))"
            )
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE properties; ANALYZE listing_amenity_distances;")
    finally:
        connection.close()


def split_statements(cypher_text: str) -> List[str]:
    """Split a Cypher script into statements.

    Args:
        cypher_text: Statements terminated by ";" at the end of a line; "//" lines are comments.

    Returns:
        list: Statements without their terminator, in order, comments removed.
    """
    lines = [line for line in cypher_text.splitlines() if not line.lstrip().startswith("//")]
    statements = re.split(r";[ \t]*(?:\n|$)", "\n".join(lines))
    return [statement.strip() for statement in statements if statement.strip()]


def as_parameterized(statement: str) -> tuple[Optional[str], str]:
    """Rewrite a LOAD CSV statement to read its rows from the $rows parameter.

    The Job cannot reach the Neo4j server's import directory, so it sends the CSV rows
    itself; each batch runs in its own transaction, which replaces IN TRANSACTIONS.

    Args:
        statement: One statement of import_search_graph.cypher.

    Returns:
        tuple: (CSV file name, rewritten statement) for a LOAD CSV statement;
        (None, statement unchanged) for any other statement.
    """
    match = LOAD_CSV_CLAUSE.search(statement)
    if match is None:
        return None, statement
    rewritten = LOAD_CSV_CLAUSE.sub("UNWIND $rows AS row", statement, count=1)
    return match.group("file"), IN_TRANSACTIONS_CLAUSE.sub("}", rewritten)


def csv_batches(path: Path, batch_size: int) -> Iterator[List[Dict[str, Optional[str]]]]:
    """Read a CSV file in batches, with empty fields as None as LOAD CSV returns them.

    Args:
        path: CSV file with a header row.
        batch_size: Maximum rows per batch.

    Yields:
        list: Up to batch_size rows keyed by the header's column names.
    """
    with path.open(encoding="utf-8-sig", newline="") as stream:
        batch: List[Dict[str, Optional[str]]] = []
        for row in csv.DictReader(stream):
            batch.append({key: (value if value != "" else None) for key, value in row.items()})
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch


def connect_neo4j():
    """Open a Neo4j driver, waiting for the server to accept connections.

    Returns:
        neo4j.Driver: A verified driver.

    Raises:
        neo4j.exceptions.Neo4jError | OSError: Neo4j stayed unreachable for ~5 minutes.
    """
    driver = GraphDatabase.driver(
        required_setting("NEO4J_URI"),
        auth=(os.environ.get("NEO4J_USER", "neo4j"), required_setting("NEO4J_PASSWORD")),
        # On an empty database the marker lookup names a label and properties that do not exist yet;
        # that is expected, so its "unrecognized" warnings would only clutter the Job log.
        notifications_disabled_categories=["UNRECOGNIZED"],
    )
    for attempt in range(1, NEO4J_CONNECT_ATTEMPTS + 1):
        try:
            driver.verify_connectivity()
            return driver
        except Exception as error:  # noqa: BLE001 - retried until the attempts run out
            if attempt == NEO4J_CONNECT_ATTEMPTS:
                driver.close()
                raise
            log(f"Neo4j not reachable yet ({type(error).__name__}); retrying")
            time.sleep(NEO4J_CONNECT_INTERVAL_SECONDS)
    raise AssertionError("unreachable")


def load_neo4j() -> None:
    """Import the graph unless a previous run completed it.

    Raises:
        neo4j.exceptions.Neo4jError: Any statement failed; the marker is not written,
            so the next run repeats the import.
    """
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    driver = connect_neo4j()
    try:
        with driver.session(database=database) as session:
            marker = session.run(
                "MATCH (marker:SeedImport {name: $name}) RETURN marker.seed_version AS version",
                name=SEED_MARKER_NAME,
            ).single()
            if marker is not None:
                log(f"Neo4j: catalog already imported (seed {marker['version']}); skipping")
                return
            cypher_text = (DATA_DIR / "import_search_graph.cypher").read_text(encoding="utf-8")
            for statement in split_statements(cypher_text):
                file_name, query = as_parameterized(statement)
                if file_name is None:
                    session.run(query).consume()
                    continue
                total = 0
                for batch in csv_batches(DATA_DIR / "graph" / file_name, NEO4J_BATCH_SIZE):
                    session.execute_write(lambda transaction: transaction.run(query, rows=batch).consume())
                    total += len(batch)
                log(f"Neo4j: {file_name}: {total} rows")
            session.run(
                "MERGE (marker:SeedImport {name: $name}) SET marker.seed_version = $version, "
                "marker.completed_at = datetime()",
                name=SEED_MARKER_NAME,
                version=seed_version(),
            ).consume()
            log("Neo4j: import complete")
    finally:
        driver.close()


def main() -> None:
    """Seed PostgreSQL, then Neo4j; any exception exits non-zero so the Job retries."""
    log(f"seed version {seed_version()}")
    load_postgres()
    load_neo4j()
    log("done")


if __name__ == "__main__":
    main()
