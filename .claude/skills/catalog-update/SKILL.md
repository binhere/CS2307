---
name: catalog-update
description: Carry a change to the listing catalog, the embedding model or the Ollama version from re-embedding through to the live homelab deployment (cs2307.reais.codes). Use when listings in data/v3 or data/gold, the graph CSVs in data/v3/graph, data/gold/images_manifest.csv, backend/src/search/text_builder.py, backend/scripts/build_search_index.py or backend/scripts/neo4j/import_search_graph.cypher change; when re-embedding the catalog; when deploy/embedding/Dockerfile changes or a different embedding model is proposed; when seed-build fails with "listings changed since their vectors were made" or "does not match deploy/embedding/Dockerfile"; or when asked to deploy a new seed or embedding image, including a new photo archive. Not for MinIO, Neo4j or cluster resource settings on their own.
---

# Catalog update: from a changed catalog or model to the live site

Backend and frontend deploy themselves on a push to `main`. The two images this skill is about do not:

- **embedding** (`ghcr.io/binhere/cs2307-embedding`) embeds search queries on the server.
- **seed** (`ghcr.io/binhere/cs2307-seed`) carries the catalog: the listings, their precomputed embedding
  vectors and the graph.

CI builds both on every relevant push, but they go live only when someone pins their tag by hand in the
iac-homelab repository. The seed runs as the CronJob `catalog` (hourly, at minute 17). Each run compares
a content digest (SHA-256) of its catalog files and of its graph files with what PostgreSQL and Neo4j
hold, and replaces only the store that differs.

Search fails silently when query vectors and catalog vectors come from different models or Ollama
versions. The backend only checks that every listing has a vector, not which model made it, so the
results are wrong and there is no error.

## 1. Classify the change

| Change | Re-embed | Tags to pin | The seed replaces |
|---|---|---|---|
| Listings or their data, the photo manifest, `text_builder.py`, `build_search_index.py`, the graph CSVs or Cypher import | only if search texts changed: `build_seed.sh` then fails with "listings changed since their vectors were made". Re-running `embed_catalog.sh` is always safe | `seed` only | whichever store's files changed: PostgreSQL for `catalog/*.csv`, Neo4j for the graph CSVs or Cypher |
| Embedding model or Ollama version | yes, every listing | `embedding` and `seed` in **one** commit | PostgreSQL only |
| `SEARCH_EMBEDDING_QUERY_PREFIX`, `_PASSAGE_PREFIX` or `SEARCH_EMBEDDING_REQUEST_DIMENSIONS` | not supported: `embed_catalog.sh` neither passes nor records them, and `check_seed.sh` does not give them to the backend | none | stop and tell the user |
| A model whose vectors are not 1024-dimensional | needs a migration of `properties.embedding vector(1024)` and its HNSW index (pgvector indexes `vector` only up to 2,000 dimensions; above that, `halfvec`), plus `EmbeddingModel.dimension` in `backend/src/search/embedding.py`, the `len(vector) == 1024` check in `.github/workflows/embedding-build.yml` and CLAUDE.md | none | stop and tell the user |

A different model also needs different memory. In iac-homelab, the embedding pod is limited to 2Gi
(`tenants/cs2307/apps/embedding.yaml`; it uses about 1.3 GiB with `qwen3-embedding:0.6b`), inside a 4Gi
request quota (`tenants/cs2307/kustomization.yaml`). The Deployment uses the Recreate strategy, so a
model that does not fit is OOM-killed after the old pod is already gone, and semantic search stays off.
A bigger model therefore needs room in the tenant quota and on the node first. That is an iac-homelab
decision: hand it over before any tag is pinned.

Photos ride along with the seed. The seed image carries `images_manifest.csv`, and the cluster's photos
CronJob runs `python /seed/photos.py` from the pinned seed image. It uploads whatever MinIO lacks from
the Google Drive archive named in `deploy/seed/photos.py`.

## 2. Repository side (any developer)

1. **Model or Ollama change, before editing anything:**
   - Check the output size. On a native Ollama run `ollama pull <model> && ollama show <model>` and
     read `embedding length`. If it is not 1024, stop (last row of the table).
   - Then set `OLLAMA_IMAGE`, `EMBEDDING_MODEL` and `EMBEDDING_MODEL_MANIFEST_SHA256` in
     `deploy/embedding/Dockerfile`. The digest is the model's `digest` in
     `curl -s localhost:11434/api/tags`.
   - After building the image (step 4), measure its memory with `docker stats --no-stream` after one
     request. If it needs more than the pod limit allows, the hand-over in section 3 must raise the
     limit and the quota.
2. **Listing count changes:** update `EXPECTED_LISTING_COUNT` in `deploy/seed/prepare_seed_input.py`,
   `EXPECTED_LISTINGS` in `deploy/seed/catalog_lib.sh` and `deploy/seed/check_seed.sh`,
   `EXPECTED_AMENITY_EDGES` in `deploy/seed/check_seed.sh`, and the counts in `deploy/README.md`.
   If photos changed, follow "Listing photos" in `deploy/README.md`:
   - upload the new `images.zip` to Google Drive, shared with anyone who has the link;
   - set `DEFAULT_ARCHIVE_URL` and `DEFAULT_ARCHIVE_SIZE` in `deploy/seed/photos.py`;
   - regenerate `data/gold/images_manifest.csv`.
   Once the new seed tag is pinned, the photos CronJob uploads the missing photos on its next run.
3. **Re-embed on a powerful developer machine,** never on the server or in CI. It takes about 10 minutes
   on an Apple GPU and about 1.5 hours on 8 CPU cores.
   ```bash
   # native Ollama of exactly the Dockerfile's version, with the model pulled:
   EMBEDDING_BASE_URL=http://host.docker.internal:11434/v1 deploy/seed/embed_catalog.sh
   # or the server's own image on this machine's CPU:
   deploy/seed/embed_catalog.sh
   ```
4. **Check locally:**
   ```bash
   docker build -t cs2307-backend:check backend
   docker build -t cs2307-embedding:check deploy/embedding
   BACKEND_IMAGE=cs2307-backend:check SEED_IMAGE=cs2307-seed:check deploy/seed/build_seed.sh
   BACKEND_IMAGE=cs2307-backend:check EMBEDDING_IMAGE=cs2307-embedding:check \
     SEED_IMAGE=cs2307-seed:check deploy/seed/check_seed.sh
   python -m pytest deploy/seed/tests
   ```
5. **Commit** the data, `data/gold/listing_embeddings.csv.gz` and `.json`, and the Dockerfile if it
   changed. Commit and push only when the user asks. A push to `main` that also touches `backend/` or
   `frontend/` deploys those immediately.
6. **After the push,** wait until `seed-build` (and `embedding-build` for a model change) are green.
   Then read the new tags anonymously:
   ```bash
   for name in seed embedding; do
     token=$(curl -fsS "https://ghcr.io/token?scope=repository:binhere/cs2307-$name:pull" \
       | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
     curl -fsS -H "Authorization: Bearer $token" "https://ghcr.io/v2/binhere/cs2307-$name/tags/list"; echo
   done
   ```
   For a model change, confirm that the seed's labels equal what the embedding image runs:
   ```bash
   docker pull -q --platform linux/amd64 ghcr.io/binhere/cs2307-seed:<seed tag>
   docker image inspect ghcr.io/binhere/cs2307-seed:<seed tag> --format '{{json .Config.Labels}}'
   docker run --rm --platform linux/amd64 --entrypoint sh ghcr.io/binhere/cs2307-embedding:<embedding tag> \
     -c 'ollama --version; cat /opt/ollama/EMBEDDING_MODEL; sha256sum /opt/ollama/models/manifests/registry.ollama.ai/library/<name>/<tag>'
   ```
   The values to compare are `cs2307.embedding-model`, `cs2307.embedding-model-sha256` and
   `cs2307.ollama-version`.

## 3. Pin the tags (iac-homelab: Lộc or the iac-homelab Claude session)

Never edit, commit or push iac-homelab from this repository. Hand Lộc this, filled in:

> CS2307 catalog update: <what changed>. In iac-homelab `tenants/cs2307/image-tags.yaml`, set entry
> `seed` to `<seed tag>` [and entry `embedding` to `<embedding tag>`, in the same commit]. [The model
> name changed: set `SEARCH_EMBEDDING_MODEL=<model>` in `tenants/cs2307/apps/backend.env` in that commit
> too.] [The new model needs <…> of memory: raise the `embedding` memory request and limit in
> `tenants/cs2307/apps/embedding.yaml` and the quota in `tenants/cs2307/kustomization.yaml` in that
> commit.] The seed's labels are model `<…>`, model digest `<…>` and Ollama `<…>`, and they match the
> embedding image. Do not touch the `backend` and `frontend` entries: Flux image automation owns them.
> Make one commit `tenants: <what changed>` through the `scripts/check.py` gate. Lộc pushes with
> `git pull --rebase && git push`.

Never pin the embedding tag alone: query and catalog vectors would come from different models.

## 4. Make it live (Lộc runs these on the server)

Give these commands to Lộc; do not run them yourself. Never use a bare `kubectl` on a laptop, because
its contexts are work clusters. Every command is `sudo k3s kubectl -n cs2307-prod …`, and anything that
changes the cluster comes after `sudo k3s kubectl get nodes` has listed `home-lab00`.

1. Wait until Flux has applied the push. This must print the new seed tag:
   `sudo k3s kubectl -n cs2307-prod get cronjob catalog -o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[0].image}'`
   For a model change, also wait for `sudo k3s kubectl -n cs2307-prod rollout status deployment/embedding`.
   The Deployment uses the Recreate strategy, so semantic search is off while the new pod loads.
2. Run the seed now; do not wait for minute 17. Until it has run, a model change gives wrong results:
   ```bash
   sudo k3s kubectl -n cs2307-prod create job --from=cronjob/catalog catalog-$(date +%Y%m%d%H%M)
   sudo k3s kubectl -n cs2307-prod logs -f job/catalog-<same suffix>
   ```
   Expect `PostgreSQL: replacing catalog …`, `Neo4j: replacing graph …` if the graph changed, then
   `done`. A Job started while the CronJob is running waits for it, then finds nothing left to do.
3. Model change only: `sudo k3s kubectl -n cs2307-prod rollout restart deployment/backend`. The backend
   keeps up to 512 query vectors in memory, and those still come from the old model.
4. Verify. This must print 5 results, `semantic True`, `graph True`, and an embedding coverage whose
   `embedded` equals `total` equals the expected listing count:
   ```bash
   sudo k3s kubectl -n cs2307-prod exec deploy/backend -- python -c "import json, urllib.request
   request = urllib.request.Request('http://localhost:8000/v1/search', method='POST', headers={'Content-Type': 'application/json'},
       data=json.dumps({'query': 'căn hộ 2 phòng ngủ gần trường học', 'top_k': 5, 'debug': True}).encode())
   data = json.load(urllib.request.urlopen(request, timeout=60))['data']
   print(len(data['results']), 'semantic', data.get('semantic_enabled'), 'graph', data.get('graph_enabled'),
         (data.get('debug') or {}).get('embedding_coverage'))"
   ```

## What a replacement does to users

- **PostgreSQL** is replaced in one transaction. Readers see the old catalog until the commit.
- **Neo4j** is deleted and imported again. Graph search returns partial results for about a minute.
- **Listing ids:** `properties.id` is assigned in import order, and detail links (`/chi-tiet/<id>`) use it.
  Adding or removing listings can shift ids, so old links (bookmarks, results stored in chat history) may
  open a different listing. A model-only change keeps every id.
- **Hand edits are overwritten:** the catalog tables belong to the seed, so any edit made by hand in
  production is lost at the next replacement.
- **No backup:** PostgreSQL has none. User tables (`users`, `conversations`, `messages`, `interactions`,
  `recommendation_events`) have no foreign key to the catalog and are never touched.
