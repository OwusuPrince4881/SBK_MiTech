import json
from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import (
    AuditResult,
    SplitConfig,
    apply_splits,
    audit_manifests,
    generate_splits,
)
from dataset_builders import build_dataset, clone, record_by_id, set_splits, sha256_hex

EXAMPLE_1_0 = Path(__file__).resolve().parents[3] / "docs" / "examples" / "example_dataset_manifest.json"


@pytest.fixture
def small(tmp_path: Path) -> dict[str, Any]:
    """Four samples x two images, each sample in its own capture session, all unassigned."""
    return build_dataset(tmp_path / "d", sample_count=4, images_per_sample=2)


def assigned(manifest: dict[str, Any]) -> dict[str, Any]:
    return set_splits(
        manifest,
        {
            "sample-000": "train",
            "sample-001": "train",
            "sample-002": "validation",
            "sample-003": "test",
        },
    )


def audit_one(manifest: Any, label: str = "manifest.json") -> AuditResult:
    return audit_manifests([(label, manifest)])


def error_codes(result: AuditResult) -> set[str]:
    return {issue.code for issue in result.report.errors}


def warning_codes(result: AuditResult) -> set[str]:
    return {issue.code for issue in result.report.warnings}


# --- a clean dataset ------------------------------------------------------------------


def test_a_correctly_split_dataset_passes_with_no_findings(small: dict[str, Any]) -> None:
    result = audit_one(assigned(small))

    assert result.passed()
    assert result.passed(strict=True)
    assert result.report.issues == []


def test_generated_splits_audit_clean(tmp_path: Path) -> None:
    manifest = build_dataset(tmp_path / "d", sample_count=30, images_per_sample=3)
    splits = generate_splits(
        manifest,
        SplitConfig(seed=5, ratios={"train": 0.7, "validation": 0.15, "test": 0.15}),
        dataset_dir=tmp_path / "d",
    )

    result = audit_one(apply_splits(manifest, splits.assignments))

    assert result.passed(strict=True), result.report.format()


def test_summary_describes_each_manifest(small: dict[str, Any]) -> None:
    result = audit_one(assigned(small), label="path/to/manifest.json")

    summary = result.summaries[0]
    assert summary.label == "path/to/manifest.json"
    assert (summary.version, summary.dataset_id, summary.category) == ("1.1", "synthetic-fixture", "synthetic")
    assert (summary.records, summary.samples, summary.sessions) == (8, 4, 4)
    assert summary.records_without_session == 0
    assert summary.split_counts == {
        "train": 4,
        "validation": 2,
        "test": 2,
        "independent_evaluation": 0,
        "unassigned": 0,
    }


# --- physical samples across splits -----------------------------------------------------


def test_physical_sample_in_two_splits_is_a_leakage_error(small: dict[str, Any]) -> None:
    manifest = assigned(small)
    record_by_id(manifest, "rec-000-1")["split"] = "test"

    result = audit_one(manifest, label="leaky.json")

    assert not result.passed()
    assert error_codes(result) == {"sample-in-multiple-splits"}
    issue = result.report.errors[0]
    assert issue.location == "leaky.json"
    assert "sample-000" in issue.message
    assert "train" in issue.message and "test" in issue.message
    assert "rec-000-0" in issue.message and "rec-000-1" in issue.message


def test_a_sample_with_some_records_unassigned_is_an_error(small: dict[str, Any]) -> None:
    manifest = assigned(small)
    record_by_id(manifest, "rec-000-1")["split"] = "unassigned"

    result = audit_one(manifest)

    # The audit now runs the full validation, which treats "unassigned" as a split of its own.
    assert not result.passed()
    assert error_codes(result) == {"sample-in-multiple-splits"}
    assert "unassigned-records" in warning_codes(result)


def test_unassigned_records_are_reported_because_they_cannot_be_assessed(
    small: dict[str, Any],
) -> None:
    result = audit_one(small)

    assert result.passed()
    assert warning_codes(result) == {"unassigned-records"}
    assert not result.passed(strict=True)
    assert "8 of 8" in result.report.warnings[0].message


# --- identical content -----------------------------------------------------------------


def test_identical_image_in_two_splits_is_a_leakage_error(small: dict[str, Any]) -> None:
    manifest = assigned(small)
    shared = record_by_id(manifest, "rec-000-0")["file"]["sha256"]
    record_by_id(manifest, "rec-003-0")["file"]["sha256"] = shared

    result = audit_one(manifest)

    # Two records with one checksum in one manifest is also a validation error.
    assert error_codes(result) == {"duplicate-content-across-splits", "duplicate-checksum"}
    message = next(i.message for i in result.report.errors if i.code == "duplicate-content-across-splits")
    assert shared in message
    assert "synthetic-fixture:rec-000-0 (train)" in message
    assert "synthetic-fixture:rec-003-0 (test)" in message
    assert all(issue.location == "manifest.json" for issue in result.report.errors if issue.code == "duplicate-checksum")


def test_identical_image_within_one_split_is_only_a_warning(small: dict[str, Any]) -> None:
    manifest = assigned(small)
    record_by_id(manifest, "rec-001-0")["file"]["sha256"] = record_by_id(
        manifest, "rec-000-0"
    )["file"]["sha256"]

    result = audit_one(manifest)

    assert not result.passed()
    assert error_codes(result) == {"duplicate-checksum"}
    assert warning_codes(result) == {"duplicate-content"}


def test_identical_images_that_are_all_unassigned_are_only_a_warning(small: dict[str, Any]) -> None:
    record_by_id(small, "rec-001-0")["file"]["sha256"] = record_by_id(small, "rec-000-0")["file"]["sha256"]

    result = audit_one(small)

    assert error_codes(result) == {"duplicate-checksum"}
    assert "duplicate-content" in warning_codes(result)


def test_a_long_list_of_duplicates_is_shortened(small: dict[str, Any]) -> None:
    manifest = assigned(small)
    shared = record_by_id(manifest, "rec-000-0")["file"]["sha256"]
    for record in manifest["records"]:
        record["file"]["sha256"] = shared

    result = audit_one(manifest)

    assert error_codes(result) == {"duplicate-content-across-splits", "duplicate-checksum"}
    message = next(i.message for i in result.report.errors if i.code == "duplicate-content-across-splits")
    assert "and 2 more" in message


# --- several manifests -------------------------------------------------------------------


def evaluation_dataset(root: Path) -> dict[str, Any]:
    manifest = build_dataset(
        root,
        sample_count=3,
        images_per_sample=2,
        dataset_id="synthetic-evaluation",
        permissions={
            "status": "approved",
            "permitted_uses": ["independent_evaluation"],
            "commercial_training": "unknown",
            "reviewed_by": "synthetic-test-fixture",
            "reviewed_on": "2026-01-01",
        },
    )
    for record in manifest["records"]:
        record["split"] = "independent_evaluation"
        record["file"]["relative_path"] = record["file"]["relative_path"].replace("images/", "eval/")
        record["record_id"] = record["record_id"].replace("rec-", "eval-")
        record["physical_sample_id"] = record["physical_sample_id"].replace("sample-", "eval-sample-")
        record["capture_session_id"] = record["capture_session_id"].replace("session-", "eval-session-")
        record["file"]["sha256"] = sha256_hex(record["record_id"].encode("utf-8"))
    return manifest


def test_separate_training_and_evaluation_datasets_pass(small: dict[str, Any], tmp_path: Path) -> None:
    result = audit_manifests(
        [("train.json", assigned(small)), ("eval.json", evaluation_dataset(tmp_path / "e"))]
    )

    assert result.passed(strict=True), result.report.format()
    assert [summary.dataset_id for summary in result.summaries] == [
        "synthetic-fixture",
        "synthetic-evaluation",
    ]


def test_training_image_found_in_the_evaluation_dataset_is_an_error(
    small: dict[str, Any], tmp_path: Path
) -> None:
    training = assigned(small)
    evaluation = evaluation_dataset(tmp_path / "e")
    evaluation["records"][0]["file"]["sha256"] = record_by_id(training, "rec-000-0")["file"]["sha256"]

    result = audit_manifests([("train.json", training), ("eval.json", evaluation)])

    assert error_codes(result) == {"duplicate-content-across-splits"}
    message = result.report.errors[0].message
    assert "synthetic-evaluation" in message and "independent_evaluation" in message


def test_unassigned_image_found_in_the_evaluation_dataset_is_an_error(
    small: dict[str, Any], tmp_path: Path
) -> None:
    evaluation = evaluation_dataset(tmp_path / "e")
    evaluation["records"][0]["file"]["sha256"] = record_by_id(small, "rec-000-0")["file"]["sha256"]

    result = audit_manifests([("train.json", small), ("eval.json", evaluation)])

    assert error_codes(result) == {"duplicate-content-across-splits"}


def test_the_same_image_in_two_training_datasets_is_only_a_warning(
    small: dict[str, Any], tmp_path: Path
) -> None:
    other = build_dataset(tmp_path / "o", sample_count=2, images_per_sample=1, dataset_id="synthetic-other")
    for record in other["records"]:
        record["split"] = "train"
    record_by_id(other, "rec-000-0")["file"]["sha256"] = record_by_id(small, "rec-000-0")["file"]["sha256"]
    first = set_splits(small, {"sample-000": "train", "sample-001": "train"})

    result = audit_manifests([("a.json", first), ("b.json", other)])

    assert result.passed()
    assert "duplicate-content" in warning_codes(result)
    assert "2 dataset(s)" in next(
        issue.message for issue in result.report.warnings if issue.code == "duplicate-content"
    )


def test_two_manifests_with_one_dataset_id_are_rejected(small: dict[str, Any]) -> None:
    result = audit_manifests([("a.json", assigned(small)), ("b.json", assigned(small))])

    assert "duplicate-dataset-id" in error_codes(result)
    assert not result.passed()


# --- capture sessions --------------------------------------------------------------------


def test_capture_session_spanning_splits_is_a_warning(small: dict[str, Any]) -> None:
    manifest = assigned(small)
    for record in manifest["records"]:
        if record["physical_sample_id"] == "sample-003":
            record["capture_session_id"] = "session-000"  # sample-000 is in train, sample-003 in test

    result = audit_one(manifest)

    assert result.passed()
    assert warning_codes(result) == {"capture-session-in-multiple-splits"}
    assert not result.passed(strict=True)
    message = result.report.warnings[0].message
    assert "session-000" in message and "--group-capture-sessions" in message


def test_session_grouped_splits_have_no_session_warning(tmp_path: Path) -> None:
    manifest = build_dataset(tmp_path / "d", sample_count=30, images_per_sample=2, sessions="shared_pairs")
    ratios = {"train": 0.7, "validation": 0.15, "test": 0.15}

    grouped = generate_splits(
        manifest,
        SplitConfig(seed=2, ratios=ratios, group_capture_sessions=True),
        dataset_dir=tmp_path / "d",
    )
    result = audit_one(apply_splits(manifest, grouped.assignments))

    assert result.passed(strict=True), result.report.format()


def test_ungrouped_splits_of_shared_sessions_are_flagged_for_some_seed(tmp_path: Path) -> None:
    manifest = build_dataset(tmp_path / "d", sample_count=30, images_per_sample=2, sessions="shared_pairs")
    ratios = {"train": 0.7, "validation": 0.15, "test": 0.15}

    flagged = [
        "capture-session-in-multiple-splits"
        in warning_codes(
            audit_one(
                apply_splits(
                    manifest,
                    generate_splits(
                        manifest, SplitConfig(seed=s, ratios=ratios), dataset_dir=tmp_path / "d"
                    ).assignments,
                )
            )
        )
        for s in range(10)
    ]

    assert any(flagged)


def test_missing_capture_session_ids_are_reported(tmp_path: Path) -> None:
    manifest = build_dataset(tmp_path / "d", sample_count=4, images_per_sample=2, sessions="none")

    result = audit_one(assigned(manifest))

    assert result.passed()
    assert warning_codes(result) == {"capture-session-missing"}
    assert "8 of 8" in result.report.warnings[0].message
    assert result.summaries[0].records_without_session == 8


def test_partly_missing_capture_session_ids_are_counted(small: dict[str, Any]) -> None:
    manifest = assigned(small)
    del record_by_id(manifest, "rec-000-0")["capture_session_id"]

    result = audit_one(manifest)

    assert warning_codes(result) == {"capture-session-missing"}
    assert "1 of 8" in result.report.warnings[0].message


def test_assumed_sample_identity_is_flagged(small: dict[str, Any]) -> None:
    manifest = assigned(small)
    manifest["dataset"]["sample_identity"] = {"basis": "assumed_each_image_distinct"}

    result = audit_one(manifest)

    assert result.passed()
    assert warning_codes(result) == {"unverified-sample-identity"}


# --- schema 1.0 and bad input ---------------------------------------------------------


def test_schema_1_0_manifest_can_be_audited_with_clear_caveats() -> None:
    manifest = json.loads(EXAMPLE_1_0.read_text(encoding="utf-8"))

    result = audit_one(manifest)

    assert result.passed()
    assert warning_codes(result) == {
        "no-checksums",
        "capture-session-missing",
        "unassigned-records",
        "unverified-sample-identity",
    }
    assert result.summaries[0].category is None
    assert result.summaries[0].sample_identity_basis is None


def test_schema_1_0_sample_in_two_splits_is_a_leakage_error() -> None:
    manifest = json.loads(EXAMPLE_1_0.read_text(encoding="utf-8"))
    second = clone({"image": manifest["images"][0]})["image"]
    second["image_id"] = "fictional-image-id-002"
    second["relative_path"] = "datasets/raw/second.jpg"
    manifest["images"][0]["split"] = "train"
    second["split"] = "test"
    manifest["images"].append(second)

    result = audit_one(manifest)

    assert error_codes(result) == {"sample-in-multiple-splits"}


def test_a_structurally_invalid_manifest_is_not_audited_and_cannot_pass(small: dict[str, Any]) -> None:
    broken = assigned(small)
    del broken["dataset"]["permissions"]

    result = audit_manifests([("good.json", assigned(small)), ("broken.json", broken)])

    assert error_codes(result) == {"manifest-not-auditable"}
    assert result.report.errors[0].location == "broken.json"
    assert not result.passed()
    assert [summary.label for summary in result.summaries] == ["good.json"]


@pytest.mark.parametrize("document", [None, [], {"schema_version": "9.9"}, {}])
def test_unsupported_documents_cannot_pass(document: object) -> None:
    result = audit_one(document)

    assert error_codes(result) == {"manifest-not-auditable"}
    assert not result.passed()


# --- sample identity that is not verified -----------------------------------------------


def test_assumed_identity_warning_states_the_risk_and_is_exposed(small: dict[str, Any]) -> None:
    manifest = assigned(small)
    manifest["dataset"]["sample_identity"] = {"basis": "assumed_each_image_distinct"}

    result = audit_one(manifest, label="assumed.json")

    message = result.report.warnings[0].message
    assert "ASSUMED" in message and "cannot detect" in message
    assert "does not show that the splits are free" in message
    assert result.unverified_identity == ["assumed.json"]
    assert result.summaries[0].sample_identity_basis == "assumed_each_image_distinct"
    assert result.passed()
    assert not result.passed(strict=True)


@pytest.mark.parametrize(
    "basis", ["recorded_by_collector", "declared_by_source", "inferred_by_documented_analysis"]
)
def test_other_identity_bases_are_reported_but_not_as_a_limitation(
    small: dict[str, Any], basis: str
) -> None:
    manifest = assigned(small)
    manifest["dataset"]["sample_identity"] = {
        "basis": basis,
        "notes": "Samples were tagged by the collectors and re-checked against the field log.",
    }

    result = audit_one(manifest)

    assert result.unverified_identity == []
    assert result.summaries[0].sample_identity_basis == basis
    assert result.passed(strict=True)


def test_schema_1_0_counts_as_unverified_identity() -> None:
    manifest = json.loads(EXAMPLE_1_0.read_text(encoding="utf-8"))

    result = audit_one(manifest, label="old.json")

    assert result.unverified_identity == ["old.json"]
    assert "unverified-sample-identity" in warning_codes(result)


def test_only_the_manifests_with_unverified_identity_are_listed(
    small: dict[str, Any], tmp_path: Path
) -> None:
    assumed = evaluation_dataset(tmp_path / "e")
    assumed["dataset"]["sample_identity"] = {"basis": "assumed_each_image_distinct"}

    result = audit_manifests([("good.json", assigned(small)), ("assumed.json", assumed)])

    assert result.unverified_identity == ["assumed.json"]
