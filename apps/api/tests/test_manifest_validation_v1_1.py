import json
from pathlib import Path
from typing import Any, Callable

import pytest
from jsonschema import Draft202012Validator

from app.dataset_manifest import validate_document
from app.dataset_manifest.schema import SCHEMA_PATHS, SUPPORTED_VERSIONS
from app.dataset_manifest.validation import ValidationResult, resolve_within
from dataset_builders import (
    build_dataset,
    clone,
    link_directory,
    record_by_id,
    set_splits,
)

DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"
EXAMPLE_1_1 = DOCS_DIR / "examples" / "example_dataset_manifest_1_1.json"
EXAMPLE_1_0 = DOCS_DIR / "examples" / "example_dataset_manifest.json"


@pytest.fixture
def dataset(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    root = tmp_path / "dataset"
    manifest = build_dataset(root, sample_count=4, images_per_sample=2)
    return manifest, root


def error_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.errors}


def warning_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.warnings}


def assert_fails_only_with(result: ValidationResult, *codes: str) -> None:
    assert not result.ok, "expected the manifest to be rejected"
    assert error_codes(result) == set(codes), result.report.format()


# --- the schema file and the examples ---------------------------------------------


def test_schema_1_1_is_a_valid_json_schema_2020_12() -> None:
    schema = json.loads(SCHEMA_PATHS["1.1"].read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema_version"] == {"const": "1.1"}
    Draft202012Validator.check_schema(schema)


def test_both_schema_versions_are_supported() -> None:
    assert SUPPORTED_VERSIONS == ("1.0", "1.1")


def test_example_manifest_1_1_is_valid_but_cleared_for_nothing() -> None:
    manifest = json.loads(EXAMPLE_1_1.read_text(encoding="utf-8"))

    plain = validate_document(manifest)
    training = validate_document(manifest, uses=["training"])

    assert plain.ok, plain.report.format()
    assert plain.cleared_uses == ()
    assert_fails_only_with(training, "use-not-permitted")


def test_valid_synthetic_manifest_passes_with_files_checked(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset

    result = validate_document(manifest, dataset_dir=root)

    assert result.ok, result.report.format()
    assert result.version == "1.1"
    assert result.record_count == 8
    assert result.sample_count == 4
    assert result.files_verified is True
    assert result.report.issues == []


def test_files_are_not_reported_as_verified_without_a_dataset_directory(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, _root = dataset

    result = validate_document(manifest)

    assert result.ok
    assert result.files_verified is False


@pytest.mark.parametrize("version", [None, "2.0", "1", 1.1, ""])
def test_unsupported_or_missing_schema_version_is_rejected(
    dataset: tuple[dict[str, Any], Path], version: object
) -> None:
    manifest = clone(dataset[0])
    if version is None:
        del manifest["schema_version"]
    else:
        manifest["schema_version"] = version

    assert_fails_only_with(validate_document(manifest), "unsupported-schema-version")


@pytest.mark.parametrize("document", [[], "text", 7, None])
def test_a_manifest_that_is_not_an_object_is_rejected(document: object) -> None:
    assert_fails_only_with(validate_document(document), "unsupported-schema-version")


def test_unknown_requested_use_is_a_programming_error(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    with pytest.raises(ValueError, match="Unknown use"):
        validate_document(dataset[0], uses=["deployment"])


# --- structure ----------------------------------------------------------------------


def _pop(path: list[str | int]) -> Callable[[dict[str, Any]], None]:
    def mutate(manifest: dict[str, Any]) -> None:
        target: Any = manifest
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]

    return mutate


def _set(path: list[str | int], value: object) -> Callable[[dict[str, Any]], None]:
    def mutate(manifest: dict[str, Any]) -> None:
        target: Any = manifest
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


STRUCTURAL_CASES = [
    ("missing dataset category", _pop(["dataset", "category"]), "dataset"),
    ("missing permissions", _pop(["dataset", "permissions"]), "dataset"),
    ("missing licence", _pop(["dataset", "licence"]), "dataset"),
    ("missing sample identity", _pop(["dataset", "sample_identity"]), "dataset"),
    ("missing source", _pop(["dataset", "source"]), "dataset"),
    ("missing record id", _pop(["records", 0, "record_id"]), "records[0]"),
    ("missing physical sample id", _pop(["records", 0, "physical_sample_id"]), "records[0]"),
    ("missing file", _pop(["records", 0, "file"]), "records[0]"),
    ("missing checksum", _pop(["records", 0, "file", "sha256"]), "records[0].file"),
    ("missing split", _pop(["records", 0, "split"]), "records[0]"),
    ("no records", _set(["records"], []), "records"),
    ("unknown dataset category", _set(["dataset", "category"], "mn273_team"), "dataset.category"),
    ("unknown split", _set(["records", 0, "split"], "development"), "records[0].split"),
    ("unknown permission status", _set(["dataset", "permissions", "status"], "granted"), "dataset.permissions.status"),
    ("unknown permitted use", _set(["dataset", "permissions", "permitted_uses"], ["deployment"]), "dataset.permissions.permitted_uses"),
    ("permitted use listed twice", _set(["dataset", "permissions", "permitted_uses"], ["training", "training"]), "dataset.permissions.permitted_uses"),
    ("unknown commercial value", _set(["dataset", "permissions", "commercial_training"], "yes"), "dataset.permissions.commercial_training"),
    ("approved without reviewer", _pop(["dataset", "permissions", "reviewed_by"]), "dataset.permissions"),
    ("approved without review date", _pop(["dataset", "permissions", "reviewed_on"]), "dataset.permissions"),
    ("unknown sample identity basis", _set(["dataset", "sample_identity", "basis"], "guessed"), "dataset.sample_identity.basis"),
    ("unknown field on the dataset", _set(["dataset", "owner"], "someone"), "dataset"),
    ("unknown field on a record", _set(["records", 0, "cbr"], 80), "records[0]"),
    ("unknown field on a file", _set(["records", 0, "file", "extra"], 1), "records[0].file"),
    ("uppercase checksum", _set(["records", 0, "file", "sha256"], "A" * 64), "records[0].file.sha256"),
    ("short checksum", _set(["records", 0, "file", "sha256"], "a" * 63), "records[0].file.sha256"),
    ("checksum with trailing newline", _set(["records", 0, "file", "sha256"], "a" * 64 + "\n"), "records[0].file.sha256"),
    ("record id with a space", _set(["records", 0, "record_id"], "rec 1"), "records[0].record_id"),
    ("record id with a trailing newline", _set(["records", 0, "record_id"], "rec-1\n"), "records[0].record_id"),
    ("record id too long", _set(["records", 0, "record_id"], "r" * 129), "records[0].record_id"),
    ("sample id starting with a dot", _set(["records", 0, "physical_sample_id"], ".hidden"), "records[0].physical_sample_id"),
    ("session id with a slash", _set(["records", 0, "capture_session_id"], "a/b"), "records[0].capture_session_id"),
    ("filename containing a slash", _set(["records", 0, "file", "filename"], "a/b.jpg"), "records[0].file.filename"),
    ("size of zero", _set(["records", 0, "file", "size_bytes"], 0), "records[0].file.size_bytes"),
    ("unsupported media type", _set(["records", 0, "file", "media_type"], "image/gif"), "records[0].file.media_type"),
    ("unknown label source type", _set(["records", 0, "label", "source", "type"], "guess"), "records[0].label.source.type"),
    ("label without source", _pop(["records", 0, "label", "source"]), "records[0].label"),
    ("empty vocabulary", _set(["dataset", "label_vocabulary", "categories"], []), "dataset.label_vocabulary.categories"),
    ("whitespace-only dataset name", _set(["dataset", "name"], "   "), "dataset.name"),
    ("licence URL that is not http(s)", _set(["dataset", "licence", "url"], "ftp://example.invalid/x"), "dataset.licence.url"),
    ("source URL with a space", _set(["dataset", "source", "url"], "https://example.invalid/a b"), "dataset.source.url"),
    ("malformed DOI", _set(["dataset", "source", "doi"], "doi:10.1234/x"), "dataset.source.doi"),
    ("date in the wrong shape", _set(["dataset", "permissions", "reviewed_on"], "01/02/2026"), "dataset.permissions.reviewed_on"),
    ("timestamp without a time zone", _set(["records", 0, "acquisition"], {"captured_at": "2026-01-02T03:04:05"}), "records[0].acquisition.captured_at"),
    ("scale reference without present", _set(["records", 0, "acquisition"], {"scale_reference": {}}), "records[0].acquisition.scale_reference"),
    ("derived_from entry without dataset", _set(["records", 0, "provenance"], {"derived_from": [{"record_id": "x"}]}), "records[0].provenance.derived_from[0]"),
    ("empty annotator list", _set(["records", 0, "label", "source", "annotator_ids"], []), "records[0].label.source.annotator_ids"),
]


@pytest.mark.parametrize(
    ("mutate", "location"),
    [pytest.param(case[1], case[2], id=case[0]) for case in STRUCTURAL_CASES],
)
def test_structural_violations_are_rejected_at_the_right_place(
    dataset: tuple[dict[str, Any], Path],
    mutate: Callable[[dict[str, Any]], None],
    location: str,
) -> None:
    manifest = clone(dataset[0])
    mutate(manifest)

    result = validate_document(manifest)

    assert not result.ok
    assert error_codes(result) == {"schema"}
    assert any(issue.location.startswith(location) for issue in result.report.errors), (
        result.report.format()
    )


@pytest.mark.parametrize(
    "path",
    [
        "/abs/a.jpg",
        "C:/a.jpg",
        "C:a.jpg",
        "../a.jpg",
        "a/../b.jpg",
        "./a.jpg",
        "a//b.jpg",
        "a/",
        "a\\b.jpg",
        "a\nb.jpg",
        "",
    ],
)
def test_unsafe_relative_paths_are_rejected(
    dataset: tuple[dict[str, Any], Path], path: str
) -> None:
    manifest = clone(dataset[0])
    manifest["records"][0]["file"]["relative_path"] = path

    result = validate_document(manifest)

    assert error_codes(result) == {"schema"}


@pytest.mark.parametrize("path", ["a.jpg", "nested/dir/file.v2.jpg", "ab:c/d.jpg"])
def test_ordinary_relative_paths_are_accepted(
    dataset: tuple[dict[str, Any], Path], path: str
) -> None:
    manifest = clone(dataset[0])
    manifest["records"][0]["file"]["relative_path"] = path
    manifest["records"][0]["file"]["filename"] = path.rsplit("/", 1)[-1]

    assert validate_document(manifest).ok


def test_cross_record_checks_do_not_run_on_a_structurally_invalid_manifest(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    manifest["records"][1]["record_id"] = manifest["records"][0]["record_id"]
    manifest["records"][2]["split"] = "development"

    assert error_codes(validate_document(manifest)) == {"schema"}


# --- unique IDs, paths and checksums ----------------------------------------------


def test_duplicate_record_id_is_rejected(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    manifest["records"][1]["record_id"] = manifest["records"][0]["record_id"]

    result = validate_document(manifest)

    assert_fails_only_with(result, "duplicate-record-id")
    assert result.report.errors[0].location == "records[1]"


def test_record_ids_differing_only_in_case_are_rejected(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    manifest["records"][1]["record_id"] = manifest["records"][0]["record_id"].upper()

    assert_fails_only_with(validate_document(manifest), "ambiguous-record-id")


def test_a_file_recorded_twice_is_rejected(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    first = manifest["records"][0]["file"]
    manifest["records"][1]["file"]["relative_path"] = first["relative_path"]
    manifest["records"][1]["file"]["filename"] = first["filename"]

    assert_fails_only_with(validate_document(manifest), "duplicate-relative-path")


def test_paths_differing_only_in_case_are_rejected(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    shouted = manifest["records"][0]["file"]["relative_path"].upper()
    manifest["records"][1]["file"]["relative_path"] = shouted
    manifest["records"][1]["file"]["filename"] = shouted.rsplit("/", 1)[-1]

    assert_fails_only_with(validate_document(manifest), "ambiguous-relative-path")


def test_filename_must_match_the_end_of_the_path(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    manifest["records"][0]["file"]["filename"] = "another-name.jpg"

    result = validate_document(manifest)

    assert_fails_only_with(result, "filename-mismatch")
    assert result.report.errors[0].location == "records[0].file.filename"


def test_identical_content_under_two_records_is_rejected(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    manifest["records"][1]["file"]["sha256"] = manifest["records"][0]["file"]["sha256"]

    assert_fails_only_with(validate_document(manifest), "duplicate-checksum")


def test_physical_sample_ids_differing_only_in_case_are_rejected(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    for record in manifest["records"]:
        if record["physical_sample_id"] == "sample-001":
            record["physical_sample_id"] = "SAMPLE-000"

    assert_fails_only_with(validate_document(manifest), "ambiguous-physical-sample-id")


def test_capture_session_ids_differing_only_in_case_are_rejected(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    record_by_id(manifest, "rec-001-0")["capture_session_id"] = "SESSION-000"

    assert_fails_only_with(validate_document(manifest), "ambiguous-capture-session-id")


# --- one physical sample, one split -----------------------------------------------


def test_physical_sample_in_two_splits_is_rejected(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    record_by_id(manifest, "rec-000-0")["split"] = "train"
    record_by_id(manifest, "rec-000-1")["split"] = "validation"

    result = validate_document(manifest)

    assert_fails_only_with(result, "sample-in-multiple-splits")
    message = result.report.errors[0].message
    assert "sample-000" in message and "train" in message and "validation" in message


def test_physical_sample_partly_unassigned_is_rejected(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    record_by_id(manifest, "rec-000-0")["split"] = "train"

    assert_fails_only_with(validate_document(manifest), "sample-in-multiple-splits")


def test_each_sample_wholly_in_one_split_is_accepted(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = set_splits(
        dataset[0],
        {"sample-000": "train", "sample-001": "validation", "sample-002": "test"},
    )

    assert validate_document(manifest).ok


# --- dates --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "location"),
    [
        (_set(["dataset", "permissions", "reviewed_on"], "2026-02-31"), "dataset.permissions.reviewed_on"),
        (_set(["dataset", "source", "retrieved_on"], "2026-04-31"), "dataset.source.retrieved_on"),
        (_set(["records", 0, "acquisition"], {"captured_at": "2026-02-30T10:00:00Z"}), "records[0].acquisition.captured_at"),
        (_set(["records", 0, "label", "source", "annotated_on"], "2025-02-29"), "records[0].label.source.annotated_on"),
    ],
)
def test_impossible_calendar_dates_are_rejected(
    dataset: tuple[dict[str, Any], Path],
    mutate: Callable[[dict[str, Any]], None],
    location: str,
) -> None:
    manifest = clone(dataset[0])
    mutate(manifest)

    result = validate_document(manifest)

    assert_fails_only_with(result, "invalid-date")
    assert result.report.errors[0].location == location


def test_real_dates_and_timestamps_are_accepted(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    manifest["records"][0]["acquisition"] = {"captured_at": "2024-02-29T10:00:00.5+02:00"}
    manifest["records"][0]["label"]["source"]["annotated_on"] = "2024-02-29"

    assert validate_document(manifest).ok


# --- licence and category rules -----------------------------------------------------


def test_public_dataset_needs_a_licence_url(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["category"] = "public"
    manifest["dataset"]["source"]["url"] = "https://example.invalid/source"

    assert_fails_only_with(validate_document(manifest), "public-missing-licence-url")


@pytest.mark.parametrize("field", ["url", "doi"])
def test_public_dataset_needs_a_source_url_or_doi(
    dataset: tuple[dict[str, Any], Path], field: str
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["category"] = "public"
    manifest["dataset"]["licence"]["url"] = "https://example.invalid/licence"

    assert_fails_only_with(validate_document(manifest), "public-missing-source-reference")

    manifest["dataset"]["source"][field] = (
        "https://example.invalid/source" if field == "url" else "10.1234/example"
    )
    assert validate_document(manifest).ok


def test_non_public_datasets_do_not_need_a_licence_url(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["category"] = "ghana_field"

    assert validate_document(manifest).ok


def _umat(manifest: dict[str, Any], uses: list[str], split: str) -> dict[str, Any]:
    result = clone(manifest)
    result["dataset"]["category"] = "umat_practical"
    result["dataset"]["permissions"]["permitted_uses"] = uses
    result["dataset"]["permissions"]["authorisation_reference"] = "synthetic-fixture-permission"
    for record in result["records"]:
        record["split"] = split
    return result


@pytest.mark.parametrize(
    ("uses", "split"),
    [
        (["independent_evaluation"], "independent_evaluation"),
        (["independent_evaluation", "training"], "independent_evaluation"),
        (["training", "validation", "testing"], "train"),
        ([], "unassigned"),
    ],
)
def test_umat_dataset_is_refused_whatever_uses_and_splits_it_declares(
    dataset: tuple[dict[str, Any], Path], uses: list[str], split: str
) -> None:
    manifest = _umat(dataset[0], uses, split)

    result = validate_document(manifest, dataset_dir=dataset[1])

    assert not result.ok
    assert "restricted-category" in error_codes(result)
    assert result.cleared_uses == ()


@pytest.mark.parametrize("use", ["training", "validation", "testing", "independent_evaluation"])
def test_no_use_can_be_requested_for_umat_data(
    dataset: tuple[dict[str, Any], Path], use: str
) -> None:
    manifest = _umat(dataset[0], [use], "independent_evaluation")

    result = validate_document(manifest, dataset_dir=dataset[1], uses=[use])

    assert not result.ok
    assert "restricted-category" in error_codes(result)
    assert result.cleared_uses == ()

def test_evaluation_records_cannot_share_a_manifest_with_other_splits(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = _umat(dataset[0], ["independent_evaluation"], "independent_evaluation")
    manifest["dataset"]["category"] = "synthetic"
    manifest["records"][0]["split"] = "unassigned"
    manifest["records"][1]["split"] = "unassigned"

    result = validate_document(manifest)

    assert "evaluation-records-mixed" in error_codes(result)


def test_evaluation_use_cannot_be_combined_with_training(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["permissions"]["permitted_uses"] = ["independent_evaluation", "training"]

    assert_fails_only_with(validate_document(manifest), "evaluation-use-not-exclusive")


# --- permissions --------------------------------------------------------------------


@pytest.mark.parametrize("status", ["pending_review", "unknown", "denied"])
def test_uses_cannot_be_permitted_without_approval(
    dataset: tuple[dict[str, Any], Path], status: str
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["permissions"]["status"] = status

    assert_fails_only_with(validate_document(manifest), "permitted-uses-without-approval")


def test_commercial_training_cannot_be_permitted_without_approval(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    permissions = manifest["dataset"]["permissions"]
    permissions.update(
        status="pending_review",
        permitted_uses=[],
        commercial_training="permitted",
        commercial_training_basis="Synthetic fixture.",
    )

    assert_fails_only_with(
        validate_document(manifest),
        "commercial-training-without-approval",
        "commercial-training-without-training-permission",
    )


@pytest.mark.parametrize(
    ("split", "missing_use", "kept"),
    [
        ("train", "training", ["validation", "testing"]),
        ("validation", "validation", ["training", "testing"]),
        ("test", "testing", ["training", "validation"]),
    ],
)
def test_a_split_needs_the_matching_permitted_use(
    dataset: tuple[dict[str, Any], Path], split: str, missing_use: str, kept: list[str]
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["permissions"]["permitted_uses"] = kept
    record_by_id(manifest, "rec-000-0")["split"] = split
    record_by_id(manifest, "rec-000-1")["split"] = split

    result = validate_document(manifest)

    assert_fails_only_with(result, "split-use-not-permitted")
    assert missing_use in result.report.errors[0].message


def test_independent_evaluation_split_needs_its_own_permitted_use(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    for record in manifest["records"]:
        record["split"] = "independent_evaluation"

    assert_fails_only_with(validate_document(manifest), "split-use-not-permitted")


def test_a_denied_dataset_cannot_have_assigned_records(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["permissions"].update(status="denied", permitted_uses=[])
    record_by_id(manifest, "rec-000-0")["split"] = "train"
    record_by_id(manifest, "rec-000-1")["split"] = "train"

    assert_fails_only_with(validate_document(manifest), "split-use-not-permitted")


def test_unassigned_records_need_no_permission(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["permissions"].update(status="pending_review", permitted_uses=[])

    assert validate_document(manifest).ok


# --- proposed uses ------------------------------------------------------------------


def test_a_permitted_use_is_confirmed(dataset: tuple[dict[str, Any], Path]) -> None:
    result = validate_document(dataset[0], dataset_dir=dataset[1], uses=["training", "testing"])

    assert result.ok, result.report.format()
    assert result.cleared_uses == ("testing", "training")


def test_a_use_that_is_not_listed_is_refused(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["permissions"]["permitted_uses"] = ["validation"]

    result = validate_document(manifest, uses=["training"])

    assert_fails_only_with(result, "use-not-permitted")
    assert result.cleared_uses == ()


@pytest.mark.parametrize("status", ["pending_review", "unknown", "denied"])
def test_a_use_is_refused_unless_the_status_is_approved(
    dataset: tuple[dict[str, Any], Path], status: str
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["permissions"].update(status=status, permitted_uses=[])

    result = validate_document(manifest, uses=["training"])

    assert_fails_only_with(result, "use-not-permitted")
    assert status in result.report.errors[0].message


def test_independent_evaluation_use_is_refused_for_a_training_dataset(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    assert_fails_only_with(
        validate_document(dataset[0], uses=["independent_evaluation"]), "use-not-permitted"
    )


@pytest.mark.parametrize("commercial", ["unknown", "not_permitted"])
def test_commercial_training_is_refused_unless_permitted(
    dataset: tuple[dict[str, Any], Path], commercial: str
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["permissions"]["commercial_training"] = commercial

    result = validate_document(manifest, uses=["training"], require_commercial_training=True)

    assert_fails_only_with(result, "commercial-training-not-permitted")
    assert commercial in result.report.errors[0].message


def test_commercial_training_is_confirmed_when_permitted(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["permissions"]["commercial_training"] = "permitted"
    manifest["dataset"]["permissions"]["commercial_training_basis"] = "Synthetic fixture."

    result = validate_document(
        manifest, dataset_dir=dataset[1], uses=["training"], require_commercial_training=True
    )

    assert result.ok, result.report.format()
    assert result.commercial_training_cleared is True


def test_commercial_status_is_not_demanded_unless_requested(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    result = validate_document(dataset[0], dataset_dir=dataset[1], uses=["training"])

    assert result.ok, result.report.format()
    assert result.commercial_training_cleared is False


# --- labels -------------------------------------------------------------------------


def test_label_outside_the_declared_vocabulary_is_rejected(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    manifest["records"][0]["label"]["material_category"] = "not-in-the-vocabulary"

    result = validate_document(manifest)

    assert_fails_only_with(result, "unknown-material-category")
    assert "not-in-the-vocabulary" in result.report.errors[0].message


def test_labels_need_a_declared_vocabulary(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    del manifest["dataset"]["label_vocabulary"]

    assert_fails_only_with(validate_document(manifest), "vocabulary-required")


def test_vocabulary_categories_differing_only_in_case_are_rejected(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["label_vocabulary"]["categories"].append("SYNTHETIC-CATEGORY-A")

    assert_fails_only_with(validate_document(manifest), "ambiguous-vocabulary-category")


def test_labels_are_optional(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    del manifest["dataset"]["label_vocabulary"]
    for record in manifest["records"]:
        del record["label"]

    assert validate_document(manifest).ok


def test_conflicting_labels_within_a_sample_are_a_warning(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    record_by_id(manifest, "rec-000-1")["label"]["material_category"] = "synthetic-category-b"

    result = validate_document(manifest)

    assert result.ok
    assert warning_codes(result) == {"sample-label-conflict"}


@pytest.mark.parametrize("split", ["validation", "test"])
def test_model_generated_labels_cannot_be_evaluation_ground_truth(
    dataset: tuple[dict[str, Any], Path], split: str
) -> None:
    manifest = clone(dataset[0])
    for record in manifest["records"]:
        if record["physical_sample_id"] == "sample-000":
            record["split"] = split
            record["label"]["source"]["type"] = "model_generated"

    assert_fails_only_with(validate_document(manifest), "model-label-as-ground-truth")


def test_model_generated_labels_are_allowed_in_the_training_split(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    for record in manifest["records"]:
        if record["physical_sample_id"] == "sample-000":
            record["split"] = "train"
            record["label"]["source"]["type"] = "model_generated"

    assert validate_document(manifest).ok


# --- provenance ---------------------------------------------------------------------


def test_derived_record_keeping_its_samples_identity_is_accepted(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    manifest["records"][1]["provenance"] = {
        "derived_from": [{"dataset_id": "synthetic-fixture", "record_id": "rec-000-0"}],
        "processing_steps": [{"description": "Synthetic crop", "tool": "fixture"}],
    }

    assert validate_document(manifest).ok


def test_record_cannot_derive_from_itself(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    manifest["records"][0]["provenance"] = {
        "derived_from": [{"dataset_id": "synthetic-fixture", "record_id": "rec-000-0"}]
    }

    assert_fails_only_with(validate_document(manifest), "derived-from-self")


def test_derivation_from_an_unknown_record_in_this_dataset_is_rejected(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    manifest["records"][0]["provenance"] = {
        "derived_from": [{"dataset_id": "synthetic-fixture", "record_id": "missing"}]
    }

    assert_fails_only_with(validate_document(manifest), "derived-from-unknown-record")


def test_derivative_must_share_its_sources_physical_sample(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    manifest["records"][0]["provenance"] = {
        "derived_from": [{"dataset_id": "synthetic-fixture", "record_id": "rec-001-0"}]
    }

    assert_fails_only_with(validate_document(manifest), "derived-sample-mismatch")


def test_derivation_from_a_dataset_whose_manifest_is_not_supplied_fails_closed(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    manifest["records"][0]["provenance"] = {
        "derived_from": [{"dataset_id": "some-other-dataset", "record_id": "anything"}]
    }

    result = validate_document(manifest)

    assert_fails_only_with(result, "derived-source-unverified")
    assert result.report.errors[0].location == "records[0].provenance.derived_from"


# --- sample identity ----------------------------------------------------------------


def test_assumed_sample_identity_is_flagged_as_a_warning(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["sample_identity"] = {"basis": "assumed_each_image_distinct"}

    result = validate_document(manifest)

    assert result.ok
    assert warning_codes(result) == {"unverified-sample-identity"}


# --- files --------------------------------------------------------------------------


def test_missing_file_is_reported(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    (root / "images" / "sample-000" / "rec-000-0.jpg").unlink()

    result = validate_document(manifest, dataset_dir=root)

    assert_fails_only_with(result, "file-missing")
    assert result.report.errors[0].location == "records[0]"


def test_wrong_checksum_is_reported_with_both_values(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset
    target = root / "images" / "sample-000" / "rec-000-0.jpg"
    declared = manifest["records"][0]["file"]["sha256"]
    target.write_bytes(target.read_bytes() + b"x")
    manifest["records"][0]["file"]["size_bytes"] += 1  # keep the size check out of this test

    result = validate_document(manifest, dataset_dir=root)

    assert_fails_only_with(result, "checksum-mismatch")
    assert declared in result.report.errors[0].message


def test_declared_size_must_match_the_file(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    manifest["records"][0]["file"]["size_bytes"] += 5

    assert_fails_only_with(validate_document(manifest, dataset_dir=root), "size-mismatch")


def test_size_is_optional(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    for record in manifest["records"]:
        del record["file"]["size_bytes"]

    assert validate_document(manifest, dataset_dir=root).ok


def test_missing_dataset_directory_is_reported(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    result = validate_document(dataset[0], dataset_dir=tmp_path / "does-not-exist")

    assert_fails_only_with(result, "dataset-dir-invalid")
    assert result.files_verified is False


def test_dataset_directory_must_be_a_directory(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    a_file = tmp_path / "plain-file"
    a_file.write_bytes(b"x")

    assert_fails_only_with(validate_document(dataset[0], dataset_dir=a_file), "dataset-dir-invalid")


def test_a_directory_in_place_of_a_file_is_reported_missing(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset
    target = root / "images" / "sample-000" / "rec-000-0.jpg"
    target.unlink()
    target.mkdir()

    assert_fails_only_with(validate_document(manifest, dataset_dir=root), "file-missing")


def test_resolve_within_accepts_paths_inside_the_directory(tmp_path: Path) -> None:
    root = tmp_path.resolve()

    assert resolve_within(root, "a/b.jpg") == root / "a" / "b.jpg"


@pytest.mark.parametrize("path", ["../outside.jpg", "a/../../outside.jpg", "/etc/outside.jpg"])
def test_resolve_within_refuses_paths_that_escape(tmp_path: Path, path: str) -> None:
    root = (tmp_path / "root").resolve()
    root.mkdir()

    assert resolve_within(root, path) is None


def test_link_escaping_the_dataset_directory_is_refused(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    manifest, root = dataset
    outside = tmp_path / "outside-the-dataset"
    outside.mkdir()
    (outside / "secret.jpg").write_bytes(b"x")
    if not link_directory(root / "images" / "linked", outside):
        pytest.skip("this environment cannot create a symbolic link or junction")
    manifest["records"][0]["file"]["relative_path"] = "images/linked/secret.jpg"
    manifest["records"][0]["file"]["filename"] = "secret.jpg"

    result = validate_document(manifest, dataset_dir=root)

    assert "file-outside-dataset-dir" in error_codes(result)


# --- schema 1.0 stays supported -----------------------------------------------------


def test_example_manifest_1_0_still_validates() -> None:
    manifest = json.loads(EXAMPLE_1_0.read_text(encoding="utf-8"))

    result = validate_document(manifest)

    assert result.ok, result.report.format()
    assert result.version == "1.0"


def test_schema_1_0_can_never_be_cleared_for_a_use() -> None:
    manifest = json.loads(EXAMPLE_1_0.read_text(encoding="utf-8"))

    assert_fails_only_with(validate_document(manifest, uses=["training"]), "no-permission-data")
    assert_fails_only_with(
        validate_document(manifest, require_commercial_training=True), "no-permission-data"
    )


def _manifest_1_0_with_two_images() -> dict[str, Any]:
    manifest = json.loads(EXAMPLE_1_0.read_text(encoding="utf-8"))
    second = clone({"image": manifest["images"][0]})["image"]
    second["image_id"] = "fictional-image-id-002"
    second["relative_path"] = "datasets/raw/fictional-placeholder-image-2.jpg"
    manifest["images"].append(second)
    return manifest


def test_cross_record_checks_also_cover_schema_1_0() -> None:
    manifest = _manifest_1_0_with_two_images()
    manifest["images"][1]["image_id"] = manifest["images"][0]["image_id"]

    result = validate_document(manifest)

    assert_fails_only_with(result, "duplicate-record-id")
    assert result.report.errors[0].location == "images[1]"


def test_schema_1_0_sample_in_two_splits_is_rejected() -> None:
    manifest = _manifest_1_0_with_two_images()
    manifest["images"][0]["split"] = "train"
    manifest["images"][1]["split"] = "test"

    assert_fails_only_with(validate_document(manifest), "sample-in-multiple-splits")


def test_schema_1_0_files_are_checked_for_existence_but_not_content(tmp_path: Path) -> None:
    manifest = json.loads(EXAMPLE_1_0.read_text(encoding="utf-8"))

    missing = validate_document(manifest, dataset_dir=tmp_path)
    (tmp_path / "datasets" / "raw").mkdir(parents=True)
    (tmp_path / "datasets" / "raw" / "fictional-placeholder-image.jpg").write_bytes(b"x")
    present = validate_document(manifest, dataset_dir=tmp_path)

    assert_fails_only_with(missing, "file-missing")
    assert present.ok
    assert warning_codes(present) == {"no-checksums", "unverified-sample-identity"}
