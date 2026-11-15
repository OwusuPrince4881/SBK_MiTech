# MineMat AI

MineMat AI is a machine-learning-assisted web application for preliminary
assessment of soil and aggregate materials used in mine construction.

## Development stage

The repository contains two working parts:

- `apps/api` — a FastAPI backend (SQLite) with a sample registry, sample
  photograph upload and a dataset registry.
- `apps/web` — a React + TypeScript + Vite client for the sample registry: list
  samples, register a sample, and view a sample's record.

There is no assessment logic, laboratory-result model, validated engineering
criteria or machine-learning model yet. The web client shows registration
details only and makes no claim about material suitability.

## Local development

Requirements: Python 3.10 or newer (the project virtual environment uses 3.14),
and npm with one of these Node.js versions: 22.13 or later 22.x, 24.x, or 26.x.
The frontend toolchain's declared ranges (Vitest 5, jsdom 29) exclude 20.x,
22.12 and earlier, 23.x and 25.x, so those are not supported. Releases after 26
have not been checked. The frontend is developed and tested on Node.js 24.
Commands below are PowerShell, run from the repository root unless stated.

### Backend setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r apps\api\requirements.txt
```

### Running both applications

Start the backend first. **Point it at a separate synthetic development
database and upload folder.** If `DATABASE_URL` and `IMAGE_UPLOAD_DIR` are not
set, the backend uses its defaults, `data/minemat_ai.db` and `data/uploads`,
which may hold real local data. Everything under `data/` is excluded from Git.

```powershell
$repo = (Get-Location).Path
$env:DATABASE_URL = "sqlite:///$($repo -replace '\\', '/')/data/dev_synthetic.db"
$env:IMAGE_UPLOAD_DIR = "$repo\data\dev_synthetic_uploads"
cd apps\api
..\..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a second terminal, start the frontend:

```powershell
cd apps\web
npm install
npm run dev
```

Open the address Vite prints (by default <http://localhost:5173>). Use synthetic
sample data only. The API's interactive documentation is at
<http://127.0.0.1:8000/docs>.

### How the Vite API proxy works

The browser only talks to the Vite dev server. `apps/web/vite.config.ts` makes
that server forward requests for `/api` and `/health` to
`http://127.0.0.1:8000`, so the browser sees a single origin and the backend
needs no CORS configuration. `/health` is proxied as well because the backend
serves its liveness check there, and the header's connection indicator uses it.
The proxy exists only in the dev server. A production build (`npm run build`,
output in `apps/web/dist`) does not include it, and deploying the client next to
the API is not set up yet.

### Running the tests

Backend (uses temporary databases and upload folders, never `data/`):

```powershell
cd apps\api
..\..\.venv\Scripts\python.exe -m pytest
```

Frontend, from `apps\web`:

```powershell
npm test          # Vitest + React Testing Library, API calls mocked
npm run build     # type-check (tsc) and production build
```

### Dataset tools

Offline scripts in `apps/api/scripts` validate dataset manifests, generate
reproducible train/validation/test splits grouped by physical sample, and audit
for data leakage. They do not use the database or the API. See
[`docs/dataset_workflow.md`](docs/dataset_workflow.md) for the commands and
[`docs/dataset_manifest_schema_1_1.md`](docs/dataset_manifest_schema_1_1.md) for
the manifest format.

## Architecture

The planned application uses a React and TypeScript frontend with a Python
FastAPI backend. The backend will own application logic and communicate with a
PostgreSQL database. Uploaded images will be stored separately from structured
assessment records. A future model may provide visual observations through a
backend ML interface.

Visual analysis alone cannot determine engineering suitability. Any future
assessment must consider appropriate laboratory results and validated,
applicable engineering criteria, and must communicate uncertainty and
limitations. This repository does not yet define those criteria.
