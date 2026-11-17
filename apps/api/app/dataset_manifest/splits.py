"""Reproducible train/validation/test splits grouped by physical sample.

Why grouped: several photographs of one physical sample look alike, so a split
by image lets the model see the test sample during training and overstates its
accuracy. Every record of a physical sample therefore goes to the same split.

Determinism: groups are ordered by SHA-256 of "<seed>:<group key>" rather than
by a random-number generator, so the result does not depend on Python's
`random` internals, the order of records in the manifest, or the platform.

Independent evaluation data is never generated or touched here.
"""

import copy
import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.dataset_manifest.issues import Report, summarise_ids
from app.dataset_manifest.schema import detect_version
from app.dataset_manifest.validation import (
    ASSUMED_IDENTITY,
    EVALUATION,
    RESTRICTED_CATEGORY,
    SPLIT_TO_USE,
    validate_document,
)
from app.dataset_manifest.view import UNASSIGNED, ManifestView, build_view

SPLIT_NAMES = ("train", "validation", "test")
ALGORITHM = "sha256-ordered-stratified-greedy-v1"
SPLIT_FORMAT_VERSION = "1.0"
UNLABELLED = "(unlabelled or conflicting)"
RATIO_TOLERANCE = 1e-6
MAX_SEED = 2**63 - 1
MAX_REASON_LENGTH = 500
# A validation or test split with fewer physical samples than this is flagged. A rule of thumb:
# it says the estimate will be very uncertain, not that 30 samples would make it reliable.
SMALL_SPLIT_SAMPLES = 30


def small_split_message(name: str, samples: int, records: int) -> str:
    """The warning for a validation or test split with few physical samples (also used by the audit)."""
    return (
        f"split {name!r} has only {samples} physical sample(s) "
        f"({records} record(s)). With fewer than {SMALL_SPLIT_SAMPLES} "
        "physical samples, a performance estimate from it is very uncertain and must not "
        "be presented as reliable evidence of how well a model works."
    )


class SplitGenerationError(ValueError):
    def __init__(self, message: str, report: Report | None = None) -> None:
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class SplitConfig:
    seed: int
    ratios: Mapping[str, float]
    group_capture_sessions: bool = False
    allow_resplit: bool = False
    # Splitting a dataset whose physical sample IDs are only assumed needs a written
    # reason, which is recorded in the split file. Ignored for any other basis.
    unverified_sample_identity_reason: str | None = None
    require_commercial_training: bool = False


@dataclass
class SplitResult:
    dataset_id: str
    manifest_sha256: str | None
    seed: int
    ratios: dict[str, float]
    group_capture_sessions: bool
    sample_identity_basis: str
    permissions: dict[str, Any]
    assignments: dict[str, str]
    sample_of_record: dict[str, str]
    summary: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    sample_identity_assumed: bool = False
    unverified_sample_identity_reason: str | None = None
    ignored_file_patterns: list[str] = field(default_factory=list)
    ignored_files: list[str] = field(default_factory=list)
    single_image_per_sample: bool = False
    # The source datasets assessed while the derivations were checked (not part of the split file).
    source_assessments: dict[str, Any] = field(default_factory=dict)

    def to_document(self) -> dict[str, Any]:
        """The split file. It carries no timestamp, so equal inputs give equal bytes."""
        return {
            "split_format_version": SPLIT_FORMAT_VERSION,
            "algorithm": ALGORITHM,
            "dataset_id": self.dataset_id,
            "manifest_schema_version": "1.1",
            "manifest_sha256": self.manifest_sha256,
            "seed": self.seed,
            "ratios": self.ratios,
            "group_capture_sessions": self.group_capture_sessions,
            "sample_identity_basis": self.sample_identity_basis,
            "sample_identity_assumed": self.sample_identity_assumed,
            "unverified_sample_identity_reason": self.unverified_sample_identity_reason,
            "single_image_per_sample": self.single_image_per_sample,
            "ignored_file_patterns": self.ignored_file_patterns,
            "ignored_files": self.ignored_files,
            "permissions_at_generation": self.permissions,
            "warnings": self.warnings,
            "summary": self.summary,
            "assignments": [
                {
                    "record_id": record_id,
                    "physical_sample_id": self.sample_of_record[record_id],
                    "split": self.assignments[record_id],
                }
                for record_id in sorted(self.assignments)
            ],
        }


@dataclass(frozen=True)
class _Group:
    key: str
    samples: tuple[str, ...]
    stratum: str | None
    size: int


def generate_splits(
    manifest: Any,
    config: SplitConfig,
    *,
    dataset_dir: Path | None = None,
    manifest_sha256: str | None = None,
    ignore_patterns: Sequence[str] = (),
    provenance_sources: Mapping[str, ManifestView] | None = None,
) -> SplitResult:
    ratios = _normalise_ratios(config.ratios)
    _check_seed(config.seed)

    if detect_version(manifest) != "1.1":
        raise SplitGenerationError(
            "Split generation needs a schema 1.1 manifest: schema 1.0 records no "
            "permissions or checksums, so it cannot be cleared for training."
        )
    if dataset_dir is None:
        raise SplitGenerationError(
            "Split generation needs the dataset directory: a use is accepted only after the "
            "files and the licence snapshot have been verified against their recorded checksums."
        )

    working = _without_ordinary_splits(manifest) if config.allow_resplit else manifest
    required_uses = sorted({SPLIT_TO_USE[name] for name in ratios})
    validation = validate_document(
        working,
        dataset_dir=dataset_dir,
        uses=required_uses,
        require_commercial_training=config.require_commercial_training,
        ignore_patterns=ignore_patterns,
        provenance_sources=provenance_sources,
    )
    if not validation.ok:
        kind = _restriction_kind(manifest)
        if kind == "restricted":
            raise SplitGenerationError(
                "This dataset is restricted UMaT/MN273 material. It must not be split into "
                "training, validation or test data, nor used for independent AI evaluation, and "
                "no manifest field changes that.",
                validation.report,
            )
        if kind == "evaluation":
            raise SplitGenerationError(
                "This dataset is reserved for independent evaluation and must not be split "
                "into training, validation or test data.",
                validation.report,
            )
        raise SplitGenerationError(
            "The manifest failed validation or does not permit the requested uses; "
            "no splits were generated.",
            validation.report,
        )

    view = build_view(working, "1.1")
    if any(record.split == EVALUATION for record in view.records):
        raise SplitGenerationError("Independent evaluation records must not be split.")
    assigned = [record.record_id for record in view.records if record.split != UNASSIGNED]
    if assigned:
        raise SplitGenerationError(
            f"{len(assigned)} record(s) already have a split and would be overwritten. "
            "Pass allow_resplit (--allow-resplit) to discard the existing train, validation "
            "and test assignments and generate them again."
        )

    identity = working["dataset"]["sample_identity"]["basis"]
    identity_assumed = identity == ASSUMED_IDENTITY
    reason: str | None = None
    if identity_assumed:
        reason = _unverified_identity_reason(config.unverified_sample_identity_reason)

    groups = _build_groups(view, config.group_capture_sessions)
    group_split = _assign_groups(groups, ratios, config.seed)
    assignments: dict[str, str] = {}
    sample_of_record: dict[str, str] = {}
    split_of_sample: dict[str, str] = {}
    for group in groups:
        for sample in group.samples:
            split_of_sample[sample] = group_split[group.key]
    for record in view.records:
        assignments[record.record_id] = split_of_sample[record.physical_sample_id]
        sample_of_record[record.record_id] = record.physical_sample_id

    _verify_no_crossing(view, assignments, config.group_capture_sessions)
    single_image = validation.single_image_samples
    summary, warnings = _summarise(view, assignments, ratios, len(groups), single_image)
    if reason is not None:
        warnings.insert(
            0,
            "SAMPLE IDENTITY IS ASSUMED, NOT VERIFIED: every image was treated as its own "
            "physical sample, so these splits are NOT known to be grouped by physical sample. "
            "Images of one physical sample may be on both sides of a split, and any accuracy "
            f"measured on them may be inflated. Accepted reason: {reason!r}.",
        )
    empty = [name for name in ratios if summary["per_split"][name]["records"] == 0]
    if empty:
        raise SplitGenerationError(
            f"Split(s) {empty} would be empty: there are only {summary['groups']} "
            f"group(s) of physical samples for {len(ratios)} splits. Add more physical "
            "samples or use fewer splits."
        )

    permissions = working["dataset"]["permissions"]
    return SplitResult(
        dataset_id=view.dataset_id,
        manifest_sha256=manifest_sha256,
        seed=config.seed,
        ratios=ratios,
        group_capture_sessions=config.group_capture_sessions,
        sample_identity_basis=identity,
        permissions={
            "status": permissions["status"],
            "permitted_uses": sorted(permissions["permitted_uses"]),
            "commercial_training": permissions["commercial_training"],
        },
        assignments=assignments,
        sample_of_record=sample_of_record,
        summary=summary,
        warnings=warnings,
        sample_identity_assumed=identity_assumed,
        unverified_sample_identity_reason=reason,
        ignored_file_patterns=list(ignore_patterns),
        ignored_files=list(validation.ignored_by_pattern),
        single_image_per_sample=single_image,
        source_assessments=dict(validation.source_assessments),
    )


def apply_splits(manifest: dict[str, Any], assignments: Mapping[str, str]) -> dict[str, Any]:
    """A copy of the manifest with each record's split set. The input is not modified."""
    result = copy.deepcopy(manifest)
    for record in result["records"]:
        record["split"] = assignments[record["record_id"]]
    return result


# --- internals -----------------------------------------------------------------


def _normalise_ratios(ratios: Mapping[str, float]) -> dict[str, float]:
    unknown = set(ratios) - set(SPLIT_NAMES)
    if unknown:
        raise SplitGenerationError(
            f"Unknown split name(s) {sorted(unknown)}; use {list(SPLIT_NAMES)}. "
            "'independent_evaluation' is never generated."
        )
    cleaned: dict[str, float] = {}
    for name in SPLIT_NAMES:
        value = ratios.get(name, 0.0)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise SplitGenerationError(f"Ratio for {name!r} must be a number >= 0; got {value!r}.")
        if value > 0:
            cleaned[name] = float(value)
    total = sum(cleaned.values())
    if abs(total - 1.0) > RATIO_TOLERANCE:
        raise SplitGenerationError(f"Split ratios must add up to 1; they add up to {total}.")
    if "train" not in cleaned:
        raise SplitGenerationError("The train ratio must be greater than zero.")
    if len(cleaned) < 2:
        raise SplitGenerationError("At least two splits are needed (train plus validation or test).")
    return cleaned


def _unverified_identity_reason(reason: str | None) -> str:
    """A written reason is required to split a dataset whose sample identity is only assumed."""
    if reason is None:
        raise SplitGenerationError(
            "dataset.sample_identity.basis is 'assumed_each_image_distinct': every image is "
            "treated as its own physical sample without verification, so the split cannot be "
            "known to keep a physical sample together and results measured on it may be "
            "inflated. Establish the physical sample IDs and update the manifest. Only if you "
            "must proceed anyway, give a written reason (--accept-unverified-sample-identity "
            "REASON); it is recorded in the split file and shown as a warning."
        )
    cleaned = reason.strip()
    if not 1 <= len(cleaned) <= MAX_REASON_LENGTH or any(
        ord(character) < 32 or ord(character) == 127 for character in cleaned
    ):
        raise SplitGenerationError(
            f"The reason for accepting an unverified sample identity must be 1 to "
            f"{MAX_REASON_LENGTH} characters of plain text; got {reason!r}."
        )
    return cleaned


def _check_seed(seed: Any) -> None:
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= MAX_SEED:
        raise SplitGenerationError(f"The seed must be an integer from 0 to {MAX_SEED}; got {seed!r}.")


def _without_ordinary_splits(manifest: Any) -> Any:
    """Copy with train/validation/test reset to unassigned; evaluation records stay as they are."""
    working = copy.deepcopy(manifest)
    records = working.get("records") if isinstance(working, dict) else None
    if isinstance(records, list):
        for record in records:
            if isinstance(record, dict) and record.get("split") in SPLIT_NAMES:
                record["split"] = UNASSIGNED
    return working


def _restriction_kind(manifest: Any) -> str | None:
    """'restricted' for UMaT/MN273 material, 'evaluation' for evaluation data, else None."""
    dataset = manifest.get("dataset") if isinstance(manifest, dict) else None
    records = manifest.get("records") if isinstance(manifest, dict) else None
    category = dataset.get("category") if isinstance(dataset, dict) else None
    if category == RESTRICTED_CATEGORY:
        return "restricted"
    has_evaluation = isinstance(records, list) and any(
        isinstance(record, dict) and record.get("split") == EVALUATION for record in records
    )
    return "evaluation" if has_evaluation else None


def _build_groups(view: ManifestView, group_capture_sessions: bool) -> list[_Group]:
    samples = view.samples()
    parent = {sample: sample for sample in samples}

    def find(sample: str) -> str:
        while parent[sample] != sample:
            parent[sample] = parent[parent[sample]]
            sample = parent[sample]
        return sample

    if group_capture_sessions:
        # Samples photographed in one capture session share lighting, background
        # and camera, so they are kept together as one group.
        first_sample: dict[str, str] = {}
        for record in view.records:
            session = record.capture_session_id
            if session is None:
                continue
            if session not in first_sample:
                first_sample[session] = record.physical_sample_id
                continue
            root_a, root_b = find(first_sample[session]), find(record.physical_sample_id)
            if root_a != root_b:
                low, high = sorted((root_a, root_b))
                parent[high] = low

    components: dict[str, list[str]] = {}
    for sample in samples:
        components.setdefault(find(sample), []).append(sample)

    groups = []
    for members in components.values():
        members.sort()
        records = [record for sample in members for record in samples[sample]]
        categories = {record.category for record in records}
        stratum = None
        if len(categories) == 1 and None not in categories:
            stratum = next(iter(categories))
        groups.append(_Group(members[0], tuple(members), stratum, len(records)))
    return groups


def _order_key(seed: int, group_key: str) -> str:
    return hashlib.sha256(f"{seed}:{group_key}".encode("utf-8")).hexdigest()


def _assign_groups(groups: list[_Group], ratios: dict[str, float], seed: int) -> dict[str, str]:
    """Stratified greedy assignment: each group goes to the split furthest below its target.

    Strata are the sample-level material category, so each category is spread
    over the splits in roughly the requested proportions.
    """
    by_stratum: dict[str | None, list[_Group]] = {}
    for group in groups:
        by_stratum.setdefault(group.stratum, []).append(group)

    group_split: dict[str, str] = {}
    for stratum in sorted(by_stratum, key=lambda value: (value is None, value or "")):
        members = sorted(by_stratum[stratum], key=lambda g: (_order_key(seed, g.key), g.key))
        total = sum(group.size for group in members)
        target = {name: ratio * total for name, ratio in ratios.items()}
        current = {name: 0 for name in ratios}
        for group in members:
            # max() keeps the first of equal deficits, so ties go train, validation, test.
            chosen = max(ratios, key=lambda name: target[name] - current[name])
            group_split[group.key] = chosen
            current[chosen] += group.size
    return group_split


def _verify_no_crossing(
    view: ManifestView, assignments: Mapping[str, str], group_capture_sessions: bool
) -> None:
    split_of_sample: dict[str, str] = {}
    split_of_session: dict[str, str] = {}
    for record in view.records:
        split = assignments[record.record_id]
        if split_of_sample.setdefault(record.physical_sample_id, split) != split:
            raise SplitGenerationError(
                f"Internal error: physical sample {record.physical_sample_id!r} was split."
            )
        session = record.capture_session_id
        if group_capture_sessions and session is not None:
            if split_of_session.setdefault(session, split) != split:
                raise SplitGenerationError(
                    f"Internal error: capture session {session!r} was split."
                )


def _summarise(
    view: ManifestView,
    assignments: Mapping[str, str],
    ratios: dict[str, float],
    group_count: int,
    single_image: bool,
) -> tuple[dict[str, Any], list[str]]:
    per_split: dict[str, dict[str, Any]] = {
        name: {"records": 0, "physical_samples": 0, "categories": {}} for name in ratios
    }
    totals: dict[str, int] = {}
    unlabelled: list[str] = []
    conflicting: list[str] = []
    samples = view.samples()
    for sample, records in samples.items():
        split = assignments[records[0].record_id]
        categories = {record.category for record in records}
        if len(categories) == 1 and None not in categories:
            category = next(iter(categories))
        else:
            category = UNLABELLED
            (unlabelled if categories == {None} else conflicting).append(sample)
        entry = per_split[split]
        entry["records"] += len(records)
        entry["physical_samples"] += 1
        entry["categories"][category] = entry["categories"].get(category, 0) + 1
        totals[category] = totals.get(category, 0) + 1

    warnings: list[str] = []
    for name in ("validation", "test"):
        if name in ratios and per_split[name]["physical_samples"] < SMALL_SPLIT_SAMPLES:
            warnings.append(
                small_split_message(name, per_split[name]["physical_samples"], per_split[name]["records"])
            )
    for category in sorted(totals):
        if category == UNLABELLED:
            continue
        for name in ratios:
            if category not in per_split[name]["categories"]:
                warnings.append(
                    f"category {category!r} has no physical samples in split {name!r} "
                    f"(it has {totals[category]} physical sample(s) in total)."
                )
    if unlabelled:
        warnings.append(
            f"{len(unlabelled)} physical sample(s) have no label ({summarise_ids(sorted(unlabelled))}); "
            "they cannot be balanced across the splits and no category result covers them."
        )
    if conflicting:
        warnings.append(
            f"{len(conflicting)} physical sample(s) have conflicting or partly missing labels "
            f"({summarise_ids(sorted(conflicting))}); they are split without balancing and the "
            "labels should be resolved before the splits are relied on."
        )
    if single_image:
        warnings.append(
            "every physical sample has exactly one image, so grouping by physical sample gives no "
            "protection against images of one real sample being on both sides of a split."
        )
    summary = {
        "records": len(view.records),
        "physical_samples": len(samples),
        "groups": group_count,
        "per_split": per_split,
    }
    return summary, warnings
