"""Commercial-training permission is inherited across the whole chain of derivations.

The gap closed here: a derived dataset could be cleared for commercial training, or record commercial
training as permitted, while a dataset it was derived from denied commercial training or never recorded it.
Now every source, all the way down the chain, must itself record commercial training as permitted, with a
meaningful basis, whenever commercial training is requested for the derived dataset, is needed by a dataset
derived from it, or is recorded as permitted by it (a derived dataset cannot grant a broader commercial
permission than its sources). The rule is the same in the validator, the split generator and the audit.

All data is synthetic. A recorded commercial permission and its basis are the manifest's own declaration: the
tools check that they are present, consistent and meaningful, not that any licence actually allows the use.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import (
    SplitConfig,
    SplitGenerationError,
    audit_manifests,
    describe_sources,
    generate_splits,
    source_view,
    validate_document,
)
from app.dataset_manifest.validation import ValidationResult
from dataset_builders import build_dataset
from scripts import generate_dataset_splits, validate_dataset_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
RATIOS = {"train": 0.7, "validation": 0.15, "test": 0.15}
BASIS = "Clause 4.2 of the synthetic licence permits commercial training."
DENIED = "derived-from-source-commercial-training-denied"
NOT_PERMITTED = "derived-from-source-commercial-training-not-permitted"
UNSUPPORTED = "derived-from-source-commercial-training-unsupported"
COMMERCIAL_CODES = {DENIED, NOT_PERMITTED, UNSUPPORTED}


def make_dataset(tmp_path: Path, dataset_id: str, split: str = "train", sample_count: int = 4) -> dict[str, Any]:
    """A synthetic dataset whose files are written to tmp_path/<dataset_id>. Commercial training is 'unknown'."""
    manifest = build_dataset(tmp_path / dataset_id, sample_count=sample_count, images_per_sample=2, dataset_id=dataset_id)
    for record in manifest["records"]:
        record["split"] = split
    return manifest


def derive_from(derived: dict[str, Any], source: dict[str, Any], index: int = 0) -> dict[str, Any]:
    derived["records"][index]["provenance"] = {
        "derived_from": [{"dataset_id": source["dataset"]["dataset_id"], "record_id": source["records"][index]["record_id"]}]
    }
    return derived


def commercial(manifest: dict[str, Any], recorded: str = "permitted", basis: str | None = BASIS) -> dict[str, Any]:
    permissions = manifest["dataset"]["permissions"]
    permissions["commercial_training"] = recorded
    permissions.pop("commercial_training_basis", None)
    if basis is not None:
        permissions["commercial_training_basis"] = basis
    return manifest


def bound(tmp_path: Path, *manifests: dict[str, Any]) -> dict[str, Any]:
    """Source views bound to their directories, so their files are verified as well."""
    return {m["dataset"]["dataset_id"]: source_view(m, dataset_dir=tmp_path / m["dataset"]["dataset_id"]) for m in manifests}


def check(tmp_path: Path, derived: dict[str, Any], *sources: dict[str, Any], require: bool = True) -> ValidationResult:
    return validate_document(
        derived,
        dataset_dir=tmp_path / derived["dataset"]["dataset_id"],
        uses=["training"],
        require_commercial_training=require,
        provenance_sources=bound(tmp_path, *sources),
    )


def error_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.errors}


def message(result: ValidationResult, code: str) -> str:
    return next(issue.message for issue in result.report.issues if issue.code == code)


def write_json(path: Path, document: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# --- direct sources ------------------------------------------------------------------------------------------------


def test_an_eligible_source_that_permits_commercial_training_can_be_used_for_it(tmp_path: Path) -> None:
    source = commercial(make_dataset(tmp_path, "source-a"))
    derived = commercial(derive_from(make_dataset(tmp_path, "derived-b"), source))

    result = check(tmp_path, derived, source)

    assert result.ok and result.commercial_training_cleared and result.cleared_uses == ("training",), result.report.format()
    assert result.source_assessments["source-a"].commercial_training == "permitted"


def test_a_source_that_denies_commercial_training_blocks_it(tmp_path: Path) -> None:
    source = commercial(make_dataset(tmp_path, "source-a"), "not_permitted", basis=None)
    derived = commercial(derive_from(make_dataset(tmp_path, "derived-b"), source))

    result = check(tmp_path, derived, source)

    assert DENIED in error_codes(result) and not result.commercial_training_cleared and result.cleared_uses == ()
    text = message(result, DENIED)
    assert "commercial training was requested for this dataset" in text and "'not_permitted'" in text


def test_a_source_that_does_not_record_commercial_permission_blocks_it(tmp_path: Path) -> None:
    source = make_dataset(tmp_path, "source-a")  # commercial training 'unknown'
    derived = commercial(derive_from(make_dataset(tmp_path, "derived-b"), source))

    result = check(tmp_path, derived, source)

    assert error_codes(result) == {NOT_PERMITTED} and not result.commercial_training_cleared
    assert "'unknown' is not permission" in message(result, NOT_PERMITTED)


@pytest.mark.parametrize(
    ("basis", "expected", "own_code"),
    [
        (None, "records no commercial_training_basis", "commercial-training-basis-missing"),
        ("n/a", "placeholder or too thin", "commercial-training-basis-not-meaningful"),
        ("see licence", "placeholder or too thin", "commercial-training-basis-not-meaningful"),
    ],
    ids=["missing-basis", "placeholder-basis", "see-licence"],
)
def test_a_source_whose_commercial_permission_has_no_real_basis_blocks_it(
    tmp_path: Path, basis: str | None, expected: str, own_code: str
) -> None:
    source = commercial(make_dataset(tmp_path, "source-a"), basis=basis)
    assert own_code in error_codes(validate_document(source))  # the source is itself invalid
    derived = commercial(derive_from(make_dataset(tmp_path, "derived-b"), source))

    result = check(tmp_path, derived, source)

    assert {UNSUPPORTED, "derived-from-invalid-source"} <= error_codes(result) and not result.commercial_training_cleared
    assert expected in message(result, UNSUPPORTED) and own_code in message(result, "derived-from-invalid-source")


def test_a_source_that_permits_commercial_training_but_not_training_is_ineligible(tmp_path: Path) -> None:
    source = commercial(make_dataset(tmp_path, "source-a", split="unassigned"))
    source["dataset"]["permissions"]["permitted_uses"] = ["validation", "testing"]
    derived = commercial(derive_from(make_dataset(tmp_path, "derived-b", split="unassigned"), source))

    result = check(tmp_path, derived, source)

    assert {"derived-from-source-use-not-permitted", "derived-from-invalid-source"} <= error_codes(result)
    assert "'training'" in message(result, "derived-from-source-use-not-permitted")
    assert not result.commercial_training_cleared


def test_a_source_that_is_otherwise_ineligible_still_blocks_it(tmp_path: Path) -> None:
    source = commercial(make_dataset(tmp_path, "source-a", split="unassigned"), "not_permitted", basis=None)
    source["dataset"]["permissions"].update(status="denied", permitted_uses=[])
    derived = commercial(derive_from(make_dataset(tmp_path, "derived-b"), source))

    result = check(tmp_path, derived, source)

    assert {"derived-from-denied-source", DENIED} <= error_codes(result) and not result.commercial_training_cleared


def test_a_commercial_request_alone_makes_a_drafts_source_problems_errors(tmp_path: Path) -> None:
    """A draft (nothing assigned, not approved) only warns about its sources, unless commercial training is requested."""
    source = commercial(make_dataset(tmp_path, "source-a", split="unassigned"), "not_permitted", basis=None)
    derived = derive_from(make_dataset(tmp_path, "derived-b", split="unassigned"), source)
    derived["dataset"]["permissions"] = {"status": "pending_review", "permitted_uses": [], "commercial_training": "unknown"}

    draft = validate_document(derived, provenance_sources=bound(tmp_path, source))
    requested = validate_document(
        derived, dataset_dir=tmp_path / "derived-b", require_commercial_training=True, provenance_sources=bound(tmp_path, source)
    )

    assert draft.ok and not COMMERCIAL_CODES & {i.code for i in draft.report.issues}
    assert DENIED in error_codes(requested) and not requested.commercial_training_cleared


def test_the_source_is_checked_even_when_the_derived_dataset_does_not_record_commercial_permission(tmp_path: Path) -> None:
    """The request alone makes the sources answerable: both the dataset and its source are reported."""
    source = make_dataset(tmp_path, "source-a")
    derived = derive_from(make_dataset(tmp_path, "derived-b"), source)

    result = check(tmp_path, derived, source)

    assert {"commercial-training-not-permitted", NOT_PERMITTED} <= error_codes(result)


# --- a derived dataset cannot grant a broader commercial permission than its sources -------------------------------


def test_recording_commercial_permission_over_a_source_that_does_not_permit_it_is_refused(tmp_path: Path) -> None:
    """No commercial clearance is requested: the derived manifest's own claim is what is inconsistent."""
    source = commercial(make_dataset(tmp_path, "source-a"), "not_permitted", basis=None)
    derived = commercial(derive_from(make_dataset(tmp_path, "derived-b"), source))

    result = check(tmp_path, derived, source, require=False)

    assert DENIED in error_codes(result) and result.cleared_uses == ()
    assert "this dataset records commercial training as permitted" in message(result, DENIED)


def test_an_intermediate_dataset_cannot_launder_commercial_permission(tmp_path: Path) -> None:
    """mid-b records commercial training as permitted, but base-a, which it was made from, does not."""
    base = make_dataset(tmp_path, "base-a")
    mid = commercial(derive_from(make_dataset(tmp_path, "mid-b"), base))
    top = commercial(derive_from(make_dataset(tmp_path, "top-c"), mid))

    as_validated = validate_document(mid, provenance_sources=bound(tmp_path, base))
    result = check(tmp_path, top, base, mid)

    assert NOT_PERMITTED in error_codes(as_validated)  # mid-b on its own is already refused
    assert "derived-from-invalid-source" in error_codes(result) and not result.commercial_training_cleared
    assert NOT_PERMITTED in message(result, "derived-from-invalid-source")


# --- multi-level chains ------------------------------------------------------------------------------------------


def chain(tmp_path: Path, base_recorded: str = "permitted", base_basis: str | None = BASIS) -> tuple[dict[str, Any], ...]:
    """top-c <- mid-b <- base-a, top-c and mid-b recording commercial training as permitted."""
    base = commercial(make_dataset(tmp_path, "base-a"), base_recorded, base_basis)
    mid = commercial(derive_from(make_dataset(tmp_path, "mid-b"), base))
    top = commercial(derive_from(make_dataset(tmp_path, "top-c"), mid))
    return base, mid, top


def test_a_chain_in_which_every_dataset_permits_commercial_training_is_accepted(tmp_path: Path) -> None:
    base, mid, top = chain(tmp_path)

    result = check(tmp_path, top, base, mid)

    assert result.ok and result.commercial_training_cleared, result.report.format()
    assert {a.commercial_training for a in result.source_assessments.values()} == {"permitted"}


@pytest.mark.parametrize(
    ("recorded", "basis", "expected"),
    [("not_permitted", None, DENIED), ("unknown", None, NOT_PERMITTED), ("permitted", None, UNSUPPORTED)],
    ids=["denied-at-the-base", "unknown-at-the-base", "no-basis-at-the-base"],
)
def test_a_problem_at_the_bottom_of_the_chain_blocks_the_top(
    tmp_path: Path, recorded: str, basis: str | None, expected: str
) -> None:
    base, mid, top = chain(tmp_path, recorded, basis)

    result = check(tmp_path, top, base, mid)

    assert "derived-from-invalid-source" in error_codes(result) and not result.commercial_training_cleared
    text = message(result, "derived-from-invalid-source")
    assert "'mid-b'" in text and expected in text


def test_the_need_is_inherited_even_when_the_intermediate_dataset_records_nothing_about_its_source(tmp_path: Path) -> None:
    """mid-b's own validation passes (it is not asked about commercial training), but the top's request reaches base-a."""
    base = commercial(make_dataset(tmp_path, "base-a"), "not_permitted", basis=None)
    mid = derive_from(make_dataset(tmp_path, "mid-b"), base)  # records commercial training as 'unknown'
    top = commercial(derive_from(make_dataset(tmp_path, "top-c"), mid))
    assert validate_document(mid, dataset_dir=tmp_path / "mid-b", uses=["training"], provenance_sources=bound(tmp_path, base)).ok

    result = check(tmp_path, top, base, mid)

    assert NOT_PERMITTED in error_codes(result)  # mid-b itself
    assert "derived-from-invalid-source" in error_codes(result)
    inherited = message(result, "derived-from-invalid-source")
    assert DENIED in inherited and "is to be used for commercial training" in inherited


# --- the same rule in the split generator and the audit --------------------------------------------------------------


def test_the_split_generator_refuses_commercial_training_over_a_source_that_denies_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = commercial(make_dataset(tmp_path, "source-a", split="unassigned"), "not_permitted", basis=None)
    derived = commercial(derive_from(make_dataset(tmp_path, "derived-b", split="unassigned", sample_count=30), source))
    derived_path, source_path = write_json(tmp_path / "m" / "derived.json", derived), write_json(tmp_path / "m" / "source.json", source)
    root = tmp_path / "derived-b"

    with pytest.raises(SplitGenerationError) as caught:
        generate_splits(
            derived, SplitConfig(seed=1, ratios=RATIOS, require_commercial_training=True),
            dataset_dir=root, provenance_sources=bound(tmp_path, source),
        )
    code = generate_dataset_splits.main(
        [str(derived_path), "--dataset-dir", str(root), "--seed", "1", "--output", str(tmp_path / "s.json"),
         "--require-commercial-training", "--source-manifest", str(source_path),
         "--source-dataset-dir", f"source-a={tmp_path / 'source-a'}"]
    )

    assert caught.value.report is not None and DENIED in caught.value.report.codes()
    assert code == 2 and DENIED in capsys.readouterr().err and not (tmp_path / "s.json").exists()


def test_the_split_generator_accepts_an_eligible_commercial_chain(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = commercial(make_dataset(tmp_path, "source-a", split="unassigned"))
    derived = commercial(derive_from(make_dataset(tmp_path, "derived-b", split="unassigned", sample_count=30), source))
    derived_path, source_path = write_json(tmp_path / "m" / "derived.json", derived), write_json(tmp_path / "m" / "source.json", source)

    code = generate_dataset_splits.main(
        [str(derived_path), "--dataset-dir", str(tmp_path / "derived-b"), "--seed", "1", "--output", str(tmp_path / "s.json"),
         "--require-commercial-training", "--source-manifest", str(source_path),
         "--source-dataset-dir", f"source-a={tmp_path / 'source-a'}"]
    )

    out = " ".join(capsys.readouterr().out.split())
    assert code == 0 and (tmp_path / "s.json").is_file() and "commercial training recorded as permitted" in out


def test_the_audit_refuses_a_derived_dataset_that_records_more_than_its_source_permits(tmp_path: Path) -> None:
    source = commercial(make_dataset(tmp_path, "source-a"), "not_permitted", basis=None)
    derived = commercial(derive_from(make_dataset(tmp_path, "derived-b"), source))
    directories = {"source": tmp_path / "source-a", "derived": tmp_path / "derived-b"}

    audit = audit_manifests([("source", source), ("derived", derived)], dataset_dirs=directories)
    eligible = audit_manifests(
        [("source", commercial(source)), ("derived", derived)], dataset_dirs=directories
    )

    assert DENIED in {i.code for i in audit.report.errors} and not audit.passed()
    assert not COMMERCIAL_CODES & {i.code for i in eligible.report.issues}


def test_the_validator_cli_reports_the_source_and_refuses(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_dataset(tmp_path, "source-a")
    derived = commercial(derive_from(make_dataset(tmp_path, "derived-b"), source))
    derived_path, source_path = write_json(tmp_path / "m" / "derived.json", derived), write_json(tmp_path / "m" / "source.json", source)

    code = validate_dataset_manifest.main(
        [str(derived_path), "--dataset-dir", str(tmp_path / "derived-b"), "--use", "training",
         "--require-commercial-training", "--source-manifest", str(source_path),
         "--source-dataset-dir", f"source-a={tmp_path / 'source-a'}"]
    )

    err = " ".join(capsys.readouterr().err.split())
    assert code == 2 and NOT_PERMITTED in err and "commercial training recorded as unknown" in err


# --- what does not change -----------------------------------------------------------------------------------------


def test_ordinary_training_is_unaffected_by_a_source_that_denies_commercial_training(tmp_path: Path) -> None:
    source = commercial(make_dataset(tmp_path, "source-a"), "not_permitted", basis=None)
    derived = derive_from(make_dataset(tmp_path, "derived-b"), source)  # records 'unknown', asks for no commercial use

    result = check(tmp_path, derived, source, require=False)

    assert result.ok and result.cleared_uses == ("training",) and not result.commercial_training_cleared
    assert not COMMERCIAL_CODES & {i.code for i in result.report.issues}


def test_a_restricted_source_is_still_refused_once_without_commercial_noise(tmp_path: Path) -> None:
    source = make_dataset(tmp_path, "source-a")
    source["dataset"]["category"] = "umat_practical"
    derived = commercial(derive_from(make_dataset(tmp_path, "derived-b"), source))

    result = check(tmp_path, derived, source)

    assert {c for c in error_codes(result) if c.startswith("derived-from-")} == {"derived-from-restricted-source"}


def test_a_schema_1_0_source_is_refused_as_before(tmp_path: Path) -> None:
    example = json.loads((REPO_ROOT / "docs" / "examples" / "example_dataset_manifest.json").read_text(encoding="utf-8"))
    source_id = example["dataset"]["dataset_id"]
    derived = commercial(make_dataset(tmp_path, "derived-b"))
    derived["records"][0]["provenance"] = {"derived_from": [{"dataset_id": source_id, "record_id": example["images"][0]["image_id"]}]}

    result = validate_document(
        derived, dataset_dir=tmp_path / "derived-b", uses=["training"], require_commercial_training=True,
        provenance_sources={source_id: source_view(example)},
    )

    assert "derived-from-source-without-permission-data" in error_codes(result) and not result.commercial_training_cleared
    assert not COMMERCIAL_CODES & error_codes(result)


def test_the_description_line_says_what_the_source_records(tmp_path: Path) -> None:
    source = commercial(make_dataset(tmp_path, "source-a"), "not_permitted", basis=None)
    derived = derive_from(make_dataset(tmp_path, "derived-b"), source)
    views = bound(tmp_path, source)

    result = validate_document(derived, dataset_dir=tmp_path / "derived-b", uses=["training"], provenance_sources=views)
    line = describe_sources(views, result.source_assessments)[0]

    assert "commercial training recorded as not_permitted" in line and "not independently verified" in line


def test_the_documentation_states_the_inheritance_rule() -> None:
    text = " ".join((REPO_ROOT / "docs" / "dataset_workflow.md").read_text(encoding="utf-8").split())

    for code in sorted(COMMERCIAL_CODES):
        assert code in text, code
    assert "cannot grant a broader commercial permission than its sources" in text
