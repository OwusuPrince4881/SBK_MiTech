# Dataset manifest schema 1.1

A manifest describes one image dataset: where it came from, what it may be used
for, which physical sample each image shows, how it is labelled, a SHA-256
checksum per file, and which split each image belongs to. Schema 1.1 is defined
by [`dataset_manifest_1_1.schema.json`](dataset_manifest_1_1.schema.json); a
fictional example is in
[`examples/example_dataset_manifest_1_1.json`](examples/example_dataset_manifest_1_1.json).
For the commands that use it see [`dataset_workflow.md`](dataset_workflow.md).

The JSON Schema checks structure only. The cross-record rules below are enforced
by `apps/api/scripts/validate_dataset_manifest.py`, so a manifest is only
considered valid once both have passed.

**Valid does not mean cleared, and cleared does not mean lawful.** A valid manifest is
consistent. It may still record that nobody has approved any use, which is the correct
state for a new dataset. A use is *accepted* only with `--use`, with the dataset directory,
and when the files and the licence snapshot match their recorded checksums (see the
workflow guide). Even then the tools only read what the manifest declares: they do not judge
whether a licence permits a use.

## Principles

- **Nothing is invented.** Material categories come from the dataset's own
  `label_vocabulary`; this project does not define soil or aggregate classes.
  Optional fields stay optional, and "unknown" is a recorded value, not a default.
- **Fail closed.** A use is permitted only if the manifest says so explicitly and
  records who approved it and when, and is accepted only against verified files.
  `unknown`, `pending_review` and `denied` all mean "not permitted".
- **Recorded text is not verified fact.** A reviewer name, an evidence reference, a
  commercial-training basis and an authorisation reference are claims. None of them
  establishes legal permission, and none can override the prohibition on restricted
  material.
- **The four dataset categories stay separate.** `public`, `synthetic`,
  `umat_practical` and `ghana_field` are different kinds of data (see
  [`dataset_strategy.md`](dataset_strategy.md)). Never mix them in one manifest.
  `umat_practical` is recognised only so that it can be refused (see "Restricted material").
- **Unknown fields are rejected** (`additionalProperties: false` everywhere), so a
  misspelt field cannot be silently ignored. New fields need a new schema version.

## Structure

```text
schema_version  "1.1"
dataset         what the dataset is, its licence and permissions
records[]       one entry per image file
```

### `dataset`

| Field | Required | Meaning |
|---|---|---|
| `dataset_id` | yes | Identifier (see below). Unique across the manifests you audit together. |
| `name`, `description` | yes | Free text. |
| `category` | yes | `public`, `synthetic`, `umat_practical` (refused for every use) or `ghana_field`. |
| `source` | yes | Where the data came from (below). |
| `licence` | yes | The licence it was obtained under (below). |
| `permissions` | yes | What it may be used for (below). |
| `sample_identity` | yes | How the physical sample IDs were established (below). |
| `label_vocabulary` | if any record has a `label` | The categories this dataset uses: `vocabulary_id`, `version`, `categories[]`. |

**`source`**: `name` (required); optional `url` (http or https), `doi`, `custodian`,
`retrieved_on` (date), `citation`. A `public` dataset must give a `url` or a `doi`.

**`licence`**: `name` (required); optional `version` (the licence's version, where it
has one: leave it out rather than guess), `url` (http or https), `spdx_id`,
`usage_notes` (record attribution and other conditions here) and `evidence`. A
`public` dataset must give the licence `url` so the licence can be re-read. For
data that is not public (UMaT, field data), `licence` records the terms or
permission it is held under.

**`licence.evidence`** preserves what the review was based on, because a URL alone
can change or disappear. It is required once `permissions.status` is `approved`.

| Field | Required | Meaning |
|---|---|---|
| `captured_on` | yes | Date the evidence was captured or obtained. |
| `snapshot` | one of the three | `relative_path` (relative to the dataset directory) and `sha256` of a saved copy of the licence text or permission document. With `--dataset-dir` the file must exist, match its checksum and use exactly the recorded spelling. |
| `archive_url` | one of the three | A link to an archived copy (http or https). |
| `reference` | one of the three | Where the evidence is kept, for example a permission letter number. |
| `notes` | no | Free text. |

The tools check that evidence is recorded and, for a snapshot, that the file is
present and unchanged. They cannot judge what it says; a person must read it.

**`permissions`**:

| Field | Required | Values |
|---|---|---|
| `status` | yes | `approved`, `pending_review`, `denied`, `unknown` |
| `permitted_uses` | yes | Any of `training`, `validation`, `testing`, `independent_evaluation`; may be empty. Only allowed when `status` is `approved`. |
| `commercial_training` | yes | `permitted`, `not_permitted`, `unknown`: whether models trained on this data may be used commercially. `unknown` is not permission. |
| `commercial_training_basis` | when `commercial_training` is `permitted` | Which licence clause or permission document allows it. Free text: recorded, not verified. |
| `reviewed_by`, `reviewed_on` | when `status` is `approved` | Who reviewed the licence and permissions, and when. |
| `authorisation_reference` | no | A letter, email or approval reference, for the record. It is information only: it never authorises anything and is ignored for `umat_practical` data. |
| `notes` | no | Free text. |

`approved` therefore needs three things on record: the reviewer, the review date and
`licence.evidence`, and the evidence must not have been captured after the review. Evidence
alone permits nothing: `unknown`, `pending_review` and `denied` stay closed whatever else is
recorded. `commercial_training: permitted` is allowed only if `training` is in `permitted_uses`.

**Accepting a use** (`--use`, or splitting) needs more than `approved`: the dataset directory,
files that match their checksums, and a licence **snapshot** that matches its checksum. A
reference or archive link can be recorded but does not clear a use.

`independent_evaluation` can only be listed on its own: evaluation data must never
also train or tune a model.

**`sample_identity.basis`**: how you know which images show the same physical
sample. This is what makes grouped splitting trustworthy.

| Value | Meaning |
|---|---|
| `recorded_by_collector` | The people who photographed the samples recorded it. |
| `declared_by_source` | The dataset publisher documents it. |
| `inferred_by_documented_analysis` | Reconstructed by an analysis that is described in `notes`. The notes are **required**: an inference that is not described is not documented (`identity-notes-missing`). |
| `assumed_each_image_distinct` | Not known; every image was treated as its own sample, so nothing has been verified and grouping by physical sample gives **no protection** against leakage between images of one real sample. The validator and the audit warn on every run, and the audit's result reads `PASS WITH LIMITATIONS`. The split generator refuses unless you give a written reason (`--accept-unverified-sample-identity REASON`), which is recorded in the split file and repeated as a warning. The override never makes the identity count as verified. |

Schema 1.0 does not record a basis at all, so the tools treat its sample IDs as not
verified in the same way.

A dataset in which **every physical sample has exactly one image** (two or more records) is
also treated as unverified whatever basis it declares: grouping by sample then protects
nothing. The validator warns (`single-image-samples`), the audit lists it as a limitation, and
the split file records `single_image_per_sample`.

### `records[]`

| Field | Required | Meaning |
|---|---|---|
| `record_id` | yes | Unique image record ID within the manifest. |
| `physical_sample_id` | yes | The physical sample the image shows. Images of one sample share this value. |
| `capture_session_id` | no | One photographing event (same place, lighting, device, background). Lets the audit look for session leakage. |
| `file` | yes | `filename`, `relative_path`, `sha256` (all required); optional `size_bytes`, `media_type` (`image/jpeg`, `image/png`, `image/webp`). |
| `split` | yes | `train`, `validation`, `test`, `independent_evaluation` or `unassigned`. |
| `label` | no | `material_category` (a value from `label_vocabulary`) and `source`. |
| `acquisition` | no | `captured_at`, `lighting`, `moisture_condition`, `scale_reference` (`present`, `description`), `camera` (`make`, `model`, `notes`), `viewpoint`, `notes`. |
| `provenance` | no | `derived_from[]` (`dataset_id`, `record_id`) and `processing_steps[]` (`description`, `tool`), for processed derivatives. |

**`label.source`**: `type` is required and is one of `source_dataset`, `human_visual`,
`laboratory_derived`, `model_generated`. Optional: `description`, `annotator_ids[]`,
`annotated_on`, `adjudicated`, `guideline_reference`.

**Identifiers** (`dataset_id`, `record_id`, `physical_sample_id`, `capture_session_id`,
`vocabulary_id`, annotator IDs) start with a letter or digit, then letters, digits
and `. _ : -`, up to 128 characters. No spaces. IDs that differ only in letter case
are rejected as ambiguous, because `Sample-1` and `sample-1` could otherwise be
split apart.

**Paths.** `relative_path` is relative to the **dataset directory**: the directory you
pass as `--dataset-dir`, normally `data/datasets/raw/<dataset_id>/` for original
data. It uses `/` separators and must not be absolute, contain `..`, `.`, empty
segments, backslashes, control characters or a drive letter, or end in `/`.
`filename` must equal the last part of `relative_path`. Paths are case-sensitive in
the manifest; check them on a case-sensitive system before moving data to Linux.

**Checksums** are lower-case hexadecimal SHA-256 of the file's bytes.

**Dates** are `YYYY-MM-DD`; `captured_at` is an RFC 3339 timestamp with a time zone,
for example `2026-01-02T03:04:05Z`. Impossible dates (30 February) are rejected.

## Rules checked by the validator

All are errors unless marked as a warning. Each finding has a stable code.

| Code | Rule |
|---|---|
| `schema` | The structure does not match the JSON Schema. No other check runs until this passes. |
| `unsupported-schema-version` | `schema_version` is missing or not `1.0` or `1.1`. |
| `duplicate-record-id`, `ambiguous-record-id` | Record IDs must be unique, also ignoring letter case. |
| `duplicate-relative-path`, `ambiguous-relative-path` | One file must not be recorded twice, also ignoring letter case. |
| `ambiguous-physical-sample-id`, `ambiguous-capture-session-id` | IDs differing only in case could split one sample or session. |
| `sample-in-multiple-splits` | Every record of a physical sample must be in the same split (`unassigned` counts as a split). |
| `duplicate-checksum` | Two records must not have identical content. |
| `filename-mismatch` | `filename` must match the end of `relative_path`. |
| `invalid-date` | A date or timestamp is not a real calendar date. |
| `public-missing-licence-url`, `public-missing-source-reference` | A `public` dataset must record the licence URL and a source URL or DOI. |
| `restricted-category` | Category `umat_practical` is refused for every use, in every tool, whatever else the manifest records. |
| `restricted-material-suspected` | The dataset ID, name, source or a file path mentions UMaT or MN273: refused whatever the category says. |
| `approval-without-licence-evidence` | `approved` needs `licence.evidence`. |
| `evidence-after-review` | The licence evidence was captured after the review that relied on it. |
| `commercial-training-basis-missing` | `commercial_training: permitted` needs `commercial_training_basis`. |
| `commercial-training-without-training-permission` | `commercial_training: permitted` while `training` is not in `permitted_uses`. |
| `identity-notes-missing` | `inferred_by_documented_analysis` needs `sample_identity.notes`. |
| `evaluation-records-mixed` | `independent_evaluation` applies to a whole dataset, so those records cannot share a manifest with other splits (both schema versions). |
| `evaluation-use-not-exclusive` | `independent_evaluation` cannot be combined with other permitted uses. |
| `permitted-uses-without-approval`, `commercial-training-without-approval` | Uses, and commercial permission, require `status` `approved`. |
| `split-use-not-permitted` | A record in `train`, `validation`, `test` or `independent_evaluation` needs `training`, `validation`, `testing` or `independent_evaluation` in `permitted_uses`. `unassigned` records need no permission. |
| `vocabulary-required`, `unknown-material-category`, `ambiguous-vocabulary-category` | Labels must use categories declared in `label_vocabulary`. |
| `model-label-as-ground-truth` | `model_generated` labels cannot be in `validation`, `test` or `independent_evaluation`. |
| `derived-from-self`, `derived-from-unknown-record`, `derived-sample-mismatch` | Within a dataset, a derivative must derive from another existing record and keep that record's physical sample ID. |
| `derived-source-unverified` | A record derives from a dataset whose manifest was not supplied, so the derivation cannot be established (`--source-manifest`, or the audit). |
| `derived-from-restricted-source`, `derived-from-evaluation-record`, `derived-into-evaluation`, `derivation-crosses-splits` | Across datasets: nothing may be derived from restricted material or from an evaluation record, an evaluation record may not be derived from other data, and a derivative and its source must share a split. |
| `derivation-split-undetermined` (warning) | A derivation across datasets where one side is still unassigned. |
| `sample-label-conflict` (warning) | One physical sample has records with different categories. |
| `unverified-sample-identity` (warning) | `sample_identity.basis` is `assumed_each_image_distinct`, or the manifest is schema 1.0, which does not record how the sample IDs were established. |
| `single-image-samples` (warning) | Every physical sample has exactly one image, so the identity is treated as unverified. |

With `--use` / `--require-commercial-training`: `use-not-permitted`,
`commercial-training-not-permitted`, `no-permission-data` for schema 1.0,
`clearance-needs-dataset-dir` (no directory supplied, so nothing could be verified), and
`clearance-needs-licence-snapshot` (the evidence is a reference or link, not a checksummed file).

With `--dataset-dir`:

| Code | Rule |
|---|---|
| `restricted-directory` | The directory (or a folder inside it) is a restricted location: `data/evaluation`, `data/umat_practicals`, `data/uploads`. Refused before anything is read. |
| `dataset-dir-invalid`, `directory-unreadable` | The directory is missing, or part of it could not be listed, so it cannot be fully checked. |
| `file-missing`, `file-outside-dataset-dir`, `file-through-link` | A listed file (an image or the licence snapshot) is not there, resolves outside the directory, or is reached through a link, which is never followed. |
| `path-case-mismatch` | The recorded path differs from the real name only in letter case or Unicode form. Judged from the directory listing, so it is the same on every operating system. |
| `file-unreadable`, `checksum-mismatch`, `size-mismatch` | The file cannot be read, or does not match its recorded SHA-256 or size. |
| `unmanifested-image-file` | An image or video (by extension, or by its first bytes whatever its name) in the directory is not listed in the manifest. Ignore patterns never apply. Never added, removed or changed by the tools. |
| `unmanifested-file` (warning) | Another file in the directory is not listed. The manifest itself, the licence snapshot and housekeeping files are left out. |
| `files-ignored-by-pattern` (warning) | Files an `--ignore-file` pattern left out of the unlisted-file check: the count and every name. Never an image, video or link. |
| `link-in-dataset-dir` | A symbolic link or junction in the directory, whatever it is named. Never followed; the directory is refused while any exists. |

## Restricted material

UMaT practical samples, and anything the MN273 brief classifies as RED-zone material
(photographs, raw laboratory records, supplier and purchase records, the team dataset), must
not be used for training, validation, testing or independent AI evaluation.

No manifest field overrides this: not `permissions.authorisation_reference`, `status`,
`licence`, or `category`. A manifest with category `umat_practical` is refused for every use
(`restricted-category`) by the validator, the splitter and the audit, and an authorisation
reference recorded in it is ignored. The category is not the only protection: manifests that
mention UMaT or MN273 in their dataset ID, name, source or file paths are refused too
(`restricted-material-suspected`), the restricted folders are never entered, and nothing may
be derived from such material. This holds for schema 1.0 as well as 1.1.

Any future proposal to use this material needs explicit written permission from the
appropriate authority and a separate governance review before any processing, outside these
tools. There is no exception mechanism. See [`dataset_workflow.md`](dataset_workflow.md) and
[`dataset_strategy.md`](dataset_strategy.md).
## Not modelled yet

Adding any of these needs a new schema version and an owner decision:

- **Location and privacy.** Where a photograph was taken and how precisely. This is
  required by the field-collection rules in `dataset_strategy.md` before Ghanaian
  field data is collected, so schema 1.1 is **not yet sufficient** for
  `ghana_field` datasets.
- **Laboratory measurements.** Schema 1.0 attached them to images with only a
  value, unit and method. Real use needs the physical sample, standard, date,
  laboratory and uncertainty, so they are deliberately left out of 1.1.
- **Video.** Records are image files.
- **A link to the dataset registry.** The registry's `dataset_id` is a server UUID
  and a manifest's is a free identifier; how they map is undecided.
- **Several labels per image.** 1.1 has one `label` per record.

## Migrating from schema 1.0

Schema 1.0 manifests keep validating, and the validator and the leakage audit still
accept them. Everything that depends on permissions or checksums needs 1.1: a 1.0
manifest can never be cleared for a use (`no-permission-data`), and the split
generator refuses it.

There is no automatic converter, because the new required information cannot be
inferred. Do the migration by hand, from the real source records:

| Schema 1.0 | Schema 1.1 |
|---|---|
| `schema_version: "1.0"` | `"1.1"` |
| `dataset.example_only` | Removed. Use `category: "synthetic"` and leave permissions `unknown`. |
| `dataset.source.name`, `.reference` | `dataset.source.name`; `reference` becomes `url` (an http/https URL), `doi` or `citation`. |
| `dataset.source.licence.{name, reference, usage_notes}` | `dataset.licence.{name, url, usage_notes}`. Licence moved out of `source`. |
| (none) | **New, required:** `dataset.category`, `dataset.permissions`, `dataset.sample_identity`. |
| `images[]` | `records[]` |
| `image_id` | `record_id` |
| `sample_id` | `physical_sample_id`. Only correct if it really identifies a physical sample; otherwise say so in `sample_identity.basis`. |
| (none) | `capture_session_id` (optional) |
| `relative_path` | `file.relative_path`, and **new, required** `file.filename` and `file.sha256`, computed from the actual files. |
| `material_type` | `label.material_category` with a `label.source`, and a declared `label_vocabulary`. Labels are now optional. |
| `visual_labels[]` | One `label` per record. If there were several, choose the material category; the rest cannot be represented in 1.1. |
| `acquisition_conditions.{description, captured_at, device, lighting, viewpoint}` | `acquisition.{notes, captured_at, camera, lighting, viewpoint}`; new optional `moisture_condition` and `scale_reference`. |
| `laboratory_measurements[]` | **Not carried over.** See "Not modelled yet". |
| `split` | `split` (same values) |

The validator now applies several checks to 1.0 manifests that the schema alone did not:
it rejects duplicate IDs and paths, a sample in several splits, and (like 1.1) evaluation
records mixed with other splits; it refuses manifests that mention UMaT or MN273 in their
dataset ID, name, source or file paths; it fails closed on derivations it cannot establish;
and with `--dataset-dir` it checks for unlisted files, links and case mismatches. A 1.0
manifest that passed before can therefore fail. 1.0 manifests also carry an
`unverified-sample-identity` warning, because 1.0 does not record how the sample IDs were
established. The schema-only function `validate_manifest` in the script is unchanged.
## Versioning

Schema 1.1 was tightened twice after its first draft, before any real manifest existed
(nothing under `data/` has been described with it). First: licence version and evidence,
`commercial_training_basis` and the file checks. Then, after an independent audit: the
UMaT/MN273 prohibition replaced the earlier independent-evaluation allowance, links became
errors, a use is accepted only against verified files and a licence snapshot, derivations are
checked across manifests, and contradictory permissions are rejected. These are part of 1.1,
not a new version. From here on, a manifest written for 1.1 follows the rules in this
document.

Schema 1.x versions are additive: a new minor version may add optional fields or
enum values. Anything that removes or renames a field, or tightens a rule so that
existing manifests would fail, is a new major version.
