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
  `apps/api/scripts/validate_dataset_manifest.py`. New manifests use schema 1.1;
  see `docs/dataset_manifest_schema_1_1.md` and `docs/dataset_workflow.md`.
- `uploads/` — **Restricted.** Photographs uploaded through the application. These
  are stored separately from image metadata in SQLite, and the dataset tools refuse
  to read, hash, scan or write here.
- `evaluation/umat_practicals/` — **Restricted.** UMaT practical samples. They must
  not be used for training, validation, testing or independent AI evaluation, and
  the dataset tools refuse to read, hash, scan or write here.

UMaT practical samples and material the MN273 brief classifies as RED-zone
(photographs, raw laboratory records, supplier and purchase records) must not be
used for training, validation, testing or independent AI evaluation. No manifest
field, authorisation reference, approval or category overrides this. Any future
proposal needs explicit written permission from the appropriate authority and a
separate governance review before any processing; see `docs/dataset_strategy.md`.

See `docs/dataset_strategy.md` for the separation and handling rules for public
training datasets, restricted UMaT material, and future Ghanaian field datasets.
Do not commit real datasets, photographs, private or identifying data, or
fabricated laboratory measurements. The example manifest is fictional and
stored in `docs/examples/`.
