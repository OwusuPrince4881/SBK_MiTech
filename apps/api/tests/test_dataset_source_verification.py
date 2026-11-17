"""A source's evidence must be verified in its own directory before anything derived from it is used.

The gap closed here: a source manifest was validated, but nothing checked that the record files and the
licence snapshot it lists exist, match their SHA-256 checksums, or that the snapshot is real text. A derived
dataset could be cleared on the strength of a declaration. Now a source is bound to its dataset directory
explicitly (`--source-dataset-dir DATASET_ID=DIR`; the audit's `--dataset-dir DATASET_ID=DIR`), its files are
checked there exactly as `--dataset-dir` checks a manifest's own, the same applies down the whole chain of
derivations, and a derived dataset that is in use is refused unless every source verifies.

All data is synthetic. Verifying files shows the recorded evidence is intact. It does not show that a licence
permits a use or that recorded permission or consent is true, and nothing here claims so.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import (
    RestrictedPathError,
    SourceDirectoryError,
    SplitConfig,
    SplitGenerationError,
    audit_manifests,
    describe_sources,
    generate_splits,
    load_source_views,
    parse_source_dataset_dirs,
    source_view,
    validate_document,
)
from app.dataset_manifest import validation as validation_module
from app.dataset_manifest.validation import ValidationResult
from dataset_builders import LICENCE_SNAPSHOT_PATH, build_dataset, fake_image_bytes, field_governance, sha256_hex
from scripts import audit_dataset_leakage, generate_dataset_splits, validate_dataset_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
RATIOS = {"train": 0.7, "validation": 0.15, "test": 0.15}
DENIED = {"status": "denied", "permitted_uses": [], "commercial_training": "not_permitted"}
NOT_VERIFIED = "derived-from-source-files-not-verified"
INVALID_FILES = "derived-from-source-files-invalid"


# --- fixtures ------------------------------------------------------------------------------------------------------


def make_source(tmp_path: Path, dataset_id: str = "source-a", split: str = "train", **overrides: Any) -> dict[str, Any]:
    """A synthetic source whose files are written to tmp_path/<dataset_id>."""
    manifest = build_dataset(tmp_path / dataset_id, sample_count=4, images_per_sample=2, dataset_id=dataset_id, **overrides)
    for record in manifest["records"]:
        record["split"] = split
    return manifest


def make_derived(
    tmp_path: Path, source: dict[str, Any], derived_id: str = "derived-b", split: str = "train", sample_count: int = 4
) -> tuple[dict[str, Any], Path]:
    """A derived dataset in tmp_path/<derived_id> whose first record derives from the source's first record."""
    root = tmp_path / derived_id
    derived = build_dataset(root, sample_count=sample_count, images_per_sample=2, dataset_id=derived_id)
    for record in derived["records"]:
        record["split"] = split
    derive(derived["records"][0], source, 0)
    return derived, root


def derive(record: dict[str, Any], source: dict[str, Any], index: int) -> None:
    record["provenance"] = {
        "derived_from": [{"dataset_id": source["dataset"]["dataset_id"], "record_id": source["records"][index]["record_id"]}]
    }


def bound(tmp_path: Path, *manifests: dict[str, Any]) -> dict[str, Any]:
    """Source views bound to the directories make_source wrote them to."""
    return {m["dataset"]["dataset_id"]: source_view(m, dataset_dir=tmp_path / m["dataset"]["dataset_id"]) for m in manifests}


def unbound(*manifests: dict[str, Any]) -> dict[str, Any]:
    return {m["dataset"]["dataset_id"]: source_view(m) for m in manifests}


def error_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.errors}


def warning_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.warnings}


def message(result: ValidationResult, code: str) -> str:
    return next(issue.message for issue in result.report.issues if issue.code == code)


def write_json(path: Path, document: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def file_of(tmp_path: Path, manifest: dict[str, Any], index: int) -> Path:
    return tmp_path / manifest["dataset"]["dataset_id"] / manifest["records"][index]["file"]["relative_path"]


def replace_snapshot(tmp_path: Path, source: dict[str, Any], content: bytes) -> None:
    """Give the source a new snapshot whose checksum the manifest records correctly: only its content is wrong."""
    (tmp_path / source["dataset"]["dataset_id"] / LICENCE_SNAPSHOT_PATH).write_bytes(content)
    source["dataset"]["licence"]["evidence"]["snapshot"]["sha256"] = sha256_hex(content)


# --- a fully verified, approved source can still be used -----------------------------------------------------------


def test_a_verified_approved_source_supports_a_cleared_derived_dataset(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=bound(tmp_path, source))

    assert result.ok and result.cleared_uses == ("training",), result.report.format()
    assessment = result.source_assessments["source-a"]
    assert assessment.files_checked and assessment.files_verified and assessment.file_error_count == 0
    assert assessment.files_declared == 9  # eight record files and the licence snapshot


def test_the_command_line_tools_accept_a_verified_source(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_source(tmp_path, split="unassigned")
    derived, root = make_derived(tmp_path, source, split="unassigned", sample_count=30)
    derived_path, source_path = write_json(tmp_path / "m" / "derived.json", derived), write_json(tmp_path / "m" / "source.json", source)
    bind = ["--source-manifest", str(source_path), "--source-dataset-dir", f"source-a={tmp_path / 'source-a'}"]

    validated = validate_dataset_manifest.main([str(derived_path), "--dataset-dir", str(root), "--use", "training", *bind])
    validated_out = " ".join(capsys.readouterr().out.split())
    split = generate_dataset_splits.main(
        [str(derived_path), "--dataset-dir", str(root), "--seed", "1", "--output", str(tmp_path / "s.json"), *bind]
    )
    split_out = " ".join(capsys.readouterr().out.split())

    assert validated == 0 and "recorded permission accepted for: training" in validated_out
    assert "source 'source-a': valid" in validated_out and "files VERIFIED in" in validated_out
    assert split == 0 and (tmp_path / "s.json").is_file() and "files VERIFIED in" in split_out


def test_the_audit_accepts_a_derivation_when_both_directories_are_given(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)

    without = audit_manifests([("source", source), ("derived", derived)])
    audit = audit_manifests(
        [("source", source), ("derived", derived)], dataset_dirs={"source": tmp_path / "source-a", "derived": root}
    )

    assert NOT_VERIFIED in {i.code for i in without.report.errors} and not without.passed()
    assert not {i.code for i in audit.report.errors if i.code.startswith("derived")}, audit.report.format()


# --- a derived dataset is refused when the source's evidence does not verify ----------------------------------------


def test_a_missing_source_image_is_refused(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)
    file_of(tmp_path, source, 3).unlink()

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=bound(tmp_path, source))

    assert INVALID_FILES in error_codes(result) and result.cleared_uses == ()
    text = message(result, INVALID_FILES)
    assert "[file-missing]" in text and source["records"][3]["file"]["relative_path"] in text and "do not match its manifest" in text


def test_a_source_image_whose_checksum_does_not_match_is_refused(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)
    file_of(tmp_path, source, 0).write_bytes(fake_image_bytes("replaced after the manifest was written"))

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=bound(tmp_path, source))

    assert INVALID_FILES in error_codes(result) and result.cleared_uses == ()
    assert "[checksum-mismatch]" in message(result, INVALID_FILES)


def test_a_missing_source_licence_snapshot_is_refused(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)
    (tmp_path / "source-a" / LICENCE_SNAPSHOT_PATH).unlink()

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=bound(tmp_path, source))

    assert INVALID_FILES in error_codes(result) and result.cleared_uses == ()
    text = message(result, INVALID_FILES)
    assert "[file-missing]" in text and "licence-snapshot.txt" in text


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (fake_image_bytes("a photograph"), "licence-snapshot-not-text"),
        (b"\xef\xbb\xbf", "licence-snapshot-empty"),
        (b"\xe2\x80\x8b" * 4, "licence-snapshot-empty"),
        (b"x", "licence-snapshot-meaningless"),
        (b"Terms.", "licence-snapshot-meaningless"),
    ],
    ids=["image", "bom-only", "zero-width-only", "x", "too-thin"],
)
def test_a_source_licence_snapshot_with_invalid_content_is_refused(tmp_path: Path, content: bytes, code: str) -> None:
    """The checksum matches: only the content is wrong, and the existing content rules catch it in a source too."""
    source = make_source(tmp_path)
    replace_snapshot(tmp_path, source, content)
    derived, root = make_derived(tmp_path, source)

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=bound(tmp_path, source))

    assert INVALID_FILES in error_codes(result) and result.cleared_uses == ()
    assert f"[{code}]" in message(result, INVALID_FILES)


def test_a_source_that_lists_no_licence_snapshot_is_still_refused_for_that(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    source["dataset"]["licence"]["evidence"] = {"captured_on": "2026-01-01", "reference": "a permission letter, ref 7"}
    derived, root = make_derived(tmp_path, source)

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=bound(tmp_path, source))

    assert error_codes(result) == {"derived-from-source-without-licence-snapshot"}
    assert "it lists no licence snapshot" in message(result, "derived-from-source-without-licence-snapshot")


# --- the source directory must be supplied, exist, and be unambiguous -------------------------------------------------


def test_a_source_without_a_directory_is_refused_because_its_files_were_not_verified(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=unbound(source))

    assert error_codes(result) == {NOT_VERIFIED} and result.cleared_uses == ()
    text = message(result, NOT_VERIFIED)
    assert "--source-dataset-dir source-a=DIRECTORY" in text and "not evidence that they exist" in text
    assert result.source_assessments["source-a"].files_checked is False


def test_a_source_directory_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)
    views = {"source-a": source_view(source, dataset_dir=tmp_path / "no-such-directory")}

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=views)

    assert INVALID_FILES in error_codes(result) and "[dataset-dir-invalid]" in message(result, INVALID_FILES)


def test_a_directory_that_holds_a_different_dataset_does_not_verify_the_source(tmp_path: Path) -> None:
    """Binding the wrong directory is not a way round the check: the files there do not match the manifest."""
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)
    build_dataset(tmp_path / "elsewhere", sample_count=2, images_per_sample=2, dataset_id="elsewhere")
    views = {"source-a": source_view(source, dataset_dir=tmp_path / "elsewhere")}

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=views)

    assert INVALID_FILES in error_codes(result) and "[file-missing]" in message(result, INVALID_FILES)


@pytest.mark.parametrize("where", ["the derived directory", "inside the derived directory"])
def test_a_source_directory_that_is_or_is_inside_the_derived_directory_is_ambiguous(tmp_path: Path, where: str) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)
    directory = root if where == "the derived directory" else root / "images"
    views = {"source-a": source_view(source, dataset_dir=directory)}

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=views)

    assert "ambiguous-dataset-directory" in error_codes(result) and result.cleared_uses == ()
    assert "the same directory, or one is inside the other" in message(result, "ambiguous-dataset-directory")


def test_two_sources_bound_to_one_directory_are_ambiguous(tmp_path: Path) -> None:
    first = make_source(tmp_path, "source-a")
    second = make_source(tmp_path, "source-c")
    derived, root = make_derived(tmp_path, first)
    derive(derived["records"][2], second, 0)
    views = {
        "source-a": source_view(first, dataset_dir=tmp_path / "source-a"),
        "source-c": source_view(second, dataset_dir=tmp_path / "source-a"),
    }

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=views)

    assert "ambiguous-dataset-directory" in error_codes(result)
    assert "source 'source-a'" in message(result, "ambiguous-dataset-directory")


def test_the_audit_refuses_two_manifests_that_share_a_directory(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    derived, _root = make_derived(tmp_path, source)

    audit = audit_manifests(
        [("source", source), ("derived", derived)],
        dataset_dirs={"source": tmp_path / "source-a", "derived": tmp_path / "source-a"},
    )

    assert "ambiguous-dataset-directory" in {i.code for i in audit.report.errors} and not audit.passed()


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["just-a-directory"], "must be DATASET_ID=DIRECTORY"),
        (["source-a="], "must be DATASET_ID=DIRECTORY"),
        (["=somewhere"], "must be DATASET_ID=DIRECTORY"),
        (["source-a=one", "source-a=two"], "names dataset_id 'source-a' twice"),
    ],
)
def test_a_malformed_or_repeated_source_directory_option_is_refused(values: list[str], expected: str) -> None:
    with pytest.raises(SourceDirectoryError, match=expected):
        parse_source_dataset_dirs(values)


def test_a_directory_for_a_dataset_that_no_source_manifest_has_is_refused(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    path = write_json(tmp_path / "m" / "source.json", source)

    with pytest.raises(SourceDirectoryError, match="no --source-manifest has that dataset_id"):
        load_source_views([path], dataset_dirs={"another-dataset": tmp_path / "source-a"})


def test_a_source_directory_in_a_restricted_location_is_refused_before_anything_is_read(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    path = write_json(tmp_path / "m" / "source.json", source)
    restricted = tmp_path / "data" / "evaluation" / "source-a"  # never created, never opened

    with pytest.raises(RestrictedPathError, match="restricted location"):
        load_source_views([path], dataset_dirs={"source-a": restricted})
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        (["--source-dataset-dir", "elsewhere"], "must be DATASET_ID=DIRECTORY"),
        (["--source-dataset-dir", "unknown-id=x"], "no --source-manifest has that dataset_id"),
        (["--source-dataset-dir", "source-a=x", "--source-dataset-dir", "source-a=y"], "twice"),
        (["--source-dataset-dir", "source-a=data/umat_practicals/a"], "restricted location"),
    ],
    ids=["no-id", "unknown-id", "repeated-id", "restricted"],
)
def test_the_command_line_tools_refuse_a_source_directory_option_they_cannot_use(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str], expected: str
) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)
    derived_path, source_path = write_json(tmp_path / "m" / "derived.json", derived), write_json(tmp_path / "m" / "source.json", source)
    arguments = ["--source-manifest", str(source_path), *extra]

    validated = validate_dataset_manifest.main([str(derived_path), "--dataset-dir", str(root), *arguments])
    validated_err = capsys.readouterr().err
    split = generate_dataset_splits.main(
        [str(derived_path), "--dataset-dir", str(root), "--seed", "1", "--output", str(tmp_path / "s.json"), *arguments]
    )
    split_err = capsys.readouterr().err

    assert validated == 2 and expected in " ".join(validated_err.split()) and "Traceback" not in validated_err
    assert split == 2 and expected in " ".join(split_err.split()) and not (tmp_path / "s.json").exists()


def test_the_splitter_never_writes_inside_a_source_dataset_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_source(tmp_path, split="unassigned")
    derived, root = make_derived(tmp_path, source, split="unassigned", sample_count=30)
    derived_path, source_path = write_json(tmp_path / "m" / "derived.json", derived), write_json(tmp_path / "m" / "source.json", source)
    snapshot = tmp_path / "source-a" / LICENCE_SNAPSHOT_PATH
    before = snapshot.read_bytes()

    code = generate_dataset_splits.main(
        [str(derived_path), "--dataset-dir", str(root), "--seed", "1", "--output", str(snapshot), "--overwrite",
         "--source-manifest", str(source_path), "--source-dataset-dir", f"source-a={tmp_path / 'source-a'}"]
    )

    assert code == 2 and "inside the directory of source 'source-a'" in capsys.readouterr().err
    assert snapshot.read_bytes() == before


# --- the whole chain of derivations is verified ----------------------------------------------------------------------


def chain(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path]:
    """top-c (in use) <- mid-b <- base-a, every one approved, with its files in tmp_path/<dataset_id>."""
    base = make_source(tmp_path, "base-a")
    mid, _mid_root = make_derived(tmp_path, base, derived_id="mid-b")
    top, top_root = make_derived(tmp_path, mid, derived_id="top-c")
    return base, mid, top, top_root


def test_a_fully_verified_chain_is_accepted(tmp_path: Path) -> None:
    base, mid, top, root = chain(tmp_path)

    result = validate_document(top, dataset_dir=root, uses=["training"], provenance_sources=bound(tmp_path, base, mid))

    assert result.ok and result.cleared_uses == ("training",), result.report.format()
    assert result.source_assessments["base-a"].files_verified and result.source_assessments["mid-b"].files_verified


def test_a_source_deeper_in_the_chain_with_a_missing_file_is_refused(tmp_path: Path) -> None:
    base, mid, top, root = chain(tmp_path)
    file_of(tmp_path, base, 5).unlink()

    result = validate_document(top, dataset_dir=root, uses=["training"], provenance_sources=bound(tmp_path, base, mid))

    assert "derived-from-invalid-source" in error_codes(result) and result.cleared_uses == ()
    text = message(result, "derived-from-invalid-source")
    assert "'mid-b'" in text and INVALID_FILES in text and "base-a" in text


def test_a_source_deeper_in_the_chain_without_a_directory_is_refused(tmp_path: Path) -> None:
    base, mid, top, root = chain(tmp_path)
    views = {"base-a": source_view(base), "mid-b": source_view(mid, dataset_dir=tmp_path / "mid-b")}

    result = validate_document(top, dataset_dir=root, uses=["training"], provenance_sources=views)

    assert "derived-from-invalid-source" in error_codes(result) and NOT_VERIFIED in message(result, "derived-from-invalid-source")


def test_a_use_cannot_be_laundered_through_an_intermediate_dataset(tmp_path: Path) -> None:
    """base-a permits only testing; mid-b claims training. top-c trains on data that comes from base-a."""
    base = make_source(tmp_path, "base-a", split="unassigned")
    base["dataset"]["permissions"]["permitted_uses"] = ["testing"]
    mid, _mid_root = make_derived(tmp_path, base, derived_id="mid-b", split="unassigned")
    top, root = make_derived(tmp_path, mid, derived_id="top-c", split="train")

    result = validate_document(top, dataset_dir=root, uses=["training"], provenance_sources=bound(tmp_path, base, mid))

    assert "derived-from-invalid-source" in error_codes(result) and result.cleared_uses == ()
    assert "derived-from-source-use-not-permitted" in message(result, "derived-from-invalid-source")


def test_a_cycle_in_the_chain_is_refused_and_terminates(tmp_path: Path) -> None:
    loop_b = make_source(tmp_path, "loop-b")
    loop_a, root = make_derived(tmp_path, loop_b, derived_id="loop-a")
    derive(loop_b["records"][1], loop_a, 1)  # loop-b now derives from loop-a, which derives from loop-b

    result = validate_document(loop_a, dataset_dir=root, uses=["training"], provenance_sources=bound(tmp_path, loop_a, loop_b))

    assert "derived-from-invalid-source" in error_codes(result) and result.cleared_uses == ()
    assert "derivation-cycle" in message(result, "derived-from-invalid-source")


# --- governance is still required of a source whose files verify -------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"permissions": DENIED}, "derived-from-denied-source"),
        ({"permissions": {"status": "pending_review", "permitted_uses": [], "commercial_training": "unknown"}}, "derived-from-unapproved-source"),
        ({"category": "ghana_field", "field_governance": field_governance(consent={"status": "unknown"})}, "derived-from-unresolved-field-source"),
    ],
    ids=["denied", "unapproved", "unresolved-ghana"],
)
def test_verified_files_do_not_make_an_ineligible_source_eligible(
    tmp_path: Path, overrides: dict[str, Any], expected: str
) -> None:
    source = make_source(tmp_path, split="unassigned", **overrides)
    derived, root = make_derived(tmp_path, source)

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=bound(tmp_path, source))

    assert expected in error_codes(result) and result.cleared_uses == ()
    assert result.source_assessments["source-a"].files_verified  # the evidence is intact; the governance is not
    assert "not independently verified" in message(result, expected)


# --- the protections that were already there ----------------------------------------------------------------------------


def test_a_schema_1_0_source_is_still_refused_and_its_files_are_not_checked(tmp_path: Path) -> None:
    example = json.loads((REPO_ROOT / "docs" / "examples" / "example_dataset_manifest.json").read_text(encoding="utf-8"))
    source_id = example["dataset"]["dataset_id"]
    stand_in = {"dataset": {"dataset_id": source_id}, "records": [{"record_id": example["images"][0]["image_id"]}]}
    derived, root = make_derived(tmp_path, stand_in)
    (tmp_path / "old-source").mkdir()
    views = {source_id: source_view(example, dataset_dir=tmp_path / "old-source")}

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=views)

    assert "derived-from-source-without-permission-data" in error_codes(result) and result.cleared_uses == ()
    assert NOT_VERIFIED not in error_codes(result) and INVALID_FILES not in error_codes(result)
    assert result.source_assessments[source_id].files_checked is False


def test_a_restricted_source_is_refused_and_its_directory_is_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_source(tmp_path, category="umat_practical")
    derived, root = make_derived(tmp_path, source)
    scanned: list[Path] = []
    real_scan = validation_module.scan_directory
    monkeypatch.setattr(validation_module, "scan_directory", lambda path: scanned.append(Path(path)) or real_scan(path))

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=bound(tmp_path, source))

    assert "derived-from-restricted-source" in error_codes(result) and result.cleared_uses == ()
    assert all(not str(path).startswith(str((tmp_path / "source-a").resolve())) for path in scanned)
    assert {c for c in error_codes(result) if c.startswith("derived-from-")} == {"derived-from-restricted-source"}


def test_a_source_named_after_restricted_material_is_still_refused(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    source["dataset"]["name"] = "UMaT practical photographs"
    derived, root = make_derived(tmp_path, source)

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=bound(tmp_path, source))

    assert "derived-from-restricted-source" in error_codes(result)


# --- drafts get warnings; in-use data gets errors ----------------------------------------------------------------------


def test_a_draft_derived_dataset_only_warns_about_an_unverified_or_failing_source(tmp_path: Path) -> None:
    source = make_source(tmp_path, split="unassigned")
    derived, _root = make_derived(tmp_path, source, split="unassigned")
    derived["dataset"]["permissions"] = {"status": "pending_review", "permitted_uses": [], "commercial_training": "unknown"}
    file_of(tmp_path, source, 1).unlink()

    unverified = validate_document(derived, provenance_sources=unbound(source))
    failing = validate_document(derived, provenance_sources=bound(tmp_path, source))

    assert unverified.ok and NOT_VERIFIED in warning_codes(unverified)
    assert failing.ok and INVALID_FILES in warning_codes(failing)
    assert "warning while everything is unassigned" in message(failing, INVALID_FILES)


def test_assigning_a_split_turns_an_unverified_source_into_an_error(tmp_path: Path) -> None:
    source = make_source(tmp_path, split="unassigned")
    derived, _root = make_derived(tmp_path, source, split="train")

    assert NOT_VERIFIED in error_codes(validate_document(derived, provenance_sources=unbound(source)))


# --- one rule in every tool --------------------------------------------------------------------------------------------


def test_every_tool_refuses_a_derivation_from_a_source_with_a_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = make_source(tmp_path, split="unassigned")
    derived, root = make_derived(tmp_path, source, split="unassigned", sample_count=30)
    file_of(tmp_path, source, 2).unlink()
    derived_path, source_path = write_json(tmp_path / "m" / "derived.json", derived), write_json(tmp_path / "m" / "source.json", source)
    bind = ["--source-manifest", str(source_path), "--source-dataset-dir", f"source-a={tmp_path / 'source-a'}"]
    assigned = json.loads(json.dumps(derived))  # the same derived dataset, now with splits, for the audit
    for record in assigned["records"]:
        record["split"] = "train"

    library = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=bound(tmp_path, source))
    with pytest.raises(SplitGenerationError) as split_library:
        generate_splits(derived, SplitConfig(seed=1, ratios=RATIOS), dataset_dir=root, provenance_sources=bound(tmp_path, source))
    validator = validate_dataset_manifest.main([str(derived_path), "--dataset-dir", str(root), "--use", "training", *bind])
    validator_err = capsys.readouterr().err
    splitter = generate_dataset_splits.main(
        [str(derived_path), "--dataset-dir", str(root), "--seed", "1", "--output", str(tmp_path / "s.json"), *bind]
    )
    splitter_err = capsys.readouterr().err
    audit = audit_manifests(
        [("source", source), ("derived", assigned)], dataset_dirs={"source": tmp_path / "source-a", "derived": root}
    )

    assert INVALID_FILES in error_codes(library)
    assert split_library.value.report is not None and INVALID_FILES in split_library.value.report.codes()
    assert validator == 2 and INVALID_FILES in validator_err and "files FAILED verification" in validator_err
    assert splitter == 2 and INVALID_FILES in splitter_err and not (tmp_path / "s.json").exists()
    assert INVALID_FILES in {i.code for i in audit.report.errors} and not audit.passed()


def test_the_audit_cli_takes_the_source_directory_with_dataset_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)
    source_path, derived_path = write_json(tmp_path / "m" / "source.json", source), write_json(tmp_path / "m" / "derived.json", derived)
    manifests = [str(source_path), str(derived_path)]

    without = audit_dataset_leakage.main([*manifests, "--dataset-dir", f"derived-b={root}"])
    without_out = capsys.readouterr().out
    with_both = audit_dataset_leakage.main(
        [*manifests, "--dataset-dir", f"derived-b={root}", "--dataset-dir", f"source-a={tmp_path / 'source-a'}"]
    )
    with_out = capsys.readouterr().out

    assert without == 2 and NOT_VERIFIED in without_out
    assert with_both == 0, with_out
    assert NOT_VERIFIED not in with_out and INVALID_FILES not in with_out


# --- each file is hashed once per run, and never reused across runs ------------------------------------------------------


def count_hashing(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    counts: dict[str, int] = {}
    real = validation_module._digest_file

    def counting(path: Path) -> tuple[str, int, bool]:
        key = str(path)
        counts[key] = counts.get(key, 0) + 1
        return real(path)

    monkeypatch.setattr(validation_module, "_digest_file", counting)
    return counts


def test_a_source_reached_through_two_paths_is_hashed_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """top-a derives from mid-b and mid-c, which both derive from base-d: base-d is needed twice but hashed once."""
    base = make_source(tmp_path, "base-d")
    mid_b, _ = make_derived(tmp_path, base, derived_id="mid-b")
    mid_c, _ = make_derived(tmp_path, base, derived_id="mid-c")
    top, root = make_derived(tmp_path, mid_b, derived_id="top-a")
    derive(top["records"][2], mid_c, 0)
    counts = count_hashing(monkeypatch)

    result = validate_document(top, dataset_dir=root, uses=["training"], provenance_sources=bound(tmp_path, base, mid_b, mid_c))

    assert result.ok, result.report.format()
    base_files = {key: n for key, n in counts.items() if f"{Path('base-d')}" in key}
    assert len(base_files) == 9 and set(base_files.values()) == {1}
    assert set(counts.values()) == {1}  # no file of any dataset was hashed twice


def test_a_verification_is_never_reused_after_a_file_changes(tmp_path: Path) -> None:
    """Two runs with the same views: the second re-hashes, so a file changed in between is caught."""
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)
    views = bound(tmp_path, source)

    first = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=views)
    file_of(tmp_path, source, 4).write_bytes(fake_image_bytes("changed between the two runs"))
    second = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=views)

    assert first.ok and second.cleared_uses == ()
    assert INVALID_FILES in error_codes(second) and "[checksum-mismatch]" in message(second, INVALID_FILES)


def test_the_audit_hashes_a_shared_source_once_for_all_the_manifests_that_need_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_source(tmp_path)
    first, first_root = make_derived(tmp_path, source, derived_id="derived-b")
    second, second_root = make_derived(tmp_path, source, derived_id="derived-c")
    counts = count_hashing(monkeypatch)

    audit_manifests(
        [("source", source), ("first", first), ("second", second)],
        dataset_dirs={"source": tmp_path / "source-a", "first": first_root, "second": second_root},
    )

    source_files = {key: n for key, n in counts.items() if f"{Path('source-a')}" in key}
    assert len(source_files) == 9 and set(source_files.values()) == {1}


# --- what is said, and what is not claimed ------------------------------------------------------------------------------


def test_the_output_says_what_file_verification_does_and_does_not_show(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    derived, root = make_derived(tmp_path, source)
    views = bound(tmp_path, source)

    result = validate_document(derived, dataset_dir=root, uses=["training"], provenance_sources=views)
    lines = describe_sources(views, result.source_assessments)

    line = " ".join(lines[0].split())
    assert "files VERIFIED in" in line and "match their recorded SHA-256" in line
    assert "does not show that the licence permits a use" in line and "not independently verified" in line
    for claim in ("licence is valid", "proves", "confirmed", "legally", "ownership is"):
        assert claim not in line.lower()


def test_the_documentation_describes_the_source_directory_option() -> None:
    text = " ".join((REPO_ROOT / "docs" / "dataset_workflow.md").read_text(encoding="utf-8").split())

    assert "--source-dataset-dir" in text and "derived-from-source-files-not-verified" in text
    assert "derived-from-source-files-invalid" in text and "ambiguous-dataset-directory" in text
    assert "time of check" in text.lower()
