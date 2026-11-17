"""Loading the manifests that records in another manifest derive from, and binding their directories.

The validator and the split generator both take `--source-manifest` paths, and
`--source-dataset-dir DATASET_ID=DIRECTORY` for the directory each source's files are in.
They load them here so that both behave the same way:

- every source manifest is read through `load_manifest`, so a restricted location is
  refused before the file is opened;
- every source is turned into a view that carries the same UMaT/MN273 name check as the
  manifest being validated, so a source that names itself after restricted material is
  not trusted just because it was supplied as a source;
- two sources with the same dataset_id are an error, and so is a source with the same
  dataset_id as the manifest being checked. Which one "wins" would otherwise depend on
  the order of the arguments, and this is decided by a refusal instead;
- a source's dataset directory is never inferred (not from where its manifest is, nor from
  anything the manifest says). It is bound only by an explicit DATASET_ID=DIRECTORY that
  names exactly one supplied source manifest; an ID given twice, an ID no source has, a
  malformed value and a restricted location are refused before anything is read;
- sources are processed in sorted path order, so an error message does not depend on
  the order of the arguments either.

Loading a source only reads its structure. Whether a record may be derived from it is
decided later (`validation.assess_source`): the source's whole manifest is validated, and its
record files and licence snapshot are checked in the bound directory exactly as `--dataset-dir`
checks a manifest's own. A source that is invalid, not approved, denied, unresolved Ghana field
data, without a licence snapshot, not permitted for the use in question, or whose files were
not or could not be verified makes the derivation an error once the derived data is in use, in
the validator, the split generator and the audit alike. File verification shows that the
recorded evidence is intact; what a source records is still its own declaration, which
`describe_sources` says in the tools' output.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.dataset_manifest.loading import ManifestLoadError, load_manifest
from app.dataset_manifest.restricted import ensure_not_restricted
from app.dataset_manifest.validation import SourceAssessment, source_view
from app.dataset_manifest.view import ManifestView

# The DATASET_ID part of DATASET_ID=DIRECTORY, as the audit's --dataset-dir reads it.
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


class SourceDirectoryError(ManifestLoadError):
    """A --source-dataset-dir value that cannot be used: malformed, repeated or matching no source."""


def dataset_id_of(document: Any) -> str | None:
    """The dataset_id of a parsed manifest, or None if it does not have a usable one."""
    dataset = document.get("dataset") if isinstance(document, dict) else None
    value = dataset.get("dataset_id") if isinstance(dataset, dict) else None
    return value if isinstance(value, str) else None


def parse_source_dataset_dirs(values: Sequence[str], option: str = "--source-dataset-dir") -> dict[str, Path]:
    """DATASET_ID=DIRECTORY values as {dataset_id: directory}. Nothing is touched on disk.

    Each value must name its dataset explicitly, even when there is only one source, so a
    directory can never be attached to the wrong dataset by position or by guess. Raises
    SourceDirectoryError for a value without a dataset ID, with an empty directory, or for a
    dataset ID given twice.
    """
    mapping: dict[str, Path] = {}
    for value in values:
        head, separator, tail = value.partition("=")
        if not separator or not _IDENTIFIER.fullmatch(head) or not tail.strip():
            raise SourceDirectoryError(
                f"{option} {value!r} must be DATASET_ID=DIRECTORY, naming the source dataset whose "
                "files are in DIRECTORY."
            )
        if head in mapping:
            raise SourceDirectoryError(
                f"{option} names dataset_id {head!r} twice. Give each source dataset one directory."
            )
        mapping[head] = Path(tail)
    return mapping


def load_source_views(
    paths: Sequence[Path],
    *,
    own_dataset_id: str | None = None,
    dataset_dirs: Mapping[str, Path] | None = None,
) -> dict[str, ManifestView]:
    """Views of the source manifests at `paths`, keyed by dataset_id, each bound to its directory.

    `dataset_dirs` maps a source's dataset_id to the directory its files are in. Every key must
    name a supplied source, and a directory in a restricted location is refused here, before
    anything in it is read. A source with no directory is loaded without one: it cannot then
    support a derived dataset that is in use.

    Raises ManifestLoadError for an unreadable or unusable source, for two sources that
    share a dataset_id, for a source whose dataset_id is `own_dataset_id`, and (as
    SourceDirectoryError) for a directory that names no supplied source. Raises
    RestrictedPathError for a restricted directory.
    """
    dataset_dirs = dict(dataset_dirs or {})
    for dataset_id, directory in sorted(dataset_dirs.items()):
        ensure_not_restricted(directory, f"Source dataset directory for {dataset_id!r}")
    views: dict[str, ManifestView] = {}
    origin: dict[str, Path] = {}
    for path in sorted(paths, key=str):
        loaded = load_manifest(path)
        try:
            view = source_view(loaded.document, manifest_path=path)
        except ValueError as error:
            raise ManifestLoadError(f"'{path}' cannot be used as a source manifest: {error}") from error
        if own_dataset_id is not None and view.dataset_id == own_dataset_id:
            raise ManifestLoadError(
                f"source manifest '{path}' has the same dataset_id ({view.dataset_id!r}) as the "
                "manifest being checked. A dataset is not a source for itself: a record that "
                "derives from another record of the same manifest names that record directly."
            )
        if view.dataset_id in views:
            raise ManifestLoadError(
                f"source manifests '{origin[view.dataset_id]}' and '{path}' have the same "
                f"dataset_id ({view.dataset_id!r}). Supply each dataset's manifest once; which "
                "one is used must not depend on the order of the arguments."
            )
        views[view.dataset_id] = view
        origin[view.dataset_id] = path
    unknown = sorted(set(dataset_dirs) - set(views))
    if unknown:
        raise SourceDirectoryError(
            f"a source dataset directory was given for dataset_id {unknown[0]!r}, but no "
            "--source-manifest has that dataset_id. A directory is bound only to a source whose "
            "manifest is supplied."
        )
    return {
        dataset_id: replace(view, dataset_dir=dataset_dirs.get(dataset_id)) for dataset_id, view in views.items()
    }


def describe_sources(
    views: Mapping[str, ManifestView], assessments: Mapping[str, SourceAssessment]
) -> list[str]:
    """One plain line per supplied source: what its validation and file check concluded, and the limit of that.

    `assessments` are the ones made while the derivations were checked
    (`ValidationResult.source_assessments`), so this costs nothing extra. A source that no
    derivation used, directly or through a chain, is said not to have been assessed.
    """
    lines: list[str] = []
    for dataset_id in sorted(views):
        assessment = assessments.get(dataset_id)
        if assessment is None:
            lines.append(f"source {dataset_id!r}: no record derives from it, so it was not assessed")
            continue
        if not assessment.assessed:
            lines.append(f"source {dataset_id!r}: NOT assessed (no manifest to validate)")
            continue
        if assessment.cycle:
            lines.append(f"source {dataset_id!r}: part of a derivation cycle")
            continue
        facts = [
            "valid" if assessment.valid else f"INVALID ({assessment.error_count} error(s))",
            f"recorded status {assessment.permission_status or 'none (schema 1.0)'}",
            "permits " + (", ".join(assessment.permitted_uses) or "no use"),
        ]
        if assessment.commercial_training is not None:
            facts.append(f"commercial training recorded as {assessment.commercial_training}")
        if assessment.field_gaps:
            facts.append("Ghana field safeguards UNRESOLVED")
        if assessment.files_verified:
            files = (
                f"files VERIFIED in '{assessment.dataset_dir}': its {assessment.files_declared} listed "
                "file(s)"
                + (", the licence snapshot among them," if assessment.has_licence_snapshot else "")
                + " exist and match their recorded SHA-256"
            )
        elif assessment.files_checked:
            files = (
                f"files FAILED verification in '{assessment.dataset_dir}' "
                f"({assessment.file_error_count} error(s))"
            )
        elif assessment.permission_status is None:
            files = "files NOT verified (schema 1.0 records no checksums)"
        elif assessment.dataset_dir is None:
            files = f"files NOT verified (no --source-dataset-dir {dataset_id}=DIRECTORY was given)"
        else:
            files = f"files NOT verified in '{assessment.dataset_dir}'"
        facts.append(files)
        lines.append(
            f"source {dataset_id!r}: " + "; ".join(facts) + ". File verification shows the recorded "
            "evidence is intact; it does not show that the licence permits a use or that the permission "
            "and consent the source records are true, which are declared, not independently verified"
        )
    return lines
