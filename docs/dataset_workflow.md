# Dataset workflow: validate, split, audit

How to prepare an image dataset for model development without leaking test data
into training. The manifest format is described in
[`dataset_manifest_schema_1_1.md`](dataset_manifest_schema_1_1.md) and the data
categories in [`dataset_strategy.md`](dataset_strategy.md).

These tools work offline on a manifest and a dataset directory. They do not read
or write the application database, and they never change a file in a dataset
directory. Commands are PowerShell, run from the repository root; use the project
virtual environment (`.\.venv\Scripts\python.exe`).

Exit codes for all three scripts: `0` on success, `2` on any failure, including an
unreadable, oversized or hostile input file. A failure is a message, never a
traceback.

## What the tools say, and what they cannot say

The tools read what a manifest *declares* and check it for consistency. They verify
files and the licence snapshot against the checksums the manifest records. They do
**not** judge whether a licence permits a use, and they do not confirm that any use is
lawful: a reviewer's name, an evidence reference, a commercial-training basis and an
authorisation reference are recorded text, and recording them proves nothing.

So the tools speak in two registers. *Valid* means the manifest is consistent.
*Recorded permission accepted for training* means the manifest declares that
permission, the dataset directory was supplied, and the files and licence snapshot match
their recorded checksums. Neither is a statement that the use is permitted.

## Restricted locations and material

UMaT practical samples, and anything the MN273 brief classifies as RED-zone material,
are refused for every use (see the last section). Whatever a manifest says about its
category, the tools also refuse to touch these locations, matched anywhere in a path,
without regard to letter case:

- `data/evaluation` (and everything under it, including `umat_practicals`)
- `data/umat_practicals`
- `data/uploads`

A restricted path is recognised from the path text alone, before anything is resolved,
listed, opened or hashed, and then again after resolving links, so an alias into a
restricted location is refused too. This applies to the dataset directory, to every
manifest and source manifest the tools read, and to every file they write. A restricted
folder found *inside* a dataset directory is not entered. There is no option to switch the
guard off.

As a second tripwire, a manifest whose dataset ID, name, source or file paths mention
`UMaT` or `MN273` is refused whatever its category says. If the data is not restricted,
rename it so it no longer carries those names.

## Order of work

1. Record the source, licence and permissions in a manifest (all `split` values
   `unassigned`), and save the licence text as a snapshot file.
2. **Validate** it against the real files.
3. **Generate splits** (original, ordinary datasets only).
4. **Audit** the result, together with every dataset that must stay separate from it
   and every dataset that records derive from.

## 1. Validate a dataset

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\validate_dataset_manifest.py `
    data\datasets\manifests\my-dataset.json `
    --dataset-dir data\datasets\raw\my-dataset `
    --use training --use validation --use testing
```

- Without `--dataset-dir`, only the manifest is checked and the output says files
  were **not** checked. With it, every file must exist inside that directory and match
  its SHA-256 and, if given, size.
- **A use is accepted only against verified files.** `--use` needs the manifest to record
  the permission (status `approved`, the use listed), `--dataset-dir` to be supplied, the
  files and the licence snapshot to match their recorded checksums, and the evidence to
  include a **checksummed snapshot file**. A reference or an archive link can be recorded,
  but it cannot be verified here, so it does not clear a use. Without a directory the
  result is the error `clearance-needs-dataset-dir`; without a snapshot,
  `clearance-needs-licence-snapshot`. Nothing is cleared when verification did not happen.
  Without `--use`, "valid" only means consistent and nothing is cleared.
- `--require-commercial-training` is separate from `--use training`. It needs
  `commercial_training: permitted` and the same verification, and it sets its own
  line in the output. Clearing training says nothing about commercial use, and the
  reverse. A free-text `commercial_training_basis` is recorded, not verified.
- **Letter case must match the disk exactly.** A path that differs from the real name
  only in case (or Unicode form), such as `Rec-1.JPG` on disk and `rec-1.jpg` in the
  manifest, is the error `path-case-mismatch`. Windows and some macOS volumes would open
  that file anyway, while Linux, which a cloud GPU session uses, cannot find it. The check
  compares the manifest with the real directory listing, not with the operating system's
  rules, so it gives the same answer on every system.
- **Files the manifest does not list are reported.** The directory is scanned. An image or
  video (by extension, in any letter case: `.jpg`, `.png`, `.webp`, `.tif`, `.heic`, `.mp4`,
  `.mov` and others) **or any file whose first bytes are an image or video signature**,
  whatever its name, that is not in the manifest is an **error** (`unmanifested-image-file`).
  A file with no recorded provenance or licence must not be used, and a loader that reads
  the folder would pick it up. Any other unlisted file is a warning (`unmanifested-file`).
  The tool only reports; it never adds, deletes, moves or changes a file.
- **Links are errors.** A symbolic link or Windows junction anywhere in the dataset
  directory is the error `link-in-dataset-dir`, whatever it is named (`photo.jpg` included)
  and wherever it points. Links are never followed, so they cannot lead the scan outside the
  directory, but they can conceal images or whole folders, so the directory is not accepted
  while they are present. Replace each link with the real, listed files.
- **Ignore patterns cannot hide images.** `--ignore-file PATTERN` (repeatable) leaves out
  documented *non-image* files such as `README.md` or `notes/*`. A pattern matches the whole
  relative path, case-sensitively, and `*` also matches `/`. It never applies to an image, a
  video, an image-looking file or a link, so `--ignore-file "*"` cannot make the check
  vacuous. Every file a pattern leaves out is listed, with the count
  (`files-ignored-by-pattern`), and the split generator records them.
- Left out automatically: the manifest file itself, the licence snapshot (the manifest names
  it), and housekeeping files (`.gitkeep`, `Thumbs.db`, `desktop.ini`, `.DS_Store`), which
  are listed in the output.
- `--source-manifest PATH` (repeatable) supplies the manifest of a dataset that records here
  derive from (see "Derived data" below).
- `--schema` adds a schema the manifest must *also* satisfy. The built-in schema always
  applies, so a custom schema cannot weaken it.
- Schema 1.0 and 1.1 are both accepted. A 1.0 manifest can never be cleared for a use,
  because it records no permissions, and its sample identity counts as not verified.

The output lists every problem with its code and location, for example
`ERROR [sample-in-multiple-splits] physical_sample_id sample-014: ...`. The codes are
listed in the schema document.

## 2. Generate reproducible splits

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\generate_dataset_splits.py `
    data\datasets\manifests\my-dataset.json `
    --dataset-dir data\datasets\raw\my-dataset `
    --seed 2026 `
    --output data\datasets\manifests\my-dataset.splits.json `
    --output-manifest data\datasets\manifests\my-dataset.split.json
```

- `--dataset-dir` is required: splitting grants the uses it assigns, so it needs the same
  verified files and snapshot as `--use`. The library function refuses to run without one.
- `--train`, `--validation`, `--test` set the ratios (default 0.70 / 0.15 / 0.15,
  which must add up to 1). A split with ratio 0 is not generated. Very small datasets
  can leave a split empty; the tool then refuses and says so.
- `--seed` is required and recorded. The split depends only on the seed, the
  physical sample IDs, their categories and how many images each sample has, not on
  the order of records or the platform. The same inputs always give a byte-identical
  split file. Changing the seed to get a better-looking split is not legitimate;
  choose it once.
- The **split file** lists each record's split and records the algorithm, seed, ratios,
  manifest SHA-256, sample identity basis, permissions at generation, the ignore patterns
  and the files they left out, and **every warning**. It contains no timestamp.
  `--output-manifest` also writes a copy of the manifest with the splits filled in. The
  input manifest is never modified, and existing outputs are not replaced unless you pass
  `--overwrite`. Outputs are never written into a restricted location.
- Samples are spread over the splits **per material category**, so each category appears in
  each split where there are enough samples.
- `--group-capture-sessions` keeps samples that share a capture session in the same split.
- The tool refuses, and writes nothing, when: the manifest is invalid, restricted, or a
  file does not match its checksum; the dataset directory has an unlisted image or a link;
  any requested split's use is not permitted; the dataset is restricted or holds
  `independent_evaluation` records; records already have splits (`--allow-resplit` discards
  ordinary ones); a derivation cannot be established (see below); the sample identity is
  only assumed (see below); or the manifest is schema 1.0.

### What the split warnings mean

A split is not a promise that a model's score on it can be trusted. The tool warns, on the
screen and in the split file, about:

- **A small validation or test split.** With fewer than 30 physical samples (a rule of
  thumb, not a statistical guarantee), a performance estimate from that split is very
  uncertain and must not be presented as reliable evidence of how well a model works. A
  split with 30 or more samples is not thereby reliable either.
- **A category missing from a split**, for example from the test split.
- **Unlabelled samples**, and samples whose labels conflict or are partly missing. They are
  split without balancing.
- **One image per sample**, which makes grouping by sample protect nothing.

### Splitting when the sample identity is only assumed

If `sample_identity.basis` is `assumed_each_image_distinct`, the images are treated
as separate physical samples without anyone having checked. Photographs of one real
sample may then sit on both sides of a split, so the splits are **not known to be
grouped by physical sample** and accuracy measured on them may be inflated. The tool
refuses by default. The preferred fix is to establish the real physical sample IDs
and update the manifest.

If you must proceed anyway, give a written reason:

```powershell
... --accept-unverified-sample-identity "Pilot only: no sample IDs exist yet; results not to be reported."
```

The override never makes the identity count as verified. The reason is stored in the
split file (`unverified_sample_identity_reason`, with `sample_identity_assumed: true`),
the run prints a warning that repeats it, and the manifest keeps saying the identity
is assumed, so the validator and the audit continue to flag it. A blank reason, or one
over 500 characters or containing control characters, is refused.

## 3. Run the leakage audit

```powershell
.\.venv\Scripts\python.exe apps\api\scripts\audit_dataset_leakage.py `
    data\datasets\manifests\my-dataset.split.json `
    data\datasets\manifests\other-dataset.json `
    --dataset-dir my-dataset=data\datasets\raw\my-dataset `
    --dataset-dir other-dataset=data\datasets\raw\other-dataset
```

The audit does **not** report PASS merely because a manifest can be read. For every manifest
it runs the full validation: permissions, the UMaT/MN273 prohibition, evaluation isolation,
split consistency, sample identity, licence evidence and, for each manifest whose dataset
directory is supplied (`--dataset-dir DATASET_ID=DIRECTORY`, or a bare directory when there is
one manifest), directory integrity. An invalid manifest is an audit error, named with its
file. Then, across the manifests, it reports:

| Finding | Severity |
|---|---|
| Any validation error in any manifest | error |
| The same image content (SHA-256) in two splits, or in an evaluation dataset and anything else | error |
| A record derived from a record in another manifest in a way that leaks (see below), or from a manifest not supplied | error |
| A capture session in more than one split (fix with `--group-capture-sessions`) | warning |
| Records with no split or capture session ID, or no checksums (schema 1.0) | warning |
| Identity that is assumed, absent (schema 1.0) or meaningless (one image per sample) | warning |

Pass every manifest that must stay apart in one run, for example the training dataset and the
evaluation dataset, together with the manifest of every dataset that records derive from.
`--ignore-file` and `--strict` work as above; `--strict` makes warnings fail too.

The result is **PASS** only when nothing is wrong and nothing is left unverified. Otherwise it
reads **PASS WITH LIMITATIONS** and prints a `LIMITATION` line for each of:

- a manifest whose physical sample IDs are not verified (assumed, one image per sample, or
  schema 1.0): images of one real sample can sit in different splits without the audit
  noticing;
- a manifest audited without its dataset directory: its files and licence snapshot were not
  verified against their checksums.

The audit **does not detect near-duplicate images** (the same photograph resized or
re-compressed, or burst frames), wrong physical sample IDs, or anything about the image
contents, does not judge any licence, and says so every time. Passing it is not proof that
there is no leakage.

## Derived data

A record may declare the records it was derived from (`provenance.derived_from`, with the
dataset ID and record ID). The tools preserve that declaration and fail closed on it.

- Within a dataset, the source must exist and belong to the same physical sample, so a crop
  or resize cannot land in a different split from its source.
- Across datasets, the source dataset's manifest must be supplied (`--source-manifest` for
  the validator and the splitter; every manifest together for the audit). A derivation from a
  dataset that is not supplied cannot be established and is an error
  (`derived-source-unverified`), as is a source record that does not exist.
- **Nothing may be derived from restricted material** (`derived-from-restricted-source`) or
  from an independent-evaluation record (`derived-from-evaluation-record`); an
  evaluation record may not be derived from non-evaluation data (`derived-into-evaluation`);
  and a derivative and its source must be in the same split (`derivation-crosses-splits`). If
  one side is still unassigned, that is a warning until both are assigned.

## Licensing and provenance requirements

- **Public availability is not permission.** Do not download, ingest or use a
  dataset until its licence and permitted uses have been read and recorded. If the
  rights are unclear, do not use it.
- Record, in the manifest: the source (`url` or `doi`, and `retrieved_on`), the licence name,
  its **version** where it has one, and its URL, attribution or other conditions in
  `usage_notes`, and who reviewed the permissions and when. Do not fill in a licence detail
  you have not seen; leave it out.
- **Preserve the licence evidence.** Licences and web pages change or disappear, so a URL
  alone is not evidence. `licence.evidence` records the date it was captured and a **snapshot**
  of the licence text or permission document as a file (`snapshot.relative_path`, relative to
  the dataset directory, with its `sha256`), and may add an archive link or a reference. The
  evidence must not be captured after the review that relied on it.
- **Recorded is not verified.** Approving a dataset needs evidence on record. *Clearing a use*
  needs more: the dataset directory, files that match their checksums, and a snapshot that
  matches its checksum. Even then the tools only know that the files are unchanged since the
  manifest was written. A person must read the licence.
- **Unknown permissions fail closed.** `unknown`, `pending_review` and `denied` permit nothing,
  and recording evidence does not change that.
- **Commercial training is recorded explicitly, and separately.** `commercial_training` must be
  `permitted` before data goes into a model that could be sold or used commercially, `unknown`
  is not permission, and it can be `permitted` only if training itself is. A free-text
  `commercial_training_basis` says which clause or document allows it; the tools do not verify
  it. Licences that forbid commercial use, or derivatives, need particular care.
- Keep original files unchanged under `data/datasets/raw/`. Derived files go under
  `processed/`, with `provenance.derived_from` and `processing_steps` recorded, and
  the same `physical_sample_id` as their source.
- Record a SHA-256 checksum for every file, and re-run the validator with `--dataset-dir`
  before each use to detect changed or missing files.
- Keep the four categories apart (`public`, `synthetic`, `umat_practical`, `ghana_field`).
  Real datasets, photographs and manifests stay out of Git; `data/datasets/manifests/` is
  git-ignored. How reviewed manifests will be version-controlled is still an open decision.
- Never present synthetic or example data as real. Synthetic data is for testing the
  tooling and cannot teach a model what real material looks like.

## Why splits are grouped by physical sample

A physical sample is photographed several times: different angles, distances, or
lighting. Those photographs look alike. If they are split by image, some go to
training and others to testing, and the model is then tested on pictures of a sample
it has already seen. It can score well by recognising that sample, which says
nothing about how it will do on a new one. The reported accuracy is inflated, and
with the small datasets typical here the inflation can be large.

So every record of a physical sample goes to the same split. Capture sessions have
a milder version of the problem: samples photographed in one session share a
background, light and camera, which a model can pick up. The audit flags it, and
`--group-capture-sessions` removes it.

This depends on `physical_sample_id` being right, which is why the manifest records
how it was established (`sample_identity.basis`). When the basis is only *assumed*, or every
sample has a single image, grouping gives **no protection** against this leakage: every tool
states that openly, the splitter needs a written reason to proceed on an assumed identity, and
the audit's result is `PASS WITH LIMITATIONS`. A basis of `inferred_by_documented_analysis`
must come with notes that describe the analysis. Even a recorded basis is only as good as the
people who recorded it: grouping cannot catch a photograph of the same sample that was given
a different ID, or near-duplicate images under different IDs. Those need a perceptual-hash or
embedding check, which is not part of this milestone.

## Why independent evaluation data must stay untouched

Independent evaluation data exists to answer one question honestly: how well does a finished
model work on data it had no part in building? That answer is only valid if the data had no
influence on the model. Anything it influenced is no longer independent: training on it, but
also choosing a model, tuning a setting or a confidence threshold, or deciding which classes
to use after looking at it. Each use quietly builds the evaluation data into the model and
makes the reported result look better than real performance.

(UMaT and MN273 material is not an evaluation set for this project: it is not used for any AI
purpose, evaluation included. See the next section.)

What the tools enforce for evaluation datasets that are permitted:

- `independent_evaluation` cannot be combined with any other permitted use, and those records
  cannot share a manifest with training, validation or test records, in either schema version.
- The split generator refuses evaluation data and never assigns the `independent_evaluation`
  split.
- The audit fails if an image in an evaluation dataset also appears in any other dataset or
  split, and if a record was derived from an evaluation record or into one.

What the tools cannot enforce, and people must:

- Do not open the evaluation images or labels while developing the model.
- Fit calibration, uncertainty and decision thresholds on validation data from the training
  distribution, never on evaluation data.
- Decide the class mapping and the metrics before the first evaluation run, and run the
  evaluation as rarely as possible.
- Keep the evaluation files and their labels out of Git and under restricted access.
- Respect the rules that govern the source data (next section).

## Restricted material: UMaT practicals and MN273

UMaT practical samples, and anything the MN273 brief classifies as RED-zone material
(photographs, raw laboratory records, supplier and purchase records, the team dataset), must
not be used for training, validation, testing or independent AI evaluation.

No field in a manifest overrides this: not an authorisation reference, an approval status, a
licence record or a dataset category. The tools refuse a `umat_practical` manifest for every use
and in every tool, ignore any authorisation reference recorded in it, refuse the restricted
locations, and contain no exception mechanism. They are not a route to permission.

Any future proposal to use this material needs explicit written permission from the appropriate
authority and a separate governance review before any processing. That happens outside these
tools. It would require the project's rules and the tooling to be changed deliberately; it cannot
be granted by editing a manifest. Until then, do not access, copy, process, derive from or train
on the material, and do not put it in or under a dataset directory.
