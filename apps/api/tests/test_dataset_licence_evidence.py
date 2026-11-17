import json
from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import SplitConfig, SplitGenerationError, generate_splits, validate_document
from app.dataset_manifest.validation import ValidationResult
from dataset_builders import build_dataset, clone, field_governance, sha256_hex

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_1_1 = REPO_ROOT / "docs" / "examples" / "example_dataset_manifest_1_1.json"
SNAPSHOT_PATH = "licence/licence-snapshot.txt"
SNAPSHOT_TEXT = b"Synthetic licence text for the test suite. Not a real licence.\n"


@pytest.fixture
def dataset(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    root = tmp_path / "dataset"
    return build_dataset(root, sample_count=4, images_per_sample=2), root


def error_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.errors}


def assert_fails_only_with(result: ValidationResult, *codes: str) -> None:
    assert not result.ok, "expected the manifest to be rejected"
    assert error_codes(result) == set(codes), result.report.format()


def with_snapshot(manifest: dict[str, Any], root: Path, relative_path: str = SNAPSHOT_PATH) -> dict[str, Any]:
    """Replace the evidence with a licence-text snapshot file under `root`."""
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(SNAPSHOT_TEXT)
    result = clone(manifest)
    result["dataset"]["licence"]["evidence"] = {
        "captured_on": "2026-01-02",
        "snapshot": {"relative_path": relative_path, "sha256": sha256_hex(SNAPSHOT_TEXT)},
    }
    return result


# --- approval needs preserved evidence ------------------------------------------------


def test_approval_without_licence_evidence_is_rejected(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    del manifest["dataset"]["licence"]["evidence"]

    result = validate_document(manifest)

    assert_fails_only_with(result, "approval-without-licence-evidence")
    assert result.report.errors[0].location == "dataset.licence.evidence"


@pytest.mark.parametrize(
    "evidence",
    [
        {"captured_on": "2026-01-02", "reference": "licence archive item 17"},
        {"captured_on": "2026-01-02", "archive_url": "https://archive.example.invalid/licence-copy"},
        {
            "captured_on": "2026-01-02",
            "reference": "permission letter 2026/04",
            "archive_url": "https://archive.example.invalid/letter",
            "notes": "Both recorded.",
        },
    ],
)
def test_each_kind_of_evidence_reference_is_accepted(
    dataset: tuple[dict[str, Any], Path], evidence: dict[str, Any]
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["licence"]["evidence"] = evidence

    assert validate_document(manifest).ok


def test_a_licence_text_snapshot_is_accepted_and_checked_against_the_file(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = with_snapshot(*dataset)

    result = validate_document(manifest, dataset_dir=dataset[1])

    assert result.ok, result.report.format()
    assert result.files_verified is True
    assert result.unmanifested_images == [] and result.unmanifested_other == []


def test_a_snapshot_file_that_is_missing_is_reported_where_it_was_declared(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = with_snapshot(*dataset)
    (dataset[1] / SNAPSHOT_PATH).unlink()

    result = validate_document(manifest, dataset_dir=dataset[1])

    assert_fails_only_with(result, "file-missing")
    assert result.report.errors[0].location == "dataset.licence.evidence.snapshot"


def test_a_snapshot_that_no_longer_matches_its_checksum_is_reported(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = with_snapshot(*dataset)
    (dataset[1] / SNAPSHOT_PATH).write_bytes(SNAPSHOT_TEXT + b"edited later\n")

    result = validate_document(manifest, dataset_dir=dataset[1])

    assert_fails_only_with(result, "checksum-mismatch")
    assert sha256_hex(SNAPSHOT_TEXT) in result.report.errors[0].message
    assert result.report.errors[0].location == "dataset.licence.evidence.snapshot"


def test_a_snapshot_cannot_be_one_of_the_image_records(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    image = manifest["records"][0]["file"]
    manifest["dataset"]["licence"]["evidence"] = {
        "captured_on": "2026-01-02",
        "snapshot": {"relative_path": image["relative_path"], "sha256": image["sha256"]},
    }

    assert_fails_only_with(validate_document(manifest), "duplicate-relative-path")


@pytest.mark.parametrize(
    ("evidence", "location"),
    [
        ({"captured_on": "2026-01-02"}, "dataset.licence.evidence"),
        ({"reference": "licence archive item 17"}, "dataset.licence.evidence"),
        ({"captured_on": "2026-01-02", "reference": "x", "extra": 1}, "dataset.licence.evidence"),
        ({"captured_on": "2026-01-02", "archive_url": "ftp://example.invalid/x"}, "dataset.licence.evidence.archive_url"),
        ({"captured_on": "2026-01-02", "reference": "   "}, "dataset.licence.evidence.reference"),
        ({"captured_on": "02/01/2026", "reference": "x"}, "dataset.licence.evidence.captured_on"),
        (
            {"captured_on": "2026-01-02", "snapshot": {"relative_path": "../licence.txt", "sha256": "a" * 64}},
            "dataset.licence.evidence.snapshot.relative_path",
        ),
        (
            {"captured_on": "2026-01-02", "snapshot": {"relative_path": "/licence.txt", "sha256": "a" * 64}},
            "dataset.licence.evidence.snapshot.relative_path",
        ),
        (
            {"captured_on": "2026-01-02", "snapshot": {"relative_path": "licence.txt", "sha256": "A" * 64}},
            "dataset.licence.evidence.snapshot.sha256",
        ),
        (
            {"captured_on": "2026-01-02", "snapshot": {"relative_path": "licence.txt"}},
            "dataset.licence.evidence.snapshot",
        ),
    ],
)
def test_malformed_evidence_is_rejected(
    dataset: tuple[dict[str, Any], Path], evidence: dict[str, Any], location: str
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["licence"]["evidence"] = evidence

    result = validate_document(manifest)

    assert error_codes(result) == {"schema"}
    assert any(issue.location.startswith(location) for issue in result.report.errors), result.report.format()


def test_an_impossible_evidence_date_is_rejected(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["licence"]["evidence"]["captured_on"] = "2026-02-30"

    result = validate_document(manifest)

    assert_fails_only_with(result, "invalid-date")
    assert result.report.errors[0].location == "dataset.licence.evidence.captured_on"


# --- licence name and version -----------------------------------------------------------


def test_the_licence_version_is_optional(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    assert manifest["dataset"]["licence"]["version"] == "fixture-1"
    del manifest["dataset"]["licence"]["version"]

    assert validate_document(manifest).ok


@pytest.mark.parametrize("version", ["   ", "v" * 201, 4.0])
def test_a_malformed_licence_version_is_rejected(
    dataset: tuple[dict[str, Any], Path], version: object
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["licence"]["version"] = version

    result = validate_document(manifest)

    assert error_codes(result) == {"schema"}
    assert result.report.errors[0].location == "dataset.licence.version"


def test_the_licence_name_is_still_required(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    del manifest["dataset"]["licence"]["name"]

    result = validate_document(manifest)

    assert error_codes(result) == {"schema"}


# --- unknown permissions still fail closed ------------------------------------------------


@pytest.mark.parametrize("status", ["pending_review", "unknown", "denied"])
def test_evidence_alone_does_not_permit_a_use(
    dataset: tuple[dict[str, Any], Path], status: str
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["permissions"].update(status=status, permitted_uses=[])
    assert "evidence" in manifest["dataset"]["licence"]

    plain = validate_document(manifest)
    training = validate_document(manifest, uses=["training"])

    assert plain.ok and plain.cleared_uses == ()
    assert_fails_only_with(training, "use-not-permitted")


@pytest.mark.parametrize("commercial", ["unknown", "not_permitted"])
def test_evidence_does_not_grant_commercial_training(
    dataset: tuple[dict[str, Any], Path], commercial: str
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["permissions"]["commercial_training"] = commercial

    result = validate_document(manifest, uses=["training"], require_commercial_training=True)

    assert_fails_only_with(result, "commercial-training-not-permitted")


def test_a_dataset_with_unknown_permissions_and_no_evidence_is_still_only_a_draft() -> None:
    manifest = json.loads(EXAMPLE_1_1.read_text(encoding="utf-8"))
    manifest["dataset"]["licence"].pop("evidence", None)

    assert validate_document(manifest).ok
    assert_fails_only_with(validate_document(manifest, uses=["training"]), "use-not-permitted")


# --- explicit commercial-training permission ---------------------------------------------


def test_permitted_commercial_training_must_say_where_the_permission_comes_from(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["permissions"]["commercial_training"] = "permitted"

    result = validate_document(manifest)

    assert_fails_only_with(result, "commercial-training-basis-missing")
    assert result.report.errors[0].location == "dataset.permissions.commercial_training_basis"

    manifest["dataset"]["permissions"]["commercial_training_basis"] = "Clause 4.2 of the licence text."
    assert validate_document(manifest).ok


def test_the_commercial_basis_cannot_be_blank(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["permissions"].update(commercial_training="permitted", commercial_training_basis="  ")

    result = validate_document(manifest)

    assert error_codes(result) == {"schema"}
    assert result.report.errors[0].location == "dataset.permissions.commercial_training_basis"


@pytest.mark.parametrize("commercial", ["unknown", "not_permitted"])
def test_a_basis_is_not_needed_unless_commercial_training_is_permitted(
    dataset: tuple[dict[str, Any], Path], commercial: str
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["permissions"]["commercial_training"] = commercial

    assert validate_document(manifest).ok


def test_reviewer_and_review_date_are_still_required_for_approval(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    for missing in ("reviewed_by", "reviewed_on"):
        manifest = clone(dataset[0])
        del manifest["dataset"]["permissions"][missing]

        result = validate_document(manifest)

        assert error_codes(result) == {"schema"}, missing
        assert result.report.errors[0].location == "dataset.permissions"


# --- UMaT data: nothing recorded in a manifest overrides the prohibition -------------------------


def umat(
    manifest: dict[str, Any], *, status: str = "approved", reference: str | None = "synthetic-permission"
) -> dict[str, Any]:
    result = clone(manifest)
    result["dataset"]["category"] = "umat_practical"
    permissions = result["dataset"]["permissions"]
    permissions["status"] = status
    permissions["permitted_uses"] = ["independent_evaluation"] if status == "approved" else []
    if reference is None:
        permissions.pop("authorisation_reference", None)
    else:
        permissions["authorisation_reference"] = reference
    for record in result["records"]:
        record["split"] = "independent_evaluation" if status == "approved" else "unassigned"
    return result


@pytest.mark.parametrize("status", ["approved", "pending_review", "unknown", "denied"])
@pytest.mark.parametrize(
    "reference",
    [None, "x", "Written permission, ref 2026/04, signed by the head of department."],
)
def test_no_authorisation_reference_or_approval_overrides_the_umat_prohibition(
    dataset: tuple[dict[str, Any], Path], status: str, reference: str | None
) -> None:
    manifest = umat(dataset[0], status=status, reference=reference)

    result = validate_document(manifest, dataset_dir=dataset[1], uses=["independent_evaluation"])

    assert not result.ok
    assert "restricted-category" in error_codes(result)
    assert result.cleared_uses == ()


def test_a_umat_manifest_with_every_licence_field_filled_in_is_still_refused(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = umat(dataset[0])  # approved, reviewer and date, checksummed licence snapshot
    manifest["dataset"]["permissions"].update(
        commercial_training="permitted", commercial_training_basis="Clause 4.2.", permitted_uses=["training"]
    )

    result = validate_document(manifest, dataset_dir=dataset[1], uses=["training"], require_commercial_training=True)

    assert "restricted-category" in error_codes(result)
    assert result.cleared_uses == () and result.commercial_training_cleared is False


def test_the_umat_refusal_explains_the_governance_route_and_ignores_the_reference(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    result = validate_document(umat(dataset[0]))

    message = next(issue.message for issue in result.report.errors if issue.code == "restricted-category")
    assert "must not be used for training, validation, testing or independent AI evaluation" in message
    assert "explicit written permission" in message and "separate governance review" in message
    assert "authorisation reference is ignored" in message
    assert "confirmed" not in message.lower()
    without = validate_document(umat(dataset[0], reference=None))
    assert "ignored" not in next(i.message for i in without.report.errors if i.code == "restricted-category")

def test_the_authorisation_reference_is_not_demanded_of_other_categories(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    for category in ("public", "synthetic", "ghana_field"):
        manifest = clone(dataset[0])
        manifest["dataset"]["category"] = category
        if category == "public":
            manifest["dataset"]["licence"]["url"] = "https://example.invalid/licence"
            manifest["dataset"]["source"]["url"] = "https://example.invalid/source"
        if category == "ghana_field":
            manifest["dataset"]["field_governance"] = field_governance()  # approved only with one

        assert validate_document(manifest).ok, category


# --- the splitter relies on the same rules ---------------------------------------------------


def test_splitting_is_refused_when_approval_has_no_licence_evidence(
    tmp_path: Path,
) -> None:
    manifest = build_dataset(tmp_path / "d", sample_count=30, images_per_sample=1)
    del manifest["dataset"]["licence"]["evidence"]

    with pytest.raises(SplitGenerationError) as caught:
        generate_splits(
            manifest,
            SplitConfig(seed=1, ratios={"train": 0.7, "validation": 0.15, "test": 0.15}),
            dataset_dir=tmp_path / "d",
        )

    assert caught.value.report is not None
    assert "approval-without-licence-evidence" in caught.value.report.codes()


# --- the governance statement is in the documentation ----------------------------------------


@pytest.mark.parametrize(
    "document",
    [
        "docs/dataset_strategy.md",
        "docs/dataset_workflow.md",
        "docs/dataset_manifest_schema_1_1.md",
        "data/README.md",
        "CLAUDE.md",
    ],
)
def test_governance_statement_about_restricted_material_is_documented(document: str) -> None:
    text = (REPO_ROOT / document).read_text(encoding="utf-8")
    flattened = " ".join(text.split())

    assert "MN273" in flattened
    assert "must not be used for training, validation, testing or independent AI evaluation" in flattened
    assert "explicit written permission" in flattened
    assert "governance review" in flattened


@pytest.mark.parametrize(
    "document",
    [
        "docs/dataset_strategy.md",
        "docs/dataset_workflow.md",
        "docs/dataset_manifest_schema_1_1.md",
        "data/README.md",
        "CLAUDE.md",
    ],
)
def test_the_documentation_offers_no_route_to_use_restricted_material(document: str) -> None:
    flattened = " ".join((REPO_ROOT / document).read_text(encoding="utf-8").split()).lower()

    assert "unless explicit permission has been obtained" not in flattened
    assert "independent ai evaluation unless" not in flattened
    assert "independent ai evaluation without" not in flattened
    assert "permission confirmed" not in flattened
    assert "only `independent_evaluation`" not in flattened
    assert "held for independent evaluation" not in flattened
