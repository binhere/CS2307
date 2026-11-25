#!/usr/bin/env bash
# Purpose: prove a seed image works the way the cluster runs it, before it is pushed: on an empty
#   PostgreSQL (after the backend migrations) and an empty Neo4j, the first run loads everything
#   (vectors included), the second run changes nothing, and the backend answers a search that uses
#   semantic (query embedded by the embedding image) and graph retrieval.
# Input (environment):
#   SEED_IMAGE       the seed image to check
#   BACKEND_IMAGE    an image built from backend/ (migrations, then the API)
#   EMBEDDING_IMAGE  an image built from deploy/embedding/ (embeds the test query)
# Output: exit code 0 when every check passes; the failing check's message otherwise. Temporary
#   containers and the network are removed on exit.

set -euo pipefail

: "${SEED_IMAGE:?set SEED_IMAGE}"
: "${BACKEND_IMAGE:?set BACKEND_IMAGE}"
: "${EMBEDDING_IMAGE:?set EMBEDDING_IMAGE}"
POSTGRES_IMAGE="pgvector/pgvector:pg16@sha256:484e838076e04939d27d3e176e91c86eea96236126023be8d3059761122e579f"
NEO4J_IMAGE="neo4j:5.26.33-community"
EXPECTED_LISTINGS=3857
EXPECTED_AMENITY_EDGES=150313

run_id="seed-check-$$"
network="$run_id"
postgres_container="$run_id-postgres"
neo4j_container="$run_id-neo4j"
embedding_container="$run_id-embedding"
backend_container="$run_id-backend"
database_password="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
graph_password="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"

cleanup() {
  docker rm -f "$postgres_container" "$neo4j_container" "$embedding_container" "$backend_container" > /dev/null 2>&1 || true
  docker network rm "$network" > /dev/null 2>&1 || true
}
trap cleanup EXIT

wait_for() {
  local description="$1"; shift
  for attempt in $(seq 180); do
    if "$@" > /dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo "Timed out waiting for $description" >&2
  return 1
}

fail() {
  echo "CHECK FAILED: $*" >&2
  exit 1
}

psql_query() {
  docker exec "$postgres_container" psql -U app -d app -At -v ON_ERROR_STOP=1 -c "$1"
}

cypher_query() {
  docker exec "$neo4j_container" cypher-shell -u neo4j -p "$graph_password" --format plain "$1" | tail -n 1
}

# Settings every backend-side container shares; the seed Job receives the same ones in the cluster.
database_settings=(
  -e HOST_DB="$postgres_container" -e PORT_DB=5432 -e USERNAME_DB=app
  -e PASSWORD_DB="$database_password" -e DATABASE=app
  -e NEO4J_URI="bolt://$neo4j_container:7687" -e NEO4J_USER=neo4j
  -e NEO4J_PASSWORD="$graph_password" -e NEO4J_DATABASE=neo4j
)
hardened=(--read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges)

docker network create "$network" > /dev/null
docker run -d --name "$postgres_container" --network "$network" \
  -e POSTGRES_USER=app -e POSTGRES_PASSWORD="$database_password" -e POSTGRES_DB=app "$POSTGRES_IMAGE" > /dev/null
docker run -d --name "$neo4j_container" --network "$network" \
  -e NEO4J_AUTH="neo4j/$graph_password" \
  -e NEO4J_server_memory_heap_initial__size=512m -e NEO4J_server_memory_heap_max__size=1G \
  "$NEO4J_IMAGE" > /dev/null
docker run -d --name "$embedding_container" --network "$network" "${hardened[@]}" "$EMBEDDING_IMAGE" > /dev/null
wait_for "PostgreSQL" docker exec "$postgres_container" pg_isready -U app -d app

echo "[check] migrations"
docker run --rm --network "$network" "${hardened[@]}" "${database_settings[@]}" "$BACKEND_IMAGE" alembic upgrade head

echo "[check] first seed run (loads)"
docker run --rm --network "$network" "${hardened[@]}" "${database_settings[@]}" "$SEED_IMAGE"
wait_for "Neo4j" docker exec "$neo4j_container" cypher-shell -u neo4j -p "$graph_password" "RETURN 1"

[[ "$(psql_query "SELECT count(*) FROM properties")" == "$EXPECTED_LISTINGS" ]] || fail "properties row count"
[[ "$(psql_query "SELECT count(search_text) FROM properties")" == "$EXPECTED_LISTINGS" ]] || fail "missing search text"
[[ "$(psql_query "SELECT count(embedding) FROM properties")" == "$EXPECTED_LISTINGS" ]] || fail "missing vectors"
[[ "$(psql_query "SELECT count(*) FROM listing_amenity_distances")" == "$EXPECTED_AMENITY_EDGES" ]] || fail "distance row count"
[[ "$(cypher_query "MATCH (l:Listing) RETURN count(l)")" == "$EXPECTED_LISTINGS" ]] || fail "Neo4j listing count"
[[ "$(cypher_query "MATCH ()-[r:NEAR_AMENITY]->() RETURN count(r)")" == "$EXPECTED_AMENITY_EDGES" ]] || fail "Neo4j NEAR_AMENITY count"
[[ "$(cypher_query "MATCH (m:SeedImport) RETURN count(m)")" == "1" ]] || fail "seed marker"

echo "[check] second seed run (must skip both stores)"
second_run_log="$(docker run --rm --network "$network" "${hardened[@]}" "${database_settings[@]}" "$SEED_IMAGE")"
echo "$second_run_log"
grep -q "PostgreSQL: properties already has rows; skipping" <<< "$second_run_log" || fail "PostgreSQL was not skipped"
grep -q "Neo4j: catalog already imported" <<< "$second_run_log" || fail "Neo4j was not skipped"
[[ "$(psql_query "SELECT count(*) FROM properties")" == "$EXPECTED_LISTINGS" ]] || fail "second run changed properties"

echo "[check] backend search on the seeded stores"
embedding_model="$(docker exec "$embedding_container" cat /opt/ollama/EMBEDDING_MODEL)"
[[ "$(psql_query "SELECT count(*) FROM properties WHERE embedding_model = '$embedding_model'")" == "$EXPECTED_LISTINGS" ]] \
  || fail "the seed's vectors are not from $embedding_model"
embedding_settings=(
  -e SEARCH_EMBEDDING_BASE_URL="http://$embedding_container:11434/v1" -e SEARCH_EMBEDDING_MODEL="$embedding_model"
)
docker run -d --name "$backend_container" --network "$network" "${hardened[@]}" "${database_settings[@]}" \
  "${embedding_settings[@]}" -e SEARCH_USE_NEO4J=true -e MINIO_END_POINT= \
  "$BACKEND_IMAGE" > /dev/null
wait_for "the backend" docker exec "$backend_container" python -c \
  "import urllib.request; urllib.request.urlopen('http://localhost:8000/ready', timeout=5)"
docker exec "$backend_container" python -c "
import json, urllib.request
request = urllib.request.Request('http://localhost:8000/v1/search', method='POST',
    data=json.dumps({'query': 'căn hộ 2 phòng ngủ gần trường học', 'top_k': 5, 'debug': True}).encode(),
    headers={'Content-Type': 'application/json'})
data = json.load(urllib.request.urlopen(request, timeout=60))['data']
debug = data.get('debug') or {}
print('[check] search results:', len(data['results']), '| semantic:', data.get('semantic_enabled'),
      '| graph:', data.get('graph_enabled'), '| coverage:', debug.get('embedding_coverage'))
assert len(data['results']) == 5, 'search returned too few results'
assert data.get('semantic_enabled'), 'semantic search is off'
assert data.get('graph_enabled'), 'graph search is off'
"
echo "[check] all checks passed for $SEED_IMAGE"
