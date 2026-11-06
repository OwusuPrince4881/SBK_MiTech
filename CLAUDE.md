# CLAUDE.md — MineMat AI

Persistent instructions for Claude Code sessions in this repository. Facts below were verified
against the code and by running the test suite during the initial audit on 2026-11-06 (46 tests
passed). `AGENTS.md` in the repo root is also authoritative; keep the two consistent and do not
contradict it. Where this file and `README.md` disagree about the current system, trust this file
and the code: `README.md` is stale (see "Documentation status").

## 1. Project overview and purpose

MineMat AI supports **preliminary assessment and management of soil and aggregate materials used
in mine construction**. It is decision support, never a replacement for qualified engineering
judgment. Today it is a working FastAPI backend that registers material samples, stores sample
photographs with metadata, and keeps a registry of datasets. There is no frontend, no assessment
logic, no laboratory-result model and no ML.

## 2. Product vision

A credible mining-technology product that could become a commercial startup. Credibility is the
asset: every output must be honest about its evidence, uncertainty and limits. Prefer a smaller
feature that is correct and traceable over a larger one that overclaims.

## 3. Core engineering principles

- Inspect the relevant existing code and tests before changing anything. Do not rely on docs alone.
- Small, testable, reversible changes. No unrequested rewrites or refactors. Preserve working behaviour.
- No new dependency or service without a concrete requirement; say what it is for.
- Never claim something works, or is complete, unless it was run and tested. Report failures with output.
- Never invent laboratory results, engineering thresholds, standards or scientific conclusions.
- Never delete or overwrite user data (`data/`, the SQLite file, uploads) without explicit permission.
- Do not commit unless the user asks.

## 4. Verified architecture (as implemented)

Backend only: `apps/api`, Python, FastAPI, synchronous (`def`) endpoints, SQLAlchemy 2.x ORM on **SQLite**.
(`AGENTS.md` names PostgreSQL as the *planned* store; nothing in the code uses PostgreSQL yet and no driver is installed.)

Request flow: **router** (`app/api/*.py`) → **service** (`app/services/*.py`) → **repository**
(`app/persistence/*_repository.py`) → **ORM model** (`app/domain/*.py`). Pydantic v2 request/response
models live in `app/schemas/`. Services return Pydantic response schemas, not ORM objects.

Wiring (`app/main.py`): `create_app(database_url=None, upload_directory=None, max_upload_size_bytes=None)`.
The lifespan builds the engine, runs `Base.metadata.create_all(engine)`, and sets
`app.state.session_factory` (`expire_on_commit=False`) and `app.state.upload_settings`.
`app = create_app()` runs at import time but is import-safe: no database or file access happens until
the lifespan starts. Routers: datasets, samples, sample_images. There is **no** middleware, CORS,
authentication, exception handler or logging anywhere. `/docs`, `/redoc`, `/openapi.json` are on.

Dependencies injected into routes: `get_db` (`persistence/database.py`, session per request, closed on
exit) and `get_upload_settings` (`settings/uploads.py`).

Configuration is environment variables only, read at startup; nothing loads a `.env` file and there is
no `.env.example`:

| Variable | Default |
|---|---|
| `DATABASE_URL` | `sqlite:///<repo>/data/minemat_ai.db` (`PROJECT_ROOT` = `parents[4]` of `persistence/database.py`) |
| `IMAGE_UPLOAD_DIR` | `<repo>/data/uploads` |
| `MAX_IMAGE_UPLOAD_SIZE_BYTES` | 10 MiB; must be > 0 |

Routes (all unauthenticated, no pagination):

| Route | Success | Errors |
|---|---|---|
| `GET /`, `GET /health` | 200 (`/health` does not check the DB) | |
| `POST /api/v1/samples`, `GET /api/v1/samples`, `GET /api/v1/samples/{id}` | 201 / 200 | 404 "Sample not found", 422 |
| `POST /api/v1/samples/{id}/images` (multipart field `image`) | 201 | 404, 413, 415, 422 |
| `POST /api/v1/datasets`, `GET /api/v1/datasets`, `GET /api/v1/datasets/{id}` | 201 / 200 | 404 "Dataset not found", 409 "Dataset is already registered", 422 |

There is no GET/download endpoint for images and no PUT/PATCH/DELETE anywhere.

Models: `Sample` (`samples`), `SampleImage` (`sample_images`, FK to `samples.id` ON DELETE CASCADE,
unique `stored_filename` and `storage_path`), `Dataset` (`datasets`, unique `(source_name, dataset_name)`,
unique `source_url`, unique `doi`, `licence` NOT NULL, free text). `Dataset` has no link to manifests, no
category, no checksum and no version.

## 5. Directory structure (real directories only)

```
apps/api/app/{api,domain,persistence,schemas,services,settings,storage}   implemented
apps/api/app/{engineering,ml}      EMPTY packages (placeholders for future logic)
apps/api/scripts/validate_dataset_manifest.py
apps/api/tests/                    pytest suite (conftest.py + 5 test files)
apps/api/migrations/               only .gitkeep — no Alembic yet
apps/api/requirements.txt
apps/web, packages, tests/         .gitkeep placeholders only
docs/dataset_strategy.md, docs/dataset_manifest.schema.json, docs/examples/example_dataset_manifest.json
data/                              git-ignored local data (see section 11)
```

`data/` holds `datasets/{raw,processed,manifests}/`, `evaluation/umat_practicals/`, `uploads/`, `README.md`,
and the local dev database `minemat_ai.db`. Only `.gitkeep` files and `data/README.md` are tracked.

## 6. Technology stack

Python (no version is declared anywhere; runtime code needs 3.10+ because SQLAlchemy evaluates `X | None` annotations,
one test needs 3.11+ for `datetime.fromisoformat` with a `Z` suffix; the dev venv is 3.14.3), FastAPI, Starlette,
Pydantic v2, SQLAlchemy 2.x, SQLite, python-multipart, uvicorn, pytest, httpx (TestClient), jsonschema.
Installed in `.venv` on 2026-11-06: fastapi 0.162.0, starlette 1.10.0, pydantic 2.14.2, SQLAlchemy 2.1.6,
jsonschema 4.26.0, pytest 9.2.0, httpx 0.28.1, python-multipart 0.0.33, uvicorn 0.54.0.
`requirements.txt` pins only `SQLAlchemy>=2.0,<3.0` and `jsonschema>=4.21,<5.0`; the rest are unpinned and
test-only packages (`pytest`, `httpx`) share the file with runtime ones. The virtualenv is `.venv` at the
repo root (Windows layout: `.venv\Scripts\python.exe`).

## 7. Coding conventions (observed in the code; follow them)

- British spelling `licence` in fields and docs.
- UUID primary keys (`Uuid(as_uuid=True)`), generated in services with `uuid4()`.
- Timestamps: `datetime.now(timezone.utc)` into `DateTime(timezone=True)`. SQLite returns naive values, so
  each response schema has an `ensure_utc_timestamp` validator that re-tags them as UTC.
- API prefix `/api/v1`; plural nouns; routers use `status.HTTP_*` constants and `response_model`.
- Naming: routes `create_*/read_*/upload_*`; services `register_*/retrieve_*`; repositories `save_*/list_*/get_*`.
- Request schemas strip whitespace (`str_strip_whitespace`). New request schemas must set `max_length` on every
  string field (`DatasetCreate` does; `SampleCreate` does not) and should use `extra="forbid"` (neither does yet,
  so unknown JSON fields are silently dropped).
- Type hints everywhere, `X | None` unions.
- Domain/service exceptions subclass `ValueError` and are mapped to HTTP codes in the router.
- Tests and examples use fictional placeholders only (`example.invalid`, `fictional-placeholder-*`).

## 8. Architecture and separation-of-concerns rules

Follow `AGENTS.md`: keep HTTP handling, engineering logic, domain concepts, persistence and ML in their own modules.

For new code:
- Routers: parse/validate transport input, call one service function, map domain errors to HTTP. No ORM or `Session`
  calls, no business rules.
- Services: orchestrate. No `fastapi` imports, no `HTTPException`. Raise domain exceptions.
- Repositories: queries and persistence only. Do not import from `app/schemas`.
- `app/engineering` and `app/ml` must stay free of FastAPI, SQLAlchemy and request/response schemas: plain
  functions or dataclasses, invoked from services. ML may only return **visual observations**.
- Do not add services/processes/queues beyond what a concrete requirement needs; ML inference goes through the backend.

Existing deviations — do not copy them, and do not "fix" them unprompted: repositories call `session.commit()`
themselves (no unit of work, so multi-step operations are not atomic); `persistence/dataset_repository.py` imports
`schemas.dataset.DatasetCreate`; `persistence/database.py` and `settings/uploads.py` import `fastapi.Request`;
`settings/uploads.py` imports `PROJECT_ROOT` from `persistence.database`; `api/sample_images.py` imports storage
exceptions directly. Settle transaction ownership before adding any multi-step write (for example dataset import,
sample plus laboratory result).

## 9. Database and persistence rules

- Schema comes from `Base.metadata.create_all` at startup. It only creates missing tables; it never alters existing
  ones, so a changed model **will not reach an existing `data/minemat_ai.db`** (a new column will fail with "no such column").
- **Introduce Alembic (a justified new dependency) before** any of: a column/constraint/index change on a table that
  holds data you care about; adding a dataset category or manifest link; adding laboratory-result tables; changing
  Dataset uniqueness rules; moving to PostgreSQL; running beyond one local developer process. Add a constraint
  `naming_convention` to `Base.metadata` first, and make Alembic and the app share one model-import module
  (`main.py` currently registers `Sample` only as an import side effect).
- Never open, modify, delete or `drop_all` the real `data/minemat_ai.db`. Tests use temporary databases.
- SQLite caveats: `PRAGMA foreign_keys` is **not** enabled, so `ON DELETE CASCADE` is inert (also, ORM cascades never
  delete files); datetimes come back naive; `:memory:` URLs are not supported by the engine setup; no WAL/busy timeout.
- Keep photographs on disk and only references/metadata in the database. PostgreSQL is planned, not implemented.

## 10. File upload and storage security requirements

Invariants that exist today (keep them):
- Type is decided by magic bytes (JPEG/PNG/WebP, first 12 bytes) and the client `Content-Type` must equal the sniffed type.
- Stored name is server-generated: `<upload_dir>/<sample_uuid>/<uuid4hex>.<jpg|png|webp>`, extension from the sniffed type,
  opened with exclusive create (`"xb"`). `sample_id` is a typed UUID. No request-controlled value reaches a path.
- The client filename is sanitised (basename, control characters removed, 255 max) and stored as metadata only.
- Size is checked while copying; a failed upload removes its partial file and an empty sample directory.
- The upload directory defaults outside `apps/api` (under git-ignored `data/uploads`).

Known gaps (verified; do not assume otherwise): validation is signature-only and images are never decoded (the tests
upload fake JPEG bytes and expect 201); Starlette spools whole multipart files to temporary storage with no total byte
cap, so the size limit is enforced only after the body is received; EXIF/GPS metadata is stored byte-for-byte;
`SampleImageResponse` exposes `storage_path`; the write is not atomic (no temp file plus rename); if the DB commit
succeeds but `refresh` raises, the service deletes a file whose row is already committed; no authentication or quota.

Rules for new upload/serving code: never use a client filename or client-supplied extension in a path; keep the
sniff-plus-declared-type check; make decoding the validator before any ML/processing step opens the file; do not add a
file-serving endpoint until authentication, access control, `X-Content-Type-Options: nosniff`, forced download, and an
EXIF/location privacy policy are designed; address images by id, not by `storage_path`; enforce request-body limits at the
server/proxy as well as in code.

## 11. Dataset governance and provenance requirements

`docs/dataset_strategy.md` defines three data categories that must stay separate and identifiable:
1. **Public research datasets** — only after provenance, licence and permitted uses are reviewed and recorded.
2. **UMaT practical samples** — independent evaluation only; never training, tuning or validation data.
3. **Future Ghanaian field data** — needs a collection protocol, permission and consent where applicable, location
   precision/privacy handling.

Rules: preserve original research data (`data/datasets/raw/` is treated as immutable; derived data goes under `processed/`
with documented steps); record split assignments, never infer them from folder names; record source and licence before
accepting a dataset and do not ingest or use data whose rights are unclear; keep real datasets, photographs and private or
identifying data out of Git; never present fabricated values as real measurements.

What is and is not enforced today (verified):
- The manifest schema (`docs/dataset_manifest.schema.json`, JSON Schema 2020-12, `schema_version` const `"1.0"`,
  `additionalProperties: false` everywhere) controls only `split` (`train|validation|test|independent_evaluation|unassigned`).
  Everything else is free text. It has **no** dataset category, checksums, dataset version, custodian, consent/authorisation,
  location-privacy, or derivation fields.
- `apps/api/scripts/validate_dataset_manifest.py` performs JSON Schema validation only: it never opens image files and cannot
  detect duplicate `image_id`s, one `sample_id` appearing in several splits (leakage), or `independent_evaluation` images
  outside the UMaT area. `format: date-time` for `captured_at` is **not enforced**: the installed `jsonschema` has no
  `date-time` checker without an extra package (`rfc3339-validator`), which is not in `requirements.txt`. Treat
  `captured_at` as unvalidated.
- The registry (`Dataset`) and the manifest are separate, unlinked definitions with different names and types: registry
  `dataset_name`/`source_name`/`source_url`/`doi`/`licence` (single string) versus manifest `dataset.name`/`source.name`/
  `source.reference`/`source.licence{name,reference,usage_notes}`; registry `dataset_id` is a server UUID, manifest
  `dataset_id` is a free string. The registry stores no manifest path, hash or schema version, and the app never imports
  the validator. The registry accepts any licence text (including "Unknown; use not authorized") and has no
  review/approval status.
- Manifest `sample_id`/`image_id` are free strings in a dataset's own namespace. Never treat them as `Sample.id` /
  `SampleImage.id`, and do not import public images as `Sample` rows (they would need placeholder values for NOT NULL
  fields). `relative_path` has two meanings: in manifests (base directory undefined; the example implies `data/`) and in
  `SampleImage.relative_path` (relative to the upload directory).
- Laboratory measurements in the manifest hang off images and carry only measurement/value/unit/method. They belong to
  physical samples and need standard, date, laboratory and uncertainty before real use.
- `.gitignore` excludes `data/datasets/manifests/*`, so real manifests (provenance records) are not version-controlled;
  how to track reviewed manifests while keeping restricted evaluation labels out of Git is an open decision for the owner.

Before any dataset import (Milestone 6.1 or later), the owner must decide: the registry-to-manifest mapping and identity,
a controlled dataset category and evaluation-only flag, per-file checksums, manifest hashing/versioning, a licence-approval
gate, and where manifests live. Do not guess these.

## 12. Testing expectations

Run the suite from `apps/api` (Windows; adapt the interpreter path on other platforms):

```powershell
cd apps\api
..\..\.venv\Scripts\python.exe -m pytest -v
```

Verified 2026-11-06: **46 passed** in about 7 s with one Starlette deprecation warning (`httpx` with
`starlette.testclient`). Bare `pytest` and runs from the repo root fail with `ModuleNotFoundError: No module named 'app'`
because there is no pytest configuration (no `pytest.ini`/`pyproject.toml`) and tests import `app.*` and `scripts.*`.
If the count changes, update this line.

Validate a manifest (exit 0 valid; **exit 2 for any failure**, including a missing file, because it uses `argparse` errors):

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\validate_dataset_manifest.py docs\examples\example_dataset_manifest.json
```

Run the API (import verified; the server was not started during the audit): from `apps/api`,
`..\..\.venv\Scripts\python.exe -m uvicorn app.main:app`. Starting it opens and runs `create_all` on the real
`data/minemat_ai.db`; set `DATABASE_URL`/`IMAGE_UPLOAD_DIR` to temporary locations for experiments.

Expectations:
- Use the `client` fixture in `tests/conftest.py`: per-test temporary SQLite file, temporary upload directory, 128-byte upload
  limit, lifespan run via `with TestClient(...)`. Inspect state through `client.app.state.session_factory` and
  `client.app.state.upload_settings`. Never import `app.main.app` into a test or call `create_app()` without arguments:
  that would write to the real `data/` paths.
- Test behaviour and business rules, not only status codes. Make each rule fail independently (one change per test), assert the
  error `detail`, and prove tests can fail. New endpoints need: success, each validation failure, not-found, duplicate/conflict,
  and persistence checks. New upload/storage code needs: empty, wrong type, spoofed type, size at limit and limit+1,
  traversal-style filenames, and cleanup when storage or the DB fails.
- Known coverage gaps (do not claim these are tested): empty/header-only/exact-limit uploads, missing `image` field, content-type
  `None`/`octet-stream`, DB-failure file cleanup, DOI-only and DOI-casefold duplicates in isolation (the existing 409 test also
  matches on `source_url`), the `IntegrityError` branch in `dataset_service`, list ordering with several rows, manifest
  `captured_at`, duplicate image ids, absolute/drive-letter `relative_path`, validator CLI exit codes.
- Run the **full** suite before reporting any change as done. Report counts, failures and warnings as they are.
  Do not weaken or delete a test to make it pass.

## 13. Git and version control expectations

- Branch `main`; five commits so far with short imperative summaries (no enforced convention).
- Do not commit, push, amend, rebase or reset unless the user asks. When asked to commit, stage specific files, never `git add .`,
  and use the attribution lines the session instructions specify.
- Never stage or commit: anything under `data/` except `.gitkeep`/`README.md`, `*.db`, `.env*`, caches, `.venv`.
  `.vscode/` is currently untracked and **not** ignored; do not add it without asking.
- `.gitignore` has no `*.db`/`*.sqlite*`/`*.log` patterns outside `data/`; keep `DATABASE_URL` and `IMAGE_UPLOAD_DIR` absolute
  and inside `data/` (or outside the repo) so stray files are not committed.
- Keep secrets, environment-specific config, generated files and datasets out of version control.

## 14. Scientific and engineering limitations

MineMat AI must keep three evidence tiers separate in code, schemas, APIs and UI wording:
1. **Visual observations from photographs** — descriptive only, never verified results.
2. **Laboratory measurements** — recorded with value, unit, method/standard, date, laboratory and the physical sample they belong to.
3. **Engineering evaluation** — only from laboratory results combined with appropriate, validated criteria and standards, stating
   evidence, uncertainty and limitations.

A photograph alone must never be used to claim CBR, compaction, bearing capacity, strength, grading compliance or any other
engineering requirement. Do not infer suitability when laboratory results or validated criteria are missing. Do not invent
thresholds, soil/aggregate classes or standards, claim regulatory compliance, or present model output as a professional engineering
decision. No hard-coded engineering thresholds, classes or compliance claims exist in the repo today; keep it that way. Any future
engineering evaluation belongs in `app/engineering`, any visual model behind `app/ml`, both invoked from services.
UMaT evaluation samples are never training, tuning or validation data.

## 15. Implementing new milestones

1. Read `AGENTS.md`, this file, `docs/dataset_strategy.md` (for data work) and the code and tests you will touch.
2. State the goal, the smallest useful slice, what existing behaviour must be preserved, and any open decision from
   sections 9–11 that the owner must settle. Ask rather than guess.
3. Follow the layering in section 8; reuse existing helpers and patterns; add no dependency without a stated need.
4. Write meaningful tests alongside the change (section 12), then run the full suite and report honestly.
5. Update docs that the change makes wrong (`README.md` is already stale). Do not edit `AGENTS.md` unless asked.
6. Leave the working tree uncommitted and summarise what changed, what was verified, and what was not.

## 16. Reviewing existing code before making changes

Before editing: read the file, its callers, and its tests; check `git log -p` for the file if intent is unclear; check whether the
behaviour is asserted by a test; list what could break (API contract, persisted data, file layout, existing rows in
`data/minemat_ai.db`); confirm the change does not require a schema migration (section 9); confirm it does not touch data or
uploads; decide how you will test it. Do not assume code is correct because it exists, and do not assume docs match the code.

## Known issues register (audit of 2026-11-06; remove items as they are fixed)

High — design gaps to settle before dataset import (not exploitable today; no import/training code exists):
- Registry and manifest are unlinked and diverging; no dataset category/evaluation-only flag, so the UMaT separation is stated, not enforced.
- No checksums or immutability enforcement for raw data; validator is schema-only (no leakage/duplicate/existence checks).
- Production blockers: no authentication/authorisation, CORS policy, rate limits, request-body limit or logging.

Medium:
- Manifest `format: date-time` silently not validated (verified).
- Upload hardening gaps listed in section 10; non-atomic write; file deleted after a committed row when `refresh` fails.
- No unit of work (repositories commit); layering inversions listed in section 8.
- `create_all` only, no Alembic, unnamed constraints, SQLite foreign keys off.
- Test runner not self-configuring; weak/confounded dataset duplicate tests; coverage gaps listed in section 12.
- `README.md` stale; `AGENTS.md` still calls `apps/api` "planned" and omits the services/schemas/storage/settings layers.
- Unpinned dependencies, no Python version declared, test and runtime dependencies mixed; `httpx` TestClient deprecation warning.
- Manifests git-ignored (policy decision pending); manifest lab measurements modelled on images, not physical samples.
- `DatasetCreate` and `SampleCreate` silently ignore unknown fields; `SampleCreate` has no length limits; list endpoints are unbounded.

Low: duplicated `ensure_utc_timestamp` validator (three schema files); dead model-level UUID defaults (services set ids); `SampleImage`
attribute synonyms (`id`/`image_id`, `storage_path`/`relative_path`) and inconsistent PK names (`id` vs `dataset_id`); `Sample.created_at`
exposed as `registered_at`; every `IntegrityError` reported as a duplicate; dataset duplicate detection is case-sensitive and does not
normalise URLs/DOIs; `main.py` leaks the engine if upload settings fail; error responses undocumented in OpenAPI; `.vscode/` untracked
and unignored; manifest `$id` uses an unverified domain and `1.0.0` versus `schema_version` `"1.0"`; validator does not catch
non-UTF-8 manifests; the per-sample directory can race with concurrent cleanup (500).

## Documentation status

`README.md` still describes a scaffold with no application features, database integration or installed packages and says the backend
will use PostgreSQL; it has no run, test or configuration instructions. `AGENTS.md` is accurate about rules and principles but describes
`apps/api` and `apps/web` as planned. `docs/dataset_strategy.md` promises records (processing steps, consent, location precision,
authorisation, category) that manifest schema 1.0 cannot carry. `data/README.md` does not mention that the dev database lives at
`data/minemat_ai.db`.
