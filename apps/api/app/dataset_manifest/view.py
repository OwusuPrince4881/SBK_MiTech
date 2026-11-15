"""A version-neutral view of manifest records.

Schema 1.0 and 1.1 name things differently (`images`/`records`, `sample_id`/
`physical_sample_id`). The checks that apply to both versions, and the leakage
audit, read this view instead of the raw documents.
"""

from dataclasses import dataclass
from typing import Any

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
        return ManifestView("1.0", dataset["dataset_id"], None, records)

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
    return ManifestView("1.1", dataset["dataset_id"], dataset["category"], records)
