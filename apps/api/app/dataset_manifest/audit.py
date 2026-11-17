"""Offline leakage audit over one or more manifests.

The audit does not report PASS merely because a manifest can be read. For every
manifest it runs the full semantic validation (permissions, the UMaT/MN273
prohibition, evaluation isolation, split consistency, sample identity, licence
evidence and, when a dataset directory is supplied, directory integrity), so an
invalid manifest produces audit errors. Across the manifests it then checks for
identical image content in several splits or datasets, derivations from one
manifest's records into another's, and capture sessions that straddle splits.

It trusts the checksums written in the manifests unless a dataset directory is
supplied for that manifest. The exception is a source: when a manifest that is in use
derives records from another audited manifest, that source's files and licence snapshot
must be verified in its own dataset directory (`--dataset-dir SOURCE_ID=DIR`), or the
derivation is an error. Each directory is hashed once per audit, however many manifests
need it. The audit does not detect near-duplicate images (perceptual hash or
embeddings) or wrong physical sample IDs, and passing it is not proof that no leakage
exists.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from app.dataset_manifest.issues import Issue, Report, summarise_ids
from app.dataset_manifest.schema import (
    SUPPORTED_VERSIONS,
    SchemaLoadError,
    check_structure,
    detect_version,
    load_schema,
)
from app.dataset_manifest.splits import SMALL_SPLIT_SAMPLES, SPLIT_FORMAT_VERSION, small_split_message
from app.dataset_manifest.validation import (
    ASSUMED_IDENTITY,
    ValidationRun,
    directory_binding_problems,
    validate_document,
)
from app.dataset_manifest.view import ASSIGNED_SPLITS, UNASSIGNED, ManifestView, RecordView, build_view

NOT_CHECKED = (
    "near-duplicate images (perceptual hash, embeddings)",
    "physical sample IDs that were assigned wrongly",
    "the contents of the image files",
)
# The audit answers one question: do the splits leak. It says nothing else about them.
SCOPE_NOTICE = (
    "This audit checks for leakage only. It does not assess split quality, label quality or how "
    "reliable any measured performance will be: a PASS is not approval of the splits."
)
# Splits whose size decides how far a measurement on them can be trusted.
EVALUATED_SPLITS = ("validation", "test", "independent_evaluation")
# Warnings the audit words itself, from its own point of view; the validator's copies are skipped.
AUDIT_OWNS = frozenset({"unverified-sample-identity", "no-checksums", "single-image-samples"})


@dataclass
class ManifestSummary:
    label: str
    version: str
    dataset_id: str
    category: str | None
    records: int
    samples: int
    sessions: int
    records_without_session: int
    split_counts: dict[str, int]
    # sample_identity.basis from the manifest; None for schema 1.0, which does not record it.
    sample_identity_basis: str | None = None
    single_image_samples: bool = False
    files_verified: bool = False
    samples_per_split: dict[str, int] = field(default_factory=dict)


@dataclass
class AuditResult:
    report: Report
    summaries: list[ManifestSummary] = field(default_factory=list)
    # Split-quality limitations per manifest: a validation, test or evaluation split with few
    # physical samples, and every warning in a supplied split file. They do not make the audit
    # fail (they are not leakage), but a PASS with any of them is qualified.
    split_quality: dict[str, list[str]] = field(default_factory=dict)
    # Manifests whose physical sample IDs are not known to be verified (assumed identity,
    # one image per sample, or schema 1.0), so sample-level leakage cannot be ruled out for them.
    unverified_identity: list[str] = field(default_factory=list)
    # Manifests audited without their dataset directory, so their files and licence
    # snapshot were not checked against the recorded checksums.
    files_not_verified: list[str] = field(default_factory=list)

    def passed(self, strict: bool = False) -> bool:
        """No errors; with `strict`, no warnings either."""
        return not self.report.errors and not (strict and self.report.warnings)

    @property
    def limitations(self) -> bool:
        return bool(self.unverified_identity or self.files_not_verified or self.split_quality)


def audit_manifests(
    inputs: Sequence[tuple[str, Any]],
    *,
    dataset_dirs: Mapping[str, Path] | None = None,
    manifest_paths: Mapping[str, Path] | None = None,
    ignore_patterns: Sequence[str] = (),
    split_files: Mapping[str, Any] | None = None,
) -> AuditResult:
    """Audit parsed manifests. Each input is (label, document); the label names it in findings.

    `dataset_dirs`, `manifest_paths` and `split_files` (parsed split files, as written by
    generate_dataset_splits.py) are keyed by label. A supplied split file is checked against
    the manifest and its warnings are surfaced.
    """
    dataset_dirs = dataset_dirs or {}
    manifest_paths = manifest_paths or {}
    split_files = split_files or {}
    report = Report()
    audited: list[tuple[str, dict[str, Any], ManifestView]] = []

    for label, document in inputs:
        version = detect_version(document)
        if version not in SUPPORTED_VERSIONS:
            report.error(
                "manifest-not-auditable",
                f"schema_version must be one of {list(SUPPORTED_VERSIONS)}; found {version!r}.",
                label,
            )
            continue
        try:
            schema = load_schema(version)
        except SchemaLoadError as error:
            report.error("schema-unavailable", str(error), label)
            continue
        structure = Report()
        try:
            check_structure(document, schema, structure)
        except RecursionError:
            report.error("manifest-not-auditable", "the manifest is nested too deeply.", label)
            continue
        if not structure.ok:
            report.error(
                "manifest-not-auditable",
                f"{len(structure.errors)} structural error(s), so it cannot be audited; run "
                f"validate_dataset_manifest.py for details. First: {structure.errors[0].message}",
                label,
            )
            continue
        audited.append((label, document, build_view(document, version)))

    result = AuditResult(report)
    # Every audited manifest is also a possible source for the others, bound to its own
    # dataset directory (if one was supplied) so that its files can be verified as a source.
    sources: dict[str, ManifestView] = {}
    for label, _document, view in audited:
        if view.dataset_id in sources:
            report.error(
                "duplicate-dataset-id",
                f"dataset_id {view.dataset_id!r} is used by more than one manifest; audit each "
                "dataset once.",
                label,
            )
        sources.setdefault(
            view.dataset_id,
            replace(view, dataset_dir=dataset_dirs.get(label), manifest_path=manifest_paths.get(label)),
        )
    bindings = [(f"manifest {label!r}", dataset_dirs[label]) for label, _d, _v in audited if label in dataset_dirs]
    for problem in directory_binding_problems(bindings):
        report.error("ambiguous-dataset-directory", problem, "dataset directories")

    # One run for the whole audit: a directory needed by several manifests is hashed once.
    run = ValidationRun(source_ignore_patterns=tuple(ignore_patterns))
    for label, document, view in audited:
        basis = document["dataset"]["sample_identity"]["basis"] if view.version == "1.1" else None
        outcome = validate_document(
            document,
            dataset_dir=dataset_dirs.get(label),
            manifest_path=manifest_paths.get(label),
            ignore_patterns=ignore_patterns,
            provenance_sources=sources,
            _run=run,
        )
        _merge(label, outcome.report, report)
        result.summaries.append(_summary(label, view, basis, outcome.single_image_samples, outcome.files_verified))
        if basis is None or basis == ASSUMED_IDENTITY or outcome.single_image_samples:
            result.unverified_identity.append(label)
        if not outcome.files_verified:
            result.files_not_verified.append(label)
        _check_capture_sessions(label, view, report)
        _check_coverage(label, view, basis, outcome.single_image_samples, report)
        quality = _split_quality(view)
        if label in split_files:
            quality += _check_split_file(label, view, split_files[label], report)
        quality = list(dict.fromkeys(quality))  # the same warning can come from both places
        if quality:
            result.split_quality[label] = quality
    _check_duplicate_content(audited, report)
    return result


def _merge(label: str, validation: Report, report: Report) -> None:
    """Add the validator's findings, naming the manifest, except the ones the audit words itself."""
    for issue in validation.issues:
        if issue.code in AUDIT_OWNS:
            continue
        message = f"{issue.location}: {issue.message}" if issue.location else issue.message
        report.issues.append(Issue(issue.severity, issue.code, message, label))


def _summary(
    label: str, view: ManifestView, basis: str | None, single_image: bool, files_verified: bool
) -> ManifestSummary:
    split_counts = {split: 0 for split in (*ASSIGNED_SPLITS, UNASSIGNED)}
    for record in view.records:
        split_counts[record.split] += 1
    sessions = {r.capture_session_id for r in view.records if r.capture_session_id is not None}
    samples_per_split = {
        split: len({r.physical_sample_id for r in view.records if r.split == split})
        for split in (*ASSIGNED_SPLITS, UNASSIGNED)
    }
    return ManifestSummary(
        label=label,
        version=view.version,
        dataset_id=view.dataset_id,
        category=view.category,
        records=len(view.records),
        samples=len(view.samples()),
        sessions=len(sessions),
        records_without_session=sum(1 for r in view.records if r.capture_session_id is None),
        split_counts=split_counts,
        sample_identity_basis=basis,
        single_image_samples=single_image,
        files_verified=files_verified,
        samples_per_split=samples_per_split,
    )


def _split_quality(view: ManifestView) -> list[str]:
    """Limitations on how far a measurement on the assigned splits can be trusted."""
    messages: list[str] = []
    for split in EVALUATED_SPLITS:
        records = [record for record in view.records if record.split == split]
        samples = {record.physical_sample_id for record in records}
        if records and len(samples) < SMALL_SPLIT_SAMPLES:
            messages.append(small_split_message(split, len(samples), len(records)))
    return messages


def _check_split_file(label: str, view: ManifestView, document: Any, report: Report) -> list[str]:
    """Check a split file against the manifest and return its warnings.

    The file must describe this manifest: same dataset, same records, and the same split
    and physical sample for every record. Its warnings are surfaced as audit warnings.
    """
    invalid = "split file is not a valid split file ({}). It must be a file written by generate_dataset_splits.py."
    if not isinstance(document, dict) or document.get("split_format_version") != SPLIT_FORMAT_VERSION:
        report.error("split-file-invalid", invalid.format("wrong or missing split_format_version"), label)
        return []
    assignments, warnings = document.get("assignments"), document.get("warnings")
    if not isinstance(assignments, list) or not isinstance(warnings, list):
        report.error("split-file-invalid", invalid.format("assignments and warnings must be lists"), label)
        return []
    if not all(isinstance(text, str) for text in warnings):
        report.error("split-file-invalid", invalid.format("every warning must be text"), label)
        return []
    entries: dict[str, dict[str, Any]] = {}
    for entry in assignments:
        keys = ("record_id", "physical_sample_id", "split")
        if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) for key in keys):
            report.error("split-file-invalid", invalid.format("a malformed assignment"), label)
            return []
        if entry["record_id"] in entries:
            report.error("split-file-invalid", invalid.format(f"record {entry['record_id']!r} appears twice"), label)
            return []
        entries[entry["record_id"]] = entry

    if document.get("dataset_id") != view.dataset_id:
        report.error(
            "split-file-dataset-mismatch",
            f"the split file is for dataset {document.get('dataset_id')!r}, not {view.dataset_id!r}.",
            label,
        )
    records = view.records_by_id()
    missing, extra = sorted(set(records) - set(entries)), sorted(set(entries) - set(records))
    if missing or extra:
        report.error(
            "split-file-records-mismatch",
            f"the split file and the manifest do not list the same records: "
            f"{len(missing)} in the manifest but not in the split file ({summarise_ids(missing)}), "
            f"{len(extra)} in the split file but not in the manifest ({summarise_ids(extra)}).",
            label,
        )
    shared = sorted(set(records) & set(entries))
    wrong_split = [rid for rid in shared if records[rid].split != entries[rid]["split"]]
    if wrong_split:
        report.error(
            "split-file-assignment-mismatch",
            f"{len(wrong_split)} record(s) have a different split in the manifest than in the "
            f"split file ({summarise_ids(wrong_split)}). Audit the manifest written by "
            "generate_dataset_splits.py --output-manifest, unchanged, with its split file.",
            label,
        )
    wrong_sample = [rid for rid in shared if records[rid].physical_sample_id != entries[rid]["physical_sample_id"]]
    if wrong_sample:
        report.error(
            "split-file-sample-mismatch",
            f"{len(wrong_sample)} record(s) have a different physical sample ID in the manifest "
            f"than in the split file ({summarise_ids(wrong_sample)}).",
            label,
        )
    for text in warnings:
        report.warn("split-file-warning", f"the split file warns: {text}", label)
    return list(warnings)


def _assigned_splits(records: Sequence[RecordView]) -> list[str]:
    return sorted({record.split for record in records if record.split != UNASSIGNED})


def _check_capture_sessions(label: str, view: ManifestView, report: Report) -> None:
    by_session: dict[str, list[RecordView]] = {}
    for record in view.records:
        if record.capture_session_id is not None:
            by_session.setdefault(record.capture_session_id, []).append(record)
    for session, records in by_session.items():
        splits = _assigned_splits(records)
        if len(splits) > 1:
            samples = sorted({record.physical_sample_id for record in records})
            report.warn(
                "capture-session-in-multiple-splits",
                f"capture session {session!r} appears in splits {splits} (physical samples: "
                f"{summarise_ids(samples)}). Shared lighting, background or camera can leak "
                "between splits; regenerate the splits with --group-capture-sessions.",
                label,
            )


def _check_coverage(
    label: str, view: ManifestView, basis: str | None, single_image: bool, report: Report
) -> None:
    unassigned = [record.record_id for record in view.records if record.split == UNASSIGNED]
    if unassigned:
        report.warn(
            "unassigned-records",
            f"{len(unassigned)} of {len(view.records)} record(s) have no split, so leakage "
            "cannot be assessed for them.",
            label,
        )
    missing_session = sum(1 for record in view.records if record.capture_session_id is None)
    if missing_session:
        report.warn(
            "capture-session-missing",
            f"{missing_session} of {len(view.records)} record(s) have no capture session ID, "
            "so capture-session leakage cannot be assessed for them.",
            label,
        )
    if view.version == "1.0":
        report.warn(
            "no-checksums",
            "schema 1.0 records no checksums, so exact duplicate images cannot be detected.",
            label,
        )
        report.warn(
            "unverified-sample-identity",
            "schema 1.0 does not record how the sample IDs were established, so they are not "
            "known to be verified physical samples. Sample-level leakage cannot be ruled out.",
            label,
        )
    elif basis == ASSUMED_IDENTITY:
        report.warn(
            "unverified-sample-identity",
            "sample identity is only ASSUMED: each image is treated as its own physical sample "
            "without verification. Images of one physical sample can be in different splits "
            "and this audit cannot detect it, so a pass does not show that the splits are free "
            "of sample-level leakage.",
            label,
        )
    elif single_image:
        report.warn(
            "single-image-samples",
            f"every physical sample has exactly one image although the identity basis is "
            f"{basis!r}, so the identity is treated as unverified: grouping by physical sample "
            "protects nothing, images of one real sample may be in different splits, and this "
            "audit cannot detect it.",
            label,
        )


def _check_duplicate_content(
    audited: Sequence[tuple[str, dict[str, Any], ManifestView]], report: Report
) -> None:
    by_hash: dict[str, list[tuple[str, RecordView]]] = {}
    for _label, _document, view in audited:
        for record in view.records:
            if record.sha256 is not None:
                by_hash.setdefault(record.sha256, []).append((view.dataset_id, record))

    for sha256, members in by_hash.items():
        if len(members) < 2:
            continue
        splits = {record.split for _dataset, record in members}
        datasets = {dataset for dataset, _record in members}
        listing = ", ".join(
            f"{dataset}:{record.record_id} ({record.split})" for dataset, record in members[:6]
        )
        if len(members) > 6:
            listing += f" and {len(members) - 6} more"
        crosses_splits = len(splits - {UNASSIGNED}) >= 2
        touches_evaluation = "independent_evaluation" in splits and len(splits) >= 2
        if crosses_splits or touches_evaluation:
            report.error(
                "duplicate-content-across-splits",
                f"identical image content (SHA-256 {sha256}) is in more than one split "
                f"{sorted(splits)}: {listing}. The same image must not appear in two splits.",
            )
        else:
            scope = f"{len(datasets)} dataset(s)" if len(datasets) > 1 else "one dataset"
            report.warn(
                "duplicate-content",
                f"identical image content (SHA-256 {sha256}) is recorded {len(members)} times "
                f"within {scope} and a single split: {listing}.",
            )
