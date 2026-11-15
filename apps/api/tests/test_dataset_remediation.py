"""Regression tests for the independent audit of Milestone 2B (findings C1, H1-H6, M1-M9).

All data is synthetic and lives in temporary folders.
"""

import json
import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import (
    ManifestLoadError,
    SplitConfig,
    SplitGenerationError,
    audit_manifests,
    generate_splits,
    source_view,
    validate_document,
)
from app.dataset_manifest import files as files_module
from app.dataset_manifest import loading as loading_module
from app.dataset_manifest.files import scan_directory
from app.dataset_manifest.loading import MAX_INTEGER_DIGITS, MAX_NESTING, parse_manifest_bytes
from app.dataset_manifest.splits import SMALL_SPLIT_SAMPLES
from app.dataset_manifest.validation import ValidationResult
from app.dataset_manifest.view import build_view
from dataset_builders import (
    LICENCE_SNAPSHOT_PATH,
    build_dataset,
    clone,
    link_directory,
    record_by_id,
    set_splits,
    sha256_hex,
)
from scripts import audit_dataset_leakage, generate_dataset_splits, validate_dataset_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_1_0 = REPO_ROOT / "docs" / "examples" / "example_dataset_manifest.json"
RATIOS = {"train": 0.7, "validation": 0.15, "test": 0.15}
ASSIGNMENT = {"sample-000": "train", "sample-001": "train", "sample-002": "validation", "sample-003": "test"}


@pytest.fixture
def dataset(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    root = tmp_path / "dataset"
    return build_dataset(root, sample_count=4, images_per_sample=2), root


def codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.errors}


def warning_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.warnings}


def audit_errors(result: Any) -> set[str]:
    return {issue.code for issue in result.report.errors}


def audit_warnings(result: Any) -> set[str]:
    return {issue.code for issue in result.report.warnings}


def split_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return set_splits(manifest, ASSIGNMENT)


def write_json(path: Path, document: Any) -> Path:
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def evaluation_manifest(root: Path, dataset_id: str = "synthetic-evaluation") -> dict[str, Any]:
    manifest = build_dataset(root, sample_count=2, images_per_sample=2, dataset_id=dataset_id)
    manifest["dataset"]["permissions"]["permitted_uses"] = ["independent_evaluation"]
    for record in manifest["records"]:
        record["split"] = "independent_evaluation"
        record["file"]["sha256"] = sha256_hex(f"{dataset_id}:{record['record_id']}".encode())
    return manifest


def derive(record: dict[str, Any], dataset_id: str, record_id: str) -> None:
    record["provenance"] = {"derived_from": [{"dataset_id": dataset_id, "record_id": record_id}]}


# === H2: the audit validates semantically ===================================================


def test_audit_rejects_a_manifest_the_validator_rejects_for_sample_ids(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = split_manifest(dataset[0])
    manifest["records"][1]["physical_sample_id"] = "SAMPLE-000"
    manifest["records"][1]["split"] = "test"

    result = audit_manifests([("m.json", manifest)])

    assert not result.passed()
    assert "ambiguous-physical-sample-id" in audit_errors(result)


def test_audit_rejects_evaluation_records_mixed_with_training(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    for index, record in enumerate(manifest["records"]):
        record["split"] = "independent_evaluation" if index < 2 else "train"

    result = audit_manifests([("m.json", manifest)])

    assert not result.passed()
    assert "evaluation-records-mixed" in audit_errors(result)


def test_audit_rejects_approval_without_licence_evidence(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = split_manifest(dataset[0])
    del manifest["dataset"]["licence"]["evidence"]

    result = audit_manifests([("m.json", manifest)])

    assert not result.passed()
    assert "approval-without-licence-evidence" in audit_errors(result)


def test_audit_runs_the_permission_validation(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = split_manifest(dataset[0])
    manifest["dataset"]["permissions"]["permitted_uses"] = ["validation", "testing"]

    result = audit_manifests([("m.json", manifest)])

    assert not result.passed()
    assert audit_errors(result) == {"split-use-not-permitted"}


def test_audit_runs_the_sample_identity_checks(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = split_manifest(dataset[0])
    manifest["dataset"]["sample_identity"] = {"basis": "inferred_by_documented_analysis"}

    result = audit_manifests([("m.json", manifest)])

    assert "identity-notes-missing" in audit_errors(result)


def test_audit_runs_the_licence_evidence_checks(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = split_manifest(dataset[0])
    manifest["dataset"]["permissions"]["commercial_training"] = "permitted"
    manifest["dataset"]["licence"]["evidence"]["captured_on"] = "2030-01-01"

    result = audit_manifests([("m.json", manifest)])

    assert audit_errors(result) == {"commercial-training-basis-missing", "evidence-after-review"}


def test_audit_findings_name_the_manifest_and_where_in_it(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = split_manifest(dataset[0])
    del manifest["dataset"]["licence"]["evidence"]

    result = audit_manifests([("some/path/m.json", manifest)])

    issue = next(i for i in result.report.errors if i.code == "approval-without-licence-evidence")
    assert issue.location == "some/path/m.json"
    assert issue.message.startswith("dataset.licence.evidence: ")


def test_audit_does_not_repeat_the_identity_warning(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = split_manifest(dataset[0])
    manifest["dataset"]["sample_identity"] = {"basis": "assumed_each_image_distinct"}

    result = audit_manifests([("m.json", manifest)])

    assert [i.code for i in result.report.warnings].count("unverified-sample-identity") == 1


def test_audit_passes_a_clean_verified_dataset_with_no_limitations(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset

    result = audit_manifests([("m.json", split_manifest(manifest))], dataset_dirs={"m.json": root})

    assert result.passed(strict=True), result.report.format()
    assert not result.limitations
    assert result.summaries[0].files_verified is True


def test_audit_without_a_dataset_directory_states_that_files_were_not_verified(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    result = audit_manifests([("m.json", split_manifest(dataset[0]))])

    assert result.passed(strict=True)  # nothing is wrong, but...
    assert result.files_not_verified == ["m.json"]
    assert result.limitations is True
    assert result.summaries[0].files_verified is False


def test_audit_checks_directory_integrity_when_a_directory_is_supplied(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    manifest = split_manifest(manifest)
    (root / "stray.jpg").write_bytes(b"s")
    (root / "images" / "sample-000" / "rec-000-0.jpg").write_bytes(b"changed")

    result = audit_manifests([("m.json", manifest)], dataset_dirs={"m.json": root})

    assert not result.passed()
    assert {"unmanifested-image-file", "checksum-mismatch"} <= audit_errors(result)


def test_audit_ignore_patterns_never_hide_an_image(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    (root / "stray.jpg").write_bytes(b"s")
    (root / "notes.txt").write_bytes(b"n")

    result = audit_manifests(
        [("m.json", split_manifest(manifest))], dataset_dirs={"m.json": root}, ignore_patterns=["*"]
    )

    assert audit_errors(result) == {"unmanifested-image-file"}
    assert "files-ignored-by-pattern" in audit_warnings(result)


def test_audit_cli_takes_dataset_directories_for_several_manifests(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first = build_dataset(tmp_path / "a", sample_count=4, images_per_sample=2, dataset_id="set-a")
    second = build_dataset(tmp_path / "b", sample_count=4, images_per_sample=2, dataset_id="set-b")
    for record in second["records"]:
        record["file"]["sha256"] = sha256_hex(b"b:" + record["record_id"].encode())
    first_path = write_json(tmp_path / "a.json", split_manifest(first))
    second_path = write_json(tmp_path / "b.json", split_manifest(second))
    (tmp_path / "b" / "stray.jpg").write_bytes(b"s")

    code = audit_dataset_leakage.main(
        [str(first_path), str(second_path), "--dataset-dir", f"set-a={tmp_path / 'a'}", "--dataset-dir", f"set-b={tmp_path / 'b'}"]
    )

    out = capsys.readouterr().out
    assert code == 2
    assert "unmanifested-image-file" in out and "RESULT: FAIL" in out


def test_audit_cli_needs_each_directory_named_when_there_are_several_manifests(
    tmp_path: Path,
) -> None:
    first = write_json(tmp_path / "a.json", build_dataset(tmp_path / "a", sample_count=4, images_per_sample=2, dataset_id="set-a"))
    second = write_json(tmp_path / "b.json", build_dataset(tmp_path / "b", sample_count=4, images_per_sample=2, dataset_id="set-b"))

    with pytest.raises(SystemExit) as exit_info:
        audit_dataset_leakage.main([str(first), str(second), "--dataset-dir", str(tmp_path / "a")])

    assert exit_info.value.code == 2


# === H3: links ===============================================================================


class FakeEntry:
    """A directory entry whose behaviour is scripted, so links can be tested without privileges."""

    def __init__(self, name: str, directory: str, *, symlink: bool = False, is_dir: bool = False) -> None:
        self.name = name
        self.path = os.path.join(directory, name)
        self._symlink = symlink
        self._is_dir = is_dir

    def is_symlink(self) -> bool:
        return self._symlink

    def is_junction(self) -> bool:
        return False

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        if self._symlink and follow_symlinks:
            raise AssertionError("a link was followed")
        return self._is_dir and not self._symlink

    def is_file(self, *, follow_symlinks: bool = True) -> bool:
        if self._symlink and follow_symlinks:
            raise AssertionError("a link was followed")
        return not self._is_dir and not self._symlink


def test_an_image_named_symlink_is_a_link_and_is_never_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = [FakeEntry("evil.jpg", str(tmp_path), symlink=True), FakeEntry("real.txt", str(tmp_path))]
    monkeypatch.setattr(files_module.os, "scandir", lambda path: nullcontext(entries))

    scan = scan_directory(tmp_path)

    assert scan.links == ["evil.jpg"]
    assert sorted(scan.files) == ["real.txt"]


def test_an_image_named_symlink_makes_the_dataset_directory_invalid(
    dataset: tuple[dict[str, Any], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, root = dataset
    real_scandir = os.scandir

    def with_a_symlink(path: Any) -> Any:
        if Path(path) == root.resolve():
            return nullcontext([FakeEntry("evil.jpg", str(path), symlink=True), FakeEntry("images", str(path), is_dir=True)])
        return real_scandir(path)

    monkeypatch.setattr(files_module.os, "scandir", with_a_symlink)

    result = validate_document(manifest, dataset_dir=root, ignore_patterns=["*"])

    assert "link-in-dataset-dir" in codes(result)
    assert result.links_not_followed == ["evil.jpg"]
    assert not result.ok


def test_a_real_image_named_symlink_is_refused(dataset: tuple[dict[str, Any], Path], tmp_path: Path) -> None:
    manifest, root = dataset
    target = tmp_path / "elsewhere.jpg"
    target.write_bytes(b"\xff\xd8\xff synthetic")
    try:
        (root / "evil.jpg").symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create symbolic links (the scripted-entry test covers this)")

    result = validate_document(manifest, dataset_dir=root, ignore_patterns=["*"])

    assert "link-in-dataset-dir" in codes(result)
    assert result.links_not_followed == ["evil.jpg"]


def test_a_junction_to_a_folder_of_unlisted_images_is_an_error_and_clears_nothing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ds"
    manifest = build_dataset(root, sample_count=30, images_per_sample=2)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "restricted-looking.jpg").write_bytes(b"R")
    if not link_directory(root / "linked", outside):
        pytest.skip("this environment cannot create a symbolic link or junction")

    result = validate_document(manifest, dataset_dir=root, uses=["training"])

    assert not result.ok
    assert "link-in-dataset-dir" in codes(result)
    assert result.cleared_uses == ()
    with pytest.raises(SplitGenerationError) as caught:
        generate_splits(manifest, SplitConfig(seed=1, ratios=RATIOS), dataset_dir=root)
    assert caught.value.report is not None and "link-in-dataset-dir" in caught.value.report.codes()


def test_a_link_error_cannot_be_ignored(dataset: tuple[dict[str, Any], Path], tmp_path: Path) -> None:
    manifest, root = dataset
    outside = tmp_path / "outside"
    outside.mkdir()
    if not link_directory(root / "docs", outside):
        pytest.skip("this environment cannot create a symbolic link or junction")

    result = validate_document(manifest, dataset_dir=root, ignore_patterns=["*", "docs", "docs/*"])

    assert "link-in-dataset-dir" in codes(result)


def test_a_directory_link_named_like_an_image_is_refused(dataset: tuple[dict[str, Any], Path], tmp_path: Path) -> None:
    manifest, root = dataset
    outside = tmp_path / "outside"
    outside.mkdir()
    if not link_directory(root / "photo.jpg", outside):
        pytest.skip("this environment cannot create a symbolic link or junction")

    result = validate_document(manifest, dataset_dir=root, ignore_patterns=["*"])

    assert "link-in-dataset-dir" in codes(result)


def test_links_are_listed_by_the_cli(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest, root = dataset
    outside = tmp_path / "outside"
    outside.mkdir()
    if not link_directory(root / "linked", outside):
        pytest.skip("this environment cannot create a symbolic link or junction")
    path = write_json(tmp_path / "manifest.json", manifest)

    code = validate_dataset_manifest.main([str(path), "--dataset-dir", str(root)])

    err = capsys.readouterr().err
    assert code == 2
    assert "ERROR [link-in-dataset-dir]" in err
    assert "Links in the dataset directory (refused, not followed, left untouched):" in err and "'linked'" in err


# === H4: derived data provenance ==============================================================


def test_derived_from_is_preserved_in_the_record_view(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    derive(manifest["records"][0], "other-dataset", "e1")

    view = build_view(manifest, "1.1")

    assert view.records[0].derived_from == (("other-dataset", "e1"),)
    assert view.records[1].derived_from == ()


def test_a_derivation_from_an_unsupplied_dataset_fails_closed_in_the_validator_and_the_audit(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = clone(dataset[0])
    derive(manifest["records"][0], "synthetic-evaluation", "rec-000-0")

    assert codes(validate_document(manifest)) == {"derived-source-unverified"}
    audited = audit_manifests([("t.json", manifest)])
    assert audit_errors(audited) == {"derived-source-unverified"}


def test_a_training_record_derived_from_an_evaluation_record_is_an_error_across_manifests(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    training = split_manifest(dataset[0])
    evaluation = evaluation_manifest(tmp_path / "e")
    derive(record_by_id(training, "rec-000-0"), "synthetic-evaluation", "rec-000-0")

    result = audit_manifests([("train.json", training), ("eval.json", evaluation)])

    assert not result.passed()
    assert "derived-from-evaluation-record" in audit_errors(result)
    issue = next(i for i in result.report.errors if i.code == "derived-from-evaluation-record")
    assert issue.location == "train.json" and "independent_evaluation" in issue.message


def test_an_evaluation_record_derived_from_training_data_is_an_error(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    training = split_manifest(dataset[0])
    evaluation = evaluation_manifest(tmp_path / "e")
    derive(evaluation["records"][0], "synthetic-fixture", "rec-000-0")

    result = audit_manifests([("train.json", training), ("eval.json", evaluation)])

    assert "derived-into-evaluation" in audit_errors(result)


def test_records_deriving_across_ordinary_splits_are_an_error(dataset: tuple[dict[str, Any], Path], tmp_path: Path) -> None:
    source = build_dataset(tmp_path / "s", sample_count=2, images_per_sample=2, dataset_id="source-set")
    for record in source["records"]:
        record["split"] = "test"
        record["file"]["sha256"] = sha256_hex(b"source:" + record["record_id"].encode())
    derived = split_manifest(dataset[0])
    derive(record_by_id(derived, "rec-000-0"), "source-set", "rec-000-0")  # train derived from test

    result = audit_manifests([("derived.json", derived), ("source.json", source)])

    assert "derivation-crosses-splits" in audit_errors(result)


def test_a_derivation_within_one_split_is_accepted(dataset: tuple[dict[str, Any], Path], tmp_path: Path) -> None:
    source = build_dataset(tmp_path / "s", sample_count=2, images_per_sample=2, dataset_id="source-set")
    for record in source["records"]:
        record["split"] = "train"
        record["file"]["sha256"] = sha256_hex(b"source:" + record["record_id"].encode())
    derived = split_manifest(dataset[0])
    derive(record_by_id(derived, "rec-000-0"), "source-set", "rec-000-0")  # train from train

    result = audit_manifests([("derived.json", derived), ("source.json", source)])

    assert not any(code.startswith("deriv") for code in audit_errors(result) | audit_warnings(result))


def test_a_derivation_where_one_side_is_unassigned_is_flagged_as_undetermined(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    source = build_dataset(tmp_path / "s", sample_count=2, images_per_sample=2, dataset_id="source-set")
    for record in source["records"]:
        record["file"]["sha256"] = sha256_hex(b"source:" + record["record_id"].encode())
    derived = split_manifest(dataset[0])
    derive(record_by_id(derived, "rec-000-0"), "source-set", "rec-000-0")

    result = audit_manifests([("derived.json", derived), ("source.json", source)])

    assert "derivation-split-undetermined" in audit_warnings(result)
    assert "derivation-crosses-splits" not in audit_errors(result)


def test_a_derivation_from_a_record_that_does_not_exist_is_an_error(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    training = split_manifest(dataset[0])
    evaluation = evaluation_manifest(tmp_path / "e")
    derive(record_by_id(training, "rec-000-0"), "synthetic-evaluation", "no-such-record")

    result = audit_manifests([("train.json", training), ("eval.json", evaluation)])

    assert "derived-from-unknown-record" in audit_errors(result)


def test_nothing_may_be_derived_from_restricted_material(dataset: tuple[dict[str, Any], Path], tmp_path: Path) -> None:
    restricted = evaluation_manifest(tmp_path / "u", dataset_id="restricted-set")
    restricted["dataset"]["category"] = "umat_practical"
    training = split_manifest(dataset[0])
    derive(record_by_id(training, "rec-000-0"), "restricted-set", "rec-000-0")

    result = audit_manifests([("train.json", training), ("restricted.json", restricted)])

    assert {"derived-from-restricted-source", "restricted-category"} <= audit_errors(result)


def test_supplying_the_source_lets_the_validator_establish_a_valid_derivation(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    manifest, root = dataset
    source = build_dataset(tmp_path / "s", sample_count=2, images_per_sample=2, dataset_id="source-set")
    for record in source["records"]:
        record["split"] = "unassigned"
        record["file"]["sha256"] = sha256_hex(b"source:" + record["record_id"].encode())
    derived = clone(manifest)
    derive(derived["records"][0], "source-set", "rec-000-0")

    assert codes(validate_document(derived)) == {"derived-source-unverified"}
    result = validate_document(derived, provenance_sources={"source-set": source_view(source)})

    assert result.ok, result.report.format()


def test_source_view_rejects_a_manifest_it_cannot_read() -> None:
    with pytest.raises(ValueError, match="structural error"):
        source_view({"schema_version": "1.1", "dataset": {}, "records": []})
    with pytest.raises(ValueError, match="schema_version"):
        source_view({"schema_version": "9"})


def test_the_cli_tools_take_source_manifests(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest, root = dataset
    source = evaluation_manifest(tmp_path / "e")
    derived = clone(manifest)
    derive(derived["records"][0], "synthetic-evaluation", "rec-000-0")
    derived_path = write_json(tmp_path / "derived.json", derived)
    source_path = write_json(tmp_path / "source.json", source)

    without = validate_dataset_manifest.main([str(derived_path), "--dataset-dir", str(root)])
    without_err = capsys.readouterr().err
    with_source = validate_dataset_manifest.main(
        [str(derived_path), "--dataset-dir", str(root), "--source-manifest", str(source_path)]
    )
    with_err = capsys.readouterr().err

    assert without == 2 and "derived-source-unverified" in without_err
    assert with_source == 2 and "derived-from-evaluation-record" in with_err  # established, and forbidden


def test_the_splitter_cli_takes_source_manifests(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "files"
    manifest = build_dataset(root, sample_count=30, images_per_sample=2)
    source = build_dataset(tmp_path / "src", sample_count=2, images_per_sample=2, dataset_id="source-set")
    for record in source["records"]:
        record["file"]["sha256"] = sha256_hex(b"source:" + record["record_id"].encode())
    derive(manifest["records"][0], "source-set", "rec-000-0")
    manifest_path = write_json(tmp_path / "manifest.json", manifest)
    source_path = write_json(tmp_path / "source.json", source)
    base = [str(manifest_path), "--dataset-dir", str(root), "--seed", "1", "--output", str(tmp_path / "s.json")]

    refused = generate_dataset_splits.main(base)
    err = capsys.readouterr().err
    accepted = generate_dataset_splits.main([*base, "--source-manifest", str(source_path)])

    assert refused == 2 and "derived-source-unverified" in err
    assert accepted == 0, capsys.readouterr().err


def test_a_source_manifest_that_cannot_be_read_is_reported(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = write_json(tmp_path / "m.json", dataset[0])
    bad = write_json(tmp_path / "bad.json", {"schema_version": "1.1"})

    code = validate_dataset_manifest.main([str(manifest_path), "--source-manifest", str(bad)])

    assert code == 2
    assert "cannot be used as a source manifest" in capsys.readouterr().err


# === H5 / H6: recorded evidence is not verified permission =====================================


def test_a_use_is_refused_when_no_dataset_directory_was_supplied(dataset: tuple[dict[str, Any], Path]) -> None:
    result = validate_document(dataset[0], uses=["training"])

    assert codes(result) == {"clearance-needs-dataset-dir"}
    assert result.cleared_uses == ()


def test_a_use_needs_a_checksummed_licence_snapshot_not_just_a_reference(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    for evidence in (
        {"captured_on": "2026-01-01", "reference": "licence archive item 17"},
        {"captured_on": "2026-01-01", "archive_url": "https://archive.example.invalid/licence"},
    ):
        edited = clone(manifest)
        edited["dataset"]["licence"]["evidence"] = evidence

        result = validate_document(edited, dataset_dir=root, uses=["training"])

        assert codes(result) == {"clearance-needs-licence-snapshot"}
        assert result.cleared_uses == ()
    # Recording such evidence is still allowed: it just does not clear a use.
    assert validate_document(edited, dataset_dir=root).ok


def test_a_changed_or_missing_licence_snapshot_prevents_clearance(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    snapshot = root / LICENCE_SNAPSHOT_PATH

    snapshot.write_bytes(b"edited after it was recorded")
    changed = validate_document(manifest, dataset_dir=root, uses=["training"])
    snapshot.unlink()
    missing = validate_document(manifest, dataset_dir=root, uses=["training"])

    assert "checksum-mismatch" in codes(changed) and changed.cleared_uses == ()
    assert "file-missing" in codes(missing) and missing.cleared_uses == ()


def test_a_changed_image_prevents_clearance(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    (root / "images" / "sample-000" / "rec-000-0.jpg").write_bytes(b"tampered")

    result = validate_document(manifest, dataset_dir=root, uses=["training"])

    assert "checksum-mismatch" in codes(result) and result.cleared_uses == ()


def test_clearance_is_granted_only_after_verification_succeeds(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset

    nothing_requested = validate_document(manifest, dataset_dir=root)
    requested = validate_document(manifest, dataset_dir=root, uses=["training", "validation"])

    assert nothing_requested.ok and nothing_requested.cleared_uses == ()
    assert requested.ok and requested.cleared_uses == ("training", "validation")
    assert requested.files_verified is True


def test_commercial_training_clearance_is_separate_from_training_clearance(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    manifest["dataset"]["permissions"].update(
        commercial_training="permitted", commercial_training_basis="Clause 4.2 of the licence."
    )

    training_only = validate_document(manifest, dataset_dir=root, uses=["training"])
    commercial_only = validate_document(manifest, dataset_dir=root, require_commercial_training=True)

    assert training_only.cleared_uses == ("training",) and training_only.commercial_training_cleared is False
    assert commercial_only.cleared_uses == () and commercial_only.commercial_training_cleared is True


def test_training_can_be_cleared_while_commercial_training_stays_unknown(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    assert manifest["dataset"]["permissions"]["commercial_training"] == "unknown"

    cleared = validate_document(manifest, dataset_dir=root, uses=["training"])
    commercial = validate_document(manifest, dataset_dir=root, uses=["training"], require_commercial_training=True)

    assert cleared.ok and cleared.cleared_uses == ("training",)
    assert codes(commercial) == {"commercial-training-not-permitted"}
    assert commercial.cleared_uses == () and commercial.commercial_training_cleared is False


def test_a_free_text_basis_alone_does_not_clear_commercial_training(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    manifest["dataset"]["permissions"].update(commercial_training="permitted", commercial_training_basis="x")
    manifest["dataset"]["licence"]["evidence"] = {"captured_on": "2026-01-01", "reference": "a free-text note"}

    result = validate_document(manifest, dataset_dir=root, require_commercial_training=True)

    assert codes(result) == {"clearance-needs-licence-snapshot"}
    assert result.commercial_training_cleared is False


def test_the_splitter_cannot_run_without_a_dataset_directory(dataset: tuple[dict[str, Any], Path]) -> None:
    with pytest.raises(SplitGenerationError, match="dataset directory"):
        generate_splits(dataset[0], SplitConfig(seed=1, ratios=RATIOS))


def test_the_cli_wording_separates_recorded_from_verified_and_never_says_confirmed(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest, root = dataset
    manifest["dataset"]["permissions"].update(commercial_training="permitted", commercial_training_basis="Clause 4.2.")
    path = write_json(tmp_path / "m.json", manifest)

    code = validate_dataset_manifest.main(
        [str(path), "--dataset-dir", str(root), "--use", "training", "--require-commercial-training"]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "recorded permission accepted for: training" in out
    assert "commercial training: the manifest declares it permitted" in out
    assert "free text and is not verified" in out
    assert "does not judge the licence or confirm that the use is lawful" in out
    assert "confirmed" not in out.lower().replace("does not judge the licence or confirm", "")


def test_the_cli_says_nothing_was_cleared_when_no_use_was_requested(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_json(tmp_path / "m.json", dataset[0])

    code = validate_dataset_manifest.main([str(path), "--dataset-dir", str(dataset[1])])

    out = capsys.readouterr().out
    assert code == 0
    assert "nothing was cleared for any use" in out
    assert "recorded permission accepted" not in out


def test_the_cli_refuses_a_use_without_a_dataset_directory(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_json(tmp_path / "m.json", dataset[0])

    code = validate_dataset_manifest.main([str(path), "--use", "training"])

    assert code == 2
    assert "clearance-needs-dataset-dir" in capsys.readouterr().err


def test_the_audit_cli_states_when_files_were_not_verified(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_json(tmp_path / "m.json", split_manifest(dataset[0]))

    code = audit_dataset_leakage.main([str(path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "files: NOT verified (no usable --dataset-dir)" in out
    assert "LIMITATION: files and licence snapshots were NOT verified against their checksums for:" in out
    assert "RESULT: PASS WITH LIMITATIONS" in out

# === M1: sample identity ========================================================================


def test_one_image_per_sample_is_not_treated_as_a_verified_identity(tmp_path: Path) -> None:
    root = tmp_path / "d"
    manifest = build_dataset(root, sample_count=30, images_per_sample=1)  # basis: recorded_by_collector

    validation = validate_document(manifest, dataset_dir=root)
    audit = audit_manifests([("m.json", split_manifest_all(manifest))], dataset_dirs={"m.json": root})

    assert validation.ok and validation.single_image_samples is True
    assert "single-image-samples" in warning_codes(validation)
    assert audit.unverified_identity == ["m.json"]
    assert "single-image-samples" in audit_warnings(audit)
    assert audit.limitations is True
    assert audit.summaries[0].single_image_samples is True
    assert not audit.passed(strict=True)


def split_manifest_all(manifest: dict[str, Any]) -> dict[str, Any]:
    result = clone(manifest)
    for index, record in enumerate(result["records"]):
        record["split"] = ("train", "train", "train", "validation", "test")[index % 5]
    return result


def test_several_images_per_sample_carry_no_single_image_warning(dataset: tuple[dict[str, Any], Path]) -> None:
    result = validate_document(dataset[0], dataset_dir=dataset[1])

    assert result.single_image_samples is False
    assert "single-image-samples" not in warning_codes(result)


def test_a_single_record_is_not_called_single_image_samples(tmp_path: Path) -> None:
    root = tmp_path / "d"
    manifest = build_dataset(root, sample_count=1, images_per_sample=1)

    assert validate_document(manifest, dataset_dir=root).single_image_samples is False


@pytest.mark.parametrize("notes", [None, "", "   "])
def test_an_inferred_identity_needs_notes_that_describe_the_analysis(
    dataset: tuple[dict[str, Any], Path], notes: str | None
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["sample_identity"] = {"basis": "inferred_by_documented_analysis"}
    if notes is not None:
        manifest["dataset"]["sample_identity"]["notes"] = notes

    result = validate_document(manifest)

    # Missing notes are caught by the semantic rule; blank notes already fail the schema.
    expected = "identity-notes-missing" if notes is None else "schema"
    assert not result.ok
    assert expected in codes(result)


def test_an_inferred_identity_with_notes_is_accepted_and_other_bases_need_none(dataset: tuple[dict[str, Any], Path]) -> None:
    for identity in (
        {"basis": "inferred_by_documented_analysis", "notes": "Grouped by tag photographed beside each sample."},
        {"basis": "recorded_by_collector"},
        {"basis": "declared_by_source"},
        {"basis": "assumed_each_image_distinct"},
    ):
        manifest = clone(dataset[0])
        manifest["dataset"]["sample_identity"] = identity

        assert validate_document(manifest).ok, identity


def test_the_split_file_records_when_every_sample_has_one_image(tmp_path: Path) -> None:
    root = tmp_path / "d"
    manifest = build_dataset(root, sample_count=30, images_per_sample=1)

    result = generate_splits(manifest, SplitConfig(seed=1, ratios=RATIOS), dataset_dir=root)

    document = result.to_document()
    assert document["single_image_per_sample"] is True
    assert any("exactly one image" in warning for warning in document["warnings"])


def test_the_audit_cli_states_the_single_image_limitation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "d"
    manifest = split_manifest_all(build_dataset(root, sample_count=30, images_per_sample=1))
    path = write_json(tmp_path / "m.json", manifest)

    code = audit_dataset_leakage.main([str(path), "--dataset-dir", str(root)])

    out = capsys.readouterr().out
    assert code == 0
    assert "every sample has exactly one image, so grouping protects nothing: NOT VERIFIED" in out
    assert "LIMITATION: the physical sample IDs are NOT verified" in out
    assert "RESULT: PASS WITH LIMITATIONS" in out


# === M2: ignore patterns (the audit-level behaviour; the scanner is covered elsewhere) ===========


def test_the_validator_cli_lists_every_file_an_ignore_pattern_left_out_and_images_still_fail(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest, root = dataset
    (root / "stray.jpg").write_bytes(b"s")
    (root / "notes.txt").write_bytes(b"n")
    path = write_json(tmp_path / "m.json", manifest)

    code = validate_dataset_manifest.main([str(path), "--dataset-dir", str(root), "--ignore-file", "*"])

    err = capsys.readouterr().err
    assert code == 2
    assert "ERROR [unmanifested-image-file]" in err and "'stray.jpg'" in err
    assert "1 file(s) left out of the unlisted-file check by an ignore pattern" in err
    assert "  'notes.txt'" in err


# === M3: split quality ==========================================================================


def test_warnings_are_recorded_in_the_split_file(tmp_path: Path) -> None:
    root = tmp_path / "d"
    manifest = build_dataset(root, sample_count=60, images_per_sample=2)

    result = generate_splits(manifest, SplitConfig(seed=2, ratios=RATIOS), dataset_dir=root)

    document = result.to_document()
    assert document["warnings"] == result.warnings
    assert document["warnings"], "a 60-sample dataset has small validation and test splits"
    assert [w for w in document["warnings"] if "split 'test'" in w]


def test_a_small_test_split_is_called_unreliable(tmp_path: Path) -> None:
    root = tmp_path / "d"
    manifest = build_dataset(root, sample_count=13, images_per_sample=2)

    result = generate_splits(manifest, SplitConfig(seed=1, ratios=RATIOS), dataset_dir=root)

    test_warning = next(w for w in result.warnings if "split 'test'" in w)
    assert f"fewer than {SMALL_SPLIT_SAMPLES} physical samples" in test_warning
    assert "must not be presented as reliable evidence" in test_warning
    assert result.summary["per_split"]["test"]["physical_samples"] == 1
    assert "only 1 physical sample(s)" in test_warning


def test_a_large_enough_dataset_has_no_small_split_warning(tmp_path: Path) -> None:
    root = tmp_path / "d"
    manifest = build_dataset(root, sample_count=210, images_per_sample=2)

    result = generate_splits(
        manifest, SplitConfig(seed=2, ratios={"train": 0.6, "validation": 0.2, "test": 0.2}), dataset_dir=root
    )

    assert min(e["physical_samples"] for e in result.summary["per_split"].values()) >= SMALL_SPLIT_SAMPLES
    assert not any("only " in warning and "physical sample(s)" in warning for warning in result.warnings)
    assert result.to_document()["warnings"] == result.warnings


def test_a_category_missing_from_validation_or_test_is_warned_about_in_the_file(tmp_path: Path) -> None:
    root = tmp_path / "d"
    manifest = build_dataset(root, sample_count=13, images_per_sample=2)

    document = generate_splits(manifest, SplitConfig(seed=1, ratios=RATIOS), dataset_dir=root).to_document()

    missing = [w for w in document["warnings"] if "has no physical samples in split" in w]
    assert missing, "13 samples cannot put every category in the test split"
    assert any("split 'test'" in w for w in missing)


def test_unlabelled_and_conflicting_samples_are_warned_about(tmp_path: Path) -> None:
    root = tmp_path / "d"
    manifest = build_dataset(root, sample_count=40, images_per_sample=2)
    for record in manifest["records"]:
        if record["physical_sample_id"] in ("sample-000", "sample-001"):
            del record["label"]
    record_by_id(manifest, "rec-002-1")["label"]["material_category"] = "synthetic-category-b"
    record_by_id(manifest, "rec-002-0")["label"]["material_category"] = "synthetic-category-a"

    result = generate_splits(manifest, SplitConfig(seed=1, ratios=RATIOS), dataset_dir=root)

    assert any("2 physical sample(s) have no label" in w and "sample-000" in w for w in result.warnings)
    assert any("1 physical sample(s) have conflicting or partly missing labels" in w and "sample-002" in w for w in result.warnings)


def test_split_generation_with_warnings_is_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "d"
    manifest = build_dataset(root, sample_count=13, images_per_sample=2)

    first = generate_splits(manifest, SplitConfig(seed=4, ratios=RATIOS), dataset_dir=root).to_document()
    second = generate_splits(clone(manifest), SplitConfig(seed=4, ratios=RATIOS), dataset_dir=root).to_document()

    assert first == second and first["warnings"]


def test_split_files_with_warnings_do_not_depend_on_the_hash_seed(tmp_path: Path) -> None:
    root = tmp_path / "d"
    manifest = build_dataset(root, sample_count=13, images_per_sample=2)
    path = write_json(tmp_path / "m.json", manifest)
    child = (
        "import hashlib, json, sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'apps' / 'api')!r})\n"
        "from pathlib import Path\n"
        "from app.dataset_manifest import SplitConfig, dumps_canonical, generate_splits\n"
        "m = json.load(open(sys.argv[1], encoding='utf-8'))\n"
        "r = generate_splits(m, SplitConfig(seed=7, ratios={'train': 0.7, 'validation': 0.15, 'test': 0.15}), dataset_dir=Path(sys.argv[2]))\n"
        "print(hashlib.sha256(dumps_canonical(r.to_document()).encode()).hexdigest())\n"
    )
    hashes = set()
    for seed in ("0", "1", "12345"):
        run = subprocess.run(
            [sys.executable, "-B", "-c", child, str(path), str(root)],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=False,
        )
        assert run.returncode == 0, run.stderr
        hashes.add(run.stdout.strip())

    assert len(hashes) == 1


def test_the_cli_prints_that_warnings_are_also_in_the_split_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "d"
    manifest = build_dataset(root, sample_count=13, images_per_sample=2)
    path = write_json(tmp_path / "m.json", manifest)
    output = tmp_path / "s.json"

    code = generate_dataset_splits.main([str(path), "--dataset-dir", str(root), "--seed", "1", "--output", str(output)])

    out = capsys.readouterr().out
    assert code == 0
    assert "WARNING: split 'test' has only 1 physical sample(s)" in out
    document = json.loads(output.read_text(encoding="utf-8"))
    assert f"({len(document['warnings'])} warning(s) are also recorded in the split file)" in out


# === M4 / M6: schema 1.0 and permission consistency =============================================


def _manifest_1_0() -> dict[str, Any]:
    manifest = json.loads(EXAMPLE_1_0.read_text(encoding="utf-8"))
    second = clone({"i": manifest["images"][0]})["i"]
    second.update(image_id="img-2", sample_id="other-sample", relative_path="datasets/raw/two.jpg")
    manifest["images"].append(second)
    return manifest


def test_schema_1_0_evaluation_records_cannot_share_a_manifest_with_training() -> None:
    manifest = _manifest_1_0()
    manifest["images"][0]["split"] = "train"
    manifest["images"][1]["split"] = "independent_evaluation"

    result = validate_document(manifest)

    assert codes(result) == {"evaluation-records-mixed"}
    assert not audit_manifests([("old.json", manifest)]).passed()


def test_schema_1_0_still_cannot_be_cleared_for_any_use() -> None:
    manifest = _manifest_1_0()

    assert codes(validate_document(manifest, uses=["training"])) == {"no-permission-data"}
    assert codes(validate_document(manifest, require_commercial_training=True)) == {"no-permission-data"}


def test_the_umat_restriction_also_applies_to_schema_1_0_by_name_and_path() -> None:
    by_name = _manifest_1_0()
    by_name["dataset"]["name"] = "UMaT practicals"
    by_path = _manifest_1_0()
    by_path["images"][1]["relative_path"] = "datasets/raw/umat_practicals/two.jpg"

    assert "restricted-material-suspected" in codes(validate_document(by_name))
    assert "restricted-material-suspected" in codes(validate_document(by_path))
    assert not audit_manifests([("old.json", by_name)]).passed()


@pytest.mark.parametrize("uses", [[], ["validation"], ["validation", "testing"]])
def test_commercial_training_cannot_be_permitted_when_training_is_not(
    dataset: tuple[dict[str, Any], Path], uses: list[str]
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["permissions"].update(
        permitted_uses=uses, commercial_training="permitted", commercial_training_basis="Clause 4.2."
    )
    for record in manifest["records"]:
        record["split"] = "unassigned"

    result = validate_document(manifest)

    assert "commercial-training-without-training-permission" in codes(result)


def test_commercial_training_with_training_permitted_is_consistent(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["permissions"].update(commercial_training="permitted", commercial_training_basis="Clause 4.2.")

    assert validate_document(manifest).ok


def test_evidence_must_not_postdate_the_review(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["permissions"]["reviewed_on"] = "2026-03-01"
    manifest["dataset"]["licence"]["evidence"]["captured_on"] = "2026-03-02"

    assert codes(validate_document(manifest)) == {"evidence-after-review"}

    manifest["dataset"]["licence"]["evidence"]["captured_on"] = "2026-03-01"  # the same day is fine
    assert validate_document(manifest).ok


# === M7: robust error handling ===================================================================


def test_a_custom_schema_cannot_weaken_the_built_in_structure(tmp_path: Path) -> None:
    permissive = tmp_path / "permissive.json"
    permissive.write_text("{}", encoding="utf-8")

    result = validate_document({"schema_version": "1.1"}, schema_path=permissive)

    assert not result.ok
    assert codes(result) == {"schema"}  # the built-in schema's errors, and no KeyError


def test_a_custom_schema_can_add_requirements(dataset: tuple[dict[str, Any], Path], tmp_path: Path) -> None:
    stricter = tmp_path / "stricter.json"
    stricter.write_text(json.dumps({"type": "object", "required": ["owner_note"]}), encoding="utf-8")

    result = validate_document(dataset[0], schema_path=stricter)

    assert codes(result) == {"schema"}
    assert any("owner_note" in issue.message for issue in result.report.errors)
    assert validate_document(dataset[0], schema_path=_write(tmp_path / "ok.json", "{}")).ok


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_a_malformed_custom_schema_is_reported_not_raised(dataset: tuple[dict[str, Any], Path], tmp_path: Path) -> None:
    broken = _write(tmp_path / "broken.json", '{"type": 12}')
    unreadable = _write(tmp_path / "unreadable.json", "not json")

    assert codes(validate_document(dataset[0], schema_path=broken)) == {"schema-unavailable"}
    assert codes(validate_document(dataset[0], schema_path=unreadable)) == {"schema-unavailable"}


def test_excessively_nested_json_is_refused_safely() -> None:
    for depth in (MAX_NESTING + 1, 5000, 200_000):
        with pytest.raises(ManifestLoadError, match="nested more than"):
            parse_manifest_bytes(b"[" * depth + b"]" * depth)
    assert parse_manifest_bytes(b"[" * 20 + b"]" * 20) is not None


def test_oversized_numbers_are_refused_safely() -> None:
    too_many_digits = b'{"a": ' + b"9" * (MAX_INTEGER_DIGITS + 1) + b"}"
    allowed = b'{"a": ' + b"9" * MAX_INTEGER_DIGITS + b"}"

    for text in (too_many_digits, b'{"a": ' + b"9" * 5000 + b"}", b'{"a": -' + b"9" * 5000 + b"}", b'{"a": 1e999}', b'{"a": -1e999}'):
        with pytest.raises(ManifestLoadError):
            parse_manifest_bytes(text)
    assert parse_manifest_bytes(allowed)["a"] == int("9" * MAX_INTEGER_DIGITS)


def test_an_oversized_manifest_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path / "big.json", "{" + " " * 100 + "}")
    monkeypatch.setattr(loading_module, "MAX_MANIFEST_BYTES", 50)

    with pytest.raises(ManifestLoadError, match="larger than"):
        loading_module.load_manifest(path)


@pytest.mark.parametrize(
    "content",
    [b"[" * 300_000 + b"]" * 300_000, b'{"a": ' + b"9" * 6000 + b"}", b'{"a": 1e999}'],
)
def test_the_cli_tools_exit_cleanly_on_hostile_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: bytes
) -> None:
    path = tmp_path / "hostile.json"
    path.write_bytes(content)

    codes_ = [
        validate_dataset_manifest.main([str(path)]),
        audit_dataset_leakage.main([str(path)]),
        generate_dataset_splits.main([str(path), "--dataset-dir", str(tmp_path), "--seed", "1", "--output", str(tmp_path / "s.json")]),
    ]

    captured = capsys.readouterr()
    assert codes_ == [2, 2, 2]
    assert "Traceback" not in captured.err + captured.out
    assert captured.err.count("ERROR:") == 3


def test_an_unexpected_failure_exits_2_without_a_traceback(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_json(tmp_path / "m.json", dataset[0])

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(validate_dataset_manifest, "validate_document", boom)
    monkeypatch.setattr(audit_dataset_leakage, "audit_manifests", boom)
    monkeypatch.setattr(generate_dataset_splits, "generate_splits", boom)

    codes_ = [
        validate_dataset_manifest.main([str(path)]),
        audit_dataset_leakage.main([str(path)]),
        generate_dataset_splits.main([str(path), "--dataset-dir", str(dataset[1]), "--seed", "1", "--output", str(tmp_path / "s.json")]),
    ]

    err = capsys.readouterr().err
    assert codes_ == [2, 2, 2]
    assert err.count("unexpected failure (RuntimeError): synthetic failure") == 3
    assert "Traceback" not in err


def test_the_scripts_exit_2_for_hostile_input_when_run_as_files(tmp_path: Path) -> None:
    path = tmp_path / "hostile.json"
    path.write_bytes(b"[" * 300_000 + b"]" * 300_000)
    script = REPO_ROOT / "apps" / "api" / "scripts" / "validate_dataset_manifest.py"

    run = subprocess.run([sys.executable, "-B", str(script), str(path)], capture_output=True, text=True, check=False)

    assert run.returncode == 2
    assert "Traceback" not in run.stderr


def test_the_unreadable_manifest_safety_net_reports_instead_of_raising(
    dataset: tuple[dict[str, Any], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.dataset_manifest.validation as validation_module

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise KeyError("records")

    monkeypatch.setattr(validation_module, "build_view", broken)

    result = validate_document(dataset[0])

    assert codes(result) == {"manifest-unreadable"}
