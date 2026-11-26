#!/usr/bin/env bash
# Purpose: build the cs2307-seed image: the 3,857 current listings with their embedding vectors, and
#   their graph. The vectors are not computed here: a developer computes them on a powerful machine with
#   embed_catalog.sh and commits data/gold/listing_embeddings.*. This build attaches them and refuses
#   vectors that are stale (made from a different search text) or made by another model or Ollama
#   version than deploy/embedding/Dockerfile. Used by .github/workflows/seed-build.yml and locally.
# Input (environment):
#   BACKEND_IMAGE  an image built from backend/ (runs the migrations and build_search_index.py)
#   SEED_IMAGE     name:tag to give the result
#   WORK_DIR       optional; scratch directory (default: a new temporary directory)
#   VECTORS_FILE, VECTORS_METADATA  optional; default data/gold/listing_embeddings.csv.gz and .json
# Output: the image SEED_IMAGE in the local Docker engine, labelled with the embedding model, its digest
#   and the Ollama version. Takes a few minutes.

set -euo pipefail

run_id="seed-build-$$"
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/catalog_lib.sh"

: "${BACKEND_IMAGE:?set BACKEND_IMAGE}"
: "${SEED_IMAGE:?set SEED_IMAGE}"
WORK_DIR="${WORK_DIR:-$(mktemp -d)}"
vectors_file="${VECTORS_FILE:-$repository_root/data/gold/listing_embeddings.csv.gz}"
metadata_file="${VECTORS_METADATA:-$repository_root/data/gold/listing_embeddings.json}"
model="$(expected_embedding_setting EMBEDDING_MODEL)"
model_digest="$(expected_embedding_setting EMBEDDING_MODEL_MANIFEST_SHA256)"
ollama_version="$(expected_embedding_setting OLLAMA_IMAGE)"; ollama_version="${ollama_version##*:}"

fail() {
  echo "SEED BUILD FAILED: $*" >&2
  exit 1
}

# The committed vectors must come from the model, weights and Ollama version the server runs.
python3 - "$metadata_file" "$model" "$model_digest" "$ollama_version" <<'EOF' || fail "data/gold/listing_embeddings.json does not match deploy/embedding/Dockerfile; run deploy/seed/embed_catalog.sh"
import json, sys
metadata = json.load(open(sys.argv[1]))
expected = {"model": sys.argv[2], "model_manifest_sha256": sys.argv[3], "ollama_version": sys.argv[4]}
mismatches = {key: (metadata.get(key), value) for key, value in expected.items() if metadata.get(key) != value}
for key, (found, wanted) in mismatches.items():
    print(f"{key}: vectors have {found!r}, the server image has {wanted!r}", file=sys.stderr)
sys.exit(1 if mismatches else 0)
EOF

start_catalog_database
# Migrations, catalog import, graph distances and search text, exactly as for a local stack.
import_catalog "$BACKEND_IMAGE" "$WORK_DIR" --skip-embeddings 2>&1 | grep -v "FutureWarning\|warnings.warn"

[[ "$(psql_query "SELECT count(search_text) FROM properties")" == "$EXPECTED_LISTINGS" ]] || fail "the catalog is incomplete"

psql_query "CREATE TABLE committed_vectors (listing_id bigint PRIMARY KEY, search_text_sha256 text NOT NULL,
  embedding vector NOT NULL)" > /dev/null
gunzip -c "$vectors_file" | docker exec -i "$postgres_container" psql -U seed -d catalog -v ON_ERROR_STOP=1 \
  -c "\\copy committed_vectors FROM STDIN WITH (FORMAT csv, HEADER true)" > /dev/null
attached="$(psql_query "WITH attached AS (
    UPDATE properties SET embedding = committed_vectors.embedding, embedding_model = '$model'
    FROM committed_vectors
    WHERE properties.listing_id = committed_vectors.listing_id
      AND encode(sha256(convert_to(properties.search_text, 'UTF8')), 'hex') = committed_vectors.search_text_sha256
    RETURNING 1)
  SELECT count(*) FROM attached")"
echo "[build] attached $attached of $EXPECTED_LISTINGS committed vectors"
if [[ "$attached" != "$EXPECTED_LISTINGS" ]]; then
  fail "$((EXPECTED_LISTINGS - attached)) listings changed since their vectors were made (or have none).
Run deploy/seed/embed_catalog.sh on a powerful machine and commit data/gold/listing_embeddings.*"
fi
psql_query "DROP TABLE committed_vectors" > /dev/null

context="$WORK_DIR/context"
rm -rf "$context"
mkdir -p "$context/data/catalog" "$context/data/graph"
for table in properties listing_amenity_distances; do
  # Generated columns cannot be loaded with COPY, so only stored columns are exported. created_at and
  # updated_at hold this build's clock; left out, they take the load time, and the same data exports
  # to the same bytes, so the seed's content digest changes only when the catalog does.
  columns="$(psql_query "SELECT string_agg(quote_ident(column_name), ', ' ORDER BY ordinal_position)
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = '$table' AND is_generated = 'NEVER'
      AND column_name NOT IN ('created_at', 'updated_at', 'source', 'owner_user_id', 'submission')")"
  # Only catalog rows, without the columns of user-posted listings: the seed leaves those alone, and the
  # catalog files stay byte-identical, so the content digest does not change because of them.
  rows="$table"
  [[ "$table" == "properties" ]] && rows="properties WHERE source = 'catalog'"
  docker exec "$postgres_container" psql -U seed -d catalog -v ON_ERROR_STOP=1 \
    -c "\\copy (SELECT $columns FROM $rows ORDER BY 1, 2) TO STDOUT WITH (FORMAT csv, HEADER true)" \
    > "$context/data/catalog/$table.csv"
done

cp "$WORK_DIR"/input/v3/graph/*.csv "$context/data/graph/"
cp "$repository_root/backend/scripts/neo4j/import_search_graph.cypher" "$context/data/"
# photos.py uploads the photos this manifest lists (deploy/README.md, "Listing photos").
cp "$repository_root/data/gold/images_manifest.csv" "$context/data/"
seed_version="${SEED_IMAGE##*:}"
echo "$seed_version" > "$context/data/SEED_VERSION"
cp "$seed_directory/seed.py" "$seed_directory/photos.py" "$seed_directory/requirements.txt" "$context/"

# --load keeps the image in the local engine even when a docker-container Buildx builder is the default.
docker build --load --quiet --file "$seed_directory/Dockerfile" \
  --label "cs2307.seed-version=$seed_version" \
  --label "cs2307.embedding-model=$model" \
  --label "cs2307.embedding-model-sha256=$model_digest" \
  --label "cs2307.ollama-version=$ollama_version" \
  --label "org.opencontainers.image.source=${OCI_SOURCE_URL:-https://github.com/binhere/CS2307}" \
  --label "org.opencontainers.image.version=$seed_version" \
  --label "org.opencontainers.image.revision=$(git -C "$repository_root" rev-parse HEAD 2>/dev/null || echo unknown)" \
  --tag "$SEED_IMAGE" "$context" > /dev/null
echo "[build] built $SEED_IMAGE"
