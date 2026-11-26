# CS2307 — Claude Code instructions

Vietnamese real-estate search and AI advisor for Ho Chi Minh City: FastAPI backend,
Next.js frontend, PostgreSQL + pgvector, Neo4j, MinIO, LangGraph chat agent.
Read `README.md` for setup, `docs/system_overview.md` / `docs/web_functions.html`
for how each feature works, and `docs/system_architecture.html` for how the data, training,
deployment and inference pipelines fit together.

## Repository layout

| Path | What lives there |
|---|---|
| `backend/` | FastAPI app (`src/`), Alembic migrations, scripts, tests |
| `frontend/` | Next.js app; `app/api/*` are thin proxies to the backend |
| `pipeline/` | Data pipeline code: crawl → clean → OSM/OSRM → gold export, image scaling |
| `data/bronze/` | Raw crawl output (JSONL), OSM POIs, ward list, raw images (not committed) |
| `data/silver/` | Cleaned and enriched data, scaled images (not committed) |
| `data/gold/` | SQL tables and Neo4j node/edge CSVs built by `pipeline/build_gold_data.py` |
| `data/v3/` | Dataset the backend imports today (6,703 listings) and its build inputs |
| `databases/` | Local Neo4j, PostgreSQL and MinIO loaders for `data/gold` (PowerShell) |
| `price_prediction/` | Offline price model experiments on `data/v3` (output git-ignored). The backend ships no model: it trains its own from posted listings in `backend/src/services/price_training/` |
| `deploy/` | Embedding and seed images for the homelab deployment; see `deploy/README.md` |
| `.github/workflows/` | Image builds on push to `main` (`main-build`, `embedding-build`, `seed-build`) |
| `docs/` | Standalone documentation (Vietnamese) |

- Directory names are lowercase (`data/`, not `Data/`). macOS ignores case, Linux does not:
  after renaming a folder, check `git ls-files` shows the new spelling.
- Pipeline scripts resolve paths from their own location; they run from any directory.
- Never commit images, `.env`, `node_modules/`, `.next/` or local DB volumes.

## Commands

```bash
docker compose --profile graph-search up -d --build      # full stack incl. Neo4j
docker compose exec backend python scripts/build_search_index.py --skip-embeddings   # import catalog
docker compose exec backend python scripts/build_search_index.py \
  --skip-catalog-import --skip-graph-metadata --batch-size 32                        # embed catalog (one-time)

cd backend && python -m pytest -q          # backend tests; run WITHOUT a local .env (see pitfalls)
cd frontend && npx tsc --noEmit && npm test && npm run build   # npm test: node --test, no extra dependency
python -m pytest deploy/seed/tests         # seed build/load tests
```

A push to `main` that touches `backend/` or `frontend/` **deploys**: `main-build.yml` runs the backend
tests, starts both images read-only, pushes them, and Flux rolls them out. The embedding and seed
images are built automatically but deployed only by hand (`deploy/README.md` explains why).

## Code conventions (all languages)

- **Doc comment on every new or substantially changed module, function, class, component
  and hook**, stating its purpose (why it exists), input format and output format.
  Python: Google-style docstring (`Args:`, `Returns:`, `Raises:`). TypeScript: JSDoc with
  `@param` / `@returns`. Do not restate the name.
- **Comments explain why** (a constraint, a quirk, a bug avoided); never narrate the next line.
- **English** for code, comments, docstrings, logs and commit messages. User-facing text is Vietnamese.
- **Names are spelled-out nouns**; functions are verb phrases; booleans start with
  `is/has/should/can`. No abbreviations: `index` not `i`/`idx`, `error` not `e`/`err`/`exc`,
  `response` not `res`, `configuration` not `cfg`. Allowed: `id`, `url`, `api`, `json`, `html`,
  `css`, `props`, `ref`.
- Do not mass-rename or reformat code you are not otherwise changing.
- Every behaviour change or bug fix comes with a focused test.

## Backend rules

- **Never block the event loop**: inside `async def`, run DB calls, bcrypt, sklearn/pandas and
  `requests` through `run_in_threadpool` (or make the route `def`).
- **Settings** come from `config_value()` in `src/settings/config.py`; never `os.getenv` in `src/`.
- **Containers run as user 10001 with a read-only root filesystem**: write only to `/tmp`. Logs go to
  stderr (`LOG_FILE_PATH` adds a file, for local runs only). Migrations never run on start: Compose
  runs the `migrate` service, the cluster an initContainer.
- **Optional services stay optional**: the API must start and answer without Neo4j, MinIO
  (`MINIO_END_POINT` empty) or the embedding service; `/ready` checks PostgreSQL only.
- **API responses** use the envelope `{"data", "errors": [{"loc", "msg", "detail"}], "status"}`.
  Log exceptions with `logger.exception`, answer with a generic message; never return raw
  exception text (it leaks hosts and DB users). A database failure is a 5xx, never an empty 200.
- **LLM calls use the requesting user's own key (bring your own key).** Get the client with
  `active_llm_client(llm_model)` or the endpoint with `active_credentials()` from
  `src/services/llm/credentials.py`; routes bind credentials with
  `async with use_credentials(resolve_credentials(request.headers))`. Never store, log or put a
  key into LangGraph state. Map provider errors with `credential_error_from_exception` and
  answer with `credential_error_response`. A chat step never hides a failure behind a canned reply: it raises
  `ChatModelError` (502), shown in red in the chat with the step and the provider's own message (keys masked);
  any other exception reaches the client as its class name only.
- **Listings come only from users** (the `/dang-tin` form and zip uploads); the catalog seed still exists
  but is no longer the data source. After a listing is saved the server embeds it, finds its coordinates
  when missing (`scheduled_geocoding.py`) and enriches it (`src/services/listing_enrichment/`): its 5
  nearest amenities per category from `listing_enrichment/reference/amenities.json` (OpenStreetMap; drive
  = straight line × a per-category road factor measured with OSRM, rebuilt by
  `scripts/export_amenity_reference.py`) into `listing_amenity_distances`, and its Neo4j node with ward,
  former district, street, 150 m cell and amenity relationships (`listing_enrichment/graph.py`, same model
  as `scripts/neo4j/import_search_graph.cypher`). Semantic search runs once any listing has a vector.
- **Embeddings are fixed on the server** (`qwen3-embedding:0.6b`, 1024 dimensions, CPU via the
  `embedding` service) and never use a user's key. The server embeds search queries and, in the
  background, each listing a user posts (`/dang-tin`); the catalog's vectors are computed on a powerful
  developer machine with `deploy/seed/embed_catalog.sh`
  and committed (`data/gold/listing_embeddings.*`). Re-run it after any change to the dataset or to
  `src/search/text_builder.py`: CI refuses stale vectors. Changing the model, the Ollama version or the
  query/passage prefixes requires re-embedding the whole catalog.
- The rule-based query parser only accepts places listed in `src/search/known_locations.json`;
  regenerate it with `scripts/export_known_locations.py` after `data/v3/graph` changes.
- Posted listings (`src/services/listing_submission/`) are checked against Ho Chi Minh City's official
  2025 wards, the old-ward mapping and ward boundaries in `listing_submission/reference/`, built by
  `scripts/export_address_reference.py` from public sources; validation makes no network call. Listings
  saved without coordinates get them later from a job inside the backend (`scheduled_geocoding.py`, every
  `GEOCODING_INTERVAL_SECONDS`, one process at a time through an advisory lock) that asks Nominatim
  (1 request/second on the public server). The same user posting the same content again gets 409 with the
  earlier id (`content_fingerprint`), so re-uploading a fixed zip posts only the listings that failed.
- Neo4j queries go through `_timed_query()` so they carry the configured timeout.
- The price model is retrained inside the backend (`src/services/price_training/`), daily at 02:00 Vietnam time when
  at least 10% of the training rows are new, or by "Huấn luyện ngay" (`POST /v1/models/price/train`, signed-in users).
  A run's model is served only when its R² beats the served model on the fixed holdout set
  (`price_training_holdout`); runs, reports and deployed models live in `price_model_runs`. No model ships in
  the image: until the first run deploys one (it needs about 300 complete listings), pricing answers
  `PriceModelUnavailableError` ("Chưa có mô hình định giá"). Training and pricing share `price_training/preparation.py` (cleaning,
  filling in bedrooms and coordinates, encoding): change both sides there. Keep training inside
  `threadpool_limits`: OpenMP with one thread per core crawls on a busy node (11.5 s against 1.4 s).

## Frontend rules

- Call the backend only through `app/api/*` routes built on `proxyJson` (`lib/backend.ts`).
- Fetch with `fetchJson` and read errors with `extractError` / `readEnvelopeCode`
  (`lib/http.ts`); pass an `AbortController` signal and ignore `isAbortError`.
- AI requests send `llmHeaders()` (`lib/llm-settings.ts`); without a key the AI features stay
  disabled and link to `/cai-dat-ai`.
- The listing form's map (`components/listing-form/location-picker.tsx`) calls Photon (address search) and
  OpenStreetMap tiles straight from the browser: no key, fair use, attribution shown; the ward comes from
  `/api/listings/locate` (official boundaries), never from the geocoder.
- Zip uploads (`lib/listing-archive.ts`) are opened and their photos shrunk in the browser, then posted one
  listing per request: Cloudflare refuses request bodies over 100 MB, which a whole zip easily exceeds.
- Amenity keys and labels come from `lib/amenities.ts`; listing images go through
  `getPropertyImageSrc` with `PROPERTY_IMAGE_FALLBACK` (`lib/property-images.ts`).
- Accessibility: labelled inputs, `aria-label` on icon-only buttons, `type="button"`, visible
  focus, no `transition-all`, respect `prefers-reduced-motion`.
- Vietnamese UI copy: short, sentence case, no exclamation marks, polite requests start with
  "Vui lòng". Use the new tone-mark placement already used across the app: "xóa", "tùy chọn",
  "hóa", "tọa độ" (the Vietnamese docs in `docs/` keep their own existing style).

## Pitfalls already hit

- `src/settings/config.py` reads the repository `.env` at import time, so a local `.env`
  changes test results; run the test suite with it moved aside.
- `minio/minio` and `minio/mc` are gone from Docker Hub; compose uses the Chainguard images.
- The listing price in `data/v3` (and the crawl's card price) can be wrong: the crawler takes the
  first amount in the title (e.g. rent "HĐT 300 triệu/th" instead of "giá 170 tỷ").
- Image keys: `data/v3` stores them in Unicode NFD (macOS file names), the manifest and MinIO in
  NFC; the strings look identical but never match. The seed takes keys from
  `data/gold/images_manifest.csv` (NFC). 184 v3 listings also list old-dataset photos that do not exist.
- `docker run --env-file` and `kubectl --from-env-file` keep a trailing `# comment` as part of the
  value; keep comments on their own line in `.env.example`.
- Search quality: parsed price/ward/bedroom constraints only re-rank candidates; candidate
  generation does not use them yet. Measure changes on the 488 logged evaluation queries before
  tuning: `git show 8cf7bf5:Evaluation/results/Final/per_query.json` (raw_query + filters_applied).
