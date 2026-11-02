# MineMat AI

MineMat AI is a machine-learning-assisted web application for preliminary
assessment of soil and aggregate materials used in mine construction.

## Development stage

The project is at its initial scaffolding stage. The repository contains the
planned frontend and backend directory structure, but no generated frontend,
application features, database integration, assessment criteria, laboratory
datasets, or machine-learning model.

No Python packages have been installed as part of this setup. Backend
dependencies are listed in `apps/api/requirements.txt` for a later development
setup.

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
