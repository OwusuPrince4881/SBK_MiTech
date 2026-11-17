"""A version-neutral view of manifest records.

Schema 1.0 and 1.1 name things differently (`images`/`records`, `sample_id`/
`physical_sample_id`). The checks that apply to both versions, and the leakage
audit, read this view instead of the raw documents.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.dataset_manifest.restricted_names import restricted_text_hits

ASSIGNED_SPLITS = ("train", "validation", "test", "independent_evaluation")
UNASSIGNED = "unassigned"


@dataclass(frozen=True)
class RecordView:
    index: int
    record_id: str
    physical_sample_id: str
    capture_session_id: str | None
    split: str
    relative_path: str
    sha256: str | None
    category: str | None
    label_source_type: str | None
    # (dataset_id, record_id) of every source this record is declared to derive from.
    derived_from: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ManifestView:
    version: str
    dataset_id: str
    category: str | None
    records: tuple[RecordView, ...]
    # (location, excerpt, name) of every string in the manifest that carries a UMaT/MN273
    # name. Computed for every manifest, so a source manifest is checked like any other.
    restricted_hits: tuple[tuple[str, str, str], ...] = ()
    # The parsed manifest this view was built from (a reference, not a copy). A derivation
    # from this dataset is judged by validating this document in full, never from the
    # structure-only view: permission status, licence evidence, Ghana field governance and
    # every other rule. A view built by hand has no document and cannot be used as a source.
    document: Any = field(default=None, repr=False, compare=False)
    # The dataset directory that this manifest's relative paths refer to, when the caller has
    # bound one explicitly (`--source-dataset-dir DATASET_ID=DIR`, or the audit's
    # `--dataset-dir DATASET_ID=DIR`). It is never inferred from where the manifest file is.
    # A source without one cannot have its files and licence snapshot verified, so it cannot
    # support a derived dataset that is in use.
    dataset_dir: Path | None = field(default=None, repr=False, compare=False)
    # Where the manifest file was read from, so that it can be left out of the unlisted-file
    # check when it is stored inside its own dataset directory.
    manifest_path: Path | None = field(default=None, repr=False, compare=False)

    def samples(self) -> dict[str, list[RecordView]]:
        grouped: dict[str, list[RecordView]] = {}
        for record in self.records:
            grouped.setdefault(record.physical_sample_id, []).append(record)
        return grouped

    def records_by_id(self) -> dict[str, RecordView]:
        return {record.record_id: record for record in self.records}


def build_view(manifest: dict[str, Any], version: str) -> ManifestView:
    """Build a view from a manifest that has already passed structural validation."""
    dataset = manifest["dataset"]
    if version == "1.0":
        records = tuple(
            RecordView(
                index=index,
                record_id=image["image_id"],
                physical_sample_id=image["sample_id"],
                capture_session_id=None,
                split=image["split"],
                relative_path=image["relative_path"],
                sha256=None,
                category=image["material_type"],
                label_source_type=None,
            )
            for index, image in enumerate(manifest["images"])
        )
        return ManifestView(
            "1.0", dataset["dataset_id"], None, records, restricted_text_hits(manifest), manifest
        )

    records = tuple(
        RecordView(
            index=index,
            record_id=record["record_id"],
            physical_sample_id=record["physical_sample_id"],
            capture_session_id=record.get("capture_session_id"),
            split=record["split"],
            relative_path=record["file"]["relative_path"],
            sha256=record["file"]["sha256"],
            category=record.get("label", {}).get("material_category"),
            label_source_type=record.get("label", {}).get("source", {}).get("type"),
            derived_from=tuple(
                (source["dataset_id"], source["record_id"])
                for source in record.get("provenance", {}).get("derived_from", [])
            ),
        )
        for index, record in enumerate(manifest["records"])
    )
    return ManifestView(
        "1.1", dataset["dataset_id"], dataset["category"], records, restricted_text_hits(manifest), manifest
    )
