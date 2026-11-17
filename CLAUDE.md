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
apps/api/app/dataset_manifest/     offline manifest tooling (validation, splits, leakage audit); pure Python, no FastAPI/SQLAlchemy
apps/api/scripts/{validate_dataset_manifest,generate_dataset_splits,audit_dataset_leakage}.py
apps/api/tests/                    pytest suite (conftest.py, dataset_builders.py + the test files, `test_dataset_*.py` for the dataset tooling)
apps/api/migrations/               only .gitkeep — no Alembic yet
apps/api/requirements.txt
apps/web, packages, tests/         .gitkeep placeholders only
docs/dataset_strategy.md, docs/dataset_workflow.md, docs/dataset_manifest.schema.json (1.0), docs/dataset_manifest_1_1.schema.json,
docs/dataset_manifest_schema_1_1.md, docs/examples/example_dataset_manifest.json, docs/examples/example_dataset_manifest_1_1.json
data/                              git-ignored local data (see section 11)
```

`data/` holds `datasets/{raw,processed,manifests}/`, `evaluation/umat_practicals/` (restricted), `uploads/` (restricted), `README.md`,
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
2. **UMaT practical samples** — restricted: never used for training, validation, testing or independent AI evaluation (see below).
3. **Future Ghanaian field data** — needs a collection protocol, permission and consent where applicable, location
   precision/privacy handling.

Rules: preserve original research data (`data/datasets/raw/` is treated as immutable; derived data goes under `processed/`
with documented steps); record split assignments, never infer them from folder names; record source and licence before
accepting a dataset and do not ingest or use data whose rights are unclear; keep real datasets, photographs and private or
identifying data out of Git; never present fabricated values as real measurements.

Restricted material: UMaT practical samples, and anything the MN273 brief classifies as RED-zone (photographs, raw laboratory
records, supplier and purchase records, the team dataset), must not be used for training, validation, testing or independent
AI evaluation. An authorisation reference, approval field, licence record or dataset category never overrides this, and the code
has no exception mechanism. Any future proposed exception needs explicit written permission from the appropriate authority and a
separate governance review before any processing, outside the tools. Claude sessions must not read, hash, list or process the
restricted locations (`data/evaluation`, `data/umat_practicals`, `data/uploads`) or this material.
What is and is not enforced today (verified):
- Manifest schema 1.0 (`docs/dataset_manifest.schema.json`, `schema_version` const `"1.0"`) controls only `split`
  (`train|validation|test|independent_evaluation|unassigned`); everything else is free text, with **no** dataset category,
  checksums, permissions, custodian, location-privacy or derivation fields. Schema 1.1 (`docs/dataset_manifest_1_1.schema.json`,
  Milestone 2B, documented in `docs/dataset_manifest_schema_1_1.md`) adds the dataset category (`public|synthetic|umat_practical|
  ghana_field`), licence version, URL and preserved evidence (a checksummed snapshot of the licence text, an archive link or a
  reference, with its capture date), permissions (status, permitted uses, commercial-training status and its basis, reviewer and
  date, an informational authorisation reference), the basis of the physical sample IDs, capture session IDs, per-file SHA-256, a dataset-declared
  label vocabulary and provenance, and, for `ghana_field` only, an optional dataset-level `field_governance` record (collection
  permission, consent, location privacy and handling, reviewer and date, permitted uses). Both are JSON
  Schema 2020-12 with `additionalProperties: false`. 1.1 still has **no** per-photograph location, laboratory-measurement, video or
  registry-link fields. **Ghana field data fails closed:** a `ghana_field` dataset cannot be approved, listed with a use, split or
  cleared until every safeguard in `field_governance` is resolved (`docs/ghana_field_governance.md`); the tools check that the record is
  present and consistent, never that it is true, and never fill a gap in. Material categories are never defined by this repo.
- `apps/api/scripts/validate_dataset_manifest.py` (library: `app/dataset_manifest/`) validates both versions: structure, unique
  IDs and paths, one split per physical sample, evaluation isolation (evaluation records never share a manifest with other splits,
  in either version), derivations, and for 1.1 the licence/permission rules, label vocabulary and dates. **Restricted material is
  refused in every tool:** category `umat_practical` is an error for every use (an authorisation reference is ignored), manifests
  with UMaT, MN273 or "University of Mines and Technology" in **any string** (authorisation reference, notes, licence text, sample IDs
  and paths included) are refused whatever their category (the letters `umat` are caught even glued into `UMaTData` or `myumat`,
  except inside a short list of innocent words such as Sumatra, pneumatic, humate, Umatilla, rheumatic; any other word containing
  `umat` is refused; best-effort, and lower-case `teammn273` is not caught), source manifests get the same check, and the tools refuse the
  restricted locations (`data/evaluation`, `data/umat_practicals`, `data/uploads`) lexically, before touching the file system, for
  datasets, manifests, source manifests and outputs (`app/dataset_manifest/restricted.py`). **A use is accepted only against
  verified files:** `--use` (and the splitter) need `--dataset-dir`, files and the licence **snapshot** matching their recorded
  checksums, and a snapshot (a reference or link does not clear a use); the output says "recorded permission accepted", never
  "confirmed", because the tools do not judge licences; commercial training is cleared separately and only if training is permitted.
  With `--dataset-dir` the validator also checks that the recorded path matches the on-disk letter case (judged from the
  directory listing), reports unlisted files (an unlisted image, video **or archive/container**, recognised by extension or by the
  first 512 bytes, is an error, and a housekeeping name such as `.gitkeep` is judged by content, so a JPEG so named is an error),
  and treats any symbolic link or junction as an error (never followed); `--ignore-file` leaves out only files not recognised as
  media or archives and every ignored file is listed. **The extension and signature lists are practical, not complete:** a format
  with neither a listed extension nor a recognised signature is only a warning, and a pattern can hide it; never claim that every
  image or video format is detected. Derivations across datasets need the source manifest (`--source-manifest`, or the audit) and fail closed
  otherwise; nothing may be derived from restricted or evaluation records, and **a derivative inherits its source's governance**:
  each source manifest is fully validated and a derivation from an invalid, denied, unapproved, unresolved-Ghana, snapshot-less or
  use-ineligible source is refused identically by the validator, splitter and audit (`derived-from-*-source` codes; warnings only
  while the derivative is a draft). **A source's evidence is verified, not trusted:** each source is bound explicitly to its
  dataset directory (`--source-dataset-dir DATASET_ID=DIR` for the validator and splitter; the audit's `--dataset-dir
  DATASET_ID=DIR`; never inferred from the manifest's location) and its record files and licence snapshot are checked there as
  `--dataset-dir` checks a manifest's own, recursively down the chain, with inherited uses (`derived-from-source-files-not-verified`,
  `derived-from-source-files-invalid`; same or nested directories are `ambiguous-dataset-directory`). **Commercial training is
  inherited too:** when it is requested, needed downstream, or recorded as `permitted` by a derived dataset, every source down the
  chain must record `permitted` with a meaningful basis (`derived-from-source-commercial-training-denied` / `-not-permitted` /
  `-unsupported`); a derived dataset cannot grant a broader commercial permission than its sources. Files are hashed once per run
  and never cached across runs; verification shows the evidence is intact, not that the licence permits the use or that recorded
  consent is true, and a file changed after it was hashed is not detected until the next run (time of check). `generate_dataset_splits.py` makes seeded
  splits grouped by physical sample (never by image), refuses evaluation and restricted data, and records every warning (small
  validation/test split, missing category, unlabelled or conflicting samples, one image per sample) in the split file, rendering all
  its output first and writing every output file or none. **It never writes inside the dataset directory** (`--output` and
  `--output-manifest` resolving into it are refused, with or without `--overwrite`) and never over a source manifest.
  `audit_dataset_leakage.py`
  runs the full validation on every manifest (so an invalid manifest is an audit error), takes `--dataset-dir DATASET_ID=DIR`, and
  reports identical content, cross-manifest derivations and capture sessions; its result is `PASS WITH LIMITATIONS` when identity
  is unverified (assumed, one image per sample, schema 1.0), a manifest's files were not verified, or a validation/test/evaluation
  split has fewer than 30 physical samples or a supplied `--split-file` (checked against the manifest) carries warnings. It prints on
  every run that it checks leakage only and that a PASS is not approval of the splits. An
  `assumed_each_image_distinct` identity is never treated as verified and the splitter needs a written reason
  (`--accept-unverified-sample-identity REASON`), recorded in the split file; `inferred_by_documented_analysis` needs notes. All
  tools exit 2 on any failure with a message, never a traceback (hostile JSON, custom schemas and unexpected errors included).
  **The tools are offline:** a custom `--schema` may only use references inside itself (`#...`; a remote, `file:` or relative
  `$ref`, or an `$id`, is refused before anything is opened), validators are built with a registry that cannot retrieve, a schema in a
  restricted location is refused unread, and the built-in schema always applies, so a custom schema only adds requirements. All
  output is passed through `safe_text` (plain ASCII, non-ASCII shown as escapes) so a non-Latin path cannot make a finished run
  fail. Evidence quality: a licence snapshot must be real plain text, not blank (`licence-snapshot-empty`), not an image, video,
  archive, PDF or other binary content (`licence-snapshot-not-text`; recognised by extension and a few signatures, **not
  complete**, and only the first MiB is inspected) and not a placeholder (`licence-snapshot-meaningless`);
  evidence/review/retrieval dates may not be in the future (`date-in-future`), and a commercial-training basis must be more than a
  placeholder (`commercial-training-basis-not-meaningful`). Ghana `field_governance` text must not be a placeholder either
  (`x`, `n/a`, `none`, `unknown`, `yes`, blank). All plausibility checks only, never a legal determination or verification.
  See `docs/dataset_workflow.md`. Not covered: near-duplicate images (perceptual hash/embeddings), wrongly assigned physical sample
  IDs, image contents, the dataset registry, and whether any licence actually permits a use.
  Schema 1.0 manifests get the structure and cross-record checks but can never be cleared for a use (no permission data) and
  have no checksums. `format: date-time` is still unenforced for 1.0 (no `date-time` checker without `rfc3339-validator`);
  1.1 checks timestamps with a pattern plus a calendar check.- The registry (`Dataset`) and the manifest are separate, unlinked definitions with different names and types: registry
  `dataset_name`/`source_name`/`source_url`/`doi`/`licence` (single string) versus manifest `dataset.name`/`source.name`/
  `source.reference`/`source.licence{name,reference,usage_notes}`; registry `dataset_id` is a server UUID, manifest
  `dataset_id` is a free string. The registry stores no manifest path, hash or schema version, and the app never imports
  the validator. The registry accepts any licence text (including "Unknown; use not authorized") and has no
  review/approval status.
- Manifest `sample_id`/`image_id` (1.1: `physical_sample_id`/`record_id`) are identifiers in a dataset's own namespace. Never
  treat them as `Sample.id` / `SampleImage.id`, and do not import public images as `Sample` rows (they would need placeholder
  values for NOT NULL fields). `relative_path` has two meanings: in manifests (1.1: relative to the dataset directory passed to
  `--dataset-dir`; 1.0: base directory undefined, the example implies `data/`) and in `SampleImage.relative_path` (relative to
  the upload directory).
- Laboratory measurements in schema 1.0 hang off images and carry only measurement/value/unit/method; schema 1.1 omits them. They
  belong to physical samples and need standard, date, laboratory and uncertainty before real use.
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

Verified 2026-11-16 (Milestone 2B commercial-training inheritance, not yet independently reviewed): **1623 passed, 3 skipped** with one
Starlette deprecation warning (`httpx` with `starlette.testclient`); the run takes about 11 to 25 minutes on this machine,
depending on load. The three skips are
platform-bound: a newline in a file name (cannot exist on Windows), two names differing only in case (cannot exist on a
case-insensitive file system), and a real symbolic link (this Windows account cannot create one; a scripted directory entry covers it). Bare `pytest` and runs from the repo root fail with `ModuleNotFoundError: No module named 'app'`
because there is no pytest configuration (no `pytest.ini`/`pyproject.toml`) and tests import `app.*` and `scripts.*`.
If the count changes, update this line.

Validate a manifest (exit 0 valid; **exit 2 for any failure**, including a missing file or unreadable JSON):

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\validate_dataset_manifest.py docs\examples\example_dataset_manifest_1_1.json
```

The split generator and leakage audit use the same exit codes; their commands, and the `--dataset-dir`, `--use` and `--seed`
options, are in `docs/dataset_workflow.md`. The scripts run by file path from any directory.

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
  matches on `source_url`), the `IntegrityError` branch in `dataset_service`, list ordering with several rows, and for schema
  1.0 manifests only: `captured_at` and absolute/drive-letter `relative_path` (schema 1.1 and the manifest scripts' exit
  codes are covered by the Milestone 2B tests).
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
UMaT/MN273 restricted material is never used for training, validation, testing or independent AI evaluation.

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
- Registry and manifest are unlinked and diverging; the registry has no dataset category/evaluation-only flag (schema 1.1 manifests enforce the UMaT separation in the offline tools, the registry does not).
- Schema 1.1 records checksums and the tools check them, duplicates and one-split-per-sample; raw-data immutability is still not enforced, near-duplicate (perceptual-hash/embedding) detection does not exist, and 1.0 manifests have no checksums.
- Production blockers: no authentication/authorisation, CORS policy, rate limits, request-body limit or logging.

Medium:
- Schema 1.0 manifest `format: date-time` silently not validated (verified); schema 1.1 checks timestamps.
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
