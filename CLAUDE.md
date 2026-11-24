# CS2307 — Claude Code instructions

Vietnamese real-estate search and AI advisor for Ho Chi Minh City: FastAPI backend,
Next.js frontend, PostgreSQL + pgvector, Neo4j, MinIO, LangGraph chat agent.
Read `README.md` for setup and `docs/system_overview.md` / `docs/web_functions.html`
for how each feature works.

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
| `price_prediction/` | Price model training; writes `backend/src/services/inference/artifacts/best_model.joblib` |
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
cd frontend && npx tsc --noEmit && npm run build
```

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
- **API responses** use the envelope `{"data", "errors": [{"loc", "msg", "detail"}], "status"}`.
  Log exceptions with `logger.exception`, answer with a generic message; never return raw
  exception text (it leaks hosts and DB users). A database failure is a 5xx, never an empty 200.
- **LLM calls use the requesting user's own key (bring your own key).** Get the client with
  `active_llm_client(llm_model)` or the endpoint with `active_credentials()` from
  `src/services/llm/credentials.py`; routes bind credentials with
  `async with use_credentials(resolve_credentials(request.headers))`. Never store, log or put a
  key into LangGraph state. Map provider errors with `credential_error_from_exception` and
  answer with `credential_error_response`.
- **Embeddings are fixed on the server** (`qwen3-embedding:0.6b`, 1024 dimensions, CPU via the
  `embedding` service) and never use a user's key. Changing the model or the query/passage
  prefixes requires re-embedding the whole catalog (`--force`).
- The rule-based query parser only accepts places listed in `src/search/known_locations.json`;
  regenerate it with `scripts/export_known_locations.py` after `data/v3/graph` changes.
- Neo4j queries go through `_timed_query()` so they carry the configured timeout.

## Frontend rules

- Call the backend only through `app/api/*` routes built on `proxyJson` (`lib/backend.ts`).
- Fetch with `fetchJson` and read errors with `extractError` / `readEnvelopeCode`
  (`lib/http.ts`); pass an `AbortController` signal and ignore `isAbortError`.
- AI requests send `llmHeaders()` (`lib/llm-settings.ts`); without a key the AI features stay
  disabled and link to `/cai-dat-ai`.
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
- Search quality: parsed price/ward/bedroom constraints only re-rank candidates; candidate
  generation does not use them yet. Measure changes on the 488 logged evaluation queries before
  tuning: `git show 8cf7bf5:Evaluation/results/Final/per_query.json` (raw_query + filters_applied).
