# Local data directory

This directory is for local datasets, uploaded photographs, and evaluation
materials. Actual data is excluded from Git. Keep source files unchanged and
record provenance and transformations in validated dataset manifests.

## Directory purposes

- `datasets/raw/` — Original dataset files. Treat these as immutable and record
  their source and licence before use.
- `datasets/processed/` — Derived or transformed data, linked to source
  identifiers and documented processing steps.
- `datasets/manifests/` — Dataset metadata, provenance, licence, identifiers,
  labels, acquisition context, splits, and optional laboratory measurement
  metadata. Validate manifests with
  `apps/api/scripts/validate_dataset_manifest.py`.
- `uploads/` — Photographs uploaded through the application. These are stored
  separately from image metadata in SQLite.
- `evaluation/umat_practicals/` — UMaT practical samples held for independent
  evaluation. Do not use these for training, validation, or tuning.

See `docs/dataset_strategy.md` for the separation and handling rules for public
training datasets, UMaT independent evaluation samples, and future Ghanaian
field datasets.

Do not commit real datasets, photographs, private or identifying data, or
fabricated laboratory measurements. The example manifest is fictional and
stored in `docs/examples/`.
