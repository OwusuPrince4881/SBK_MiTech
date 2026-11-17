# Dataset workflow: validate, split, audit

How to prepare an image dataset for model development without leaking test data
into training. The manifest format is described in
[`dataset_manifest_schema_1_1.md`](dataset_manifest_schema_1_1.md) and the data
categories in [`dataset_strategy.md`](dataset_strategy.md).

These tools work offline on a manifest and a dataset directory. They do not read
or write the application database. The validator and the audit only read a dataset
directory, and the splitter never writes into a dataset directory (see the splitter
section below), so none of the tools can replace an image or a licence snapshot.
Commands are PowerShell, run from the repository root; use the project
virtual environment (`.\.venv\Scripts\python.exe`).

Exit codes for all three scripts: `0` on success, `2` on any failure, including an
unreadable, oversized or hostile input file. A failure is a message, never a
traceback.

**The tools are offline.** They never use the network and never read a file because a
manifest or a schema names it: a custom `--schema` may refer only to places inside itself
(`#...`), and a remote, `file:` or relative reference, or an `$id`, is refused before anything
is opened or fetched. A custom schema in a restricted location is refused unread.

**Output is plain ASCII.** Paths and text from manifests or from disk that contain characters
outside ASCII (a Japanese folder name, say) are printed as escapes such as `日`, so a Windows
console or a redirected stream in a legacy code page can never turn a finished run into an
error. The split generator renders all its output before writing anything, and writes every
output file or none (see "Generate reproducible splits").

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

As a second tripwire, a manifest in which **any** string mentions `UMaT`, `MN273` or
`University of Mines and Technology` is refused whatever its category says: dataset ID, name,
description, source, custodian and citation, licence and permission text, authorisation
references, notes, reviewer, sample, record and session IDs, labels, acquisition notes and file
paths. A source manifest (`--source-manifest`, or any manifest given to the audit) gets the
same check, so a derivation from one that names itself after restricted material is refused.
If the data is not restricted, rename it so it no longer carries those names.

This tripwire is a **best-effort check on names, not proof that anything is safe**. Text is
normalised first (full-width letters, invisible characters, accents and a small table of
look-alike Cyrillic, Greek and Armenian letters are folded). Then:

- The letters `umat` are refused wherever they run together, including inside a longer
  identifier: `UMaT`, `umat-2024`, `umat_practicals`, `UMaTData`, `umatphotos`, `umatsamples`,
  `umatlab`, `UMaTField`, `myumat` and `images/UMaTphotos/a.jpg` all match, as do the spaced
  forms `U.M.a.T` and `U-MaT`.
- A short list of innocent words that happen to contain those letters is set aside first
  (`sumatra`, `sumatriptan`, `pneumat-`, `rheumat-`, `thaumat-`, `humate`, `umatilla`,
  `fumatori-`, `cumate`), so `Sumatra`, `pneumatic`, `humate`, `Umatilla` and `rheumatic` are
  accepted, alone or inside a compound such as `PneumaticHammer` or `SumatraTrip`. The words are
  set aside as pieces, so a name hidden next to one (`pneumaticumat`, `SumatraUMaT`) is still
  found. **Any other word that contains `umat` is refused as well** (for example `Plumat`):
  rename it. That is a deliberate trade for fail-closed behaviour.
- `MN273` is matched with any separators and leading zeros (`MN 273`, `mn-0273`) as its own
  token, and also directly after a lower-case letter when written in capitals (`ProjectMN273`,
  `teamMN273`), but not inside a longer number or word (`autumn 273`, `hymn273`, `mn2735`). A
  lower-case `MN273` glued to the word before it (`teammn273`) is **not** caught.

It cannot see a manifest that never uses those names, a copy of restricted files placed
anywhere, a spelling or look-alike it has not been told about, or what is inside an image. It is
also why there is no hash list of restricted files: building one would require reading the
restricted material.

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
- **Files the manifest does not list are reported.** The directory is scanned, and the first
  512 bytes of each unlisted file are looked at. An image or video, recognised by its extension
  (in any letter case: `.jpg`, `.png`, `.webp`, `.tif`, `.heic`, `.bmp`, `.svg`, `.tga`, `.mp4`,
  `.mov` and many others) **or by a file signature at its start** (JPEG, PNG, GIF, TIFF, WebP,
  BMP, ICO, PNM, PSD, JPEG 2000, JPEG XL, OpenEXR, DDS, MP4/QuickTime, Matroska/WebM, AVI, ASF,
  FLV, MPEG and a few more), whatever its name, that is not in the manifest is an **error**
  (`unmanifested-image-file`). An **archive or container** (`.zip`, `.tar`, `.gz`, `.7z`, `.rar`,
  `.pdf`, Office documents, `.npy`, `.npz`, `.h5`, `.sqlite`, `.parquet` and similar, by extension
  or signature) that is not in the manifest is also an **error**
  (`unmanifested-container-file`): it can hold images or a whole dataset the tool cannot see
  into. Listing one as the licence snapshot does not make it acceptable: the snapshot must be
  plain text (`licence-snapshot-not-text`). A file with no recorded provenance or licence must not be used, and a
  loader that reads the folder would pick it up. Any other unlisted file is a warning
  (`unmanifested-file`). The tool only reports; it never adds, deletes, moves or changes a file.
  **The lists are practical, not complete.** A format with no listed extension and no recognised
  signature is not recognised, and the tool does not claim to detect every image or video format.
- **Links are errors.** A symbolic link or Windows junction anywhere in the dataset
  directory is the error `link-in-dataset-dir`, whatever it is named (`photo.jpg` included)
  and wherever it points. Links are never followed, so they cannot lead the scan outside the
  directory, but they can conceal images or whole folders, so the directory is not accepted
  while they are present. Replace each link with the real, listed files.
- **Ignore patterns cannot hide recognised media or archives.** `--ignore-file PATTERN`
  (repeatable) leaves out documented files such as `README.md` or `notes/*`. A pattern matches
  the whole relative path, case-sensitively, and `*` also matches `/`. It never applies to a file
  recognised as an image, a video or an archive (by extension or first bytes), or to a link, so
  `--ignore-file "*"` cannot make the check vacuous. It *can* leave out a file whose format the
  tool does not recognise, which is why every file a pattern leaves out is listed, with the count
  (`files-ignored-by-pattern`), and the split generator records them.
- Left out automatically: the manifest file itself, the licence snapshot (the manifest names
  it), and housekeeping files (`.gitkeep`, `Thumbs.db`, `desktop.ini`, `.DS_Store`), which
  are listed in the output. A housekeeping name is judged by the file's content: a JPEG named
  `.gitkeep`, or a ZIP named `Thumbs.db`, is reported as the image or archive it is.
- `--source-manifest PATH` (repeatable) supplies the manifest of a dataset that records here
  derive from (see "Derived data" below). Two sources with the same `dataset_id`, or a source
  with the manifest's own `dataset_id`, are refused, so the result never depends on the order of
  the arguments.
- `--source-dataset-dir DATASET_ID=DIRECTORY` (repeatable) gives the dataset directory of a
  source supplied with `--source-manifest`, always with that source's `dataset_id`. The source's
  record files and licence snapshot are checked there exactly as `--dataset-dir` checks this
  manifest's own, and a derived dataset that is in use is refused unless every source verifies
  (see "Derived data"). The splitter takes the same option.
- `--schema` adds a schema the manifest must *also* satisfy. The built-in schema always
  applies, so a custom schema cannot weaken it. It is read offline (see the top of this page),
  and the output says that a custom schema was applied.
- **Dates and evidence.** `licence.evidence.captured_on`, `permissions.reviewed_on`,
  `source.retrieved_on` and the field-governance dates cannot be later than today (UTC): they
  record something that already happened (`date-in-future`). A licence snapshot must be
  **real text**, even when its checksum matches: an empty file, or one with no visible text (only
  a byte-order mark, whitespace, non-breaking or zero-width characters), is the error
  `licence-snapshot-empty`; an image, video, archive, PDF or other container (recognised by its
  extension or its first bytes), or any file that is not valid UTF-8 (UTF-16 with a byte-order
  mark is also read) or holds control characters such as NUL, is `licence-snapshot-not-text`; and
  text that is a placeholder or too thin to be a document (`.`, `x`, `n/a`, `Terms.`) is
  `licence-snapshot-meaningless`. Save a licence that exists only as a PDF or a scan as
  transcribed text, and cite the original in the evidence reference. These checks are not
  complete: a binary format the tool does not recognise is caught only because it is not valid
  text, only the first megabyte is inspected (the whole file is hashed), and nothing tells you
  whether the text really is the licence. A `commercial_training_basis` must
  name a clause or document: placeholders such as `x`, `n/a`, `TBD` or `see licence`, text with
  fewer than 8 letters and digits, and repeated characters are refused
  (`commercial-training-basis-not-meaningful`). These are plausibility checks. They do not read
  the licence, and passing them says nothing about whether it permits a use.
- **Ghana field data fails closed.** A `ghana_field` manifest must carry a `field_governance`
  record (collection permission, consent, location privacy, reviewer, review date, permitted
  uses) with every safeguard resolved before it can be approved, split or used. Placeholders
  (`x`, `n/a`, `none`, `unknown`, `yes`, whitespace) do not count as recorded information. What
  is recorded is the author's declaration: the tool checks it is present and consistent and
  does not independently verify it. See [`ghana_field_governance.md`](ghana_field_governance.md).
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
  `--overwrite`. Outputs are never written into a restricted location. **The splitter never
  writes into a dataset directory**: it refuses any `--output` or `--output-manifest` that
  resolves to a path inside the dataset directory (relative paths and `..` are resolved, links
  and junctions are followed, and letter case is ignored where the file system ignores it), with
  or without `--overwrite`, so it cannot replace an image, the licence snapshot or any other
  source file. It never writes inside a source's `--source-dataset-dir` either, and never
  replaces a source manifest. Write the split file and the manifest copy to a folder outside
  every dataset directory, as in the example above. **All output is
  rendered before anything is written, and every file is written or none is**: if the second
  file cannot be written, the first is removed again (without `--overwrite`) or was never moved
  into place (with it, each file is written completely beside its target and then moved), so a
  failure cannot leave a split file that looks finished. If the terminal cannot take the final
  summary, the run is still a success and the files are complete.
- Samples are spread over the splits **per material category**, so each category appears in
  each split where there are enough samples.
- `--group-capture-sessions` keeps samples that share a capture session in the same split.
- The tool refuses, and writes nothing, when: the manifest is invalid, restricted, or a
  file does not match its checksum; the dataset directory has an unlisted image or a link;
  any requested split's use is not permitted; the dataset is restricted or holds
  `independent_evaluation` records; records already have splits (`--allow-resplit` discards
  ordinary ones); a derivation cannot be established or a source dataset is not eligible (see
  below), including a source whose files were not or could not be verified; the sample identity
  is only assumed (see below); the manifest is schema 1.0; or an output path is inside the
  dataset directory or a source's directory, or is a source manifest.

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
    --dataset-dir other-dataset=data\datasets\raw\other-dataset `
    --split-file my-dataset=data\datasets\manifests\my-dataset.splits.json
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
| A supplied split file that does not describe the manifest (other dataset, other records, a different split or physical sample for any record, or not a split file) | error |
| A warning recorded in a supplied split file | warning |

Pass every manifest that must stay apart in one run, for example the training dataset and the
evaluation dataset, together with the manifest of every dataset that records derive from.
`--ignore-file` and `--strict` work as above; `--strict` makes warnings fail too.

`--split-file [DATASET_ID=]PATH` (repeatable) supplies the split file that
`generate_dataset_splits.py` wrote for a manifest. Audit the manifest written by
`--output-manifest`, unchanged, with its split file: the audit checks that the split file is for
the same dataset, lists the same records, and gives every record the same split and physical
sample as the manifest, so a manifest edited after splitting, or paired with the wrong split
file, is an error. The split file's warnings are then reported as warnings and as limitations.

**The audit checks for leakage and nothing else.** It prints, before every result, that it
"does not assess split quality, label quality or how reliable any measured performance will be:
a PASS is not approval of the splits". It also looks at how big each evaluated split is,
without needing a split file: a validation, test or independent-evaluation split with fewer than
30 physical samples is a limitation (the same threshold, and the same words, as the split
generator's warning). Small splits are not leakage, so they never turn a PASS into a FAIL, but
the result is then qualified. A split of 30 or more samples is not thereby reliable either.

The result is **PASS** only when nothing is wrong and nothing is left unverified. Otherwise it
reads **PASS WITH LIMITATIONS** and prints a `LIMITATION` line for each of:

- a manifest whose physical sample IDs are not verified (assumed, one image per sample, or
  schema 1.0): images of one real sample can sit in different splits without the audit
  noticing;
- a manifest audited without its dataset directory: its files and licence snapshot were not
  verified against their checksums;
- an evaluated split that is small, and every warning in a supplied split file (`LIMITATION:
  split quality`): a measurement on those splits must not be presented as reliable evidence.

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
- **A source dataset must itself be in good standing.** Each source manifest is fully
  validated, not just read for its structure, and a derivative inherits the governance of what
  it was derived from. A derivation is refused when the source manifest does not validate
  (`derived-from-invalid-source`); its permission status is `denied`
  (`derived-from-denied-source`) or anything other than `approved`, such as `unknown` or
  `pending_review` (`derived-from-unapproved-source`); it is a schema 1.0 manifest, which
  records no permissions (`derived-from-source-without-permission-data`); it is a Ghana field
  dataset whose safeguards are unresolved (`derived-from-unresolved-field-source`); it is
  approved but preserves no licence snapshot
  (`derived-from-source-without-licence-snapshot`); or it does not list the use the derivative
  is put to (`derived-from-source-use-not-permitted`). The same rules apply, in the same way, to
  the validator, the splitter and the audit, and a chain of derivations is followed (a cycle is
  `derivation-cycle`; a source supplied only as a structural view, with no manifest to validate,
  is `derived-source-unassessed`). While the derivative is still a draft (nothing assigned, no
  use requested, not recorded as approved) these are warnings; once it is in use they are
  errors.
- **A source's evidence must be verified, not just declared.** A manifest that lists files and a
  licence snapshot is not evidence that they exist or are unchanged. Each source is therefore
  checked in its own dataset directory, in the same way `--dataset-dir` checks a manifest's own
  files: every listed record file and the licence snapshot must exist with the recorded spelling
  and match its SHA-256, the snapshot must pass the content rules (real text, not an image, an
  archive, a byte-order mark alone or a placeholder), and the directory must hold no unlisted
  image, archive or link and must not be a restricted location. The derivation is refused when
  no directory was supplied for the source (`derived-from-source-files-not-verified`) and when
  the check fails (`derived-from-source-files-invalid`, naming the first errors: a missing file,
  a checksum mismatch, a missing or invalid snapshot, a directory that does not exist). As with
  the governance rules above, these are warnings while the derivative is a draft and errors once
  it is in use, and a derived dataset that is in use is never cleared on an unverified source.
- **How a source's directory is found.** It is never guessed: not from where the source's
  manifest is, and not from anything the manifest says (its relative paths are relative to a
  directory it does not record). It is bound explicitly, by dataset ID: `--source-dataset-dir
  DATASET_ID=DIRECTORY` for the validator and the splitter, and the audit's existing
  `--dataset-dir DATASET_ID=DIRECTORY`, which serves every audited manifest both as itself and as
  a source. The ID must be that of exactly one supplied source manifest; an ID given twice, an
  ID no source has, a value without an ID and a restricted location are refused before anything
  is read. Two datasets checked in the same directory, or in directories one inside the other
  (including a source and the dataset derived from it), are refused as
  `ambiguous-dataset-directory`, because the files of one would be counted, or hidden, as files
  of the other. Links are followed for that comparison.
- **The whole chain.** A source that itself derives from another dataset is checked the same
  way, recursively: every dataset in the chain must be supplied, bound to its directory, verify
  and be eligible, and a use the top dataset is put to must be permitted all the way down, so a
  use cannot be laundered through an intermediate dataset. A loop is `derivation-cycle`.
  Restricted material is refused before anything else and its directory is never read. A schema
  1.0 source records no checksums, so it cannot be verified and is refused as before.

  ```powershell
  .\.venv\Scripts\python.exe apps\api\scripts\validate_dataset_manifest.py `
      data\datasets\manifests\my-derived.json --dataset-dir data\datasets\processed\my-derived `
      --use training `
      --source-manifest data\datasets\manifests\my-source.json `
      --source-dataset-dir my-source=data\datasets\raw\my-source
  ```
- **Commercial training is inherited across the whole chain.** A derived dataset cannot grant a
  broader commercial permission than its sources. Whenever commercial training is requested for
  a dataset (`--require-commercial-training`, in the validator or the splitter), is needed by a
  dataset derived from it, or is recorded as `permitted` by the dataset itself (which the audit
  checks too), every source it derives from, and every source of those, must itself record
  `commercial_training: permitted` with a meaningful `commercial_training_basis`, and must permit
  training. Otherwise the derivation is refused: a source that records `not_permitted`
  (`derived-from-source-commercial-training-denied`); one that records `unknown`
  (`derived-from-source-commercial-training-not-permitted`, because `unknown` is not permission);
  and one that records `permitted` with no basis or a placeholder basis
  (`derived-from-source-commercial-training-unsupported`). A source that is otherwise ineligible is
  refused by the rules above as well. Ordinary (non-commercial) training is unaffected: a source
  that denies commercial training can still support training that is not commercial. As with
  every recorded permission, the tools check that the record is present, consistent and
  meaningful, not that the licence allows commercial use.
- **Within one run, files are hashed once.** A source reached through several derivations, or a
  manifest the audit checks both as itself and as a source, is hashed once per run. Nothing is
  kept between runs: every run re-reads and re-hashes every file, so a result is never reused
  after a file has changed.
- **What verification does not establish.** It shows that the recorded evidence is intact: the
  files the source lists are present and unchanged since its manifest was written, and its
  licence snapshot is real text. It does not show that the licence permits the use, that the
  data is owned as recorded, or that recorded permission or consent is genuine; those remain
  the source's declarations, and the tools' output says so beside each source.
- **Time of check, time of use.** Verification happens when the tool runs. A file changed or
  replaced after it was hashed, by another process during the run or at any time after it, is
  not detected until the next run, and the tools cannot prevent a later training job from
  reading different bytes. Verify again immediately before use, and keep dataset directories
  read-only while they are in use.

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
  snapshot must be real text (an empty file, an image, an archive, a PDF, another binary file
  or a placeholder is rejected, though the tool cannot tell what the text says), the evidence
  must not be captured after the review that relied on it, and neither date may be in the
  future.
- **Recorded is not verified.** Approving a dataset needs evidence on record. *Clearing a use*
  needs more: the dataset directory, files that match their checksums, and a snapshot that
  matches its checksum. Even then the tools only know that the files are unchanged since the
  manifest was written. A person must read the licence.
- **Unknown permissions fail closed.** `unknown`, `pending_review` and `denied` permit nothing,
  and recording evidence does not change that.
- **Commercial training is recorded explicitly, and separately.** `commercial_training` must be
  `permitted` before data goes into a model that could be sold or used commercially, `unknown`
  is not permission, and it can be `permitted` only if training itself is. A free-text
  `commercial_training_basis` says which clause or document allows it and must be more than a
  placeholder; the tools do not verify it, and the tools cannot determine whether a licence
  legally permits commercial use. Licences that forbid commercial use, or derivatives, need
  particular care.
- Keep original files unchanged under `data/datasets/raw/`. Derived files go under
  `processed/`, with `provenance.derived_from` and `processing_steps` recorded, and
  the same `physical_sample_id` as their source.
- Record a SHA-256 checksum for every file, and re-run the validator with `--dataset-dir`
  before each use to detect changed or missing files.
- Keep the four categories apart (`public`, `synthetic`, `umat_practical`, `ghana_field`).
  Ghana field data additionally needs its safeguards documented first
  ([`ghana_field_governance.md`](ghana_field_governance.md)). Real datasets, photographs and manifests stay out of Git; `data/datasets/manifests/` is
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
