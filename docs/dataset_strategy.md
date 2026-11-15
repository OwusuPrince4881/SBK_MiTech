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

## 2. UMaT practical samples: restricted, not used for any AI purpose

UMaT practical samples are restricted material. They must not be used for
training, validation, testing or independent AI evaluation. This replaces the
earlier plan to hold them back as an independent evaluation set. The folder
`data/evaluation/umat_practicals/` is a restricted location that the project's
tools refuse to read, hash, scan or write.

Do not inspect, copy, process or derive anything from these samples. Keep access
to them, and to their labels, under the controls that apply to them, and confirm
institutional permissions and any applicable consent, privacy, and use
conditions before any collection or use. See "Restricted material" below.

Independent evaluation as a *practice* remains valid for data that is permitted
for it, for example a held-out part of a later Ghanaian field dataset: such data
is kept untouched, in its own manifest, with the `independent_evaluation` split.

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

## Restricted material: UMaT practicals and MN273

UMaT practical samples, and anything the MN273 brief classifies as RED-zone
material (photographs, raw laboratory records, supplier and purchase records,
the team dataset), must not be used for training, validation, testing or
independent AI evaluation.

No field in a manifest overrides this: not an authorisation reference, an
approval status, a licence record or a dataset category. The offline tools refuse
such material for every use, refuse the restricted folders (`data/evaluation`,
`data/umat_practicals`, `data/uploads`) whatever a manifest says, refuse anything
derived from it, and contain no exception mechanism.

Any future proposal to use this material needs explicit written permission from
the appropriate authority and a separate governance review before any
processing. That happens outside the tools, and would require the project's
rules and the tooling to be changed deliberately; it cannot be granted by editing
a manifest.
## Shared handling rules

- Keep the three categories separate; never silently mix independent
  evaluation data into training or tuning.
- Create a manifest for each dataset and validate it with
  `apps/api/scripts/validate_dataset_manifest.py`. New manifests use schema 1.1
  ([`dataset_manifest_schema_1_1.md`](dataset_manifest_schema_1_1.md)); splits and
  the leakage audit are described in [`dataset_workflow.md`](dataset_workflow.md).
- Record image and sample identifiers, material types, visual label sources,
  acquisition conditions, split assignments, and optional laboratory
  measurements as described by `dataset_manifest.schema.json`.
- Record the source and licence before accepting a dataset. If rights or
  permitted uses are unclear, do not ingest or use the data.
- Keep actual datasets, uploaded photographs, and other local data out of Git.
- Use only fictional placeholders in examples and tests; never present
  fabricated laboratory results as real measurements.
