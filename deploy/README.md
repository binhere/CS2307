# Deployment images

The homelab cluster (tenant `cs2307`, https://cs2307.reais.codes) runs four images built by GitHub
Actions on pushes to `main`. The Kubernetes manifests live in the iac-homelab repository, not here.

| Image | Built from | Workflow (paths that trigger it) | Deployed |
|---|---|---|---|
| `ghcr.io/binhere/cs2307-backend` | `backend/Dockerfile` | `main-build.yml` (`backend/**`, `frontend/**`) | automatically (Flux image automation) |
| `ghcr.io/binhere/cs2307-frontend` | `frontend/Dockerfile` | `main-build.yml` (same run, same tag) | automatically (Flux image automation) |
| `ghcr.io/binhere/cs2307-embedding` | `deploy/embedding/Dockerfile` | `embedding-build.yml` (`deploy/embedding/**`) | by hand: tag pinned in iac-homelab |
| `ghcr.io/binhere/cs2307-seed` | `deploy/seed/build_seed.sh` | `seed-build.yml` (dataset, photo manifest, committed vectors, `deploy/seed/**`, import code) | by hand: tag pinned in iac-homelab |

Every tag is `main-<commit date, UTC, YYYYMMDDHHMMSS>-<sha7>`, built for `linux/amd64` only (the
server is x86_64). There is no `latest`. A push to `main` that touches `backend/` or `frontend/` is a
deploy: the workflow runs the backend tests and starts both images before pushing them.

## Runtime contract

| Container | Port | Probes | Writable paths | Notes |
|---|---|---|---|---|
| backend | 8000 | liveness `GET /health`, readiness `GET /ready` (PostgreSQL answers) | `/tmp` | run `alembic upgrade head` with the same image first (initContainer); logs on stderr |
| frontend | 3000 | `GET /` | `/tmp`, `/app/.next/cache` | `BACKEND_URL` at runtime; no domain or key in the image |
| embedding | 11434 | `GET /` | `/tmp` | embeds search queries only; model baked in; stays loaded |
| seed (CronJob) | none | exit code | `/tmp` | runs after the migrations; replaces the catalog when its own differs, otherwise does nothing |

All four run as user and group `10001` with a read-only root filesystem and no capabilities. The
backend starts without Neo4j (search falls back to PostgreSQL), without MinIO (leave
`MINIO_END_POINT` empty; the frontend shows an illustration) and without the embedding service
(keyword search only).

## Catalog vectors are computed on a developer machine

Embedding the 3,857 listings takes about 1.5 hours on 8 CPU cores and several hours on the server or
a CI runner, so neither does it. A developer with a powerful machine runs `deploy/seed/embed_catalog.sh`
and commits its output; the server only embeds search queries (about 80 ms each).

```bash
# Ollama running natively (GPU), same version and model as deploy/embedding/Dockerfile:
EMBEDDING_BASE_URL=http://host.docker.internal:11434/v1 deploy/seed/embed_catalog.sh
# or, without a native Ollama, the server's own image on this machine's CPU:
deploy/seed/embed_catalog.sh
git add data/gold/listing_embeddings.csv.gz data/gold/listing_embeddings.json
```

- The script refuses an endpoint that does not serve the Ollama version and model digest of
  `deploy/embedding/Dockerfile`. A GPU produces vectors that are not bit-identical to the server's CPU
  but practically the same (measured: cosine similarity ≥ 0.9994 between GPU and CPU vectors of the
  same listing; search scores differ by ≤ 0.003).
- `data/gold/listing_embeddings.csv.gz` stores each listing's vector with the SHA-256 of the search
  text it was made from. The seed build refuses vectors whose text has changed since, so any change to
  the dataset or to `backend/src/search/text_builder.py` fails CI until someone re-runs the script.
- Semantic search turns on only when every listing has a vector, which the seed guarantees.
- The deployed embedding tag must stay on the model and Ollama version the vectors were made with. To
  change either: edit `deploy/embedding/Dockerfile`, re-run `embed_catalog.sh`, commit, and deploy the
  new embedding and seed tags together; the seed's next run replaces the vectors in the database.

## The seed

`build_seed.sh` selects the 3,857 current-crawl listings (the listings of `data/gold`) from
`data/v3` (which also has the columns the backend imports), takes each listing's photos from
`data/gold/images_manifest.csv`, imports them with the backend image, attaches the committed vectors,
and exports the catalog tables to CSV. `seed.py` makes a deployment hold that catalog. It compares each
store with the image by a content digest (the SHA-256 of the store's files) and does nothing when they
match, so the cluster runs it every hour:

- PostgreSQL: `properties` (vectors included) and `listing_amenity_distances`. The digest of
  `catalog/*.csv` is kept in the table `catalog_imports`. When it differs, both tables are replaced in
  one transaction; the backend reads the old catalog until the commit.
- Neo4j: the statements of `backend/scripts/neo4j/import_search_graph.cypher`, with the CSV rows sent
  over Bolt, so the Job needs no access to the Neo4j import directory. The digest of `graph/*.csv` and
  the script is kept on the marker node `(:SeedImport {name: "catalog"})`. When it differs, every node
  is deleted (the backend never writes to Neo4j) and the graph is imported again, which takes about a
  minute of partial graph results; the marker is written last, so an interrupted run starts over.
- A new embedding model changes only the vectors, so it replaces PostgreSQL and leaves the graph.
- The exported rows leave out `created_at` and `updated_at` (they take the load time), so rebuilding
  the same data gives the same digest and replaces nothing.
- One run at a time: a PostgreSQL advisory lock is held for the whole run.
- The catalog tables belong to the seed: the API never writes them, and no other table has a foreign
  key to them. `interactions` and `recommendation_events` keep a `listing_id` without one.

Build and check it locally (Docker; a few minutes):

```bash
docker build -t cs2307-backend:check backend
docker build -t cs2307-embedding:check deploy/embedding
BACKEND_IMAGE=cs2307-backend:check SEED_IMAGE=cs2307-seed:check deploy/seed/build_seed.sh
BACKEND_IMAGE=cs2307-backend:check EMBEDDING_IMAGE=cs2307-embedding:check \
  SEED_IMAGE=cs2307-seed:check deploy/seed/check_seed.sh
python -m pytest deploy/seed/tests      # needs deploy/seed/requirements.txt
```

## Listing photos

The photos are the archive `images.zip` (33,882 files for the 3,857 listings, laid out as
`images/<property type>/<ward>/<listing id>/<file>`; Unicode NFC names). They are not in git and not
in any image. `data/gold/images_manifest.csv` lists the archive's files; regenerate it after the
archive changes:

```bash
unzip -Z1 images.zip | python3 -c "
import csv, sys, unicodedata
rows = sorted((name.split('/')[3], unicodedata.normalize('NFC', name)) for name in sys.stdin.read().splitlines()
              if not name.endswith('/'))
writer = csv.writer(sys.stdout); writer.writerow(['listing_id', 'object_key']); writer.writerows(rows)
" > data/gold/images_manifest.csv
```

MinIO object keys must equal the manifest keys, so upload the extracted `images/` directory as is
(for example `mc mirror images local/<bucket>/images`). The keys in `data/v3/master_v3.csv` are NFD
and include 1,484 photos of the old dataset that the archive does not have; the seed never uses them.
