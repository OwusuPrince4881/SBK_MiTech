"""The audit does not print a clean PASS for splits that are too small to trust (audit N11).

The original gap: a manifest whose test split held six physical samples got `RESULT: PASS` with no mention of
it, and split-file warnings were never seen by the audit at all. The audit now says that it checks leakage
only, qualifies its result when an evaluated split is small, and can check a split file against its manifest.
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import SplitConfig, apply_splits, audit_manifests, dumps_canonical, generate_splits
from app.dataset_manifest.audit import SCOPE_NOTICE
from app.dataset_manifest.splits import SMALL_SPLIT_SAMPLES, small_split_message
from dataset_builders import base_manifest, build_dataset, make_record, set_splits
from scripts import audit_dataset_leakage

RATIOS = {"train": 0.7, "validation": 0.15, "test": 0.15}


def counted_manifest(permitted_uses: list[str] | None = None, **counts: int) -> dict[str, Any]:
    """A manifest (no files) with `counts[split]` physical samples of two images in each split."""
    manifest = base_manifest()
    if permitted_uses is not None:
        manifest["dataset"]["permissions"]["permitted_uses"] = permitted_uses
    for split, count in counts.items():
        for index in range(count):
            for image in range(2):
                record, _data, _path = make_record(
                    f"{split}-{index:03d}-{image}", f"{split}-s{index:03d}", session_id=f"{split}-c{index:03d}",
                    split=split, data=f"{split}-{index}-{image}".encode(),
                )
                manifest["records"].append(record)
    manifest["dataset"]["licence"]["evidence"]["snapshot"] = {"relative_path": "licence/s.txt", "sha256": "0" * 64}
    return manifest


def split_document(manifest: dict[str, Any], warnings: list[str] | None = None) -> dict[str, Any]:
    """A split file that matches `manifest` (written by hand: the audit must not trust the splitter's own output)."""
    return {
        "split_format_version": "1.0",
        "algorithm": "sha256-ordered-stratified-greedy-v1",
        "dataset_id": manifest["dataset"]["dataset_id"],
        "warnings": list(warnings or []),
        "assignments": [
            {"record_id": r["record_id"], "physical_sample_id": r["physical_sample_id"], "split": r["split"]}
            for r in manifest["records"]
        ],
    }


def write_json(path: Path, document: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def error_codes(result: Any) -> set[str]:
    return {issue.code for issue in result.report.errors}


# --- small evaluated splits qualify the result --------------------------------------------------------------


@pytest.mark.parametrize("split", ["validation", "test"])
def test_the_threshold_is_exactly_the_one_the_split_generator_uses(split: str) -> None:
    just_below = counted_manifest(train=60, **{split: SMALL_SPLIT_SAMPLES - 1})
    at_threshold = counted_manifest(train=60, **{split: SMALL_SPLIT_SAMPLES})

    below = audit_manifests([("m.json", just_below)])
    at = audit_manifests([("m.json", at_threshold)])

    assert below.split_quality == {"m.json": [small_split_message(split, SMALL_SPLIT_SAMPLES - 1, 2 * (SMALL_SPLIT_SAMPLES - 1))]}
    assert at.split_quality == {}


def test_a_small_independent_evaluation_split_is_flagged_too() -> None:
    manifest = counted_manifest(permitted_uses=["independent_evaluation"], independent_evaluation=5)

    result = audit_manifests([("m.json", manifest)])

    assert "split 'independent_evaluation' has only 5 physical sample(s)" in result.split_quality["m.json"][0]


def test_the_train_split_and_unassigned_records_are_not_judged_by_size() -> None:
    result = audit_manifests([("m.json", counted_manifest(train=2, unassigned=3))])

    assert result.split_quality == {}


def test_a_small_split_qualifies_the_result_without_being_leakage() -> None:
    result = audit_manifests([("m.json", counted_manifest(train=28, validation=6, test=6))])

    assert result.passed() and result.passed(strict=True)  # no leakage finding at all
    assert result.limitations and len(result.split_quality["m.json"]) == 2
    assert [issue.code for issue in result.report.issues if "split" in issue.code] == []
    assert result.summaries[0].samples_per_split == {
        "train": 28, "validation": 6, "test": 6, "independent_evaluation": 0, "unassigned": 0,
    }


def test_the_cli_never_prints_a_bare_pass_for_a_six_sample_test_split(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_json(tmp_path / "m.json", counted_manifest(train=28, validation=6, test=6))

    code = audit_dataset_leakage.main([str(path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "RESULT: PASS WITH LIMITATIONS" in out and "RESULT: PASS\n" not in out
    assert "LIMITATION: split quality (" in out and "split 'test' has only 6 physical sample(s)" in out
    assert "must not be presented as reliable evidence" in out
    assert "physical samples per split: train 28, validation 6, test 6, independent_evaluation 0, unassigned 0" in out
    assert out.index("LIMITATION: split quality") < out.index(SCOPE_NOTICE) < out.index("RESULT:")


def test_a_large_verified_dataset_gets_a_plain_pass_with_the_scope_notice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "files"
    manifest = build_dataset(root, sample_count=120, images_per_sample=2)
    assignment = {f"sample-{i:03d}": "train" if i < 60 else "validation" if i < 90 else "test" for i in range(120)}
    path = write_json(tmp_path / "m.json", set_splits(manifest, assignment))

    code = audit_dataset_leakage.main([str(path), "--dataset-dir", str(root)])

    out = capsys.readouterr().out
    assert code == 0 and "RESULT: PASS\n" in out and "LIMITATION" not in out
    assert SCOPE_NOTICE in out and "not approval of the splits" in out


def test_the_scope_notice_is_printed_whatever_the_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    broken = counted_manifest(train=3)
    broken["records"][0]["split"] = "test"  # one sample in two splits: leakage
    path = write_json(tmp_path / "m.json", broken)

    code = audit_dataset_leakage.main([str(path)])

    out = capsys.readouterr().out
    assert code == 2 and "RESULT: FAIL" in out and SCOPE_NOTICE in out


def test_the_scope_notice_says_what_the_audit_does_not_do() -> None:
    assert "leakage only" in SCOPE_NOTICE
    for not_assessed in ("split quality", "label quality", "how reliable any measured performance will be"):
        assert not_assessed in SCOPE_NOTICE
    assert "not approval of the splits" in SCOPE_NOTICE


# --- a supplied split file is checked and its warnings are surfaced --------------------------------------------------


@pytest.fixture
def generated(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    """(manifest with splits filled in, its split file, dataset directory), from the real generator."""
    root = tmp_path / "files"
    manifest = build_dataset(root, sample_count=40, images_per_sample=2)
    result = generate_splits(manifest, SplitConfig(seed=3, ratios=RATIOS), dataset_dir=root)
    return apply_splits(manifest, result.assignments), result.to_document(), root


def test_split_file_warnings_reach_the_audit_once_and_make_the_pass_a_qualified_one(
    generated: tuple[dict[str, Any], dict[str, Any], Path],
) -> None:
    manifest, split_file, root = generated
    assert split_file["warnings"]  # the generator warned about the small validation and test splits

    result = audit_manifests([("m.json", manifest)], dataset_dirs={"m.json": root}, split_files={"m.json": split_file})

    assert error_codes(result) == set() and result.passed()
    assert [i.code for i in result.report.warnings] == ["split-file-warning"] * len(split_file["warnings"])
    assert not result.passed(strict=True)
    assert set(result.split_quality["m.json"]) == set(split_file["warnings"])  # not repeated by the audit's own check
    assert len(result.split_quality["m.json"]) == len(split_file["warnings"])


def test_the_cli_surfaces_split_file_warnings_and_strict_mode_fails_on_them(
    generated: tuple[dict[str, Any], dict[str, Any], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest, split_file, root = generated
    manifest_path = write_json(tmp_path / "out" / "m.json", manifest)
    split_path = tmp_path / "out" / "s.json"
    split_path.write_text(dumps_canonical(split_file), encoding="utf-8")
    arguments = [str(manifest_path), "--dataset-dir", str(root), "--split-file", str(split_path)]

    relaxed = audit_dataset_leakage.main(arguments)
    out = capsys.readouterr().out
    strict = audit_dataset_leakage.main([*arguments, "--strict"])

    assert relaxed == 0 and strict == 2
    assert "split file: supplied and checked" in out
    assert "WARNING [split-file-warning]" in out and "LIMITATION: split quality" in out
    assert "RESULT: PASS WITH LIMITATIONS" in out and "RESULT: PASS\n" not in out


def test_a_split_file_without_warnings_for_large_splits_keeps_a_plain_pass(tmp_path: Path) -> None:
    root = tmp_path / "files"
    manifest = build_dataset(root, sample_count=120, images_per_sample=2)
    assignment = {f"sample-{i:03d}": "train" if i < 60 else "validation" if i < 90 else "test" for i in range(120)}
    manifest = set_splits(manifest, assignment)

    result = audit_manifests([("m.json", manifest)], dataset_dirs={"m.json": root}, split_files={"m.json": split_document(manifest)})

    assert result.passed(strict=True) and not result.limitations


def test_the_genuine_output_of_the_generator_matches_its_manifest(
    generated: tuple[dict[str, Any], dict[str, Any], Path],
) -> None:
    manifest, split_file, _root = generated

    result = audit_manifests([("m.json", manifest)], split_files={"m.json": split_file})

    assert not {code for code in error_codes(result) if code.startswith("split-file")}


MISMATCHES: dict[str, tuple[Any, str]] = {}


def _mismatch(name: str, code: str) -> Any:
    def register(function: Any) -> Any:
        MISMATCHES[name] = (function, code)
        return function

    return register


@_mismatch("another dataset", "split-file-dataset-mismatch")
def _other_dataset(document: dict[str, Any]) -> None:
    document["dataset_id"] = "some-other-dataset"


@_mismatch("a record missing from the file", "split-file-records-mismatch")
def _drop_record(document: dict[str, Any]) -> None:
    document["assignments"].pop()


@_mismatch("a record only in the file", "split-file-records-mismatch")
def _add_record(document: dict[str, Any]) -> None:
    document["assignments"].append({"record_id": "ghost-record", "physical_sample_id": "ghost", "split": "train"})


@_mismatch("a different split", "split-file-assignment-mismatch")
def _other_split(document: dict[str, Any]) -> None:
    entry = document["assignments"][0]
    entry["split"] = "test" if entry["split"] != "test" else "train"


@_mismatch("a different physical sample", "split-file-sample-mismatch")
def _other_sample(document: dict[str, Any]) -> None:
    document["assignments"][0]["physical_sample_id"] = "somebody-else"


@pytest.mark.parametrize("name", sorted(MISMATCHES))
def test_a_split_file_that_does_not_describe_the_manifest_is_an_audit_error(name: str) -> None:
    mutate, code = MISMATCHES[name]
    manifest = counted_manifest(train=28, validation=6, test=6)
    document = split_document(manifest)
    mutate(document)

    result = audit_manifests([("m.json", manifest)], split_files={"m.json": document})

    assert code in error_codes(result) and not result.passed()


@pytest.mark.parametrize(
    "broken",
    [
        [],
        "text",
        {"split_format_version": "9.9", "assignments": [], "warnings": []},
        {"split_format_version": "1.0", "assignments": "x", "warnings": []},
        {"split_format_version": "1.0", "assignments": [], "warnings": "x"},
        {"split_format_version": "1.0", "assignments": [], "warnings": [1]},
        {"split_format_version": "1.0", "assignments": [{"record_id": "a"}], "warnings": []},
        {"split_format_version": "1.0", "assignments": ["x"], "warnings": []},
    ],
)
def test_a_malformed_split_file_is_an_error_not_a_crash(broken: Any) -> None:
    manifest = counted_manifest(train=28, validation=6, test=6)

    result = audit_manifests([("m.json", manifest)], split_files={"m.json": broken})

    assert "split-file-invalid" in error_codes(result) and not result.passed()


def test_a_record_listed_twice_in_a_split_file_is_an_error() -> None:
    manifest = counted_manifest(train=28, validation=6, test=6)
    document = split_document(manifest)
    document["assignments"].append(copy.deepcopy(document["assignments"][0]))

    result = audit_manifests([("m.json", manifest)], split_files={"m.json": document})

    assert "split-file-invalid" in error_codes(result)


# --- the command line options ---------------------------------------------------------------------------------------------


def test_the_cli_refuses_a_split_file_for_an_unknown_dataset_or_without_an_id_among_several(
    tmp_path: Path,
) -> None:
    first = write_json(tmp_path / "a.json", counted_manifest(train=28, validation=6, test=6))
    other = counted_manifest(train=28, validation=6, test=6)
    other["dataset"]["dataset_id"] = "second-dataset"
    second = write_json(tmp_path / "b.json", other)
    split_path = write_json(tmp_path / "s.json", split_document(counted_manifest(train=28, validation=6, test=6)))

    with pytest.raises(SystemExit) as unknown:
        audit_dataset_leakage.main([str(first), "--split-file", f"no-such-dataset={split_path}"])
    with pytest.raises(SystemExit) as ambiguous:
        audit_dataset_leakage.main([str(first), str(second), "--split-file", str(split_path)])

    assert unknown.value.code == 2 and ambiguous.value.code == 2


def test_the_cli_maps_a_split_file_to_its_dataset_by_id(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first_manifest = counted_manifest(train=28, validation=6, test=6)
    second_manifest = counted_manifest(train=28, validation=6, test=6)
    second_manifest["dataset"]["dataset_id"] = "second-dataset"
    first, second = write_json(tmp_path / "a.json", first_manifest), write_json(tmp_path / "b.json", second_manifest)
    split_path = write_json(tmp_path / "s.json", split_document(second_manifest))

    code = audit_dataset_leakage.main([str(first), str(second), "--split-file", f"second-dataset={split_path}"])

    out = capsys.readouterr().out
    assert code == 0 and out.count("split file: supplied and checked") == 1 and out.count("split file: not supplied") == 1


def test_a_missing_or_restricted_split_file_is_a_clean_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_json(tmp_path / "m.json", counted_manifest(train=28, validation=6, test=6))
    restricted = write_json(tmp_path / "data" / "evaluation" / "s.json", {})

    missing_code = audit_dataset_leakage.main([str(path), "--split-file", str(tmp_path / "nope.json")])
    restricted_code = audit_dataset_leakage.main([str(path), "--split-file", str(restricted)])

    err = capsys.readouterr().err
    assert (missing_code, restricted_code) == (2, 2)
    assert "Unable to read" in err and "restricted location" in err and "Traceback" not in err
