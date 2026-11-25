#!/usr/bin/env bash
# Purpose: compute the catalog's embedding vectors on a powerful developer machine, so neither CI nor
#   the server has to (on CPU this takes hours). Run it after any change that alters a listing's
#   search text (the dataset, the photo-independent columns, backend/src/search/text_builder.py), then
#   commit the two output files; seed-build.yml packs them into the seed image.
# Input (environment):
#   EMBEDDING_BASE_URL  optional; an Ollama OpenAI-compatible endpoint as seen from a Docker container,
#                       e.g. http://host.docker.internal:11434/v1 for Ollama running natively on this
#                       machine with its GPU. It must serve the same Ollama version and model digest as
#                       deploy/embedding/Dockerfile; the script checks and stops otherwise. Default: run
#                       the deploy/embedding image here, on CPU.
#   BACKEND_IMAGE       optional; default: build backend/ as cs2307-backend:embed
#   BATCH_SIZE          optional; listings per embedding request (default 16)
#   WORK_DIR            optional; scratch directory (default: a new temporary directory)
# Output: data/gold/listing_embeddings.csv.gz (listing_id, search_text_sha256, embedding) and
#   data/gold/listing_embeddings.json (model, digest, Ollama version, dimensions, count, time).

set -euo pipefail

run_id="embed-catalog-$$"
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/catalog_lib.sh"

WORK_DIR="${WORK_DIR:-$(mktemp -d)}"
BATCH_SIZE="${BATCH_SIZE:-16}"
model="$(expected_embedding_setting EMBEDDING_MODEL)"
model_digest="$(expected_embedding_setting EMBEDDING_MODEL_MANIFEST_SHA256)"
ollama_version="$(expected_embedding_setting OLLAMA_IMAGE)"; ollama_version="${ollama_version##*:}"
output_vectors="$repository_root/data/gold/listing_embeddings.csv.gz"
output_metadata="$repository_root/data/gold/listing_embeddings.json"

if [[ -z "${BACKEND_IMAGE:-}" ]]; then
  BACKEND_IMAGE=cs2307-backend:embed
  docker build --load --quiet --tag "$BACKEND_IMAGE" "$repository_root/backend" > /dev/null
fi

start_catalog_database
if [[ -z "${EMBEDDING_BASE_URL:-}" ]]; then
  embedding_container="$run_id-embedding"
  extra_containers+=("$embedding_container")
  docker build --load --quiet --tag cs2307-embedding:embed "$repository_root/deploy/embedding" > /dev/null
  docker run -d --name "$embedding_container" --network "$network" cs2307-embedding:embed > /dev/null
  EMBEDDING_BASE_URL="http://$embedding_container:11434/v1"
  endpoint_kind="deploy/embedding image on CPU"
else
  endpoint_kind="native Ollama"
fi

# Same model, same weights, same Ollama version as the server, or the vectors are not comparable.
echo "[embed] checking $EMBEDDING_BASE_URL serves $model ($model_digest) on Ollama $ollama_version"
docker run --rm --network "$network" --add-host=host.docker.internal:host-gateway \
  -e BASE_URL="${EMBEDDING_BASE_URL%/v1}" -e MODEL="$model" -e DIGEST="$model_digest" -e VERSION="$ollama_version" \
  "$BACKEND_IMAGE" python -c "
import json, os, time, urllib.request
base = os.environ['BASE_URL']
for attempt in range(120):
    try:
        version = json.load(urllib.request.urlopen(base + '/api/version', timeout=5))['version']
        break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit('embedding endpoint not reachable: ' + base)
digests = {item['name']: item['digest'] for item in json.load(urllib.request.urlopen(base + '/api/tags', timeout=10))['models']}
problems = []
if version != os.environ['VERSION']:
    problems.append(f'Ollama {version}, expected {os.environ[\"VERSION\"]}')
if digests.get(os.environ['MODEL']) != os.environ['DIGEST']:
    problems.append(f'model {os.environ[\"MODEL\"]} has digest {digests.get(os.environ[\"MODEL\"])}, expected {os.environ[\"DIGEST\"]}')
if problems:
    raise SystemExit('wrong embedding endpoint: ' + '; '.join(problems))
"

import_options=(
  -e SEARCH_EMBEDDING_BASE_URL="$EMBEDDING_BASE_URL" -e SEARCH_EMBEDDING_MODEL="$model"
  -e SEARCH_EMBEDDING_TIMEOUT=600
)
started=$(date +%s)
import_catalog "$BACKEND_IMAGE" "$WORK_DIR" --batch-size "$BATCH_SIZE" 2>&1 | grep -v "FutureWarning\|warnings.warn"
read -r listing_count embedded_count dimensions <<< "$(psql_query \
  "SELECT count(*), count(embedding), coalesce(max(vector_dims(embedding)), 0) FROM properties" | tr '|' ' ')"
if [[ "$listing_count" != "$EXPECTED_LISTINGS" || "$embedded_count" != "$EXPECTED_LISTINGS" ]]; then
  echo "Only $embedded_count of $listing_count listings were embedded" >&2
  exit 1
fi

# The hash ties each vector to the exact text it was made from; the seed build refuses stale vectors.
docker exec "$postgres_container" psql -U seed -d catalog -v ON_ERROR_STOP=1 -c "\\copy (
  SELECT listing_id, encode(sha256(convert_to(search_text, 'UTF8')), 'hex') AS search_text_sha256,
         embedding::text AS embedding
  FROM properties ORDER BY listing_id) TO STDOUT WITH (FORMAT csv, HEADER true)" | gzip -n -9 > "$output_vectors"
python3 - "$output_metadata" <<EOF
import json, sys, datetime
json.dump({
    "model": "$model",
    "model_manifest_sha256": "$model_digest",
    "ollama_version": "$ollama_version",
    "dimensions": $dimensions,
    "listings": $embedded_count,
    "embedded_with": "$endpoint_kind",
    "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}, open(sys.argv[1], "w"), indent=2)
EOF
echo "[embed] $embedded_count vectors ($dimensions dimensions) in $(( $(date +%s) - started )) s"
echo "[embed] wrote ${output_vectors#$repository_root/} ($(du -h "$output_vectors" | cut -f1)) and ${output_metadata#$repository_root/}"
echo "[embed] commit both files; the seed build checks them against the current search text"
