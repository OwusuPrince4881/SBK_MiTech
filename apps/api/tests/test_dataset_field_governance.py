"""Ghana field datasets fail closed until their safeguards are documented (audit N5).

Every record here is a fictional placeholder. No field data is collected or read, and the tool never
fills in a missing permission, consent or privacy decision: it reports the gap.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import SplitConfig, SplitGenerationError, audit_manifests, generate_splits, validate_document
from app.dataset_manifest.validation import ValidationResult
from dataset_builders import build_dataset, field_governance
from scripts import validate_dataset_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
RATIOS = {"train": 0.7, "validation": 0.15, "test": 0.15}


def error_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.errors}


def warning_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.warnings}


def governance(**overrides: Any) -> dict[str, Any]:
    """A resolved record with `overrides` applied; an override of None removes that key."""
    record = field_governance(**{key: value for key, value in overrides.items() if value is not None})
    for key, value in overrides.items():
        if value is None:
            record.pop(key, None)
    return record


def ghana(root: Path, record: Any = "resolved", **permissions: Any) -> dict[str, Any]:
    manifest = build_dataset(root, sample_count=4, images_per_sample=2, category="ghana_field")
    if record == "resolved":
        manifest["dataset"]["field_governance"] = field_governance()
    elif record is not None:
        manifest["dataset"]["field_governance"] = record
    manifest["dataset"]["permissions"].update(permissions)
    return manifest


# --- a documented dataset is accepted, and only that ------------------------------------------------


def test_a_ghana_dataset_with_resolved_safeguards_can_be_cleared(tmp_path: Path) -> None:
    manifest = ghana(tmp_path / "files")

    result = validate_document(manifest, dataset_dir=tmp_path / "files", uses=["training", "validation"])

    assert result.ok and result.cleared_uses == ("training", "validation")


@pytest.mark.parametrize(
    "record",
    [
        governance(location_privacy={"handling": "removed"}),
        governance(location_privacy={"handling": "generalised", "precision": "no finer than the district"}),
        governance(location_privacy={"handling": "exact_restricted", "access_restriction": "named reviewers only"}),
        governance(consent={"status": "obtained", "basis": "Written consent of the landowner, reference X-1."}),
        governance(consent={"status": "not_applicable", "basis": "No people or private property appear."}),
    ],
)
def test_each_resolved_form_of_a_safeguard_is_accepted(tmp_path: Path, record: dict[str, Any]) -> None:
    manifest = ghana(tmp_path / "files", record)

    assert validate_document(manifest, dataset_dir=tmp_path / "files", uses=["training"]).cleared_uses == ("training",)


# --- anything missing or unresolved blocks approval, splitting and use ------------------------------------

UNRESOLVED: dict[str, tuple[dict[str, Any], str]] = {
    "permission pending": ({"collection_permission": {"status": "pending"}}, "collection permission is 'pending'"),
    "permission not obtained": ({"collection_permission": {"status": "not_obtained"}}, "collection permission is 'not_obtained'"),
    "permission unknown": ({"collection_permission": {"status": "unknown"}}, "collection permission is 'unknown'"),
    "permission without granter": (
        {"collection_permission": {"status": "documented", "reference": "r-1", "recorded_on": "2026-01-15"}},
        "granted_by is not recorded",
    ),
    "permission without reference": (
        {"collection_permission": {"status": "documented", "granted_by": "g", "recorded_on": "2026-01-15"}},
        "reference is not recorded",
    ),
    "permission without date": (
        {"collection_permission": {"status": "documented", "granted_by": "g", "reference": "r-1"}},
        "recorded_on is not recorded",
    ),
    "consent pending": ({"consent": {"status": "pending"}}, "consent is 'pending'"),
    "consent not obtained": ({"consent": {"status": "not_obtained"}}, "consent is 'not_obtained'"),
    "consent unknown": ({"consent": {"status": "unknown"}}, "consent is 'unknown'"),
    "consent obtained without basis": ({"consent": {"status": "obtained"}}, "its basis"),
    "consent not applicable without basis": ({"consent": {"status": "not_applicable"}}, "its basis"),
    "location unresolved": ({"location_privacy": {"handling": "unresolved"}}, "location privacy handling is 'unresolved'"),
    "generalised without precision": ({"location_privacy": {"handling": "generalised"}}, "precision"),
    "exact without access restriction": ({"location_privacy": {"handling": "exact_restricted"}}, "how access to them is restricted"),
    "no reviewer": ({"reviewed_by": None}, "responsible reviewer and review date"),
    "no review date": ({"reviewed_on": None}, "responsible reviewer and review date"),
    "empty permitted uses": ({"permitted_uses": []}, "no permitted uses"),
    "missing permitted uses": ({"permitted_uses": None}, "no permitted uses"),
}


@pytest.mark.parametrize("name", sorted(UNRESOLVED))
def test_every_unresolved_safeguard_blocks_an_approved_dataset(tmp_path: Path, name: str) -> None:
    overrides, phrase = UNRESOLVED[name]
    manifest = ghana(tmp_path / "files", governance(**overrides))

    plain = validate_document(manifest)
    requested = validate_document(manifest, dataset_dir=tmp_path / "files", uses=["training"])

    for result in (plain, requested):
        assert not result.ok and "field-governance-unresolved" in error_codes(result)
        message = next(i.message for i in result.report.errors if i.code == "field-governance-unresolved")
        assert phrase in message and "docs/ghana_field_governance.md" in message
    assert requested.cleared_uses == ()


def test_a_ghana_dataset_with_no_governance_record_cannot_be_approved(tmp_path: Path) -> None:
    manifest = ghana(tmp_path / "files", record=None)

    result = validate_document(manifest, dataset_dir=tmp_path / "files", uses=["training"])

    assert not result.ok and result.cleared_uses == ()
    assert "no field_governance record is present" in result.report.errors[0].message


def test_a_draft_ghana_dataset_is_recorded_as_unresolved_but_can_never_be_cleared(tmp_path: Path) -> None:
    pending = governance(
        collection_permission={"status": "pending"}, consent={"status": "pending"},
        location_privacy={"handling": "unresolved"}, reviewed_by=None, reviewed_on=None, permitted_uses=[],
    )
    manifest = ghana(tmp_path / "files", pending, status="pending_review", permitted_uses=[])

    plain = validate_document(manifest)
    requested = validate_document(manifest, dataset_dir=tmp_path / "files", uses=["training"])

    assert plain.ok and warning_codes(plain) == {"field-governance-unresolved"}
    assert "Nothing can be cleared for this dataset" in plain.report.warnings[0].message
    assert not requested.ok and requested.cleared_uses == ()
    assert {"field-governance-unresolved", "use-not-permitted"} <= error_codes(requested)


def test_assigning_a_split_counts_as_using_the_dataset(tmp_path: Path) -> None:
    pending = governance(collection_permission={"status": "pending"})
    manifest = ghana(tmp_path / "files", pending, status="pending_review", permitted_uses=[])
    manifest["records"][0]["split"] = "train"

    result = validate_document(manifest)

    assert "field-governance-unresolved" in error_codes(result)


# --- consistency of a resolved record ---------------------------------------------------------------------


def test_permitted_uses_cannot_exceed_what_the_field_permission_covers(tmp_path: Path) -> None:
    manifest = ghana(tmp_path / "files", governance(permitted_uses=["testing"]))

    result = validate_document(manifest)

    assert "use-exceeds-field-permission" in error_codes(result)
    assert "['testing']" in next(i.message for i in result.report.errors if i.code == "use-exceeds-field-permission")


def test_the_permission_cannot_be_recorded_after_the_review_that_relied_on_it(tmp_path: Path) -> None:
    permission = {"status": "documented", "granted_by": "Synthetic Site Authority", "reference": "PERM-2026-014", "recorded_on": "2026-03-01"}
    manifest = ghana(tmp_path / "files", governance(collection_permission=permission, reviewed_on="2026-02-01"))

    assert "field-permission-after-review" in error_codes(validate_document(manifest))


def test_field_dates_cannot_be_in_the_future(tmp_path: Path) -> None:
    permission = {"status": "documented", "granted_by": "Synthetic Site Authority", "reference": "PERM-2026-014", "recorded_on": "2099-01-01"}
    manifest = ghana(tmp_path / "files", governance(collection_permission=permission, reviewed_on="2099-02-01"))

    result = validate_document(manifest)

    assert {i.location for i in result.report.errors if i.code == "date-in-future"} == {
        "dataset.field_governance.reviewed_on",
        "dataset.field_governance.collection_permission.recorded_on",
    }


def test_a_governance_record_is_refused_on_any_other_category(tmp_path: Path) -> None:
    manifest = build_dataset(tmp_path / "files", sample_count=4, images_per_sample=2)
    manifest["dataset"]["field_governance"] = field_governance()

    result = validate_document(manifest)

    assert error_codes(result) == {"field-governance-not-applicable"}


@pytest.mark.parametrize(
    "bad",
    [
        governance(consent={"status": "yes"}),
        governance(location_privacy={"handling": "somewhere"}),
        governance(collection_permission={"granted_by": "g"}),
        governance(permitted_uses=["deployment"]),
        governance(permitted_uses=["training", "training"]),
        {**governance(), "extra": 1},
        {key: value for key, value in governance().items() if key != "location_privacy"},
    ],
)
def test_the_governance_record_has_a_strict_structure(tmp_path: Path, bad: dict[str, Any]) -> None:
    manifest = ghana(tmp_path / "files", bad)

    assert "schema" in error_codes(validate_document(manifest))


def test_validation_never_fills_in_or_changes_a_governance_record(tmp_path: Path) -> None:
    pending = governance(consent={"status": "unknown"})
    manifest = ghana(tmp_path / "files", pending)
    before = json.dumps(manifest, sort_keys=True)

    validate_document(manifest, dataset_dir=tmp_path / "files", uses=["training"])

    assert json.dumps(manifest, sort_keys=True) == before


# --- the other tools rely on the same rule -----------------------------------------------------------------


def test_the_splitter_refuses_unresolved_field_data_and_accepts_resolved(tmp_path: Path) -> None:
    root = tmp_path / "files"
    manifest = build_dataset(root, sample_count=40, images_per_sample=2, category="ghana_field")
    manifest["dataset"]["field_governance"] = governance(consent={"status": "unknown"})

    with pytest.raises(SplitGenerationError) as caught:
        generate_splits(manifest, SplitConfig(seed=1, ratios=RATIOS), dataset_dir=root)
    assert caught.value.report is not None and "field-governance-unresolved" in caught.value.report.codes()

    manifest["dataset"]["field_governance"] = field_governance()
    result = generate_splits(manifest, SplitConfig(seed=1, ratios=RATIOS), dataset_dir=root)
    assert set(result.assignments.values()) == {"train", "validation", "test"}


def test_the_audit_fails_approved_field_data_with_gaps_and_only_warns_about_a_draft(tmp_path: Path) -> None:
    approved = ghana(tmp_path / "a", governance(consent={"status": "unknown"}))
    draft = ghana(
        tmp_path / "b", governance(consent={"status": "unknown"}), status="pending_review", permitted_uses=[]
    )

    failing = audit_manifests([("approved.json", approved)])
    passing = audit_manifests([("draft.json", draft)])

    assert "field-governance-unresolved" in {i.code for i in failing.report.errors} and not failing.passed()
    assert passing.passed() and "field-governance-unresolved" in {i.code for i in passing.report.warnings}
    assert not passing.passed(strict=True)


def test_the_validator_cli_explains_what_is_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "files"
    manifest = ghana(root, governance(location_privacy={"handling": "unresolved"}))
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    refused = validate_dataset_manifest.main([str(path), "--dataset-dir", str(root), "--use", "training"])
    err = capsys.readouterr().err
    manifest["dataset"]["field_governance"] = field_governance()
    path.write_text(json.dumps(manifest), encoding="utf-8")
    accepted = validate_dataset_manifest.main([str(path), "--dataset-dir", str(root), "--use", "training"])

    assert refused == 2 and "location privacy handling is 'unresolved'" in err and "docs/ghana_field_governance.md" in err
    assert accepted == 0 and "recorded permission accepted for: training" in capsys.readouterr().out


# --- the requirements for future field collection are written down -----------------------------------------


def test_the_field_collection_requirements_are_documented() -> None:
    text = (REPO_ROOT / "docs" / "ghana_field_governance.md").read_text(encoding="utf-8")

    for required in (
        "collection permission", "consent", "location privacy", "reviewer", "permitted uses",
        "fails closed", "does not collect", "never fills in",
    ):
        assert required in text, required


# --- placeholders are not records (final audit finding M-2) ---------------------------------------------------------
#
# The original gap: a Ghana dataset whose every governance text was the single letter "x", or whose consent basis
# and reviewer were "n/a", was cleared for training. A value that is present but records nothing is a gap, never a
# default, and nothing is filled in. What a record says is still only the manifest's own declaration.

PLACEHOLDERS = [
    "x", "n/a", "N/A", "none", "None", "unknown", "yes", "no", "TBD", "tba", "-", "...", "?", "​", "xxxx",
    "pending", "not applicable", "see above", "name", "lorem ipsum", "anonymous", "same as above", "null", "ok",
]

# (record path, a kind of field, the label the message uses)
FIELD_PATHS: list[tuple[tuple[str, ...], str]] = [
    (("collection_permission", "granted_by"), "collection permission 'granted_by'"),
    (("collection_permission", "reference"), "collection permission 'reference'"),
    (("consent", "basis"), "consent 'basis'"),
    (("consent", "reference"), "consent 'reference'"),
    (("location_privacy", "precision"), "location 'precision'"),
    (("location_privacy", "access_restriction"), "location 'access_restriction'"),
    (("location_privacy", "notes"), "location privacy 'notes'"),
    (("reviewed_by",), "'reviewed_by'"),
]


def with_value(path: tuple[str, ...], value: str) -> dict[str, Any]:
    """A resolved record in which the field at `path` is set to `value` (optional fields are added)."""
    record = field_governance()
    node = record
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return record


@pytest.mark.parametrize(("path", "label"), FIELD_PATHS, ids=[".".join(p) for p, _ in FIELD_PATHS])
@pytest.mark.parametrize("placeholder", PLACEHOLDERS)
def test_a_placeholder_in_any_governance_text_field_leaves_the_dataset_unresolved(
    tmp_path: Path, path: tuple[str, ...], label: str, placeholder: str
) -> None:
    manifest = ghana(tmp_path / "files", with_value(path, placeholder))
    before = json.dumps(manifest, sort_keys=True)

    result = validate_document(manifest, dataset_dir=tmp_path / "files", uses=["training"])

    assert not result.ok and result.cleared_uses == ()
    message = next(i.message for i in result.report.errors if i.code == "field-governance-unresolved")
    assert label in message
    assert json.dumps(manifest, sort_keys=True) == before  # nothing was filled in or changed


@pytest.mark.parametrize("path", [p for p, _ in FIELD_PATHS], ids=[".".join(p) for p, _ in FIELD_PATHS])
def test_whitespace_only_governance_text_is_refused_by_the_schema(tmp_path: Path, path: tuple[str, ...]) -> None:
    manifest = ghana(tmp_path / "files", with_value(path, "   \t "))

    result = validate_document(manifest, dataset_dir=tmp_path / "files", uses=["training"])

    assert not result.ok and result.cleared_uses == ()
    assert "schema" in error_codes(result)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("collection_permission", "granted_by"), "Kofi A."),
        (("collection_permission", "granted_by"), "MoF"),
        (("collection_permission", "granted_by"), "Ɔbɛng Mensah"),  # non-ASCII letters are letters
        (("collection_permission", "reference"), "L-12"),
        (("collection_permission", "reference"), "MoF/2026/014"),
        (("consent", "basis"), "Written consent of the landowner, reference X-1."),
        (("consent", "basis"), "No people or private land appear in these files."),
        (("consent", "reference"), "Letter 7 of 2026-01-10"),
        (("location_privacy", "precision"), "1 km"),
        (("location_privacy", "precision"), "no finer than the district"),
        (("location_privacy", "access_restriction"), "named reviewers only"),
        (("location_privacy", "notes"), "GPS removed before the files were stored"),
        (("reviewed_by",), "A. Boateng"),
        (("reviewed_by",), "Synthetic Review Board"),
    ],
)
def test_real_recorded_information_is_still_accepted(tmp_path: Path, path: tuple[str, ...], value: str) -> None:
    manifest = ghana(tmp_path / "files", with_value(path, value))

    result = validate_document(manifest, dataset_dir=tmp_path / "files", uses=["training"])

    assert result.ok and result.cleared_uses == ("training",), result.report.format()


NAME_PATHS = [item for item in FIELD_PATHS if item[0][-1] in ("granted_by", "reference", "precision", "reviewed_by")]
TEXT_PATHS = [item for item in FIELD_PATHS if item[0][-1] in ("basis", "access_restriction", "notes")]


@pytest.mark.parametrize(("path", "label"), NAME_PATHS, ids=[".".join(p) for p, _ in NAME_PATHS])
@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("ab", "is too short to identify anything"),  # a name or reference needs 3 letters or digits
        ("AB", "is too short to identify anything"),
        ("5m", "is too short to identify anything"),
        ("a1", "is too short to identify anything"),
        ("7", "is a placeholder"),  # one character, however it is dressed, is a placeholder
        ("--7", "is a placeholder"),
    ],
)
def test_a_name_or_reference_too_short_to_identify_anything_is_a_gap(
    tmp_path: Path, path: tuple[str, ...], label: str, value: str, reason: str
) -> None:
    """Two characters cannot identify a person, a body or a document, and one is only a placeholder."""
    manifest = ghana(tmp_path / "files", with_value(path, value))

    result = validate_document(manifest, dataset_dir=tmp_path / "files", uses=["training"])

    assert not result.ok and result.cleared_uses == ()
    message = next(i.message for i in result.report.errors if i.code == "field-governance-unresolved")
    assert f"{label} {reason}" in message


@pytest.mark.parametrize(("path", "label"), TEXT_PATHS, ids=[".".join(p) for p, _ in TEXT_PATHS])
@pytest.mark.parametrize("value", ["short", "reason", "aaaaaaaaaaaa", "1234567", "ababababab"])
def test_an_explanation_too_thin_to_say_anything_specific_is_a_gap(
    tmp_path: Path, path: tuple[str, ...], label: str, value: str
) -> None:
    manifest = ghana(tmp_path / "files", with_value(path, value))

    result = validate_document(manifest, dataset_dir=tmp_path / "files", uses=["training"])

    assert not result.ok and result.cleared_uses == ()
    message = next(i.message for i in result.report.errors if i.code == "field-governance-unresolved")
    assert f"{label} is too short or too repetitive" in message


def test_every_placeholder_at_once_is_named_field_by_field(tmp_path: Path) -> None:
    record = field_governance(
        collection_permission={"status": "documented", "granted_by": "x", "reference": "n/a", "recorded_on": "2026-01-15"},
        consent={"status": "obtained", "basis": "yes"},
        location_privacy={"handling": "generalised", "precision": "unknown"},
        reviewed_by="none",
    )
    manifest = ghana(tmp_path / "files", record)

    message = next(i.message for i in validate_document(manifest).report.errors if i.code == "field-governance-unresolved")

    for label in ("collection permission 'granted_by'", "collection permission 'reference'", "consent 'basis'",
                  "location 'precision'", "'reviewed_by'"):
        assert label in message, label
    assert "placeholder" in message


def test_a_placeholder_is_unresolved_even_in_a_draft_and_blocks_approval_afterwards(tmp_path: Path) -> None:
    draft = ghana(tmp_path / "a", with_value(("consent", "basis"), "x"), status="pending_review", permitted_uses=[])
    approved = ghana(tmp_path / "b", with_value(("consent", "basis"), "x"))

    as_draft = validate_document(draft)
    as_approved = validate_document(approved)

    assert as_draft.ok and "field-governance-unresolved" in {i.code for i in as_draft.report.warnings}
    assert not as_approved.ok and "field-governance-unresolved" in error_codes(as_approved)


def test_the_message_says_recorded_values_are_not_independently_verified(tmp_path: Path) -> None:
    manifest = ghana(tmp_path / "files", with_value(("reviewed_by",), "x"))

    message = next(i.message for i in validate_document(manifest).report.errors if i.code == "field-governance-unresolved")

    assert "does not independently verify" in message and "never fills in" in message


def test_a_cleared_ghana_dataset_is_reported_as_recorded_not_verified(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "files"
    manifest = ghana(root)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    code = validate_dataset_manifest.main([str(path), "--dataset-dir", str(root), "--use", "training"])

    out = capsys.readouterr().out
    assert code == 0 and "recorded permission accepted for: training" in out
    assert "field governance: the collection permission, consent and location privacy are recorded text" in out
    assert "does not independently verify any of them" in out
