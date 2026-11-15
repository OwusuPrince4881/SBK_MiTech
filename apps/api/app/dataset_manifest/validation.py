"""Offline manifest validation: structure, cross-record rules, permissions, files.

Two things this module never does. It never treats a manifest's own claim as a
verified fact: a reviewer name, an evidence reference, a commercial-training
basis or an authorisation reference is recorded text, and the checks here only
decide whether the record is consistent. And it never lets any such field
override the prohibition on UMaT/MN273 restricted material, which is refused
for every use.

Passing validation means the manifest is consistent. It does not mean the
images are good, correctly labelled, free of near-duplicates, or lawfully
usable: whether a licence permits a use is a human judgement.
"""

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

from app.dataset_manifest.files import DirectoryScan, find_unmanifested, scan_directory
from app.dataset_manifest.issues import Report, summarise_ids
from app.dataset_manifest.restricted import refusal_message, restriction_of
from app.dataset_manifest.schema import (
    SUPPORTED_VERSIONS,
    SchemaLoadError,
    check_structure,
    detect_version,
    load_schema,
)
from app.dataset_manifest.view import (
    ASSIGNED_SPLITS,
    UNASSIGNED,
    ManifestView,
    RecordView,
    build_view,
)

USES = ("training", "validation", "testing", "independent_evaluation")
SPLIT_TO_USE = {
    "train": "training",
    "validation": "validation",
    "test": "testing",
    "independent_evaluation": "independent_evaluation",
}
HASH_CHUNK_SIZE = 1024 * 1024
ASSUMED_IDENTITY = "assumed_each_image_distinct"
INFERRED_IDENTITY = "inferred_by_documented_analysis"
RESTRICTED_CATEGORY = "umat_practical"
EVALUATION = "independent_evaluation"
LISTED_NAMES = 20  # How many unmanifested file names a single message shows.
# A best-effort tripwire on identifying text, in addition to the category and the path guard.
RESTRICTED_TOKENS = re.compile(r"umat|mn[\s_\-]?273", re.IGNORECASE)


@dataclass
class ValidationResult:
    report: Report
    version: str | None = None
    record_count: int = 0
    sample_count: int = 0
    files_verified: bool = False
    # Uses for which the manifest's recorded permission was accepted after its files and
    # licence snapshot were verified against their checksums. This is the manifest's own
    # declaration, not a determination that the licence allows the use.
    cleared_uses: tuple[str, ...] = field(default_factory=tuple)
    commercial_training_cleared: bool = False
    # How the physical sample IDs were established: the manifest's
    # sample_identity.basis, or None for schema 1.0, which does not record it.
    sample_identity_basis: str | None = None
    # True when every physical sample has exactly one image, so grouping by sample protects nothing.
    single_image_samples: bool = False
    # Files in the dataset directory that the manifest does not list (full lists).
    unmanifested_images: list[str] = field(default_factory=list)
    unmanifested_other: list[str] = field(default_factory=list)
    links_not_followed: list[str] = field(default_factory=list)
    ignored_by_pattern: list[str] = field(default_factory=list)
    ignored_housekeeping: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.report.ok


def validate_document(
    manifest: Any,
    *,
    dataset_dir: Path | None = None,
    uses: Iterable[str] = (),
    require_commercial_training: bool = False,
    schema_path: Path | None = None,
    manifest_path: Path | None = None,
    ignore_patterns: Sequence[str] = (),
    provenance_sources: Mapping[str, ManifestView] | None = None,
) -> ValidationResult:
    """Validate a parsed manifest.

    `uses` are the uses the caller proposes (training, validation, testing,
    independent_evaluation). A proposed use is accepted only if the manifest
    records the permission, the dataset directory is supplied, and the files and
    the licence snapshot in it match their recorded checksums.

    With `dataset_dir`, every file must exist inside it with exactly the recorded
    spelling and match its checksum; files, links and folders in it that the
    manifest does not account for are reported (never changed); and the
    directory must not be in a restricted location. `manifest_path` lets the
    manifest file itself be left out of that comparison; `ignore_patterns` leave
    out documented non-image files, never images or videos.

    `provenance_sources` maps dataset IDs to the views of the other manifests a
    record may derive from. A derivation from a dataset that is not supplied
    cannot be established and is an error.
    """
    requested = set(uses)
    unknown = requested - set(USES)
    if unknown:
        raise ValueError(f"Unknown use(s) {sorted(unknown)}; expected a subset of {list(USES)}.")

    report = Report()
    result = ValidationResult(report)

    version = detect_version(manifest)
    if version not in SUPPORTED_VERSIONS:
        report.error(
            "unsupported-schema-version",
            f"schema_version must be one of {list(SUPPORTED_VERSIONS)}; found {version!r}.",
            "schema_version",
        )
        return result
    result.version = version

    # The built-in schema always applies. A custom schema can only add to it, so it
    # cannot weaken the structure that the checks below rely on.
    try:
        schemas = [load_schema(version)]
        if schema_path is not None:
            schemas.append(load_schema(version, schema_path))
    except SchemaLoadError as error:
        report.error("schema-unavailable", str(error))
        return result
    try:
        for schema in schemas:
            check_structure(manifest, schema, report)
    except RecursionError:
        report.error("manifest-too-deep", "the manifest is nested too deeply to be checked.")
        return result
    report.issues = list(dict.fromkeys(report.issues))
    if not report.ok:
        # Cross-record checks assume the structure is sound.
        return result

    try:
        view = build_view(manifest, version)
    except (KeyError, TypeError, AttributeError, IndexError, ValueError) as error:
        report.error(
            "manifest-unreadable",
            f"the manifest does not have the structure this tool needs ({type(error).__name__}).",
        )
        return result
    result.record_count = len(view.records)
    result.sample_count = len(view.samples())
    result.single_image_samples = len(view.records) >= 2 and result.sample_count == len(view.records)

    _check_restricted_material(manifest, view, report)
    _check_evaluation_isolation(view, report)
    _check_ids_and_paths(view, report)
    _check_sample_splits(view, report)
    _check_provenance(view, report, provenance_sources)
    if version == "1.1":
        result.sample_identity_basis = manifest["dataset"]["sample_identity"]["basis"]
        _check_checksums_and_filenames(manifest, view, report)
        _check_dates(manifest, report)
        _check_public_requirements(manifest, report)
        _check_permissions(manifest, view, report)
        _check_licence_evidence(manifest, view, report)
        _check_labels(manifest, view, report)
        _check_sample_identity(manifest, result, report)
    else:
        report.warn(
            "unverified-sample-identity",
            "schema 1.0 does not record how the sample IDs were established, so they are "
            "not known to be verified physical samples. Images of one physical sample "
            "may be spread over several IDs and so over several splits.",
            "schema_version",
        )
    _check_requested_uses(
        manifest, version, requested, require_commercial_training, dataset_dir is not None, report
    )
    if dataset_dir is not None:
        result.files_verified = _verify_files(
            manifest, view, dataset_dir, report, result, manifest_path, ignore_patterns
        )

    wants_clearance = bool(requested) or require_commercial_training
    if report.ok and version == "1.1" and wants_clearance and result.files_verified:
        result.cleared_uses = tuple(sorted(requested))
        result.commercial_training_cleared = require_commercial_training
    return result


def source_view(document: Any) -> ManifestView:
    """A view of another manifest, for checking derivations. Raises ValueError if it is unreadable.

    Only the structure must be sound: a source manifest that is itself restricted or
    otherwise invalid is still loaded, so that a derivation from it can be refused.
    """
    version = detect_version(document)
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(f"schema_version must be one of {list(SUPPORTED_VERSIONS)}; found {version!r}.")
    report = Report()
    try:
        check_structure(document, load_schema(version), report)
    except (SchemaLoadError, RecursionError) as error:
        raise ValueError(str(error) or "the manifest is nested too deeply") from error
    if not report.ok:
        raise ValueError(f"{len(report.errors)} structural error(s); first: {report.errors[0].message}")
    return build_view(document, version)

# --- helpers ---------------------------------------------------------------


def _fold(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _ambiguous_groups(values: Iterable[str]) -> list[list[str]]:
    """Groups of distinct values that differ only in case or Unicode form."""
    grouped: dict[str, set[str]] = {}
    for value in set(values):
        grouped.setdefault(_fold(value), set()).add(value)
    groups = [sorted(group) for group in grouped.values() if len(group) > 1]
    return sorted(groups)


def _container(view: ManifestView) -> str:
    return "images" if view.version == "1.0" else "records"


def _location(view: ManifestView, index: int) -> str:
    return f"{_container(view)}[{index}]"


# --- restricted material and evaluation isolation (both schema versions) ---------


def _identifying_strings(manifest: dict[str, Any], view: ManifestView) -> list[tuple[str, str]]:
    dataset = manifest["dataset"]
    items: list[tuple[str, Any]] = [
        ("dataset.dataset_id", dataset.get("dataset_id")),
        ("dataset.name", dataset.get("name")),
    ]
    source = dataset.get("source", {})
    for key in ("name", "url", "custodian", "reference"):
        items.append((f"dataset.source.{key}", source.get(key)))
    for record in view.records:
        items.append((f"{_location(view, record.index)}.relative_path", record.relative_path))
    return [(location, text) for location, text in items if isinstance(text, str)]


def _check_restricted_material(
    manifest: dict[str, Any], view: ManifestView, report: Report
) -> None:
    """UMaT/MN273 material is refused for every use, whatever any other field says."""
    if view.category == RESTRICTED_CATEGORY:
        mentioned = ""
        if "authorisation_reference" in manifest["dataset"]["permissions"]:
            mentioned = " The recorded authorisation reference is ignored: it does not change this."
        report.error(
            "restricted-category",
            "category 'umat_practical' is restricted. UMaT/MN273 material must not be used for "
            "training, validation, testing or independent AI evaluation, and no approval, "
            "authorisation reference, licence record or category overrides that. This manifest "
            "is refused for every use. Any proposed exception needs explicit written permission "
            f"from the appropriate authority and a separate governance review before processing, "
            f"outside these tools.{mentioned}",
            "dataset.category",
        )

    # The category is only a declaration, so also look at what the manifest identifies itself by.
    hits = [
        (location, text)
        for location, text in _identifying_strings(manifest, view)
        if RESTRICTED_TOKENS.search(text)
    ]
    if hits:
        shown = "; ".join(f"{location}={text!r}" for location, text in hits[:5])
        more = f" and {len(hits) - 5} more" if len(hits) > 5 else ""
        report.error(
            "restricted-material-suspected",
            f"identifying text mentions UMaT or MN273 ({shown}{more}). UMaT/MN273 material must "
            "not be used for any AI purpose, and this tool does not rely on the declared "
            "category to protect it. If this is not restricted material, rename it so that it no "
            "longer carries those names.",
            "dataset",
        )


def _check_evaluation_isolation(view: ManifestView, report: Report) -> None:
    """Independent evaluation applies to a whole dataset, in either schema version."""
    splits_present = {record.split for record in view.records}
    if EVALUATION in splits_present and len(splits_present) > 1:
        report.error(
            "evaluation-records-mixed",
            f"independent evaluation applies to a whole dataset, but this manifest also "
            f"has records in {sorted(splits_present - {EVALUATION})}. "
            "Keep evaluation records in their own dataset.",
        )


# --- checks that apply to schema 1.0 and 1.1 ---------------------------------


def _check_ids_and_paths(view: ManifestView, report: Report) -> None:
    first_seen: dict[str, int] = {}
    for record in view.records:
        if record.record_id in first_seen:
            report.error(
                "duplicate-record-id",
                f"record ID {record.record_id!r} is already used by "
                f"{_location(view, first_seen[record.record_id])}.",
                _location(view, record.index),
            )
        else:
            first_seen[record.record_id] = record.index
    for group in _ambiguous_groups(r.record_id for r in view.records):
        report.error(
            "ambiguous-record-id",
            f"record IDs {group} differ only in case or Unicode form; use one spelling.",
        )

    first_path: dict[str, int] = {}
    for record in view.records:
        if record.relative_path in first_path:
            report.error(
                "duplicate-relative-path",
                f"path {record.relative_path!r} is already used by "
                f"{_location(view, first_path[record.relative_path])}; "
                "one file must not be recorded twice.",
                _location(view, record.index),
            )
        else:
            first_path[record.relative_path] = record.index
    for group in _ambiguous_groups(r.relative_path for r in view.records):
        report.error(
            "ambiguous-relative-path",
            f"paths {group} differ only in case and would name one file on a "
            "case-insensitive file system.",
        )

    for group in _ambiguous_groups(r.physical_sample_id for r in view.records):
        report.error(
            "ambiguous-physical-sample-id",
            f"physical sample IDs {group} differ only in case or Unicode form. They could "
            "be treated as different samples and split apart; use one spelling.",
        )
    sessions = [r.capture_session_id for r in view.records if r.capture_session_id is not None]
    for group in _ambiguous_groups(sessions):
        report.error(
            "ambiguous-capture-session-id",
            f"capture session IDs {group} differ only in case or Unicode form; use one spelling.",
        )


def _check_sample_splits(view: ManifestView, report: Report) -> None:
    for sample, records in view.samples().items():
        splits = sorted({record.split for record in records})
        if len(splits) > 1:
            ids = [record.record_id for record in records]
            report.error(
                "sample-in-multiple-splits",
                f"physical sample {sample!r} has records in more than one split "
                f"({', '.join(splits)}); records: {summarise_ids(ids)}. "
                "Every record of a physical sample must be in the same split.",
                f"physical_sample_id {sample}",
            )


def _check_provenance(
    view: ManifestView, report: Report, sources: Mapping[str, ManifestView] | None
) -> None:
    """Every declared derivation must be traceable; one that cannot be traced fails closed."""
    by_id = view.records_by_id()
    other_records: dict[str, dict[str, RecordView]] = {}
    for dataset_id, source_view in (sources or {}).items():
        if dataset_id != view.dataset_id:
            other_records[dataset_id] = source_view.records_by_id()

    for record in view.records:
        location = f"{_location(view, record.index)}.provenance.derived_from"
        for source_dataset, source_id in record.derived_from:
            if source_dataset == view.dataset_id:
                if source_id == record.record_id:
                    report.error("derived-from-self", "a record cannot derive from itself.", location)
                elif source_id not in by_id:
                    report.error(
                        "derived-from-unknown-record",
                        f"derived_from names record {source_id!r}, which is not in this manifest.",
                        location,
                    )
                elif by_id[source_id].physical_sample_id != record.physical_sample_id:
                    report.error(
                        "derived-sample-mismatch",
                        f"record derives from {source_id!r} but belongs to a different "
                        "physical sample. A derivative must keep its source's physical sample ID, "
                        "or derivatives of one sample could land in different splits.",
                        location,
                    )
                continue

            if source_dataset not in other_records:
                report.error(
                    "derived-source-unverified",
                    f"record derives from {source_id!r} in dataset {source_dataset!r}, whose "
                    "manifest was not supplied, so the derivation cannot be established. Supply "
                    "that manifest (the audit takes every manifest together; the validator and "
                    "the splitter take --source-manifest) or remove the derivation.",
                    location,
                )
                continue
            source = other_records[source_dataset].get(source_id)
            if source is None:
                report.error(
                    "derived-from-unknown-record",
                    f"derived_from names record {source_id!r}, which is not in the manifest of "
                    f"dataset {source_dataset!r}.",
                    location,
                )
                continue
            _check_cross_dataset_derivation(
                record, source, sources[source_dataset], source_dataset, location, report
            )


def _check_cross_dataset_derivation(
    record: RecordView,
    source: RecordView,
    source_view: ManifestView,
    source_dataset: str,
    location: str,
    report: Report,
) -> None:
    derived_split, source_split = record.split, source.split
    where = f"{source.record_id!r} of dataset {source_dataset!r} ({source_split})"
    if source_view.category == RESTRICTED_CATEGORY:
        report.error(
            "derived-from-restricted-source",
            f"record derives from {where}, which is restricted UMaT/MN273 material. Nothing may "
            "be derived from it.",
            location,
        )
    elif source_split == EVALUATION and derived_split != EVALUATION:
        report.error(
            "derived-from-evaluation-record",
            f"record ({derived_split}) derives from {where}. Independent-evaluation records must "
            "not be used to produce any other record, least of all training or validation data.",
            location,
        )
    elif derived_split == EVALUATION and source_split != EVALUATION:
        report.error(
            "derived-into-evaluation",
            f"an independent-evaluation record derives from {where}, which is not evaluation "
            "data. That would contaminate the evaluation.",
            location,
        )
    elif derived_split != source_split and UNASSIGNED not in (derived_split, source_split):
        report.error(
            "derivation-crosses-splits",
            f"record ({derived_split}) derives from {where}; a derivative and its source must "
            "be in the same split.",
            location,
        )
    elif derived_split != source_split:
        report.warn(
            "derivation-split-undetermined",
            f"record ({derived_split}) derives from {where}; the splits differ because one is "
            "still unassigned, so leakage cannot be judged until both are assigned.",
            location,
        )


# --- schema 1.1 checks ---------------------------------------------------------


def _check_checksums_and_filenames(
    manifest: dict[str, Any], view: ManifestView, report: Report
) -> None:
    for index, record in enumerate(manifest["records"]):
        file = record["file"]
        basename = PurePosixPath(file["relative_path"]).name
        if file["filename"] != basename:
            report.error(
                "filename-mismatch",
                f"filename {file['filename']!r} does not match the last part of "
                f"relative_path ({basename!r}).",
                f"records[{index}].file.filename",
            )

    by_hash: dict[str, list[str]] = {}
    for record in view.records:
        if record.sha256 is not None:
            by_hash.setdefault(record.sha256, []).append(record.record_id)
    for sha256, ids in by_hash.items():
        if len(ids) > 1:
            report.error(
                "duplicate-checksum",
                f"records {summarise_ids(ids)} have identical content (SHA-256 {sha256}); "
                "one image must not be recorded more than once.",
            )


def _calendar_valid(value: str) -> bool:
    try:
        date.fromisoformat(value[:10])
    except ValueError:
        return False
    return True


def _check_dates(manifest: dict[str, Any], report: Report) -> None:
    dataset = manifest["dataset"]
    candidates = [
        ("dataset.source.retrieved_on", dataset["source"].get("retrieved_on")),
        ("dataset.permissions.reviewed_on", dataset["permissions"].get("reviewed_on")),
        (
            "dataset.licence.evidence.captured_on",
            dataset["licence"].get("evidence", {}).get("captured_on"),
        ),
    ]
    for index, record in enumerate(manifest["records"]):
        candidates.append(
            (
                f"records[{index}].acquisition.captured_at",
                record.get("acquisition", {}).get("captured_at"),
            )
        )
        candidates.append(
            (
                f"records[{index}].label.source.annotated_on",
                record.get("label", {}).get("source", {}).get("annotated_on"),
            )
        )
    for location, value in candidates:
        if value is not None and not _calendar_valid(value):
            report.error("invalid-date", f"{value!r} is not a real calendar date.", location)


def _check_public_requirements(manifest: dict[str, Any], report: Report) -> None:
    dataset = manifest["dataset"]
    if dataset["category"] != "public":
        return
    if "url" not in dataset["licence"]:
        report.error(
            "public-missing-licence-url",
            "a public dataset must record the licence URL so the licence can be re-checked.",
            "dataset.licence.url",
        )
    if "url" not in dataset["source"] and "doi" not in dataset["source"]:
        report.error(
            "public-missing-source-reference",
            "a public dataset must record a source URL or DOI.",
            "dataset.source",
        )


def _check_permissions(manifest: dict[str, Any], view: ManifestView, report: Report) -> None:
    permissions = manifest["dataset"]["permissions"]
    status = permissions["status"]
    uses = set(permissions["permitted_uses"])

    if uses and status != "approved":
        report.error(
            "permitted-uses-without-approval",
            f"permitted_uses is {sorted(uses)} but the permission status is {status!r}; "
            "uses can only be permitted once the status is 'approved'.",
            "dataset.permissions",
        )
    if permissions["commercial_training"] == "permitted":
        if status != "approved":
            report.error(
                "commercial-training-without-approval",
                f"commercial_training is 'permitted' but the permission status is {status!r}.",
                "dataset.permissions",
            )
        if "training" not in uses:
            report.error(
                "commercial-training-without-training-permission",
                "commercial_training is 'permitted' but 'training' is not in permitted_uses. "
                "Commercial training cannot be permitted where training itself is not.",
                "dataset.permissions.commercial_training",
            )
    if EVALUATION in uses and uses != {EVALUATION}:
        report.error(
            "evaluation-use-not-exclusive",
            f"'independent_evaluation' cannot be combined with other uses; "
            f"permitted_uses is {sorted(uses)}. Evaluation data must never also train or tune.",
            "dataset.permissions.permitted_uses",
        )

    for split in ASSIGNED_SPLITS:
        needed = SPLIT_TO_USE[split]
        ids = [record.record_id for record in view.records if record.split == split]
        if ids and needed not in uses:
            report.error(
                "split-use-not-permitted",
                f"{len(ids)} record(s) are assigned to split {split!r}, which needs the use "
                f"{needed!r}, but the permissions are status {status!r} with permitted_uses "
                f"{sorted(uses)}: {summarise_ids(ids)}.",
                "dataset.permissions",
            )


def _check_licence_evidence(manifest: dict[str, Any], view: ManifestView, report: Report) -> None:
    """Approval must be backed by preserved evidence; the tool checks it is recorded, not its merit."""
    dataset = manifest["dataset"]
    permissions = dataset["permissions"]
    licence = dataset["licence"]
    evidence = licence.get("evidence")

    if permissions["status"] == "approved" and evidence is None:
        report.error(
            "approval-without-licence-evidence",
            "the permission status is 'approved' but no licence evidence is recorded. Preserve "
            "a snapshot of the licence text or permission document (licence.evidence.snapshot), "
            "an archive link (archive_url) or a reference to it (reference), with the date it "
            "was captured.",
            "dataset.licence.evidence",
        )
    if permissions["commercial_training"] == "permitted" and "commercial_training_basis" not in permissions:
        report.error(
            "commercial-training-basis-missing",
            "commercial_training is 'permitted' but commercial_training_basis does not say "
            "which licence clause or permission document allows it.",
            "dataset.permissions.commercial_training_basis",
        )

    reviewed_on = permissions.get("reviewed_on")
    captured_on = (evidence or {}).get("captured_on")
    if (
        reviewed_on is not None
        and captured_on is not None
        and _calendar_valid(reviewed_on)
        and _calendar_valid(captured_on)
        and captured_on[:10] > reviewed_on[:10]
    ):
        report.error(
            "evidence-after-review",
            f"the licence evidence was captured on {captured_on}, after the review on "
            f"{reviewed_on}, so the review cannot have been based on it. Review it again and "
            "update reviewed_on.",
            "dataset.licence.evidence.captured_on",
        )

    snapshot = (evidence or {}).get("snapshot")
    if snapshot is not None:
        folded = _fold(snapshot["relative_path"])
        if any(_fold(record.relative_path) == folded for record in view.records):
            report.error(
                "duplicate-relative-path",
                f"the licence snapshot path {snapshot['relative_path']!r} is also an image "
                "record's path; keep them as separate files.",
                "dataset.licence.evidence.snapshot.relative_path",
            )


def _check_labels(manifest: dict[str, Any], view: ManifestView, report: Report) -> None:
    dataset = manifest["dataset"]
    vocabulary = dataset.get("label_vocabulary")
    labelled = [record for record in view.records if record.category is not None]

    if labelled and vocabulary is None:
        report.error(
            "vocabulary-required",
            "records carry labels but dataset.label_vocabulary is missing; declare the "
            "categories this dataset uses.",
            "dataset.label_vocabulary",
        )
    if vocabulary is not None:
        declared = vocabulary["categories"]
        for group in _ambiguous_groups(declared):
            report.error(
                "ambiguous-vocabulary-category",
                f"vocabulary categories {group} differ only in case or Unicode form.",
                "dataset.label_vocabulary.categories",
            )
        unknown: dict[str, list[str]] = {}
        for record in labelled:
            if record.category not in declared:
                unknown.setdefault(record.category or "", []).append(record.record_id)
        for category, ids in unknown.items():
            report.error(
                "unknown-material-category",
                f"material_category {category!r} is not in the declared vocabulary "
                f"{declared}: {summarise_ids(ids)}.",
            )

    for sample, records in view.samples().items():
        categories = sorted({r.category for r in records if r.category is not None})
        if len(categories) > 1:
            report.warn(
                "sample-label-conflict",
                f"physical sample {sample!r} has records with different categories "
                f"{categories}; a physical sample should have one category.",
                f"physical_sample_id {sample}",
            )

    generated = [
        record.record_id
        for record in view.records
        if record.label_source_type == "model_generated"
        and record.split in ("validation", "test", "independent_evaluation")
    ]
    if generated:
        report.error(
            "model-label-as-ground-truth",
            f"model-generated labels cannot be ground truth in validation, test or "
            f"independent evaluation splits: {summarise_ids(generated)}.",
        )


def _check_sample_identity(
    manifest: dict[str, Any], result: ValidationResult, report: Report
) -> None:
    identity = manifest["dataset"]["sample_identity"]
    basis = identity["basis"]
    if basis == ASSUMED_IDENTITY:
        report.warn(
            "unverified-sample-identity",
            "sample identity is only ASSUMED: each image is treated as its own physical sample "
            "without verification. The physical sample IDs are not verified, and grouping by "
            "them cannot prevent leakage if several images show the same sample.",
            "dataset.sample_identity.basis",
        )
        return
    if basis == INFERRED_IDENTITY and not identity.get("notes", "").strip():
        report.error(
            "identity-notes-missing",
            "the sample identity is 'inferred_by_documented_analysis' but sample_identity.notes "
            "does not describe the analysis. An inference that is not described is not documented.",
            "dataset.sample_identity.notes",
        )
    if result.single_image_samples:
        report.warn(
            "single-image-samples",
            f"every physical sample has exactly one image although the identity basis is "
            f"{basis!r}. Grouping by physical sample then protects nothing, so the identity is "
            "treated as unverified: images of one real sample may still be spread across splits.",
            "dataset.sample_identity.basis",
        )


# --- requested uses -----------------------------------------------------------


def _check_requested_uses(
    manifest: dict[str, Any],
    version: str,
    requested: set[str],
    require_commercial_training: bool,
    dataset_dir_given: bool,
    report: Report,
) -> None:
    if not requested and not require_commercial_training:
        return
    if version != "1.1":
        report.error(
            "no-permission-data",
            f"schema {version} carries no permission information, so it cannot be cleared "
            "for any use. Migrate the manifest to schema 1.1 and record the permissions.",
            "schema_version",
        )
        return

    permissions = manifest["dataset"]["permissions"]
    status = permissions["status"]
    problems = 0
    for use in sorted(requested):
        if status != "approved":
            problems += 1
            report.error(
                "use-not-permitted",
                f"cannot be used for {use}: the permission status is {status!r}, not 'approved'.",
                "dataset.permissions.status",
            )
        elif use not in permissions["permitted_uses"]:
            problems += 1
            report.error(
                "use-not-permitted",
                f"cannot be used for {use}: permitted_uses is {sorted(permissions['permitted_uses'])}.",
                "dataset.permissions.permitted_uses",
            )
    if require_commercial_training and permissions["commercial_training"] != "permitted":
        problems += 1
        report.error(
            "commercial-training-not-permitted",
            f"commercial training was requested but commercial_training is "
            f"{permissions['commercial_training']!r}; it must be 'permitted'.",
            "dataset.permissions.commercial_training",
        )
    if problems:
        return

    # The manifest records the permission. Accepting it also needs verified files and a
    # verified licence text, because a free-text reference or basis is only a claim.
    if not dataset_dir_given:
        report.error(
            "clearance-needs-dataset-dir",
            "a use is accepted only after the files and the licence snapshot have been verified "
            "against their recorded checksums, which needs the dataset directory. None was "
            "supplied, so nothing is cleared.",
            "dataset directory",
        )
    if "snapshot" not in manifest["dataset"]["licence"].get("evidence", {}):
        report.error(
            "clearance-needs-licence-snapshot",
            "a use is accepted only when the licence text or permission document is preserved as "
            "a checksummed snapshot file (licence.evidence.snapshot). A reference or archive link "
            "cannot be verified here, and a free-text basis does not by itself establish "
            "permission.",
            "dataset.licence.evidence.snapshot",
        )


# --- files -----------------------------------------------------------------------


def resolve_within(root: Path, relative_path: str) -> Path | None:
    """Resolve a manifest path under `root`; None if it escapes (for example via a symlink)."""
    candidate = root.joinpath(*PurePosixPath(relative_path).parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def sha256_of_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


@dataclass(frozen=True)
class _DeclaredFile:
    relative_path: str
    location: str
    sha256: str | None
    size_bytes: int | None


def _declared_files(manifest: dict[str, Any], view: ManifestView) -> list[_DeclaredFile]:
    """Every file the manifest says should exist: each record's image and the licence snapshot."""
    declared: list[_DeclaredFile] = []
    seen: set[str] = set()
    for record in view.records:
        if record.relative_path in seen:
            continue  # The duplicate path is already reported.
        seen.add(record.relative_path)
        sha256: str | None = None
        size_bytes: int | None = None
        if view.version == "1.1":
            file = manifest["records"][record.index]["file"]
            sha256, size_bytes = file["sha256"], file.get("size_bytes")
        declared.append(
            _DeclaredFile(record.relative_path, _location(view, record.index), sha256, size_bytes)
        )
    if view.version == "1.1":
        snapshot = manifest["dataset"]["licence"].get("evidence", {}).get("snapshot")
        if snapshot is not None and snapshot["relative_path"] not in seen:
            declared.append(
                _DeclaredFile(
                    snapshot["relative_path"],
                    "dataset.licence.evidence.snapshot",
                    snapshot["sha256"],
                    None,
                )
            )
    return declared


def _names(paths: Sequence[str]) -> str:
    """Disk-derived names are untrusted: ascii() escapes control and non-ASCII characters."""
    shown = ", ".join(ascii(path) for path in paths[:LISTED_NAMES])
    extra = len(paths) - LISTED_NAMES
    return f"{shown} and {extra} more" if extra > 0 else shown


def _check_declared_file(
    root: Path,
    scan: DirectoryScan,
    declared: _DeclaredFile,
    report: Report,
    case_counterparts: set[str],
) -> None:
    path = declared.relative_path
    if resolve_within(root, path) is None:
        report.error(
            "file-outside-dataset-dir",
            f"{ascii(path)} resolves outside the dataset directory.",
            declared.location,
        )
        return

    target = scan.files.get(path)
    if target is None:
        # Judge case from the directory listing, not from the operating system, so
        # the answer is the same on Windows, macOS and Linux.
        variants = scan.case_variants(path)
        if variants:
            case_counterparts.update(variants)
            more = f" (and {len(variants) - 1} similar)" if len(variants) > 1 else ""
            report.error(
                "path-case-mismatch",
                f"{ascii(path)} is recorded in the manifest, but the file on disk is named "
                f"{ascii(variants[0])}{more}. The names differ only in letter case or Unicode "
                "form: one file on some systems, a missing file on others. Change the manifest "
                "to the exact name on disk.",
                declared.location,
            )
            return
        link = scan.link_on_path(path)
        if link is not None:
            report.error(
                "file-through-link",
                f"{ascii(path)} is reached through the link {ascii(link)}, which is not followed.",
                declared.location,
            )
            return
        report.error(
            "file-missing",
            f"{ascii(path)} is not a file in the dataset directory.",
            declared.location,
        )
        return

    if declared.sha256 is None:
        return  # Schema 1.0 records no checksums.
    try:
        actual_sha256, actual_size = sha256_of_file(target)
    except OSError as error:
        report.error("file-unreadable", f"{ascii(path)}: {error}", declared.location)
        return
    if actual_sha256 != declared.sha256:
        report.error(
            "checksum-mismatch",
            f"{ascii(path)}: declared SHA-256 {declared.sha256}, actual {actual_sha256}. "
            "The file changed or the manifest is wrong.",
            declared.location,
        )
    if declared.size_bytes is not None and declared.size_bytes != actual_size:
        report.error(
            "size-mismatch",
            f"{ascii(path)}: declared {declared.size_bytes} bytes, actual {actual_size}.",
            declared.location,
        )


def _verify_files(
    manifest: dict[str, Any],
    view: ManifestView,
    dataset_dir: Path,
    report: Report,
    result: ValidationResult,
    manifest_path: Path | None,
    ignore_patterns: Sequence[str],
) -> bool:
    # Refuse a restricted location before anything is resolved, listed, opened or hashed.
    restricted = restriction_of(dataset_dir)
    if restricted is not None:
        report.error(
            "restricted-directory",
            refusal_message(dataset_dir, "The dataset directory", restricted),
            "dataset directory",
        )
        return False
    try:
        root = dataset_dir.resolve(strict=True)
    except (OSError, RuntimeError):
        report.error("dataset-dir-invalid", f"dataset directory '{dataset_dir}' does not exist.")
        return False
    if not root.is_dir():
        report.error("dataset-dir-invalid", f"'{dataset_dir}' is not a directory.")
        return False

    scan = scan_directory(root)
    for directory in scan.unreadable:
        report.error(
            "directory-unreadable",
            f"the directory {ascii(directory)} could not be listed, so the dataset directory "
            "cannot be fully checked.",
            "dataset directory",
        )
    if scan.restricted:
        report.error(
            "restricted-directory",
            f"the dataset directory contains restricted location(s) that were not entered: "
            f"{_names(scan.restricted)}. UMaT/MN273 material and uploads must not be placed in or "
            "under a dataset directory.",
            "dataset directory",
        )

    declared = _declared_files(manifest, view)
    case_counterparts: set[str] = set()
    for item in declared:
        _check_declared_file(root, scan, item, report, case_counterparts)

    found = find_unmanifested(
        scan,
        {item.relative_path for item in declared},
        case_counterparts=case_counterparts,
        manifest_path=manifest_path,
        ignore_patterns=ignore_patterns,
    )
    result.unmanifested_images = found.images
    result.unmanifested_other = found.others
    result.links_not_followed = list(scan.links)
    result.ignored_by_pattern = found.ignored_by_pattern
    result.ignored_housekeeping = found.housekeeping
    if found.images:
        report.error(
            "unmanifested-image-file",
            f"{len(found.images)} image or video file(s) in the dataset directory are not listed "
            f"in the manifest: {_names(found.images)}. They were left exactly as they are. Add "
            "them to the manifest with their provenance and licence, or remove them from the "
            "dataset directory yourself; this tool does neither. A file that is not in the "
            "manifest has unknown rights and must not be used. Ignore patterns never apply to "
            "images or videos.",
            "dataset directory",
        )
    if found.others:
        report.warn(
            "unmanifested-file",
            f"{len(found.others)} other file(s) in the dataset directory are not listed in the "
            f"manifest: {_names(found.others)}. They were left untouched. If they are documented "
            "metadata, leave them out of this check with an ignore pattern (--ignore-file).",
            "dataset directory",
        )
    if found.ignored_by_pattern:
        report.warn(
            "files-ignored-by-pattern",
            f"{len(found.ignored_by_pattern)} file(s) were left out of the unlisted-file check by "
            f"an ignore pattern: {_names(found.ignored_by_pattern)}. A pattern can never hide an "
            "image or a video.",
            "dataset directory",
        )
    if scan.links:
        report.error(
            "link-in-dataset-dir",
            f"{len(scan.links)} link(s) (symbolic links or junctions) in the dataset directory: "
            f"{_names(scan.links)}. Links are refused, whatever they are named: they can conceal "
            "image files or whole folders, so they are not followed and the directory cannot be "
            "checked while they are present. Replace each link with the real, listed files or "
            "remove it yourself; this tool does neither.",
            "dataset directory",
        )

    if view.version == "1.0":
        report.warn(
            "no-checksums",
            "schema 1.0 records no checksums, so file contents were not verified; "
            "only existence was checked.",
        )
    return True
