from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import (
    SplitConfig,
    SplitGenerationError,
    SplitResult,
    apply_splits,
    audit_manifests,
    dumps_canonical,
    generate_splits,
    validate_document,
)
from app.dataset_manifest.splits import ALGORITHM, UNLABELLED
from dataset_builders import build_dataset, clone, make_record, record_by_id

RATIOS = {"train": 0.7, "validation": 0.15, "test": 0.15}


_BIG_ROOT: list[Path] = []


def make_splits(manifest: Any, cfg: SplitConfig, **kwargs: Any) -> SplitResult:
    """generate_splits with the dataset directory of the shared `big` dataset by default."""
    kwargs.setdefault("dataset_dir", _BIG_ROOT[0] if _BIG_ROOT else None)
    return generate_splits(manifest, cfg, **kwargs)

def config(seed: int = 1, **overrides: Any) -> SplitConfig:
    ratios = overrides.pop("ratios", RATIOS)
    return SplitConfig(seed=seed, ratios=ratios, **overrides)


def samples_by_split(result: SplitResult) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = {}
    for record_id, split in result.assignments.items():
        grouped.setdefault(split, set()).add(result.sample_of_record[record_id])
    return grouped


@pytest.fixture(scope="module")
def big(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """60 synthetic samples x 3 images, 3 categories. Treat as read-only; clone before editing."""
    root = tmp_path_factory.mktemp("big")
    _BIG_ROOT[:] = [root]
    return build_dataset(root, sample_count=60, images_per_sample=3)


# --- grouping by physical sample ------------------------------------------------------


@pytest.mark.parametrize("seed", range(10))
def test_no_physical_sample_is_ever_split(big: dict[str, Any], seed: int) -> None:
    result = make_splits(big, config(seed))

    split_of_sample: dict[str, set[str]] = {}
    for record_id, split in result.assignments.items():
        split_of_sample.setdefault(result.sample_of_record[record_id], set()).add(split)
    assert len(result.assignments) == 180
    assert all(len(splits) == 1 for splits in split_of_sample.values())


def test_each_sample_occupies_exactly_one_split_in_the_summary(big: dict[str, Any]) -> None:
    result = make_splits(big, config(3))

    per_split = result.summary["per_split"]
    assert sum(entry["physical_samples"] for entry in per_split.values()) == 60
    assert sum(entry["records"] for entry in per_split.values()) == 180
    assert result.summary["groups"] == 60


def test_all_requested_splits_are_filled_close_to_the_ratios(big: dict[str, Any]) -> None:
    result = make_splits(big, config(5))

    for name, ratio in RATIOS.items():
        share = result.summary["per_split"][name]["records"] / 180
        assert abs(share - ratio) <= 0.05, (name, share)


def test_every_category_appears_in_every_split(big: dict[str, Any]) -> None:
    result = make_splits(big, config(5))

    for name in RATIOS:
        categories = result.summary["per_split"][name]["categories"]
        assert set(categories) == {
            "synthetic-category-a",
            "synthetic-category-b",
            "synthetic-category-c",
        }
    # Small splits are warned about (see the split-quality tests), but no category is missing.
    assert not any("has no physical samples" in warning for warning in result.warnings)


def test_only_ordinary_splits_are_ever_assigned(big: dict[str, Any]) -> None:
    result = make_splits(big, config(2))

    assert set(result.assignments.values()) == {"train", "validation", "test"}


def test_two_way_split_is_supported(big: dict[str, Any]) -> None:
    result = make_splits(big, config(2, ratios={"train": 0.8, "test": 0.2}))

    assert set(result.assignments.values()) == {"train", "test"}


def test_unrequested_split_needs_no_permission(big: dict[str, Any]) -> None:
    manifest = clone(big)
    manifest["dataset"]["permissions"]["permitted_uses"] = ["training", "testing"]

    result = make_splits(manifest, config(2, ratios={"train": 0.8, "test": 0.2}))

    assert "validation" not in result.assignments.values()


# --- reproducibility ----------------------------------------------------------------


def test_same_inputs_give_identical_output(big: dict[str, Any]) -> None:
    first = make_splits(big, config(11), manifest_sha256="a" * 64)
    second = make_splits(clone(big), config(11), manifest_sha256="a" * 64)

    assert first.to_document() == second.to_document()
    assert dumps_canonical(first.to_document()) == dumps_canonical(second.to_document())


def test_record_order_in_the_manifest_does_not_matter(big: dict[str, Any]) -> None:
    reordered = clone(big)
    reordered["records"].reverse()

    assert make_splits(big, config(11)).assignments == make_splits(
        reordered, config(11)
    ).assignments


def test_a_different_seed_gives_a_different_split(big: dict[str, Any]) -> None:
    assert make_splits(big, config(1)).assignments != make_splits(big, config(2)).assignments


def test_unrelated_manifest_edits_do_not_move_samples(big: dict[str, Any]) -> None:
    edited = clone(big)
    edited["dataset"]["description"] = "Edited description."
    record_by_id(edited, "rec-000-0")["acquisition"] = {"lighting": "changed"}

    assert make_splits(big, config(4)).assignments == make_splits(edited, config(4)).assignments


def test_assignments_are_pinned_for_a_known_seed(tmp_path: Path) -> None:
    """Regression pin: if this fails the algorithm changed, and every recorded split is stale."""
    manifest = build_dataset(tmp_path / "d", sample_count=12, images_per_sample=1)

    result = make_splits(
        manifest,
        config(2026, ratios={"train": 0.6, "validation": 0.2, "test": 0.2}),
        dataset_dir=tmp_path / "d",
    )

    by_sample = {result.sample_of_record[k]: v for k, v in result.assignments.items()}
    assert ALGORITHM == "sha256-ordered-stratified-greedy-v1"
    assert by_sample == {
        "sample-000": "train",
        "sample-001": "validation",
        "sample-002": "train",
        "sample-003": "test",
        "sample-004": "train",
        "sample-005": "test",
        "sample-006": "train",
        "sample-007": "test",
        "sample-008": "validation",
        "sample-009": "validation",
        "sample-010": "train",
        "sample-011": "train",
    }


def test_split_document_records_how_it_was_made(big: dict[str, Any]) -> None:
    result = make_splits(big, config(9, group_capture_sessions=False), manifest_sha256="b" * 64)

    document = result.to_document()

    assert document["algorithm"] == ALGORITHM
    assert document["dataset_id"] == "synthetic-fixture"
    assert document["manifest_sha256"] == "b" * 64
    assert document["seed"] == 9
    assert document["ratios"] == RATIOS
    assert document["group_capture_sessions"] is False
    assert document["sample_identity_basis"] == "recorded_by_collector"
    assert document["permissions_at_generation"]["status"] == "approved"
    ids = [item["record_id"] for item in document["assignments"]]
    assert ids == sorted(ids)
    assert not any("time" in key or "date" in key for key in document)


# --- capture sessions ---------------------------------------------------------------


def _straddling_sessions(result: SplitResult, manifest: dict[str, Any]) -> int:
    splits_of_session: dict[str, set[str]] = {}
    for record in manifest["records"]:
        session = record.get("capture_session_id")
        if session is not None:
            splits_of_session.setdefault(session, set()).add(result.assignments[record["record_id"]])
    return sum(1 for splits in splits_of_session.values() if len(splits) > 1)


def test_sessions_can_straddle_splits_unless_grouped(tmp_path: Path) -> None:
    manifest = build_dataset(
        tmp_path / "d", sample_count=30, images_per_sample=2, sessions="shared_pairs"
    )

    here = tmp_path / "d"
    plain = [
        _straddling_sessions(make_splits(manifest, config(s), dataset_dir=here), manifest) for s in range(10)
    ]
    grouped = [
        _straddling_sessions(
            make_splits(manifest, config(s, group_capture_sessions=True), dataset_dir=here), manifest
        )
        for s in range(10)
    ]

    assert max(plain) > 0
    assert grouped == [0] * 10


def test_grouping_by_session_still_never_splits_a_physical_sample(tmp_path: Path) -> None:
    manifest = build_dataset(
        tmp_path / "d", sample_count=30, images_per_sample=2, sessions="shared_pairs"
    )

    result = make_splits(manifest, config(3, group_capture_sessions=True), dataset_dir=tmp_path / "d")

    assert result.summary["groups"] == 15
    for samples in samples_by_split(result).values():
        for index in range(0, 30, 2):
            pair = {f"sample-{index:03d}", f"sample-{index + 1:03d}"}
            assert pair <= samples or not (pair & samples)


def test_session_grouping_changes_nothing_when_no_sessions_are_recorded(tmp_path: Path) -> None:
    manifest = build_dataset(tmp_path / "d", sample_count=20, images_per_sample=2, sessions="none")

    assert (
        make_splits(manifest, config(3), dataset_dir=tmp_path / "d").assignments
        == make_splits(
            manifest, config(3, group_capture_sessions=True), dataset_dir=tmp_path / "d"
        ).assignments
    )


# --- labels and strata --------------------------------------------------------------


def test_a_category_missing_from_a_split_is_warned_about(tmp_path: Path) -> None:
    manifest = build_dataset(tmp_path / "d", sample_count=30, images_per_sample=1)
    extra, content, path = make_record("rec-rare", "sample-rare", category="rare-category")
    (tmp_path / "d" / path).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "d" / path).write_bytes(content)
    manifest["records"].append(extra)
    manifest["dataset"]["label_vocabulary"]["categories"].append("rare-category")

    result = make_splits(manifest, config(1), dataset_dir=tmp_path / "d")

    assert any("'rare-category'" in warning and "validation" in warning for warning in result.warnings)


def test_unlabelled_records_are_split_as_their_own_group(tmp_path: Path) -> None:
    manifest = build_dataset(tmp_path / "d", sample_count=20, images_per_sample=2)
    del manifest["dataset"]["label_vocabulary"]
    for record in manifest["records"]:
        del record["label"]

    result = make_splits(manifest, config(1), dataset_dir=tmp_path / "d")

    categories = {
        name for entry in result.summary["per_split"].values() for name in entry["categories"]
    }
    assert categories == {UNLABELLED}
    assert set(result.assignments.values()) == {"train", "validation", "test"}


# --- refusals -----------------------------------------------------------------------


def _refused(manifest: Any, cfg: SplitConfig, **kwargs: Any) -> SplitGenerationError:
    with pytest.raises(SplitGenerationError) as caught:
        make_splits(manifest, cfg, **kwargs)
    return caught.value


def test_umat_practical_data_is_never_split(big: dict[str, Any]) -> None:
    manifest = clone(big)
    manifest["dataset"]["category"] = "umat_practical"
    manifest["dataset"]["permissions"]["permitted_uses"] = ["independent_evaluation"]
    for record in manifest["records"]:
        record["split"] = "independent_evaluation"

    error = _refused(manifest, config())

    assert "restricted UMaT/MN273 material" in str(error)
    assert error.report is not None and "restricted-category" in error.report.codes()


def test_independent_evaluation_records_are_never_split(big: dict[str, Any]) -> None:
    manifest = clone(big)
    manifest["dataset"]["permissions"]["permitted_uses"] = ["independent_evaluation"]
    for record in manifest["records"]:
        record["split"] = "independent_evaluation"

    assert "independent evaluation" in str(_refused(manifest, config()))


def test_independent_evaluation_records_survive_allow_resplit(big: dict[str, Any]) -> None:
    manifest = clone(big)
    manifest["dataset"]["permissions"]["permitted_uses"] = ["independent_evaluation"]
    for record in manifest["records"]:
        record["split"] = "independent_evaluation"

    _refused(manifest, config(allow_resplit=True))

    assert all(record["split"] == "independent_evaluation" for record in manifest["records"])


def test_schema_1_0_manifests_are_refused() -> None:
    manifest = {"schema_version": "1.0", "dataset": {}, "images": []}

    assert "schema 1.1" in str(_refused(manifest, config()))


@pytest.mark.parametrize("not_a_manifest", [None, [], "text"])
def test_non_manifests_are_refused(not_a_manifest: object) -> None:
    assert "schema 1.1" in str(_refused(not_a_manifest, config()))


@pytest.mark.parametrize("status", ["pending_review", "unknown", "denied"])
def test_a_dataset_that_is_not_approved_is_refused(big: dict[str, Any], status: str) -> None:
    manifest = clone(big)
    manifest["dataset"]["permissions"].update(status=status, permitted_uses=[])

    error = _refused(manifest, config())

    assert error.report is not None and error.report.codes() == {"use-not-permitted"}
    assert status in error.report.errors[0].message


@pytest.mark.parametrize(
    ("permitted", "ratios", "missing"),
    [
        (["validation", "testing"], RATIOS, "training"),
        (["training", "testing"], RATIOS, "validation"),
        (["training", "validation"], RATIOS, "testing"),
    ],
)
def test_every_requested_split_needs_its_permitted_use(
    big: dict[str, Any], permitted: list[str], ratios: dict[str, float], missing: str
) -> None:
    manifest = clone(big)
    manifest["dataset"]["permissions"]["permitted_uses"] = permitted

    error = _refused(manifest, config(ratios=ratios))

    assert error.report is not None
    assert [issue.code for issue in error.report.errors] == ["use-not-permitted"]
    assert missing in error.report.errors[0].message


@pytest.mark.parametrize("commercial", ["unknown", "not_permitted"])
def test_commercial_training_can_be_required(big: dict[str, Any], commercial: str) -> None:
    manifest = clone(big)
    manifest["dataset"]["permissions"]["commercial_training"] = commercial

    error = _refused(manifest, config(require_commercial_training=True))

    assert error.report is not None
    assert error.report.codes() == {"commercial-training-not-permitted"}
    assert make_splits(manifest, config()).assignments  # not required by default


def test_commercial_training_permitted_passes_when_required(big: dict[str, Any]) -> None:
    manifest = clone(big)
    manifest["dataset"]["permissions"]["commercial_training"] = "permitted"
    manifest["dataset"]["permissions"]["commercial_training_basis"] = "Synthetic fixture."

    assert make_splits(manifest, config(require_commercial_training=True)).assignments


def test_existing_assignments_are_not_overwritten(big: dict[str, Any]) -> None:
    manifest = clone(big)
    for record in manifest["records"]:
        record["split"] = "train"

    assert "already have a split" in str(_refused(manifest, config()))


def test_allow_resplit_discards_old_assignments(big: dict[str, Any]) -> None:
    manifest = clone(big)
    for record in manifest["records"]:
        record["split"] = "train"

    resplit = make_splits(manifest, config(8, allow_resplit=True))

    assert resplit.assignments == make_splits(big, config(8)).assignments
    assert all(record["split"] == "train" for record in manifest["records"])  # input untouched


def test_assumed_sample_identity_is_refused_unless_accepted(big: dict[str, Any]) -> None:
    manifest = clone(big)
    manifest["dataset"]["sample_identity"] = {"basis": "assumed_each_image_distinct"}

    assert "assumed_each_image_distinct" in str(_refused(manifest, config()))
    accepted = make_splits(
        manifest, config(unverified_sample_identity_reason="synthetic test fixture")
    )
    assert accepted.sample_identity_basis == "assumed_each_image_distinct"


@pytest.mark.parametrize(
    ("ratios", "message"),
    [
        ({"train": 0.5, "validation": 0.2, "test": 0.2}, "add up to 1"),
        ({"train": 0.9, "validation": 0.2, "test": 0.2}, "add up to 1"),
        ({"train": 1.2, "validation": -0.2}, "number >= 0"),
        ({"train": 0.0, "validation": 0.5, "test": 0.5}, "train ratio"),
        ({"train": 1.0}, "At least two"),
        ({"train": 0.8, "independent_evaluation": 0.2}, "never generated"),
        ({"train": "0.8", "test": 0.2}, "number >= 0"),
        ({"train": True, "test": 0.0}, "number >= 0"),
        ({"train": float("nan"), "test": 0.2}, "number >= 0"),
    ],
)
def test_invalid_ratios_are_refused(
    big: dict[str, Any], ratios: dict[str, Any], message: str
) -> None:
    assert message in str(_refused(big, config(ratios=ratios)))


@pytest.mark.parametrize("seed", [-1, 1.5, True, "1", 2**63, None])
def test_invalid_seeds_are_refused(big: dict[str, Any], seed: object) -> None:
    assert "seed" in str(_refused(big, SplitConfig(seed=seed, ratios=RATIOS)))  # type: ignore[arg-type]


def test_too_few_samples_for_the_requested_splits_is_refused(tmp_path: Path) -> None:
    manifest = build_dataset(tmp_path / "d", sample_count=2, images_per_sample=2, categories=("synthetic-category-a",))

    assert "would be empty" in str(_refused(manifest, config(), dataset_dir=tmp_path / "d"))


def test_an_invalid_manifest_is_refused_with_its_report(big: dict[str, Any]) -> None:
    manifest = clone(big)
    manifest["records"][1]["record_id"] = manifest["records"][0]["record_id"]

    error = _refused(manifest, config())

    assert error.report is not None and "duplicate-record-id" in error.report.codes()


def test_files_are_verified_when_a_dataset_directory_is_given(tmp_path: Path) -> None:
    root = tmp_path / "d"
    manifest = build_dataset(root, sample_count=30, images_per_sample=1)
    (root / "images" / "sample-000" / "rec-000-0.jpg").write_bytes(b"tampered")

    error = _refused(manifest, config(), dataset_dir=root)

    assert error.report is not None and "checksum-mismatch" in error.report.codes()
    # Without a directory nothing could be verified, so nothing is split.
    assert "dataset directory" in str(_refused(manifest, config(), dataset_dir=None))


def test_the_input_manifest_is_not_modified(big: dict[str, Any]) -> None:
    before = clone(big)

    make_splits(big, config(6))

    assert big == before


# --- applying the splits -----------------------------------------------------------


def test_applied_splits_validate_and_audit_clean(tmp_path: Path) -> None:
    root = tmp_path / "d"
    manifest = build_dataset(root, sample_count=30, images_per_sample=2)

    result = make_splits(manifest, config(4), dataset_dir=root)
    applied = apply_splits(manifest, result.assignments)

    validation = validate_document(
        applied, dataset_dir=root, uses=["training", "validation", "testing"]
    )
    audit = audit_manifests([("applied", applied)])
    assert validation.ok, validation.report.format()
    assert audit.passed()
    assert not audit.report.errors
    assert {record["split"] for record in applied["records"]} == {"train", "validation", "test"}


def test_apply_splits_leaves_the_original_unassigned(big: dict[str, Any]) -> None:
    result = make_splits(big, config(4))

    applied = apply_splits(big, result.assignments)

    assert all(record["split"] == "unassigned" for record in big["records"])
    assert applied is not big
    assert applied["records"][0]["split"] == result.assignments[applied["records"][0]["record_id"]]


# --- sample identity that is only assumed ---------------------------------------------


def _assumed(manifest: dict[str, Any]) -> dict[str, Any]:
    result = clone(manifest)
    result["dataset"]["sample_identity"] = {"basis": "assumed_each_image_distinct"}
    return result


def test_splitting_assumed_identities_needs_a_written_reason(big: dict[str, Any]) -> None:
    message = str(_refused(_assumed(big), config()))

    assert "written reason" in message
    assert "--accept-unverified-sample-identity REASON" in message
    assert "not verified" in message.lower() or "without verification" in message


@pytest.mark.parametrize("reason", ["", "   ", "x" * 501, "bad\nreason", "bad\x00reason"])
def test_an_unusable_reason_does_not_unlock_the_override(big: dict[str, Any], reason: str) -> None:
    error = _refused(_assumed(big), config(unverified_sample_identity_reason=reason))

    assert "reason" in str(error)


def test_accepting_assumed_identities_is_recorded_and_warned_about(big: dict[str, Any]) -> None:
    result = make_splits(
        _assumed(big),
        config(unverified_sample_identity_reason="  Pilot only: no sample IDs exist yet.  "),
    )

    document = result.to_document()
    assert result.sample_identity_assumed is True
    assert result.unverified_sample_identity_reason == "Pilot only: no sample IDs exist yet."
    assert document["sample_identity_basis"] == "assumed_each_image_distinct"
    assert document["sample_identity_assumed"] is True
    assert document["unverified_sample_identity_reason"] == "Pilot only: no sample IDs exist yet."
    assert result.warnings[0].startswith("SAMPLE IDENTITY IS ASSUMED, NOT VERIFIED")
    assert "Pilot only" in result.warnings[0]
    assert "NOT known to be grouped by physical sample" in result.warnings[0]


@pytest.mark.parametrize(
    "basis", ["recorded_by_collector", "declared_by_source", "inferred_by_documented_analysis"]
)
def test_other_identity_bases_are_not_flagged_and_ignore_a_reason(
    big: dict[str, Any], basis: str
) -> None:
    manifest = clone(big)
    manifest["dataset"]["sample_identity"] = {
        "basis": basis,
        "notes": "Samples were tagged by the collectors and re-checked against the field log.",
    }

    result = make_splits(manifest, config(unverified_sample_identity_reason="not needed"))

    document = result.to_document()
    assert result.sample_identity_assumed is False
    assert document["sample_identity_assumed"] is False
    assert document["unverified_sample_identity_reason"] is None
    assert document["sample_identity_basis"] == basis
    assert not any("ASSUMED" in warning for warning in result.warnings)


def test_the_split_file_records_which_file_patterns_were_ignored(big: dict[str, Any]) -> None:
    assert make_splits(big, config()).to_document()["ignored_file_patterns"] == []

    document = make_splits(big, config(), ignore_patterns=["README.md", "notes/*"]).to_document()

    assert document["ignored_file_patterns"] == ["README.md", "notes/*"]
