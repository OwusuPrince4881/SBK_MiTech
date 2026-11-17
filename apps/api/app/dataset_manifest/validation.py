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

import codecs
import hashlib
import os
import re
import unicodedata
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from app.dataset_manifest.files import (
    CONTAINER_EXTENSIONS,
    HEADER_BYTES,
    MEDIA_EXTENSIONS,
    DirectoryScan,
    find_unmanifested,
    looks_like_container,
    looks_like_media,
    scan_directory,
)
from app.dataset_manifest.issues import ERROR, Issue, Report, summarise_ids
from app.dataset_manifest.restricted import (
    RestrictedPathError,
    lexical_restriction,
    refusal_message,
    restriction_of,
)
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
FIELD_CATEGORY = "ghana_field"
EVALUATION = "independent_evaluation"
LISTED_NAMES = 20  # How many unmanifested file names a single message shows.
# What a Ghana field dataset's governance record must say before the dataset can be approved.
FIELD_PERMISSION_RESOLVED = "documented"
FIELD_CONSENT_RESOLVED = ("obtained", "not_applicable")
FIELD_LOCATION_RESOLVED = ("removed", "generalised", "exact_restricted")
# A recorded basis, reference, name or snapshot must say something. These are the words of a
# placeholder: text made only of them records nothing.
PLACEHOLDER_WORDS = frozenset(
    {
        "n", "a", "na", "nan", "null", "none", "nil", "nothing", "empty", "blank", "tbd", "tba", "tbc",
        "todo", "yes", "no", "ok", "okay", "unknown", "unspecified", "unnamed", "anonymous", "pending",
        "required", "applicable", "not", "see", "above", "licence", "license", "same", "as", "permitted",
        "allowed", "fine", "x", "xx", "xxx", "test", "text", "basis", "clause", "the", "of", "name",
        "reviewer", "placeholder", "dummy", "example", "foo", "bar", "asdf", "qwerty", "abc", "abcd",
        "lorem", "ipsum", "dolor", "sit", "amet", "me", "self", "user", "admin", "someone", "anyone",
        "nobody",
    }
)
MIN_BASIS_CHARACTERS = 8  # letters and digits, so "Clause 4.2" qualifies and "x" or "n/a" does not
MIN_BASIS_DISTINCT = 4
MIN_NAME_CHARACTERS = 3  # a recorded name or reference needs at least this many letters and digits
BLANK_BYTES = b" \t\r\n\x0b\x0c\x00"
# A licence snapshot is judged from its first megabyte: it must be real text, not media or an
# archive or other binary content. The whole file is still hashed.
SNAPSHOT_INSPECT_BYTES = 1024 * 1024
TEXT_CONTROLS = "\t\n\r\f\v"  # the only control characters ordinary text may contain
INVISIBLE_CATEGORIES = ("Cc", "Cf", "Cs", "Co", "Cn")


def today_utc() -> date:
    """Today's date in UTC. Evidence and review dates must not be later than this."""
    return datetime.now(timezone.utc).date()


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
    unmanifested_containers: list[str] = field(default_factory=list)
    unmanifested_other: list[str] = field(default_factory=list)
    links_not_followed: list[str] = field(default_factory=list)
    ignored_by_pattern: list[str] = field(default_factory=list)
    ignored_housekeeping: list[str] = field(default_factory=list)
    # Every source dataset assessed while the derivations were checked (directly or through a
    # chain), by dataset_id. Filled in by the top-level call only.
    source_assessments: dict[str, "SourceAssessment"] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.report.ok


@dataclass(frozen=True)
class SourceAssessment:
    """What validating a source manifest, and checking its files, says about using it as a source.

    The manifest is validated in full, with the same sources, so a chain of derivations is
    followed. When the caller bound the source to its dataset directory, its files are also
    checked there exactly as `--dataset-dir` checks a manifest's own files: every listed record
    file and the licence snapshot must exist with the recorded spelling and match its SHA-256,
    the snapshot must be real text, and nothing unlisted, linked or restricted may be in the
    directory. That verifies the integrity of the recorded evidence. It does not establish
    that the licence permits a use, that the data is owned as recorded, or that recorded
    consent is genuine: what the manifest records is still its own declaration.
    """

    assessed: bool  # False for a view that carries no manifest: nothing can be concluded
    valid: bool = False
    errors: tuple[str, ...] = ()  # "[code] message" for the first few validation errors
    error_count: int = 0
    category: str | None = None
    permission_status: str | None = None  # None for schema 1.0, which records no permissions
    permitted_uses: tuple[str, ...] = ()
    has_licence_snapshot: bool = False
    field_gaps: tuple[str, ...] = ()  # unresolved Ghana field governance, if the category is ghana_field
    cycle: bool = False  # the source derives, directly or not, from the dataset being checked
    # What the source records about commercial training ('permitted', 'not_permitted' or 'unknown'),
    # and, when it records 'permitted', why that is not supported by its recorded basis (None if it is).
    commercial_training: str | None = None
    commercial_basis_problem: str | None = None
    # The dataset directory the caller bound to this source, or None if none was supplied.
    dataset_dir: str | None = None
    files_checked: bool = False  # the files were checked in that directory
    files_verified: bool = False  # they were checked and nothing was wrong
    file_errors: tuple[str, ...] = ()  # "[code] message" for the first few file errors
    file_error_count: int = 0
    files_declared: int = 0  # record files plus the licence snapshot that the manifest lists


@dataclass(frozen=True)
class _FileCheck:
    """The outcome of checking one manifest's files in one directory (see `_check_files`)."""

    document: Any = field(repr=False, compare=False)  # the manifest that was checked, by identity
    verified: bool
    issues: tuple[Issue, ...]
    declared: int
    unmanifested_images: tuple[str, ...]
    unmanifested_containers: tuple[str, ...]
    unmanifested_other: tuple[str, ...]
    links_not_followed: tuple[str, ...]
    ignored_by_pattern: tuple[str, ...]
    ignored_housekeeping: tuple[str, ...]


@dataclass
class ValidationRun:
    """What the checks of one validation operation may share, and nothing that outlives it.

    A run is created for each top-level `validate_document` call, and the audit creates one
    for all the manifests it audits. It is dropped when that call returns: nothing is stored
    on a view or anywhere else. Inside a run, a dataset directory's files are hashed once
    however many derivations or audited manifests need them, and each source is validated
    once per context. A later run re-reads and re-hashes everything, so a result is never
    reused after a file has changed between two runs. Within a run, files are assumed not to
    change while it is in progress; that time-of-check limit is documented.
    """

    # Ignore patterns for the unlisted-file check of a source directory. They never hide an
    # image, a video, an archive or a link, so they cannot turn a failing check into a pass.
    source_ignore_patterns: tuple[str, ...] = ()
    files: dict[tuple[str, str, tuple[str, ...]], _FileCheck] = field(default_factory=dict)
    assessments: dict[tuple[str, frozenset[str], frozenset[str], bool], SourceAssessment] = field(default_factory=dict)
    assessed_sources: dict[str, SourceAssessment] = field(default_factory=dict)


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
    _assessing: frozenset[str] = frozenset(),
    _inherited_uses: frozenset[str] = frozenset(),
    _inherited_commercial: bool = False,
    _run: ValidationRun | None = None,
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
    cannot be established and is an error. Each source that a record derives from is
    also validated in full (see `assess_source`): a derivation from a source that is
    invalid, not approved, denied, unresolved Ghana field data, without a licence
    snapshot, or not permitted for the use in question is refused. So is one whose
    files and licence snapshot could not be verified: a source view carries the
    dataset directory that the caller bound to it (`ManifestView.dataset_dir`), and its
    files are checked there as `dataset_dir` checks this manifest's own. Directories
    that are the same, or nested, are refused as ambiguous.

    The private arguments are for the recursion only. `_assessing` holds the datasets
    whose validation is already in progress, so that a chain of derivations that loops
    back is caught instead of followed. `_inherited_uses` are the uses that a dataset
    derived from this one is put to: this manifest's own sources must permit them too,
    so a use cannot be laundered through an intermediate dataset. `_inherited_commercial`
    is the same for commercial training: a dataset derived from this one is to be used for it,
    so every source down the chain must permit it. `_run` is shared by the checks of one
    operation (see `ValidationRun`).

    Commercial training is inherited in the same way whenever it matters: when it is
    requested (`require_commercial_training`), when it is inherited, and when this manifest
    itself records commercial training as permitted, because a derived dataset cannot grant a
    broader commercial permission than the data it was made from.
    """
    requested = set(uses)
    unknown = (requested | set(_inherited_uses)) - set(USES)
    if unknown:
        raise ValueError(f"Unknown use(s) {sorted(unknown)}; expected a subset of {list(USES)}.")

    top_level = _run is None
    run = _run if _run is not None else ValidationRun()
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
    except RestrictedPathError as error:
        report.error("restricted-schema", str(error), "custom schema")
        return result
    except SchemaLoadError as error:
        report.error("schema-unavailable", str(error))
        return result
    try:
        for schema in schemas:
            check_structure(manifest, schema, report)
    except RecursionError:
        report.error("manifest-too-deep", "the manifest is nested too deeply to be checked.")
        return result
    except SchemaLoadError as error:  # a reference that cannot be resolved without going offline
        report.error("schema-unavailable", str(error))
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
    if top_level:
        _check_dataset_directories(dataset_dir, view.dataset_id, provenance_sources, report)
    _check_evaluation_isolation(view, report)
    _check_ids_and_paths(view, report)
    _check_sample_splits(view, report)
    _check_provenance(
        view,
        report,
        provenance_sources,
        requested | set(_inherited_uses),
        _assessing | {view.dataset_id},
        run,
        _commercial_reason(manifest, version, require_commercial_training, _inherited_commercial),
    )
    if version == "1.1":
        result.sample_identity_basis = manifest["dataset"]["sample_identity"]["basis"]
        _check_checksums_and_filenames(manifest, view, report)
        _check_dates(manifest, report)
        _check_public_requirements(manifest, report)
        _check_permissions(manifest, view, report)
        _check_licence_evidence(manifest, view, report)
        _check_field_governance(manifest, view, report, bool(requested) or require_commercial_training)
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
        check = _check_files(run, manifest, view, dataset_dir, manifest_path, ignore_patterns)
        report.issues.extend(check.issues)
        result.files_verified = check.verified
        result.unmanifested_images = list(check.unmanifested_images)
        result.unmanifested_containers = list(check.unmanifested_containers)
        result.unmanifested_other = list(check.unmanifested_other)
        result.links_not_followed = list(check.links_not_followed)
        result.ignored_by_pattern = list(check.ignored_by_pattern)
        result.ignored_housekeeping = list(check.ignored_housekeeping)

    wants_clearance = bool(requested) or require_commercial_training
    if report.ok and version == "1.1" and wants_clearance and result.files_verified:
        result.cleared_uses = tuple(sorted(requested))
        result.commercial_training_cleared = require_commercial_training
    if top_level:
        result.source_assessments = dict(run.assessed_sources)
    return result


def source_view(
    document: Any, *, dataset_dir: Path | None = None, manifest_path: Path | None = None
) -> ManifestView:
    """A view of another manifest, for checking derivations. Raises ValueError if it is unreadable.

    Only the structure must be sound to load it: a source manifest that is itself
    restricted or otherwise invalid is still loaded, so that a derivation from it can be
    refused. The view keeps the parsed manifest, and whether a derivation from it is
    allowed is decided by validating that manifest in full (`assess_source`), not by this
    structure-only reading.

    `dataset_dir` binds the source to the directory its relative paths refer to. Only an
    explicit binding is accepted (it is never inferred from where the manifest is), and
    only a bound source can have its files and licence snapshot verified.
    `manifest_path` is where the manifest was read from.
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
    return replace(build_view(document, version), dataset_dir=dataset_dir, manifest_path=manifest_path)

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

    # The category is only a declaration, so also look at every string the manifest contains.
    hits = view.restricted_hits
    if hits:
        shown = "; ".join(f"{location}={ascii(text)} [{name}]" for location, text, name in hits[:5])
        more = f" and {len(hits) - 5} more" if len(hits) > 5 else ""
        report.error(
            "restricted-material-suspected",
            f"the manifest mentions UMaT or MN273 ({shown}{more}). UMaT/MN273 material must "
            "not be used for any AI purpose, and this tool does not rely on the declared "
            "category to protect it. If this is not restricted material, rename it so that it no "
            "longer carries those names. This is a best-effort check of names: it is not proof "
            "that a manifest or its files are free of restricted material.",
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


def assess_source(
    view: ManifestView,
    sources: Mapping[str, ManifestView] | None,
    assessing: frozenset[str] = frozenset(),
    *,
    uses: Collection[str] = (),
    commercial: bool = False,
    run: ValidationRun | None = None,
) -> SourceAssessment:
    """Validate a source manifest in full, check its files, and summarise what that means.

    This runs `validate_document` on the source's own manifest (with the same `sources`,
    so a source that itself derives from something unfit is unfit too, all the way down
    a chain), then reads its recorded permission status, permitted uses, licence snapshot
    and, for Ghana field data, the unresolved safeguards. It never relies on the
    structure-only view. `uses` are the uses the derived data is put to; the source's own
    sources must permit them as well. `assessing` holds the datasets whose validation is
    already in progress: meeting one of them again is a cycle.

    If the caller bound the source to a dataset directory, its files are checked there
    as `--dataset-dir` checks a manifest's own (see `SourceAssessment`). A source that
    is restricted UMaT/MN273 material is never checked: its directory is not touched.
    Results are kept in `run` for the rest of that one operation only.

    File verification shows that the recorded evidence is intact. It does not make a
    recorded status, licence or consent true: those remain the manifest's declarations.
    """
    if view.document is None:
        return SourceAssessment(assessed=False)
    if view.dataset_id in assessing:
        return SourceAssessment(assessed=True, cycle=True)
    run = run if run is not None else ValidationRun()
    inherited = frozenset(uses)
    key = (view.dataset_id, assessing, inherited, commercial)
    cached = run.assessments.get(key)
    if cached is not None:
        return cached

    outcome = validate_document(
        view.document,
        provenance_sources=sources,
        _assessing=assessing,
        _inherited_uses=inherited,
        _inherited_commercial=commercial,
        _run=run,
    )
    errors = outcome.report.errors
    category = status = commercial_recorded = basis_problem = None
    permitted: tuple[str, ...] = ()
    has_snapshot = False
    gaps: tuple[str, ...] = ()
    document = view.document
    structurally_sound = outcome.version == "1.1" and not any(issue.code == "schema" for issue in errors)
    if structurally_sound:
        dataset = document["dataset"]
        category = dataset["category"]
        status = dataset["permissions"]["status"]
        permitted = tuple(sorted(dataset["permissions"]["permitted_uses"]))
        has_snapshot = "snapshot" in dataset["licence"].get("evidence", {})
        commercial_recorded = dataset["permissions"]["commercial_training"]
        if commercial_recorded == "permitted":
            basis = dataset["permissions"].get("commercial_training_basis")
            if basis is None:
                basis_problem = "it records no commercial_training_basis"
            elif not _basis_is_meaningful(basis):
                basis_problem = (
                    "its commercial_training_basis is a placeholder or too thin to name a licence clause "
                    "or permission document"
                )
        if category == FIELD_CATEGORY:
            gaps = tuple(_field_governance_gaps(dataset.get("field_governance")))

    files: dict[str, Any] = {}
    if view.dataset_dir is not None:
        files["dataset_dir"] = str(view.dataset_dir)
        restricted = view.category == RESTRICTED_CATEGORY or bool(view.restricted_hits)
        if structurally_sound and not restricted:
            check = _check_files(
                run, document, view, view.dataset_dir, view.manifest_path, run.source_ignore_patterns
            )
            file_errors = [issue for issue in check.issues if issue.severity == ERROR]
            files.update(
                files_checked=True,
                files_verified=check.verified and not file_errors,
                file_errors=tuple(f"[{issue.code}] {issue.message}"[:240] for issue in file_errors[:3]),
                file_error_count=len(file_errors),
                files_declared=check.declared,
            )
    assessment = SourceAssessment(
        assessed=True,
        valid=outcome.ok,
        errors=tuple(f"[{issue.code}] {issue.message}"[:240] for issue in errors[:3]),
        error_count=len(errors),
        category=category,
        permission_status=status,
        permitted_uses=permitted,
        has_licence_snapshot=has_snapshot,
        field_gaps=gaps,
        commercial_training=commercial_recorded,
        commercial_basis_problem=basis_problem,
        **files,
    )
    run.assessments[key] = assessment
    run.assessed_sources.setdefault(view.dataset_id, assessment)
    return assessment


def _check_source_governance(
    view: ManifestView,
    source_dataset: str,
    pairs: Sequence[tuple[RecordView, RecordView]],
    sources: Mapping[str, ManifestView],
    requested: Collection[str],
    assessing: frozenset[str],
    report: Report,
    run: ValidationRun,
    commercial: str | None = None,
) -> None:
    """A source must itself be fit to be used, and its evidence intact, before anything derived from it can be.

    `commercial` says why the sources must also permit commercial training (it is requested,
    inherited from a dataset derived from this one, or recorded as permitted here), or is None.

    `pairs` are the (derived record, source record) derivations from one source dataset.
    The source is validated in full and its files are checked in the directory bound to it,
    once. Its problems are errors when the derived data is in use (a record has a split, a use
    was requested, or the manifest records itself as approved or lists permitted uses) and
    warnings while everything is still a draft. Evaluation-related rules are reported
    separately, by `_check_cross_dataset_derivation`, and are not repeated here.
    """
    source = sources[source_dataset]
    if source.category == RESTRICTED_CATEGORY or source.restricted_hits:
        return  # refused outright, record by record, as restricted material; its files are never read
    first = pairs[0][0]
    location = f"{_location(view, first.index)}.provenance.derived_from"
    count = len(pairs)
    who = f"{count} record(s) derive from dataset {source_dataset!r}"

    # The derived data is in use if it says so: a requested use, a record with a split, or a
    # manifest that records itself as approved or lists permitted uses. Until then it is a draft.
    in_use = bool(requested) or any(record.split != UNASSIGNED for record in view.records)
    if view.version == "1.1" and view.document is not None:
        permissions = view.document["dataset"]["permissions"]
        in_use = in_use or permissions["status"] == "approved" or bool(permissions["permitted_uses"])
    # Pairs that involve an evaluation record are refused by the evaluation rules, so they
    # are not judged again here.
    ordinary = [pair for pair in pairs if EVALUATION not in (pair[0].split, pair[1].split)]
    needed: set[str] = set(requested) if ordinary else set()
    for record, _source_record in ordinary:
        if record.split in SPLIT_TO_USE:
            needed.add(SPLIT_TO_USE[record.split])

    # Commercial training is training, and it is passed down the chain like any other use.
    needs_commercial = commercial is not None and bool(ordinary)
    if needs_commercial:
        needed.add("training")
        in_use = True
    assessment = assess_source(source, sources, assessing, uses=needed, commercial=needs_commercial, run=run)
    if not assessment.assessed:
        report.error(
            "derived-source-unassessed",
            f"{who}, but the source is a view without its manifest, so it cannot be validated and "
            "nothing can be concluded about its permission or governance. Supply the source "
            "manifest itself.",
            location,
        )
        return
    if assessment.cycle:
        report.error(
            "derivation-cycle",
            f"{who}, which derives, directly or through other datasets, from this one. Derivations "
            "may not form a loop.",
            location,
        )
        return

    problems: list[tuple[str, str]] = []
    if not assessment.valid:
        shown = "; ".join(assessment.errors)
        problems.append(
            (
                "derived-from-invalid-source",
                f"the manifest of dataset {source_dataset!r} does not validate "
                f"({assessment.error_count} error(s)); first: {shown}",
            )
        )
    if assessment.permission_status is None:
        problems.append(
            (
                "derived-from-source-without-permission-data",
                f"dataset {source_dataset!r} is a schema 1.0 manifest, which records no permissions, "
                "so it cannot be cleared for any use and nothing may be derived from it for one",
            )
        )
    elif assessment.permission_status == "denied":
        problems.append(
            (
                "derived-from-denied-source",
                f"dataset {source_dataset!r} records its permission status as 'denied'",
            )
        )
    elif assessment.permission_status != "approved":
        problems.append(
            (
                "derived-from-unapproved-source",
                f"dataset {source_dataset!r} records its permission status as "
                f"{assessment.permission_status!r}, not 'approved'",
            )
        )
    if assessment.category == FIELD_CATEGORY and assessment.field_gaps:
        problems.append(
            (
                "derived-from-unresolved-field-source",
                f"dataset {source_dataset!r} is Ghana field data whose safeguards are unresolved: "
                + "; ".join(assessment.field_gaps),
            )
        )
    if assessment.permission_status == "approved":
        if not assessment.has_licence_snapshot:
            problems.append(
                (
                    "derived-from-source-without-licence-snapshot",
                    f"dataset {source_dataset!r} is recorded as approved but preserves no checksummed "
                    "licence snapshot (licence.evidence.snapshot), which a use needs",
                )
            )
        missing = sorted(needed - set(assessment.permitted_uses))
        if missing:
            problems.append(
                (
                    "derived-from-source-use-not-permitted",
                    f"dataset {source_dataset!r} does not record {missing} among its permitted uses "
                    f"({list(assessment.permitted_uses)})",
                )
            )
    # A derived dataset can be cleared for, or record, commercial training only if every source permits it.
    if needs_commercial and assessment.permission_status is not None:
        recorded = assessment.commercial_training
        if recorded == "not_permitted":
            problems.append(
                (
                    "derived-from-source-commercial-training-denied",
                    f"{commercial}, and dataset {source_dataset!r} records commercial training as "
                    "'not_permitted'. A dataset cannot be cleared for, or record, commercial training "
                    "when a source it derives from denies it",
                )
            )
        elif recorded != "permitted":
            problems.append(
                (
                    "derived-from-source-commercial-training-not-permitted",
                    f"{commercial}, and dataset {source_dataset!r} records commercial training as "
                    f"{recorded!r}, not 'permitted'. 'unknown' is not permission, and a derived dataset "
                    "cannot have a broader commercial permission than its source",
                )
            )
        elif assessment.commercial_basis_problem is not None:
            problems.append(
                (
                    "derived-from-source-commercial-training-unsupported",
                    f"{commercial}, and dataset {source_dataset!r} records commercial training as "
                    f"permitted, but {assessment.commercial_basis_problem}, so that permission is not "
                    "supported by its recorded evidence",
                )
            )
    # A manifest's declaration that its files and licence snapshot exist is not evidence that
    # they do. A schema 1.0 source records no checksums and is refused above in any case.
    if assessment.permission_status is not None:
        if assessment.dataset_dir is None:
            problems.append(
                (
                    "derived-from-source-files-not-verified",
                    f"no dataset directory was supplied for dataset {source_dataset!r}, so its record "
                    "files and licence snapshot were not checked against its manifest. A manifest that "
                    "lists files is not evidence that they exist or are unchanged. Supply the directory "
                    f"with --source-dataset-dir {source_dataset}=DIRECTORY (the audit: --dataset-dir "
                    f"{source_dataset}=DIRECTORY)",
                )
            )
        elif not assessment.files_verified:
            shown = "; ".join(assessment.file_errors) or "the directory could not be checked"
            problems.append(
                (
                    "derived-from-source-files-invalid",
                    f"the files of dataset {source_dataset!r} in '{assessment.dataset_dir}' do not match "
                    f"its manifest ({assessment.file_error_count} error(s)); first: {shown}",
                )
            )

    if assessment.files_verified:
        snapshot = assessment.has_licence_snapshot
        note = (
            f" The source's {assessment.files_declared} listed file(s)"
            + (", its licence snapshot among them," if snapshot else " (it lists no licence snapshot)")
            + f" were checked in '{assessment.dataset_dir}': each exists and matches its recorded SHA-256"
            + (", and the snapshot is real text" if snapshot else "")
            + ". That shows the recorded evidence is intact, not that the licence permits the use or "
            "that what the source records (permission, consent) is true: that is declared, not "
            "independently verified."
        )
    else:
        note = (
            " The source's files and licence snapshot were NOT verified, and everything it records is "
            "declared, not independently verified."
        )
    for code, text in problems:
        message = f"{who}, but {text}.{note}"
        if in_use:
            report.error(code, message, location)
        else:
            report.warn(
                code, message + " This is a warning while everything is unassigned and no use is requested.", location
            )


def _check_provenance(
    view: ManifestView,
    report: Report,
    sources: Mapping[str, ManifestView] | None,
    requested: Collection[str],
    assessing: frozenset[str],
    run: ValidationRun,
    commercial: str | None = None,
) -> None:
    """Every declared derivation must be traceable; one that cannot be traced fails closed."""
    by_id = view.records_by_id()
    other_records: dict[str, dict[str, RecordView]] = {}
    for dataset_id, source_view in (sources or {}).items():
        if dataset_id != view.dataset_id:
            other_records[dataset_id] = source_view.records_by_id()
    derived_from: dict[str, list[tuple[RecordView, RecordView]]] = {}

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
            derived_from.setdefault(source_dataset, []).append((record, source))
            _check_cross_dataset_derivation(
                record, source, sources[source_dataset], source_dataset, location, report
            )

    # Each source is judged once, however many records derive from it.
    for source_dataset, pairs in derived_from.items():
        _check_source_governance(
            view, source_dataset, pairs, sources or {}, requested, assessing, report, run, commercial
        )


def _commercial_reason(manifest: Any, version: str, requested: bool, inherited: bool) -> str | None:
    """Why this dataset's sources must permit commercial training, or None if they need not."""
    if requested:
        return "commercial training was requested for this dataset"
    if inherited:
        return "a dataset derived from this one is to be used for commercial training"
    if version == "1.1" and manifest["dataset"]["permissions"]["commercial_training"] == "permitted":
        return "this dataset records commercial training as permitted"
    return None


def directory_binding_problems(entries: Sequence[tuple[str, Path]]) -> list[str]:
    """Messages for every pair of dataset directories that are the same or nested.

    `entries` are (who, directory), for example ("this dataset", ...) and ("source 'a'", ...).
    Two datasets checked in one directory, or one inside the other, would count the files of
    one as files of the other, or hide them, so the binding is ambiguous and refused. Links are
    followed and letter case is ignored where the file system ignores it. A directory in a
    restricted location is not resolved here: it is refused where it is checked, before
    anything in it is touched.
    """
    resolved: list[tuple[str, Path, str]] = []
    for who, path in entries:
        if lexical_restriction(path) is not None:
            continue
        try:
            real = os.path.realpath(os.fspath(path))
        except (OSError, ValueError, RuntimeError):
            real = os.path.abspath(os.fspath(path))
        resolved.append((who, path, os.path.normcase(real)))
    problems: list[str] = []
    for index, (who, path, real) in enumerate(resolved):
        for other_who, other_path, other_real in resolved[index + 1 :]:
            if _same_or_inside(real, other_real) or _same_or_inside(other_real, real):
                problems.append(
                    f"the dataset directory of {who} ('{path}') and that of {other_who} ('{other_path}') "
                    "are the same directory, or one is inside the other. Each dataset is checked in a "
                    "directory of its own; otherwise the files of one would be counted, or hidden, as "
                    "files of the other. Give each dataset its own directory."
                )
    return problems


def _same_or_inside(outer: str, inner: str) -> bool:
    try:
        return os.path.commonpath([outer, inner]) == outer
    except ValueError:  # on different drives
        return False


def _check_dataset_directories(
    dataset_dir: Path | None, own_id: str, sources: Mapping[str, ManifestView] | None, report: Report
) -> None:
    """The directories bound to this manifest and to its sources must be distinct and not nested."""
    entries: list[tuple[str, Path]] = []
    if dataset_dir is not None:
        entries.append(("this dataset", dataset_dir))
    for dataset_id in sorted(sources or {}):
        bound = (sources or {})[dataset_id].dataset_dir
        if dataset_id != own_id and bound is not None:
            entries.append((f"source {dataset_id!r}", bound))
    for problem in directory_binding_problems(entries):
        report.error("ambiguous-dataset-directory", problem, "dataset directories")


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
    if source_view.category == RESTRICTED_CATEGORY or source_view.restricted_hits:
        report.error(
            "derived-from-restricted-source",
            f"record derives from {where}, which is restricted UMaT/MN273 material (its manifest "
            "has the category 'umat_practical' or carries those names). Nothing may be derived "
            "from it.",
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
    governance = dataset.get("field_governance", {})
    # (location, value, may not be later than today). Evidence, review and retrieval dates
    # record something that has already happened; a camera clock or annotation date is not
    # judged that way.
    candidates: list[tuple[str, Any, bool]] = [
        ("dataset.source.retrieved_on", dataset["source"].get("retrieved_on"), True),
        ("dataset.permissions.reviewed_on", dataset["permissions"].get("reviewed_on"), True),
        (
            "dataset.licence.evidence.captured_on",
            dataset["licence"].get("evidence", {}).get("captured_on"),
            True,
        ),
        ("dataset.field_governance.reviewed_on", governance.get("reviewed_on"), True),
        (
            "dataset.field_governance.collection_permission.recorded_on",
            governance.get("collection_permission", {}).get("recorded_on"),
            True,
        ),
    ]
    for index, record in enumerate(manifest["records"]):
        candidates.append(
            (
                f"records[{index}].acquisition.captured_at",
                record.get("acquisition", {}).get("captured_at"),
                False,
            )
        )
        candidates.append(
            (
                f"records[{index}].label.source.annotated_on",
                record.get("label", {}).get("source", {}).get("annotated_on"),
                False,
            )
        )
    today = today_utc()
    for location, value, not_in_future in candidates:
        if value is None:
            continue
        if not _calendar_valid(value):
            report.error("invalid-date", f"{value!r} is not a real calendar date.", location)
        elif not_in_future and date.fromisoformat(value[:10]) > today:
            report.error(
                "date-in-future",
                f"{value!r} is later than today ({today.isoformat()}, UTC). An evidence, review or "
                "retrieval date records something that has already happened.",
                location,
            )


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


def _basis_is_meaningful(text: str) -> bool:
    """A plausibility check on a commercial-training basis, not a legal judgement.

    It rejects what cannot name a licence clause or permission document: placeholders
    ("x", "n/a", "TBD", "yes", "see licence"), text with fewer than 8 letters and digits
    in all, and text built from fewer than 4 different characters. Text that passes can
    still be wrong: nothing here reads the licence.
    """
    return _text_problem(text) is None


def _tokens(text: str) -> list[str]:
    """The words of `text`: runs of letters and digits in any script, case folded."""
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold())


def _text_problem(text: str) -> str | None:
    """Why `text` cannot be a specific statement (a basis, a reason, a restriction), or None."""
    tokens = _tokens(text)
    joined = "".join(tokens)
    if not joined:
        return "holds no letters or digits"
    if all(token in PLACEHOLDER_WORDS for token in tokens):
        return "is a placeholder, not a recorded statement"
    if len(joined) < MIN_BASIS_CHARACTERS or len(set(joined)) < MIN_BASIS_DISTINCT:
        return (
            f"is too short or too repetitive to say anything specific (it needs at least "
            f"{MIN_BASIS_CHARACTERS} letters and digits)"
        )
    return None


def _name_problem(text: str) -> str | None:
    """Why `text` cannot name a person, body, document or value (a recorded name or reference)."""
    tokens = _tokens(text)
    joined = "".join(tokens)
    if not joined:
        return "holds no letters or digits"
    if all(token in PLACEHOLDER_WORDS for token in tokens) or len(set(joined)) == 1:
        return "is a placeholder, not a recorded value"
    if len(joined) < MIN_NAME_CHARACTERS:
        return f"is too short to identify anything (it needs at least {MIN_NAME_CHARACTERS} letters and digits)"
    return None


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
    elif permissions["commercial_training"] == "permitted" and not _basis_is_meaningful(
        permissions["commercial_training_basis"]
    ):
        report.error(
            "commercial-training-basis-not-meaningful",
            "commercial_training is 'permitted' but commercial_training_basis does not name a "
            "licence clause or permission document: it needs at least 8 letters and digits, must "
            "not be a placeholder such as 'x', 'n/a', 'TBD' or 'see licence', and must not be "
            "made of repeated characters. This is a plausibility check on the text; it does not "
            "establish that the licence permits commercial training.",
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


# The recorded text of a Ghana field governance record. "name" fields identify a person, body or
# document (a short value is fine, a placeholder is not); "text" fields explain something and
# must say something specific.
_GOVERNANCE_TEXT_FIELDS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("collection_permission", "granted_by"), "name", "collection permission 'granted_by'"),
    (("collection_permission", "reference"), "name", "collection permission 'reference'"),
    (("consent", "basis"), "text", "consent 'basis'"),
    (("consent", "reference"), "name", "consent 'reference'"),
    (("location_privacy", "precision"), "name", "location 'precision'"),
    (("location_privacy", "access_restriction"), "text", "location 'access_restriction'"),
    (("location_privacy", "notes"), "text", "location privacy 'notes'"),
    (("reviewed_by",), "name", "'reviewed_by'"),
)


def _placeholder_gaps(governance: dict[str, Any]) -> list[str]:
    """Recorded governance text that is blank, a placeholder, or too thin to be a record."""
    gaps: list[str] = []
    for path, kind, label in _GOVERNANCE_TEXT_FIELDS:
        value: Any = governance
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if not isinstance(value, str):
            continue  # absent: reported elsewhere, when the safeguard needs it
        problem = _name_problem(value) if kind == "name" else _text_problem(value)
        if problem is not None:
            gaps.append(f"{label} {problem}")
    return gaps


def _field_governance_gaps(governance: dict[str, Any] | None) -> list[str]:
    """What is still missing or unresolved in a Ghana field dataset's governance record.

    A gap is only ever reported: nothing is filled in, defaulted or assumed. A value that is
    present but is a placeholder ("x", "n/a", "none", "unknown", "yes", blank or invisible text)
    records nothing, so it is a gap too.
    """
    if governance is None:
        return ["no field_governance record is present"]
    gaps: list[str] = []
    permission = governance["collection_permission"]
    if permission["status"] != FIELD_PERMISSION_RESOLVED:
        gaps.append(f"collection permission is {permission['status']!r}, not 'documented'")
    else:
        for key in ("granted_by", "reference", "recorded_on"):
            if key not in permission:
                gaps.append(f"collection permission is 'documented' but {key} is not recorded")
    consent = governance["consent"]
    if consent["status"] not in FIELD_CONSENT_RESOLVED:
        gaps.append(f"consent is {consent['status']!r}, not 'obtained' or 'not_applicable'")
    elif "basis" not in consent:
        gaps.append(
            "consent is recorded as resolved but its basis (who consented, or why consent does "
            "not apply) is not recorded"
        )
    location = governance["location_privacy"]
    handling = location["handling"]
    if handling not in FIELD_LOCATION_RESOLVED:
        gaps.append("location privacy handling is 'unresolved'")
    elif handling == "generalised" and "precision" not in location:
        gaps.append("location is 'generalised' but the precision that is kept is not recorded")
    elif handling == "exact_restricted" and "access_restriction" not in location:
        gaps.append("exact locations are kept but how access to them is restricted is not recorded")
    if "reviewed_by" not in governance or "reviewed_on" not in governance:
        gaps.append("no responsible reviewer and review date are recorded")
    if not governance.get("permitted_uses"):
        gaps.append("no permitted uses are recorded for the field permission and consent")
    gaps.extend(_placeholder_gaps(governance))
    return gaps


def _check_field_governance(
    manifest: dict[str, Any], view: ManifestView, report: Report, use_requested: bool
) -> None:
    """Ghana field data fails closed: nothing is approved, split or used until it is documented.

    The record is what the project says happened (permission obtained, consent obtained
    or not applicable, location handled, a named reviewer). This tool cannot check that any
    of it is true, and it never fills a gap in: a missing or unresolved safeguard is
    reported as it is.
    """
    dataset = manifest["dataset"]
    governance = dataset.get("field_governance")
    if dataset["category"] != FIELD_CATEGORY:
        if governance is not None:
            report.error(
                "field-governance-not-applicable",
                "field_governance describes the collection of Ghana field data and may only be "
                "recorded when the category is 'ghana_field'.",
                "dataset.field_governance",
            )
        return

    permissions = dataset["permissions"]
    gaps = _field_governance_gaps(governance)
    if gaps:
        claims_use = (
            permissions["status"] == "approved"
            or bool(permissions["permitted_uses"])
            or any(record.split != UNASSIGNED for record in view.records)
            or use_requested
        )
        message = (
            "Ghana field data must not be approved, assigned to a split or used for any AI "
            "purpose until the collection permission, consent and location privacy are "
            "documented and reviewed (see docs/ghana_field_governance.md). Unresolved: "
            + "; ".join(gaps)
            + ". Whatever is recorded is the manifest's own declaration; this tool does not "
            "independently verify it, and it never fills in a missing value."
        )
        if claims_use:
            report.error("field-governance-unresolved", message, "dataset.field_governance")
        else:
            report.warn(
                "field-governance-unresolved",
                message + " Nothing can be cleared for this dataset while they are unresolved.",
                "dataset.field_governance",
            )
        return

    if governance is None:  # unreachable: `gaps` is empty only when a record is present
        return
    extra = sorted(set(permissions["permitted_uses"]) - set(governance["permitted_uses"]))
    if extra:
        report.error(
            "use-exceeds-field-permission",
            f"dataset.permissions.permitted_uses allows {extra}, which the field permission and "
            f"consent do not cover (field_governance.permitted_uses is "
            f"{sorted(governance['permitted_uses'])}).",
            "dataset.permissions.permitted_uses",
        )
    recorded_on = governance["collection_permission"]["recorded_on"]
    reviewed_on = governance["reviewed_on"]
    if (
        _calendar_valid(recorded_on)
        and _calendar_valid(reviewed_on)
        and recorded_on[:10] > reviewed_on[:10]
    ):
        report.error(
            "field-permission-after-review",
            f"the collection permission was recorded on {recorded_on}, after the review on "
            f"{reviewed_on}, so the review cannot have been based on it.",
            "dataset.field_governance.collection_permission.recorded_on",
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
    digest, size, _has_content = _digest_file(path)
    return digest, size


def _digest_file(path: Path) -> tuple[str, int, bool]:
    """(SHA-256, size, whether any byte is something other than whitespace or NUL)."""
    digest = hashlib.sha256()
    size = 0
    has_content = False
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
            if not has_content and chunk.strip(BLANK_BYTES):
                has_content = True
    return digest.hexdigest(), size, has_content


def _snapshot_problem(target: Path, relative_path: str, size: int) -> tuple[str, str] | None:
    """Why a licence snapshot is not usable text, as (code, message), or None if it looks fine.

    A snapshot preserves the licence text or permission document, so it must be text. It is
    refused if it is recognised as an image, video, archive or other container (by its extension
    or by its first bytes), if it is not valid UTF-8 (UTF-8 with or without a byte-order mark, or
    UTF-16 with one) or holds control characters such as NUL (other binary content), if nothing
    visible is left once a byte-order mark, whitespace and zero-width or other invisible
    characters are ignored, or if what is there is a placeholder or too thin to be a document.

    Detection is by extension and a few known signatures and is not complete: a binary format
    this tool does not recognise is caught only because it is not UTF-8 text. Only the first
    megabyte is inspected (the whole file is hashed). Whether the text is really the licence is
    a human judgement.
    """
    try:
        with target.open("rb") as handle:
            head = handle.read(SNAPSHOT_INSPECT_BYTES)
    except OSError:
        return None  # the hashing pass has already reported the unreadable file
    shown = ascii(relative_path)
    suffix = PurePosixPath(relative_path).suffix.casefold()
    recognised = None
    if suffix in MEDIA_EXTENSIONS:
        recognised = f"an image or video by its extension ({suffix})"
    elif suffix in CONTAINER_EXTENSIONS:
        recognised = f"an archive, document or other container by its extension ({suffix})"
    elif looks_like_media(head[:HEADER_BYTES]):
        recognised = "an image or video by its first bytes"
    elif looks_like_container(head[:HEADER_BYTES]):
        recognised = "an archive, document or other container by its first bytes"
    if recognised is not None:
        return (
            "licence-snapshot-not-text",
            f"the licence snapshot {shown} is recognised as {recognised}, not text. A snapshot must be "
            "the licence text or permission document saved as plain UTF-8 text (a PDF or scan can be "
            "transcribed to text, or kept elsewhere and cited as the evidence reference). Recognition "
            "is by extension and a few known signatures and is not complete.",
        )
    if head.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    elif head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    else:
        encoding = "utf-8"
    decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
    try:
        text = decoder.decode(head, final=size <= len(head))
    except UnicodeDecodeError:
        return (
            "licence-snapshot-not-text",
            f"the licence snapshot {shown} is not UTF-8 text: it holds bytes that are not valid text, "
            "which usually means a binary file. Save the licence as plain UTF-8 text.",
        )
    if any(unicodedata.category(c) == "Cc" and c not in TEXT_CONTROLS for c in text):
        return (
            "licence-snapshot-not-text",
            f"the licence snapshot {shown} contains control characters (such as NUL) that text does "
            "not, so it is binary content, not text.",
        )
    if not any(not c.isspace() and unicodedata.category(c) not in INVISIBLE_CATEGORIES for c in text):
        return (
            "licence-snapshot-empty",
            f"the licence snapshot {shown} holds no visible text: only a byte-order mark, whitespace "
            "or zero-width or other invisible characters. Save the actual licence text.",
        )
    thin = _text_problem(text)
    if thin is not None:
        return (
            "licence-snapshot-meaningless",
            f"the licence snapshot {shown} {thin}, so it cannot preserve a licence or permission "
            "document. (The tool checks that real-looking text is there, not what it says.)",
        )
    return None


@dataclass(frozen=True)
class _DeclaredFile:
    relative_path: str
    location: str
    sha256: str | None
    size_bytes: int | None
    is_licence_snapshot: bool = False


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
                    is_licence_snapshot=True,
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
        actual_sha256, actual_size, has_content = _digest_file(target)
    except OSError as error:
        report.error("file-unreadable", f"{ascii(path)}: {error}", declared.location)
        return
    if declared.is_licence_snapshot:
        if not has_content:
            # A checksum of nothing is still a checksum, but it preserves no evidence at all.
            report.error(
                "licence-snapshot-empty",
                f"the licence snapshot {ascii(path)} is empty or holds only whitespace, so it preserves "
                "no licence text or permission document. Save the actual text and record its "
                "checksum. (The tool checks that something is there, not what it says.)",
                declared.location,
            )
        else:
            problem = _snapshot_problem(target, path, actual_size)
            if problem is not None:
                report.error(problem[0], problem[1], declared.location)
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
    result.unmanifested_containers = found.containers
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
            "manifest has unknown rights and must not be used. Ignore patterns never apply to a "
            "file recognised as an image or video by its extension or first bytes. (Not every "
            "image or video format is recognised.)",
            "dataset directory",
        )
    if found.containers:
        report.error(
            "unmanifested-container-file",
            f"{len(found.containers)} archive or container file(s) in the dataset directory are "
            f"not listed in the manifest: {_names(found.containers)}. An archive, document or "
            "data container can hold images, videos or a whole dataset that this tool cannot see "
            "into, with unknown rights. They were left exactly as they are. Unpack and list the "
            "contents in the manifest, or remove them from the dataset directory yourself; this "
            "tool does neither. Ignore patterns never apply. (Listing a document or archive as "
            "the licence snapshot does not help either: the snapshot must be plain text, so a "
            "licence held as a PDF or scan has to be transcribed.)",
            "dataset directory",
        )
    if found.others:
        report.warn(
            "unmanifested-file",
            f"{len(found.others)} other file(s) in the dataset directory are not listed in the "
            f"manifest: {_names(found.others)}. They were left untouched. They were not "
            "recognised as images, videos or archives, but only their first bytes were looked at, "
            "so a file in a format this tool does not recognise could still hold images. If they "
            "are documented metadata, leave them out of this check with an ignore pattern "
            "(--ignore-file).",
            "dataset directory",
        )
    if found.ignored_by_pattern:
        report.warn(
            "files-ignored-by-pattern",
            f"{len(found.ignored_by_pattern)} file(s) were left out of the unlisted-file check by "
            f"an ignore pattern: {_names(found.ignored_by_pattern)}. A pattern never hides a file "
            "recognised as an image, a video or an archive, but it can hide a file whose format "
            "this tool does not recognise.",
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


def _lexical_key(path: Path | None) -> str:
    """A path as a cache key, without touching the file system (so never inside a restricted folder)."""
    return "" if path is None else os.path.normcase(os.path.abspath(os.fspath(path)))


def _check_files(
    run: ValidationRun,
    manifest: dict[str, Any],
    view: ManifestView,
    dataset_dir: Path,
    manifest_path: Path | None,
    ignore_patterns: Sequence[str],
) -> _FileCheck:
    """Check a manifest's files in `dataset_dir` (`_verify_files`), at most once per run.

    The result is reused only within the same run, for the same manifest object, directory,
    manifest path and ignore patterns, so a manifest that is both validated itself and used as
    a source (in the audit, or through several derivations) is hashed once. A new run always
    checks the files again.
    """
    key = (_lexical_key(dataset_dir), _lexical_key(manifest_path), tuple(ignore_patterns))
    cached = run.files.get(key)
    if cached is not None and cached.document is manifest:
        return cached
    scratch = ValidationResult(Report())
    verified = _verify_files(manifest, view, dataset_dir, scratch.report, scratch, manifest_path, ignore_patterns)
    check = _FileCheck(
        document=manifest,
        verified=verified,
        issues=tuple(scratch.report.issues),
        declared=len(_declared_files(manifest, view)),
        unmanifested_images=tuple(scratch.unmanifested_images),
        unmanifested_containers=tuple(scratch.unmanifested_containers),
        unmanifested_other=tuple(scratch.unmanifested_other),
        links_not_followed=tuple(scratch.links_not_followed),
        ignored_by_pattern=tuple(scratch.ignored_by_pattern),
        ignored_housekeeping=tuple(scratch.ignored_housekeeping),
    )
    run.files[key] = check
    return check
