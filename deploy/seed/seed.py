"""Bring the deployment's catalog to the one in this image: PostgreSQL first, then Neo4j.

Runs as the cluster's catalog CronJob (and as a Job created from it), after the backend's
migrations. Safe to run any number of times; each store is compared with the image by the
SHA-256 of its files (its content digest):
- PostgreSQL: the digest of catalog/*.csv is kept in the table catalog_imports. Same digest:
  skipped. Different or none: both catalog tables are replaced in one transaction (all or
  nothing); the backend keeps reading the old catalog until the commit.
- Neo4j: the digest of graph/*.csv and the import script is kept on the marker node
  (:SeedImport {name: "catalog"}). Same digest: skipped. Different or no marker: the marker is
  removed, every node is deleted (the graph holds only what the seed imported) and the graph
  is imported again; the marker is written last, so an interrupted run starts over next time.
  Graph search returns partial results while that runs (about a minute).
Only one run works at a time: a PostgreSQL advisory lock is held for the whole run.

Input: environment variables HOST_DB, PORT_DB, USERNAME_DB, PASSWORD_DB, DATABASE (the
    backend's PostgreSQL settings) and NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
    NEO4J_DATABASE; optional SEED_DATA_DIR (default /seed/data) holding catalog/*.csv,
    graph/*.csv, import_search_graph.cypher and SEED_VERSION.
Output: exit code 0 when both stores hold this image's catalog (loaded now or before);
    non-zero on any failure, so the Job retries. Progress goes to stdout.
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence

import psycopg2
from psycopg2 import sql
from neo4j import GraphDatabase

csv.field_size_limit(sys.maxsize)

DATA_DIR = Path(os.environ.get("SEED_DATA_DIR", "/seed/data"))
# Catalog tables in load order, each read from catalog/<table>.csv.
CATALOG_TABLES = ("properties", "listing_amenity_distances")
CATALOG_ROWS_ONLY = sql.SQL(" WHERE source = 'catalog'")
# Any constant works; it only has to be the same for every seed run.
RUN_LOCK_KEY = 2307_0001
NEO4J_BATCH_SIZE = 2000
# Nodes deleted per transaction when the graph is replaced; small enough for a 512 MB heap.
NEO4J_DELETE_BATCH_SIZE = 1000
NEO4J_CONNECT_ATTEMPTS = 60
NEO4J_CONNECT_INTERVAL_SECONDS = 5
# Name of the catalog_imports row and of the Neo4j marker node.
CATALOG_RECORD_NAME = "catalog"
DIGEST_READ_SIZE = 1 << 20

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


def content_digest(paths: Sequence[Path]) -> str:
    """Hash a list of files into one content digest.

    Each file contributes its name, its size and its bytes, so renaming, moving bytes from
    one file to another or reordering the list all change the digest.

    Args:
        paths: Files in a fixed order.

    Returns:
        str: 64 lowercase hex characters (SHA-256).
    """
    digest = hashlib.sha256()
    for path in paths:
        digest.update(f"{path.name}\0{path.stat().st_size}\0".encode("utf-8"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(DIGEST_READ_SIZE), b""):
                digest.update(chunk)
    return digest.hexdigest()


def catalog_files() -> List[Path]:
    """The PostgreSQL catalog files, in load order."""
    return [DATA_DIR / "catalog" / f"{table}.csv" for table in CATALOG_TABLES]


def graph_files() -> List[Path]:
    """The graph CSV files (sorted by name), then the Cypher import script that reads them."""
    return sorted((DATA_DIR / "graph").glob("*.csv")) + [DATA_DIR / "import_search_graph.cypher"]


def short(digest: Optional[str]) -> str:
    """The first 12 characters of a digest for log lines, or "none"."""
    return digest[:12] if digest else "none"


def connect_postgres():
    """Open a PostgreSQL connection with the backend's settings.

    Returns:
        psycopg2.extensions.connection: An open connection.
    """
    return psycopg2.connect(
        host=required_setting("HOST_DB"),
        port=os.environ.get("PORT_DB", "5432"),
        user=required_setting("USERNAME_DB"),
        password=required_setting("PASSWORD_DB"),
        dbname=required_setting("DATABASE"),
        connect_timeout=10,
    )


def load_postgres(digest: str) -> None:
    """Make the catalog tables hold this image's catalog.

    Args:
        digest: content_digest(catalog_files()).

    Raises:
        SystemExit: The schema is missing (migrations have not run yet).
        psycopg2.Error: Any database failure; the transaction is rolled back and the old
            catalog stays.
    """
    connection = connect_postgres()
    try:
        with connection, connection.cursor() as cursor:
            for table in ("properties", "catalog_imports"):
                cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
                if not cursor.fetchone()[0]:
                    raise SystemExit(f"[seed] table {table} does not exist; run the backend migrations first")
            cursor.execute("SELECT content_sha256 FROM catalog_imports WHERE name = %s", (CATALOG_RECORD_NAME,))
            record = cursor.fetchone()
            loaded_digest = record[0] if record else None
            if loaded_digest == digest:
                log(f"PostgreSQL: catalog {short(digest)} already loaded; skipping")
                return
            # Listings users posted (source "user", migration 0007) are not part of the catalog and
            # must survive its replacement; before that migration every row is a catalog row.
            cursor.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                           "WHERE table_name = 'properties' AND column_name = 'source')")
            has_source = cursor.fetchone()[0]
            cursor.execute("SELECT EXISTS (SELECT 1 FROM properties)")
            if cursor.fetchone()[0]:
                log(f"PostgreSQL: replacing catalog {short(loaded_digest)} with {short(digest)}")
                # DELETE rather than TRUNCATE: TRUNCATE would block the backend's reads until the
                # commit, while with DELETE they keep seeing the old catalog.
                for table in CATALOG_TABLES:
                    cursor.execute(sql.SQL("DELETE FROM {table}").format(table=sql.Identifier(table))
                                   + (CATALOG_ROWS_ONLY if table == "properties" and has_source else sql.SQL("")))
            for table, path in zip(CATALOG_TABLES, catalog_files()):
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
            # Posted listings take ids from their own sequence (9 billion and up), so only catalog ids count here.
            cursor.execute(
                "SELECT setval(pg_get_serial_sequence('properties', 'id'), (SELECT max(id) FROM properties"
                + (" WHERE source = 'catalog'" if has_source else "") + "))"
            )
            cursor.execute(
                "INSERT INTO catalog_imports (name, content_sha256, seed_version) VALUES (%s, %s, %s) "
                "ON CONFLICT (name) DO UPDATE SET content_sha256 = EXCLUDED.content_sha256, "
                "seed_version = EXCLUDED.seed_version, loaded_at = now()",
                (CATALOG_RECORD_NAME, digest, seed_version()),
            )
        connection.autocommit = True
        with connection.cursor() as cursor:
            # VACUUM also reclaims the rows a replacement deleted; it cannot run inside a transaction.
            cursor.execute("VACUUM (ANALYZE) properties, listing_amenity_distances")
        log(f"PostgreSQL: catalog {short(digest)} loaded")
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


def load_neo4j(digest: str) -> None:
    """Make the graph hold this image's graph, unless the marker says it already does.

    Args:
        digest: content_digest(graph_files()).

    Raises:
        neo4j.exceptions.Neo4jError: Any statement failed; the marker is not written,
            so the next run replaces the graph again.
    """
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    driver = connect_neo4j()
    try:
        with driver.session(database=database) as session:
            marker = session.run(
                "MATCH (marker:SeedImport {name: $name}) RETURN marker.content_sha256 AS digest",
                name=CATALOG_RECORD_NAME,
            ).single()
            loaded_digest = marker["digest"] if marker is not None else None
            if loaded_digest == digest:
                log(f"Neo4j: graph {short(digest)} already imported; skipping")
                return
            if marker is None:
                log(f"Neo4j: importing graph {short(digest)} (no completed import found)")
            else:
                log(f"Neo4j: replacing graph {short(loaded_digest)} with {short(digest)}")
            # The marker goes first, so a run that stops half way leaves none and the next run starts over.
            session.run("MATCH (marker:SeedImport {name: $name}) DELETE marker", name=CATALOG_RECORD_NAME).consume()
            # The backend never writes to Neo4j: every node came from a seed, so all of them go.
            session.run(
                f"MATCH (node) CALL (node) {{ DETACH DELETE node }} IN TRANSACTIONS OF {NEO4J_DELETE_BATCH_SIZE} ROWS"
            ).consume()
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
                "MERGE (marker:SeedImport {name: $name}) SET marker.content_sha256 = $digest, "
                "marker.seed_version = $version, marker.completed_at = datetime()",
                name=CATALOG_RECORD_NAME,
                digest=digest,
                version=seed_version(),
            ).consume()
            log(f"Neo4j: graph {short(digest)} imported")
    finally:
        driver.close()


def main() -> None:
    """Sync PostgreSQL, then Neo4j, holding the run lock; any exception exits non-zero so the Job retries."""
    log(f"seed version {seed_version()}")
    catalog_digest = content_digest(catalog_files())
    graph_digest = content_digest(graph_files())
    lock_connection = connect_postgres()
    try:
        lock_connection.autocommit = True
        with lock_connection.cursor() as cursor:
            # A session lock, released when the connection closes, so it spans both stores: a Job
            # created by hand while the CronJob runs waits here, then finds nothing left to do.
            cursor.execute("SELECT pg_advisory_lock(%s)", (RUN_LOCK_KEY,))
        load_postgres(catalog_digest)
        load_neo4j(graph_digest)
    finally:
        lock_connection.close()
    log("done")


if __name__ == "__main__":
    main()
