"""A dataset derived from a source is only as usable as its source (final audit finding H-A).

The original gap: the validator and the split generator checked a source manifest only for its restricted
category and names. A public training dataset derived from unresolved Ghana field data, or from a dataset
whose permission was 'denied', was cleared. A source is now validated in full, and a derivation from one that
is invalid, not approved, denied, unresolved Ghana field data, without a licence snapshot or not permitted for
the use in question is refused, in the validator, the split generator and the audit alike.

All data is synthetic. Everything a source records is its own declaration: nothing here verifies it.
"""

import copy
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import (
    SplitConfig,
    SplitGenerationError,
    assess_source,
    audit_manifests,
    describe_sources,
    generate_splits,
    load_source_views,
    source_view,
    validate_document,
)
from app.dataset_manifest import validation as validation_module
from app.dataset_manifest.validation import ValidationResult
from app.dataset_manifest.view import build_view
from dataset_builders import build_dataset, field_governance
from scripts import generate_dataset_splits, validate_dataset_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
RATIOS = {"train": 0.7, "validation": 0.15, "test": 0.15}


def error_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.errors}


def warning_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.warnings}


def make_source(tmp_path: Path, dataset_id: str = "source-a", split: str = "train", **overrides: Any) -> dict[str, Any]:
    manifest = build_dataset(tmp_path / dataset_id, sample_count=4, images_per_sample=2, dataset_id=dataset_id, **overrides)
    for record in manifest["records"]:
        record["split"] = split
    return manifest


def make_derived(
    tmp_path: Path, source: dict[str, Any], derived_id: str = "derived-b", split: str = "train", sample_count: int = 4
) -> tuple[dict[str, Any], Path]:
    root = tmp_path / derived_id
    derived = build_dataset(root, sample_count=sample_count, images_per_sample=2, dataset_id=derived_id)
    for record in derived["records"]:
        record["split"] = split
    derived["records"][0]["provenance"] = {
        "derived_from": [
            {"dataset_id": source["dataset"]["dataset_id"], "record_id": source["records"][0]["record_id"]}
        ]
    }
    return derived, root


def sources_of(*manifests: dict[str, Any], at: Path | None = None) -> dict[str, Any]:
    """Source views; with `at`, each is bound to `at/<dataset_id>`, the directory make_source wrote its files to.

    Without `at` the sources carry no directory, so their files cannot be verified.
    """
    return {
        m["dataset"]["dataset_id"]: source_view(m, dataset_dir=None if at is None else at / m["dataset"]["dataset_id"])
        for m in manifests
    }


def validate_with(derived: dict[str, Any], root: Path, *sources: dict[str, Any], uses: tuple[str, ...] = ("training",)) -> ValidationResult:
    """Validate `derived` (in `root`) against `sources`, each bound to the directory its files are in."""
    return validate_document(
        derived, dataset_dir=root, uses=list(uses), provenance_sources=sources_of(*sources, at=root.parent)
    )


def write_json(path: Path, document: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


GHANA_UNRESOLVED = field_governance(consent={"status": "unknown"})
DENIED = {"status": "denied", "permitted_uses": [], "commercial_training": "not_permitted"}


# --- a source that is fit can still be used --------------------------------------------------------------------


def test_an_approved_eligible_source_still_supports_a_cleared_derived_dataset(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)

    result = validate_with(derived, root, source, uses=("training", "validation", "testing"))

    assert result.ok and result.cleared_uses == ("testing", "training", "validation")
    assessment = assess_source(source_view(source), sources_of(source))
    assert assessment.valid and assessment.permission_status == "approved" and assessment.has_licence_snapshot
    assert "training" in assessment.permitted_uses and assessment.errors == ()


def test_a_good_ghana_source_with_every_safeguard_resolved_can_be_used(tmp_path: Path) -> None:
    source = make_source(tmp_path, category="ghana_field", field_governance=field_governance())
    derived, root = make_derived(tmp_path, source)

    assert validate_with(derived, root, source).ok


def test_an_eligible_source_gives_the_same_result_through_every_tool(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source, sample_count=30)  # enough samples for three non-empty splits

    audit = audit_manifests(
        [("source", source), ("derived", derived)], dataset_dirs={"source": tmp_path / "source-a", "derived": root}
    )
    unassigned = copy.deepcopy(derived)
    for record in unassigned["records"]:
        record["split"] = "unassigned"
    splits = generate_splits(
        unassigned, SplitConfig(seed=1, ratios=RATIOS), dataset_dir=root, provenance_sources=sources_of(source, at=tmp_path)
    )

    assert validate_with(derived, root, source).ok and audit.passed()
    assert set(splits.assignments.values()) == {"train", "validation", "test"}


# --- an unfit source cannot produce a cleared derived dataset --------------------------------------------------


def test_an_unresolved_ghana_source_cannot_produce_a_cleared_derived_dataset(tmp_path: Path) -> None:
    """The original gap: a public training dataset derived from unresolved Ghana field data was cleared."""
    source = make_source(tmp_path, category="ghana_field", field_governance=GHANA_UNRESOLVED)
    derived, root = make_derived(tmp_path, source)
    assert not validate_document(source).ok  # the source is itself invalid: approved with consent 'unknown'

    result = validate_with(derived, root, source)

    assert not result.ok and result.cleared_uses == ()
    assert {"derived-from-unresolved-field-source", "derived-from-invalid-source"} <= error_codes(result)
    message = next(i.message for i in result.report.errors if i.code == "derived-from-unresolved-field-source")
    assert "consent is 'unknown'" in message and "not independently verified" in message


def test_a_draft_ghana_source_cannot_either(tmp_path: Path) -> None:
    source = make_source(
        tmp_path, split="unassigned", category="ghana_field", field_governance=GHANA_UNRESOLVED,
        permissions={"status": "pending_review", "permitted_uses": [], "commercial_training": "unknown"},
    )
    assert validate_document(source).ok  # a draft is only a warning on its own
    derived, root = make_derived(tmp_path, source)

    result = validate_with(derived, root, source)

    assert not result.ok and result.cleared_uses == ()
    assert {"derived-from-unresolved-field-source", "derived-from-unapproved-source"} <= error_codes(result)


def test_a_denied_source_cannot_produce_a_cleared_derived_dataset(tmp_path: Path) -> None:
    source = make_source(tmp_path, split="unassigned", permissions=DENIED)
    derived, root = make_derived(tmp_path, source)

    result = validate_with(derived, root, source)

    assert not result.ok and result.cleared_uses == ()
    assert "derived-from-denied-source" in error_codes(result)
    assert "'denied'" in next(i.message for i in result.report.errors if i.code == "derived-from-denied-source")


@pytest.mark.parametrize("status", ["pending_review", "unknown"])
def test_a_source_that_is_not_approved_cannot_produce_a_cleared_derived_dataset(tmp_path: Path, status: str) -> None:
    source = make_source(
        tmp_path, split="unassigned",
        permissions={"status": status, "permitted_uses": [], "commercial_training": "unknown"},
    )
    derived, root = make_derived(tmp_path, source)

    result = validate_with(derived, root, source)

    assert not result.ok and result.cleared_uses == ()
    assert "derived-from-unapproved-source" in error_codes(result)
    assert status in next(i.message for i in result.report.errors if i.code == "derived-from-unapproved-source")


@pytest.mark.parametrize(
    "break_source",
    [
        lambda m: m["dataset"]["licence"].pop("evidence"),  # approved without any licence evidence
        lambda m: m["records"][1].update(split="test"),  # one physical sample in two splits
        lambda m: m["dataset"]["permissions"].update(commercial_training="permitted"),  # permitted without a basis
        lambda m: m["dataset"]["licence"]["evidence"].update(captured_on="2099-01-01"),  # a future evidence date
    ],
    ids=["no-evidence", "sample-in-two-splits", "commercial-without-basis", "future-evidence-date"],
)
def test_an_invalid_source_cannot_produce_a_cleared_derived_dataset(tmp_path: Path, break_source: Any) -> None:
    source = make_source(tmp_path)
    break_source(source)
    assert not validate_document(source).ok
    derived, root = make_derived(tmp_path, source)

    result = validate_with(derived, root, source)

    assert not result.ok and result.cleared_uses == ()
    assert "derived-from-invalid-source" in error_codes(result)
    assert "does not validate" in next(i.message for i in result.report.errors if i.code == "derived-from-invalid-source")


def test_a_source_without_a_licence_snapshot_is_not_enough_to_clear_a_use(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    source["dataset"]["licence"]["evidence"] = {"captured_on": "2026-01-01", "reference": "a permission letter, ref 7"}
    assert validate_document(source).ok  # recorded evidence is enough to approve, not to clear
    derived, root = make_derived(tmp_path, source)

    result = validate_with(derived, root, source)

    assert error_codes(result) == {"derived-from-source-without-licence-snapshot"} and result.cleared_uses == ()


def test_a_source_that_does_not_permit_the_use_is_refused(tmp_path: Path) -> None:
    source = make_source(tmp_path, split="unassigned")
    source["dataset"]["permissions"]["permitted_uses"] = ["validation", "testing"]
    derived, root = make_derived(tmp_path, source)  # the derived records are in 'train'

    result = validate_with(derived, root, source)

    assert "derived-from-source-use-not-permitted" in error_codes(result) and result.cleared_uses == ()
    assert "'training'" in next(i.message for i in result.report.errors if i.code == "derived-from-source-use-not-permitted")


def test_a_requested_use_the_source_does_not_permit_is_refused_even_for_unassigned_records(tmp_path: Path) -> None:
    source = make_source(tmp_path, split="unassigned")
    source["dataset"]["permissions"]["permitted_uses"] = ["testing"]
    derived, root = make_derived(tmp_path, source, split="unassigned")

    result = validate_with(derived, root, source, uses=("training",))

    assert "derived-from-source-use-not-permitted" in error_codes(result)


def test_a_schema_1_0_source_cannot_support_a_use(tmp_path: Path) -> None:
    example = json.loads((REPO_ROOT / "docs" / "examples" / "example_dataset_manifest.json").read_text(encoding="utf-8"))
    source_id = example["dataset"]["dataset_id"]
    derived, root = make_derived(tmp_path, {"dataset": {"dataset_id": source_id}, "records": [{"record_id": example["images"][0]["image_id"]}]})

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources={source_id: source_view(example)})

    assert "derived-from-source-without-permission-data" in error_codes(result) and result.cleared_uses == ()


# --- a draft stays a draft: warnings until something is in use -----------------------------------------------------


def test_while_everything_is_a_draft_and_no_use_is_requested_the_problems_are_warnings(tmp_path: Path) -> None:
    source = make_source(tmp_path, split="unassigned", permissions=DENIED)
    derived, _root = make_derived(tmp_path, source, split="unassigned")
    derived["dataset"]["permissions"] = {"status": "pending_review", "permitted_uses": [], "commercial_training": "unknown"}

    result = validate_document(derived, provenance_sources=sources_of(source))

    assert result.ok and "derived-from-denied-source" in warning_codes(result)
    assert "warning while everything is unassigned" in next(
        i.message for i in result.report.warnings if i.code == "derived-from-denied-source"
    )


def test_a_derived_dataset_that_records_itself_as_approved_is_already_in_use(tmp_path: Path) -> None:
    """Even with every record unassigned: approval with permitted uses is a claim to use the data."""
    source = make_source(tmp_path, split="unassigned", permissions=DENIED)
    derived, _root = make_derived(tmp_path, source, split="unassigned")
    assert derived["dataset"]["permissions"]["status"] == "approved"

    result = validate_document(derived, provenance_sources=sources_of(source))

    assert not result.ok and "derived-from-denied-source" in error_codes(result)


def test_assigning_a_split_or_requesting_a_use_turns_the_warning_into_an_error(tmp_path: Path) -> None:
    source = make_source(tmp_path, split="unassigned", permissions=DENIED)
    unassigned, _root = make_derived(tmp_path, source, split="unassigned")
    assigned, _root2 = make_derived(tmp_path, source, derived_id="derived-c", split="train")

    by_use = validate_document(unassigned, uses=["training"], dataset_dir=_root, provenance_sources=sources_of(source))
    by_split = validate_document(assigned, provenance_sources=sources_of(source))

    assert "derived-from-denied-source" in error_codes(by_use)
    assert "derived-from-denied-source" in error_codes(by_split)


# --- the same rule in the split generator, the audit and the command line --------------------------------------------


@pytest.mark.parametrize(
    ("label", "source_kwargs", "expected"),
    [
        ("ghana", {"category": "ghana_field", "field_governance": GHANA_UNRESOLVED}, "derived-from-unresolved-field-source"),
        ("denied", {"permissions": DENIED}, "derived-from-denied-source"),
    ],
)
def test_the_split_generator_refuses_an_unfit_source_and_writes_nothing(
    tmp_path: Path, label: str, source_kwargs: dict[str, Any], expected: str, capsys: pytest.CaptureFixture[str]
) -> None:
    source = make_source(tmp_path, split="unassigned", **source_kwargs)
    derived, root = make_derived(tmp_path, source, split="unassigned")
    manifest_path = write_json(tmp_path / "m" / "derived.json", derived)
    source_path = write_json(tmp_path / "m" / "source.json", source)

    with pytest.raises(SplitGenerationError) as caught:
        generate_splits(derived, SplitConfig(seed=1, ratios=RATIOS), dataset_dir=root, provenance_sources=sources_of(source))
    code = generate_dataset_splits.main(
        [str(manifest_path), "--dataset-dir", str(root), "--seed", "1", "--output", str(tmp_path / "s.json"),
         "--source-manifest", str(source_path)]
    )

    assert caught.value.report is not None and expected in caught.value.report.codes()
    assert code == 2 and expected in capsys.readouterr().err and not (tmp_path / "s.json").exists()


@pytest.mark.parametrize(
    ("source_kwargs", "expected"),
    [
        ({"category": "ghana_field", "field_governance": GHANA_UNRESOLVED}, "derived-from-unresolved-field-source"),
        ({"permissions": DENIED}, "derived-from-denied-source"),
    ],
)
def test_the_audit_and_the_validator_agree_about_an_unfit_source(
    tmp_path: Path, source_kwargs: dict[str, Any], expected: str, capsys: pytest.CaptureFixture[str]
) -> None:
    source = make_source(tmp_path, split="unassigned", **source_kwargs)
    derived, root = make_derived(tmp_path, source)
    manifest_path = write_json(tmp_path / "m" / "derived.json", derived)
    source_path = write_json(tmp_path / "m" / "source.json", source)

    audit = audit_manifests([("source", source), ("derived", derived)])
    library = validate_with(derived, root, source)
    code = validate_dataset_manifest.main(
        [str(manifest_path), "--dataset-dir", str(root), "--use", "training", "--source-manifest", str(source_path)]
    )
    captured = capsys.readouterr()

    assert expected in {i.code for i in audit.report.errors} and not audit.passed()
    assert expected in error_codes(library) and code == 2
    assert expected in captured.err


def test_the_cli_says_what_the_source_check_did_and_did_not_verify(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)
    manifest_path = write_json(tmp_path / "m" / "derived.json", derived)
    source_path = write_json(tmp_path / "m" / "source.json", source)

    code = validate_dataset_manifest.main(
        [str(manifest_path), "--dataset-dir", str(root), "--use", "training", "--source-manifest", str(source_path),
         "--source-dataset-dir", f"source-a={tmp_path / 'source-a'}"]
    )

    out = " ".join(capsys.readouterr().out.split())
    assert code == 0 and "recorded permission accepted for: training" in out
    assert "source 'source-a': valid; recorded status approved; permits" in out
    assert "files VERIFIED in" in out and "the licence snapshot among them, exist and match their recorded SHA-256" in out
    assert "does not show that the licence permits a use" in out and "not independently verified" in out


def test_describe_sources_reports_unused_and_unfit_sources(tmp_path: Path) -> None:
    good = make_source(tmp_path, "source-a")
    bad = make_source(tmp_path, "source-z", split="unassigned", permissions=DENIED)
    derived, root = make_derived(tmp_path, bad)
    views = sources_of(good, bad)

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=views)
    lines = describe_sources(views, result.source_assessments)

    assert "no record derives from it, so it was not assessed" in lines[0] and lines[0].startswith("source 'source-a'")
    assert "recorded status denied" in lines[1] and "files NOT verified (no --source-dataset-dir source-z=DIRECTORY" in lines[1]


# --- chains, loops, views without a manifest, and cost ---------------------------------------------------------------


def test_governance_is_inherited_through_a_chain_of_derivations(tmp_path: Path) -> None:
    a = make_source(tmp_path, "chain-a", split="unassigned", permissions=DENIED)
    b, _b_root = make_derived(tmp_path, a, derived_id="chain-b", split="unassigned")
    c, c_root = make_derived(tmp_path, b, derived_id="chain-c", split="train")

    result = validate_with(c, c_root, a, b)

    assert "derived-from-invalid-source" in error_codes(result) and result.cleared_uses == ()
    assert "derived-from-denied-source" in next(i.message for i in result.report.errors if i.code == "derived-from-invalid-source")


def test_a_chain_that_loops_back_is_refused_and_terminates(tmp_path: Path) -> None:
    a, a_root = make_derived(tmp_path, make_source(tmp_path, "loop-b"), derived_id="loop-a")
    b = make_source(tmp_path, "loop-b")
    b["records"][0]["provenance"] = {"derived_from": [{"dataset_id": "loop-a", "record_id": a["records"][0]["record_id"]}]}

    result = validate_document(a, dataset_dir=a_root, uses=["training"], provenance_sources=sources_of(a, b))

    assert "derived-from-invalid-source" in error_codes(result)
    assert "derivation-cycle" in next(i.message for i in result.report.errors if i.code == "derived-from-invalid-source")
    assert result.cleared_uses == ()


def test_a_view_that_carries_no_manifest_cannot_be_used_as_a_source(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)
    structure_only = dataclasses.replace(build_view(source, "1.1"), document=None)

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources={"source-a": structure_only})

    assert "derived-source-unassessed" in error_codes(result) and result.cleared_uses == ()
    assert assess_source(structure_only, {}).assessed is False


def test_a_source_is_validated_once_however_many_records_derive_from_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)
    for record in derived["records"][1:4]:
        record["provenance"] = {"derived_from": [{"dataset_id": "source-a", "record_id": source["records"][1]["record_id"]}]}
    calls = []
    real = validation_module.validate_document
    monkeypatch.setattr(validation_module, "validate_document", lambda *a, **k: calls.append(k.get("_assessing")) or real(*a, **k))

    result = validate_with(derived, root, source)

    assert result.ok and len(calls) == 1  # the top-level call is not counted: only the source's own validation is


# --- the protections that were already there still work -------------------------------------------------------------------


def test_restricted_sources_are_still_refused_once_and_without_noise(tmp_path: Path) -> None:
    source = make_source(tmp_path, category="umat_practical")
    derived, root = make_derived(tmp_path, source)

    result = validate_with(derived, root, source)

    assert "derived-from-restricted-source" in error_codes(result)
    assert not {c for c in error_codes(result) if c.startswith("derived-from-") and c != "derived-from-restricted-source"}


def test_a_source_that_names_itself_after_restricted_material_is_still_refused(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    source["dataset"]["name"] = "UMaT practical photographs"
    derived, root = make_derived(tmp_path, source)

    assert "derived-from-restricted-source" in error_codes(validate_with(derived, root, source))


def test_evaluation_sources_are_still_refused_with_their_own_codes_and_not_repeated(tmp_path: Path) -> None:
    source = make_source(tmp_path, split="independent_evaluation")
    source["dataset"]["permissions"]["permitted_uses"] = ["independent_evaluation"]
    derived, root = make_derived(tmp_path, source)

    result = validate_with(derived, root, source)

    assert "derived-from-evaluation-record" in error_codes(result)
    assert "derived-from-source-use-not-permitted" not in error_codes(result)


def test_an_evaluation_record_derived_from_ordinary_data_is_still_refused(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source, split="independent_evaluation")
    derived["dataset"]["permissions"]["permitted_uses"] = ["independent_evaluation"]

    result = validate_document(derived, dataset_dir=root, provenance_sources=sources_of(source))

    assert "derived-into-evaluation" in error_codes(result)


def test_a_derivative_and_its_source_must_still_share_a_split(tmp_path: Path) -> None:
    source = make_source(tmp_path, split="test")
    derived, root = make_derived(tmp_path, source, split="train")

    assert "derivation-crosses-splits" in error_codes(validate_with(derived, root, source))


def test_a_source_that_was_not_supplied_still_fails_closed(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)

    result = validate_document(derived, dataset_dir=root, uses=["training"])

    assert "derived-source-unverified" in error_codes(result) and result.cleared_uses == ()


def test_load_source_views_keeps_the_manifest_so_the_source_can_be_validated(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    path = write_json(tmp_path / "m" / "source.json", source)

    views = load_source_views([path])

    assert views["source-a"].document == source
    assert assess_source(views["source-a"], views).valid
