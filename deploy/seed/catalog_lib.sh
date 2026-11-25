# Purpose: steps shared by build_seed.sh and embed_catalog.sh: a throwaway PostgreSQL on its own Docker
#   network, and the catalog import the backend performs for a local stack.
# Input: sourced by those scripts (bash, set -euo pipefail), which set `run_id` before sourcing.
# Output: functions start_catalog_database, psql_query, import_catalog, expected_embedding_setting;
#   variables network, postgres_container, database_password. Containers and the network are removed
#   when the sourcing script exits.

POSTGRES_IMAGE="pgvector/pgvector:pg16@sha256:484e838076e04939d27d3e176e91c86eea96236126023be8d3059761122e579f"
EXPECTED_LISTINGS=3857
seed_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd -- "$seed_directory/../.." && pwd)"
network="$run_id"
postgres_container="$run_id-postgres"
database_password="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
extra_containers=()

cleanup_catalog_database() {
  docker rm -f "$postgres_container" ${extra_containers[@]+"${extra_containers[@]}"} > /dev/null 2>&1 || true
  docker network rm "$network" > /dev/null 2>&1 || true
}
trap cleanup_catalog_database EXIT

# Start PostgreSQL with pgvector and wait until it accepts connections.
start_catalog_database() {
  docker network create "$network" > /dev/null
  docker run -d --name "$postgres_container" --network "$network" \
    -e POSTGRES_USER=seed -e POSTGRES_PASSWORD="$database_password" -e POSTGRES_DB=catalog \
    "$POSTGRES_IMAGE" > /dev/null
  for attempt in $(seq 120); do
    if docker exec "$postgres_container" pg_isready -U seed -d catalog > /dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo "PostgreSQL did not start" >&2
  return 1
}

# Run one SQL statement; prints unaligned rows.
psql_query() {
  docker exec "$postgres_container" psql -U seed -d catalog -At -v ON_ERROR_STOP=1 -c "$1"
}

# Select the 3,857 listings and import them with the backend's own script.
#   $1: backend image; $2: work directory; remaining arguments go to build_search_index.py.
#   Extra `docker run` options (embedding settings) come from the array import_options.
import_options=()
import_catalog() {
  local backend_image="$1" work_dir="$2"; shift 2
  rm -rf "$work_dir/input"
  python3 "$seed_directory/prepare_seed_input.py" --output-dir "$work_dir/input"
  # host-gateway lets a container reach an Ollama running natively on this machine (Linux needs it).
  docker run --rm --network "$network" --add-host=host.docker.internal:host-gateway \
    -v "$work_dir/input:/seed-input:ro" \
    -e SEARCH_DATA_DIR=/seed-input \
    -e HOST_DB="$postgres_container" -e PORT_DB=5432 -e USERNAME_DB=seed \
    -e PASSWORD_DB="$database_password" -e DATABASE=catalog \
    -e MINIO_END_POINT= \
    ${import_options[@]+"${import_options[@]}"} \
    "$backend_image" python scripts/build_search_index.py "$@"
}

# Read a build argument's default from deploy/embedding/Dockerfile (the image the server runs).
#   $1: argument name, e.g. EMBEDDING_MODEL.
expected_embedding_setting() {
  sed -n "s/^ARG $1=//p" "$repository_root/deploy/embedding/Dockerfile" | head -n 1
}
