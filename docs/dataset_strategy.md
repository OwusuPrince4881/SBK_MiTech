# MineMat AI dataset strategy

MineMat AI will manage three distinct data categories. They serve different
purposes and must remain identifiable through dataset manifests, storage
locations, and evaluation reports.

## 1. Public training datasets

Public datasets may be considered for model development only after their
provenance, licence, permitted uses, and image attribution requirements have
been reviewed and recorded. Public availability does not itself grant
permission to download, redistribute, modify, or use data for model training.

Keep original files immutable under `data/datasets/raw/`. Derived or
transformed files belong under `data/datasets/processed/`, with the processing
steps and source image identifiers recorded in manifests. Split data at the
sample or source level where appropriate to reduce leakage between training,
validation, and test sets. Record the split; do not infer it from folder names.

## 2. UMaT practical samples for independent evaluation

UMaT practical samples are reserved for independent evaluation and are not
training, tuning, or validation data. Store them under
`data/evaluation/umat_practicals/`, with their source, authorization, labels,
and acquisition context documented in manifests.

Restrict access to the evaluation data and its labels to reduce accidental
exposure during model development. Preserve the separation when making
processed copies or evaluation reports. Confirm institutional permissions and
any applicable consent, privacy, and use conditions before collection or use.

## 3. Future Ghanaian field datasets

Future field datasets will require documented collection protocols and
appropriate permission before acquisition. Record provenance, consent or
authorization where applicable, location precision and privacy constraints,
acquisition conditions, label sources, and any laboratory measurement
methods and units.

Do not treat field labels, photographs, or model predictions as verified
engineering results. Keep raw records immutable; document transformations and
retain links from derived records to their sources. Any future assessment must
use relevant laboratory results and validated, applicable criteria, and must
state its evidence, uncertainty, and limitations.

## Shared handling rules

- Keep the three categories separate; never silently mix independent
  evaluation data into training or tuning.
- Create a manifest for each dataset and validate it with
  `apps/api/scripts/validate_dataset_manifest.py`.
- Record image and sample identifiers, material types, visual label sources,
  acquisition conditions, split assignments, and optional laboratory
  measurements as described by `dataset_manifest.schema.json`.
- Record the source and licence before accepting a dataset. If rights or
  permitted uses are unclear, do not ingest or use the data.
- Keep actual datasets, uploaded photographs, and other local data out of Git.
- Use only fictional placeholders in examples and tests; never present
  fabricated laboratory results as real measurements.
