import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import (
    ManifestLoadError,
    audit_manifests,
    dumps_canonical,
    load_manifest,
    validate_document,
)
from dataset_builders import build_dataset, record_by_id
from scripts import audit_dataset_leakage, generate_dataset_splits, validate_dataset_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
EXAMPLE_1_0 = REPO_ROOT / "docs" / "examples" / "example_dataset_manifest.json"
EXAMPLE_1_1 = REPO_ROOT / "docs" / "examples" / "example_dataset_manifest_1_1.json"


def write_json(path: Path, document: Any) -> Path:
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def make_workspace(root: Path, sample_count: int = 30, **kwargs: Any) -> tuple[Path, Path, dict[str, Any]]:
    """(manifest path, dataset directory, manifest) for a synthetic dataset under `root`."""
    root.mkdir(parents=True, exist_ok=True)
    dataset_dir = root / "files"
    manifest = build_dataset(dataset_dir, sample_count=sample_count, images_per_sample=2, **kwargs)
    return write_json(root / "manifest.json", manifest), dataset_dir, manifest


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def shared(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, dict[str, Any]]:
    """A valid 30-sample workspace used read-only by many tests."""
    return make_workspace(tmp_path_factory.mktemp("shared"))


def run_split(
    manifest_path: Path, dataset_dir: Path, output: Path, *extra: str, seed: str = "7"
) -> int:
    return generate_dataset_splits.main(
        [str(manifest_path), "--dataset-dir", str(dataset_dir), "--seed", seed, "--output", str(output), *extra]
    )


def assert_ascii(*texts: str) -> None:
    for text in texts:
        assert text.isascii(), "console output must be plain ASCII so Windows consoles can print it"


# --- loading --------------------------------------------------------------------------


def test_load_manifest_records_the_hash_of_the_exact_bytes(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_bytes(b'{"schema_version": "1.1"}\r\n')

    loaded = load_manifest(path)

    assert loaded.sha256 == hashlib.sha256(b'{"schema_version": "1.1"}\r\n').hexdigest()
    assert loaded.document == {"schema_version": "1.1"}


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b'{"a": 1, "a": 2}', "Duplicate JSON key 'a'"),
        (b'{"outer": {"split": "train", "split": "test"}}', "Duplicate JSON key 'split'"),
        (b'{"a": NaN}', "Non-standard JSON constant NaN"),
        (b'{"a": Infinity}', "Non-standard JSON constant Infinity"),
        (b'{"a": ', "not valid JSON"),
        (b"\xff\xfe\x00bad", "not valid UTF-8"),
        (b"\xef\xbb\xbf{}", "not valid JSON"),
        (b"", "not valid JSON"),
    ],
)
def test_load_manifest_rejects_unsafe_or_malformed_files(
    tmp_path: Path, content: bytes, message: str
) -> None:
    path = tmp_path / "m.json"
    path.write_bytes(content)

    with pytest.raises(ManifestLoadError, match=message):
        load_manifest(path)


def test_load_manifest_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ManifestLoadError, match="Unable to read"):
        load_manifest(tmp_path / "nope.json")


def test_canonical_json_is_sorted_lf_terminated_and_stable() -> None:
    text = dumps_canonical({"b": 1, "a": {"d": 2, "c": "é"}})

    assert text == '{\n  "a": {\n    "c": "é",\n    "d": 2\n  },\n  "b": 1\n}\n'
    assert "\r" not in text


# --- validate_dataset_manifest ---------------------------------------------------------


def test_validate_cli_accepts_a_valid_manifest_and_checks_files(
    shared: tuple[Path, Path, dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, dataset_dir, _ = shared

    code = validate_dataset_manifest.main([str(manifest_path), "--dataset-dir", str(dataset_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert f"Manifest is valid: {manifest_path}" in out
    assert "schema 1.1; 60 record(s); 30 physical sample(s)" in out
    assert "every file exists, with the recorded spelling, and was checked" in out
    assert "no use was requested" in out
    assert_ascii(out)


def test_validate_cli_says_when_files_were_not_checked(
    shared: tuple[Path, Path, dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    code = validate_dataset_manifest.main([str(shared[0])])

    out = capsys.readouterr().out
    assert code == 0
    assert "NOT checked" in out


def test_validate_cli_confirms_a_requested_use(
    shared: tuple[Path, Path, dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    code = validate_dataset_manifest.main(
        [str(shared[0]), "--dataset-dir", str(shared[1]), "--use", "training", "--use", "testing"]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "recorded permission accepted for: testing, training" in out
    assert "does not judge the licence or confirm that the use is lawful" in out
    assert "permission confirmed" not in out


def test_validate_cli_refuses_a_use_the_manifest_does_not_permit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = validate_dataset_manifest.main([str(EXAMPLE_1_1), "--use", "training"])

    captured = capsys.readouterr()
    assert code == 2
    assert "ERROR [use-not-permitted]" in captured.err
    assert "Manifest is NOT valid" in captured.err
    assert captured.out == ""


def test_validate_cli_can_require_commercial_training(
    shared: tuple[Path, Path, dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    code = validate_dataset_manifest.main([str(shared[0]), "--require-commercial-training"])

    assert code == 2
    assert "commercial-training-not-permitted" in capsys.readouterr().err


def test_validate_cli_reports_a_checksum_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest_path, dataset_dir, _ = make_workspace(tmp_path, sample_count=4)
    (dataset_dir / "images" / "sample-000" / "rec-000-0.jpg").write_bytes(b"changed after hashing")

    code = validate_dataset_manifest.main([str(manifest_path), "--dataset-dir", str(dataset_dir)])

    err = capsys.readouterr().err
    assert code == 2
    assert "ERROR [checksum-mismatch] records[0]:" in err


def test_validate_cli_prints_every_problem_with_its_location(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, _dir, manifest = make_workspace(tmp_path, sample_count=4)
    manifest["records"][1]["record_id"] = manifest["records"][0]["record_id"]
    record_by_id(manifest, "rec-001-0")["split"] = "train"
    write_json(manifest_path, manifest)

    code = validate_dataset_manifest.main([str(manifest_path)])

    err = capsys.readouterr().err
    assert code == 2
    assert "ERROR [duplicate-record-id] records[1]:" in err
    assert "ERROR [sample-in-multiple-splits] physical_sample_id sample-001:" in err
    assert "2 error(s)" in err
    assert_ascii(err)


def test_validate_cli_prints_warnings_but_still_succeeds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, _dir, manifest = make_workspace(tmp_path, sample_count=4)
    manifest["dataset"]["sample_identity"] = {"basis": "assumed_each_image_distinct"}
    write_json(manifest_path, manifest)

    code = validate_dataset_manifest.main([str(manifest_path)])

    assert code == 0
    assert "WARNING [unverified-sample-identity]" in capsys.readouterr().out


def test_validate_cli_still_accepts_schema_1_0(capsys: pytest.CaptureFixture[str]) -> None:
    code = validate_dataset_manifest.main([str(EXAMPLE_1_0)])

    out = capsys.readouterr().out
    assert code == 0
    assert "schema 1.0" in out


def test_validate_cli_cannot_clear_schema_1_0_for_a_use(capsys: pytest.CaptureFixture[str]) -> None:
    code = validate_dataset_manifest.main([str(EXAMPLE_1_0), "--use", "training"])

    assert code == 2
    assert "no-permission-data" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b'{"a": 1, "a": 2}', "Duplicate JSON key"),
        (b"\xff\xfe", "not valid UTF-8"),
        (b"not json", "not valid JSON"),
    ],
)
def test_validate_cli_reports_unreadable_manifests_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: bytes, message: str
) -> None:
    path = tmp_path / "m.json"
    path.write_bytes(content)

    code = validate_dataset_manifest.main([str(path)])

    err = capsys.readouterr().err
    assert code == 2
    assert message in err and "Traceback" not in err


def test_validate_cli_reports_a_missing_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = validate_dataset_manifest.main([str(tmp_path / "missing.json")])

    assert code == 2
    assert "Unable to read" in capsys.readouterr().err


def test_validate_cli_reports_a_missing_schema_file(
    shared: tuple[Path, Path, dict[str, Any]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = validate_dataset_manifest.main([str(shared[0]), "--schema", str(tmp_path / "none.json")])

    assert code == 2
    assert "schema-unavailable" in capsys.readouterr().err


def test_validate_cli_rejects_an_unknown_use() -> None:
    with pytest.raises(SystemExit) as exit_info:
        validate_dataset_manifest.main([str(EXAMPLE_1_1), "--use", "deployment"])

    assert exit_info.value.code == 2


def test_legacy_schema_only_function_is_still_available() -> None:
    manifest = json.loads(EXAMPLE_1_0.read_text(encoding="utf-8"))

    validate_dataset_manifest.validate_manifest(manifest)

    with pytest.raises(validate_dataset_manifest.ManifestValidationError):
        validate_dataset_manifest.validate_manifest(json.loads(EXAMPLE_1_1.read_text(encoding="utf-8")))


# --- generate_dataset_splits -----------------------------------------------------------


def test_split_cli_writes_a_reproducible_split_file(
    shared: tuple[Path, Path, dict[str, Any]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, dataset_dir, _ = shared
    before = sha256_of(manifest_path)

    first = run_split(manifest_path, dataset_dir, tmp_path / "a.json")
    second = run_split(manifest_path, dataset_dir, tmp_path / "b.json")

    out = capsys.readouterr().out
    assert (first, second) == (0, 0)
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()
    assert b"\r" not in (tmp_path / "a.json").read_bytes()
    assert sha256_of(manifest_path) == before
    assert f"manifest SHA-256 {before}" in out
    assert "seed 7" in out
    assert "Next: run audit_dataset_leakage.py" in out
    assert_ascii(out)
    document = json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))
    assert document["manifest_sha256"] == before
    assert document["seed"] == 7
    assert len(document["assignments"]) == 60


def test_split_cli_can_write_a_manifest_that_validates_and_audits_clean(
    shared: tuple[Path, Path, dict[str, Any]], tmp_path: Path
) -> None:
    manifest_path, dataset_dir, _ = shared
    before = sha256_of(manifest_path)

    code = run_split(manifest_path, dataset_dir, tmp_path / "s.json", "--output-manifest", str(tmp_path / "m.json"))

    applied = load_manifest(tmp_path / "m.json").document
    assert code == 0
    assert sha256_of(manifest_path) == before
    assert validate_document(applied, dataset_dir=dataset_dir, uses=["training", "validation", "testing"]).ok
    assert audit_manifests([("m", applied)]).passed(strict=True)
    split_file = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert {a["record_id"]: a["split"] for a in split_file["assignments"]} == {
        r["record_id"]: r["split"] for r in applied["records"]
    }


def test_split_cli_refuses_to_replace_existing_output(
    shared: tuple[Path, Path, dict[str, Any]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, dataset_dir, _ = shared
    existing = tmp_path / "s.json"
    existing.write_text("precious", encoding="utf-8")

    code = run_split(manifest_path, dataset_dir, existing)

    assert code == 2
    assert "already exists" in capsys.readouterr().err
    assert existing.read_text(encoding="utf-8") == "precious"
    assert run_split(manifest_path, dataset_dir, existing, "--overwrite") == 0
    assert existing.read_text(encoding="utf-8") != "precious"


def test_split_cli_cannot_replace_a_file_that_appears_after_the_check(
    shared: tuple[Path, Path, dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The up-front existence check can be raced; exclusive creation is the second guard."""
    manifest_path, dataset_dir, _ = shared
    existing = tmp_path / "s.json"
    existing.write_text("precious", encoding="utf-8")

    with monkeypatch.context() as patched:
        patched.setattr(Path, "exists", lambda self: False)  # as if the file appeared later
        code = run_split(manifest_path, dataset_dir, existing)

    assert code == 2
    assert "could not write output" in capsys.readouterr().err
    assert existing.read_text(encoding="utf-8") == "precious"


def test_split_cli_never_overwrites_the_input_manifest(
    shared: tuple[Path, Path, dict[str, Any]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, dataset_dir, _ = shared
    before = sha256_of(manifest_path)

    code = run_split(manifest_path, dataset_dir, tmp_path / "s.json", "--output-manifest", str(manifest_path), "--overwrite")

    assert code == 2
    assert "never overwritten" in capsys.readouterr().err
    assert sha256_of(manifest_path) == before
    assert not (tmp_path / "s.json").exists()


def test_split_cli_rejects_identical_output_paths(
    shared: tuple[Path, Path, dict[str, Any]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_split(shared[0], shared[1], tmp_path / "s.json", "--output-manifest", str(tmp_path / "s.json"))

    assert code == 2
    assert "same path" in capsys.readouterr().err


def test_split_cli_requires_the_output_folder_to_exist(
    shared: tuple[Path, Path, dict[str, Any]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_split(shared[0], shared[1], tmp_path / "missing-folder" / "s.json")

    assert code == 2
    assert "does not exist" in capsys.readouterr().err


def test_split_cli_writes_nothing_for_an_invalid_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest_path, dataset_dir, manifest = make_workspace(tmp_path / "w", sample_count=30)
    manifest["records"][1]["record_id"] = manifest["records"][0]["record_id"]
    write_json(manifest_path, manifest)

    code = run_split(manifest_path, dataset_dir, tmp_path / "s.json", "--output-manifest", str(tmp_path / "m.json"))

    err = capsys.readouterr().err
    assert code == 2
    assert "duplicate-record-id" in err and "no splits were generated" in err
    assert not (tmp_path / "s.json").exists() and not (tmp_path / "m.json").exists()


def test_split_cli_writes_nothing_when_a_file_was_tampered_with(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, dataset_dir, _ = make_workspace(tmp_path / "w", sample_count=30)
    (dataset_dir / "images" / "sample-000" / "rec-000-0.jpg").write_bytes(b"tampered")

    code = run_split(manifest_path, dataset_dir, tmp_path / "s.json")

    assert code == 2
    assert "checksum-mismatch" in capsys.readouterr().err
    assert not (tmp_path / "s.json").exists()


def test_split_cli_refuses_evaluation_data(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest_path, dataset_dir, manifest = make_workspace(tmp_path / "w", sample_count=30)
    manifest["dataset"]["category"] = "umat_practical"
    manifest["dataset"]["permissions"]["permitted_uses"] = ["independent_evaluation"]
    for record in manifest["records"]:
        record["split"] = "independent_evaluation"
    write_json(manifest_path, manifest)

    code = run_split(manifest_path, dataset_dir, tmp_path / "s.json")

    assert code == 2
    assert "restricted UMaT/MN273 material" in capsys.readouterr().err
    assert not (tmp_path / "s.json").exists()


def test_split_cli_reports_bad_ratios(
    shared: tuple[Path, Path, dict[str, Any]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_split(shared[0], shared[1], tmp_path / "s.json", "--train", "0.5")

    assert code == 2
    assert "add up to 1" in capsys.readouterr().err
    assert not (tmp_path / "s.json").exists()


def test_split_cli_applies_commercial_and_resplit_options(
    shared: tuple[Path, Path, dict[str, Any]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_split(shared[0], shared[1], tmp_path / "s.json", "--require-commercial-training")

    assert code == 2
    assert "commercial-training-not-permitted" in capsys.readouterr().err

    manifest_path, dataset_dir, manifest = make_workspace(tmp_path / "w", sample_count=30)
    for record in manifest["records"]:
        record["split"] = "train"
    write_json(manifest_path, manifest)
    assert run_split(manifest_path, dataset_dir, tmp_path / "t.json") == 2
    assert "already have a split" in capsys.readouterr().err
    assert run_split(manifest_path, dataset_dir, tmp_path / "t.json", "--allow-resplit") == 0


def test_split_cli_applies_the_session_and_identity_options(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest_path, dataset_dir, manifest = make_workspace(tmp_path / "w", sample_count=30, sessions="shared_pairs")
    assert run_split(manifest_path, dataset_dir, tmp_path / "g.json", "--group-capture-sessions") == 0
    assert "kept together as 15 group(s)" in capsys.readouterr().out

    manifest["dataset"]["sample_identity"] = {"basis": "assumed_each_image_distinct"}
    write_json(manifest_path, manifest)
    assert run_split(manifest_path, dataset_dir, tmp_path / "x.json") == 2
    assert "assumed_each_image_distinct" in capsys.readouterr().err
    assert (
        run_split(
            manifest_path,
            dataset_dir,
            tmp_path / "x.json",
            "--accept-unverified-sample-identity",
            "synthetic test: the fixture has no real samples",
        )
        == 0
    )


@pytest.mark.parametrize("missing", ["--seed", "--output", "--dataset-dir"])
def test_split_cli_requires_seed_output_and_dataset_directory(missing: str, tmp_path: Path) -> None:
    arguments = {
        "--dataset-dir": str(tmp_path),
        "--seed": "1",
        "--output": str(tmp_path / "s.json"),
    }
    argv = [str(EXAMPLE_1_1)]
    for name, value in arguments.items():
        if name != missing:
            argv += [name, value]

    with pytest.raises(SystemExit) as exit_info:
        generate_dataset_splits.main(argv)

    assert exit_info.value.code == 2


# --- audit_dataset_leakage -------------------------------------------------------------


def split_workspace(root: Path, sample_count: int = 30) -> tuple[Path, dict[str, Any]]:
    manifest_path, dataset_dir, _ = make_workspace(root, sample_count=sample_count)
    assert run_split(manifest_path, dataset_dir, root / "s.json", "--output-manifest", str(root / "split.json")) == 0
    return root / "split.json", load_manifest(root / "split.json").document


def test_audit_cli_passes_a_clean_dataset(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, _ = split_workspace(tmp_path)
    capsys.readouterr()

    code = audit_dataset_leakage.main([str(path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "Findings: 0 error(s), 0 warning(s)" in out
    assert "Not checked: near-duplicate images" in out
    assert "RESULT: PASS" in out
    assert_ascii(out)


def test_audit_cli_fails_on_leakage_and_names_the_sample(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, manifest = split_workspace(tmp_path)
    first = record_by_id(manifest, "rec-000-0")
    other = "test" if first["split"] != "test" else "train"
    record_by_id(manifest, "rec-000-1")["split"] = other
    write_json(path, manifest)
    capsys.readouterr()

    code = audit_dataset_leakage.main([str(path)])

    out = capsys.readouterr().out
    assert code == 2
    assert "ERROR [sample-in-multiple-splits]" in out
    assert "sample-000" in out
    assert "RESULT: FAIL" in out


def test_audit_cli_strict_mode_fails_on_warnings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest_path, _dir, manifest = make_workspace(tmp_path, sample_count=4)
    capsys.readouterr()

    relaxed = audit_dataset_leakage.main([str(manifest_path)])
    strict = audit_dataset_leakage.main([str(manifest_path), "--strict"])

    out = capsys.readouterr().out
    assert (relaxed, strict) == (0, 2)
    assert "WARNING [unassigned-records]" in out
    assert "RESULT: FAIL (strict: warnings count as failures)" in out


def test_audit_cli_catches_a_training_image_in_the_evaluation_dataset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    train_path, train = split_workspace(tmp_path / "train")
    evaluation = build_dataset(
        tmp_path / "eval-files",
        sample_count=2,
        images_per_sample=1,
        dataset_id="synthetic-evaluation",
        permissions={
            "status": "approved",
            "permitted_uses": ["independent_evaluation"],
            "commercial_training": "unknown",
            "reviewed_by": "synthetic-test-fixture",
            "reviewed_on": "2026-01-01",
        },
    )
    for record in evaluation["records"]:
        record["split"] = "independent_evaluation"
    evaluation["records"][0]["file"]["sha256"] = record_by_id(train, "rec-000-0")["file"]["sha256"]
    eval_path = write_json(tmp_path / "eval.json", evaluation)
    capsys.readouterr()

    code = audit_dataset_leakage.main([str(train_path), str(eval_path)])

    out = capsys.readouterr().out
    assert code == 2
    assert "duplicate-content-across-splits" in out
    assert "synthetic-evaluation" in out


def test_audit_cli_reports_an_unreadable_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")

    code = audit_dataset_leakage.main([str(path)])

    assert code == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_audit_cli_requires_at_least_one_manifest() -> None:
    with pytest.raises(SystemExit) as exit_info:
        audit_dataset_leakage.main([])

    assert exit_info.value.code == 2


# --- running the scripts as files ------------------------------------------------------


def run_script(name: str, *arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / name), *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_scripts_run_by_file_path_from_any_directory(tmp_path: Path) -> None:
    """The documented commands run `python apps/api/scripts/<name>.py` from the repo root."""
    manifest_path, dataset_dir, _ = make_workspace(tmp_path / "w")

    validated = run_script(
        "validate_dataset_manifest.py", str(manifest_path), "--dataset-dir", str(dataset_dir), "--use", "training", cwd=tmp_path
    )
    split = run_script(
        "generate_dataset_splits.py",
        str(manifest_path),
        "--dataset-dir", str(dataset_dir),
        "--seed", "3",
        "--output", str(tmp_path / "s.json"),
        "--output-manifest", str(tmp_path / "m.json"),
        cwd=tmp_path,
    )
    audited = run_script(
        "audit_dataset_leakage.py", str(tmp_path / "m.json"), "--dataset-dir", str(dataset_dir), cwd=tmp_path
    )

    assert validated.returncode == 0, validated.stderr
    assert "recorded permission accepted for: training" in validated.stdout
    assert split.returncode == 0, split.stderr
    assert audited.returncode == 0, audited.stderr
    assert "RESULT: PASS" in audited.stdout


@pytest.mark.parametrize(
    "name",
    ["validate_dataset_manifest.py", "generate_dataset_splits.py", "audit_dataset_leakage.py"],
)
def test_script_help_runs(name: str, tmp_path: Path) -> None:
    completed = run_script(name, "--help", cwd=tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout


# --- sample identity in the command-line output -----------------------------------------


def test_validate_cli_always_states_the_identity_basis(
    shared: tuple[Path, Path, dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    code = validate_dataset_manifest.main([str(shared[0])])

    assert code == 0
    assert "(sample identity basis: recorded_by_collector)" in capsys.readouterr().out


def test_validate_cli_flags_assumed_identity_on_the_summary_line_and_as_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, _dir, _manifest = make_workspace(
        tmp_path, sample_count=4, sample_identity={"basis": "assumed_each_image_distinct"}
    )

    code = validate_dataset_manifest.main([str(manifest_path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "(sample identity ASSUMED, NOT VERIFIED: each image is treated as its own sample)" in out
    assert "WARNING [unverified-sample-identity]" in out


def test_validate_cli_flags_schema_1_0_identity_as_not_verified(capsys: pytest.CaptureFixture[str]) -> None:
    code = validate_dataset_manifest.main([str(EXAMPLE_1_0)])

    out = capsys.readouterr().out
    assert code == 0
    assert "sample identity NOT VERIFIED: schema 1.0 does not record how it was established" in out


def test_split_cli_override_needs_a_written_reason_and_records_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, dataset_dir, _ = make_workspace(
        tmp_path / "w", sample_count=30, sample_identity={"basis": "assumed_each_image_distinct"}
    )
    splits = tmp_path / "splits.json"
    with_manifest = ("--output-manifest", str(tmp_path / "split.json"))

    refused = run_split(manifest_path, dataset_dir, splits)
    assert refused == 2
    assert "written reason" in capsys.readouterr().err

    blank = run_split(manifest_path, dataset_dir, splits, "--accept-unverified-sample-identity", "   ")
    assert blank == 2
    assert "reason" in capsys.readouterr().err
    assert not splits.exists()

    with pytest.raises(SystemExit) as exit_info:  # the flag needs a value
        run_split(manifest_path, dataset_dir, splits, "--accept-unverified-sample-identity")
    assert exit_info.value.code == 2
    capsys.readouterr()

    accepted = run_split(
        manifest_path,
        dataset_dir,
        splits,
        *with_manifest,
        "--accept-unverified-sample-identity",
        "Pilot only: no sample IDs exist yet.",
    )

    out = capsys.readouterr().out
    assert accepted == 0
    assert "WARNING: SAMPLE IDENTITY IS ASSUMED, NOT VERIFIED" in out
    assert "(sample identity ASSUMED, NOT VERIFIED)" in out
    document = json.loads(splits.read_text(encoding="utf-8"))
    assert document["sample_identity_assumed"] is True
    assert document["unverified_sample_identity_reason"] == "Pilot only: no sample IDs exist yet."
    produced = load_manifest(tmp_path / "split.json").document
    assert produced["dataset"]["sample_identity"]["basis"] == "assumed_each_image_distinct"


def test_split_cli_reports_a_verified_basis_plainly(
    shared: tuple[Path, Path, dict[str, Any]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_split(shared[0], shared[1], tmp_path / "s.json")

    out = capsys.readouterr().out
    assert code == 0
    assert "(sample identity basis: recorded_by_collector)" in out
    assert "ASSUMED" not in out
    assert json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))["sample_identity_assumed"] is False


def test_audit_cli_never_prints_a_bare_pass_for_unverified_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, manifest = split_workspace(tmp_path)
    manifest["dataset"]["sample_identity"] = {"basis": "assumed_each_image_distinct"}
    write_json(path, manifest)
    capsys.readouterr()

    relaxed = audit_dataset_leakage.main([str(path)])
    out = capsys.readouterr().out
    strict = audit_dataset_leakage.main([str(path), "--strict"])
    strict_out = capsys.readouterr().out

    assert relaxed == 0
    assert "sample identity: ASSUMED, NOT VERIFIED (each image is treated as its own physical sample)" in out
    assert "LIMITATION: the physical sample IDs are NOT verified for:" in out
    assert "sample-level leakage cannot be ruled out" in out
    assert "RESULT: PASS WITH LIMITATIONS" in out
    assert "RESULT: PASS\n" not in out
    assert strict == 2
    assert "RESULT: FAIL (strict: warnings count as failures)" in strict_out


def test_audit_cli_gives_a_plain_pass_when_the_identity_basis_is_recorded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 240 samples, so that the validation and test splits each hold the 30 or more physical
    # samples below which the audit qualifies its result (see the split-quality tests).
    path, _ = split_workspace(tmp_path, sample_count=240)
    capsys.readouterr()

    code = audit_dataset_leakage.main([str(path), "--dataset-dir", str(tmp_path / "files")])

    out = capsys.readouterr().out
    assert code == 0
    assert "sample identity: basis recorded as recorded_by_collector" in out
    assert "files: verified against the dataset directory" in out
    assert "LIMITATION" not in out
    assert "RESULT: PASS\n" in out
    assert "leakage only" in out and "not approval of the splits" in out


def test_audit_cli_treats_schema_1_0_as_an_unverified_identity(capsys: pytest.CaptureFixture[str]) -> None:
    code = audit_dataset_leakage.main([str(EXAMPLE_1_0)])

    out = capsys.readouterr().out
    assert code == 0
    assert "sample identity: NOT VERIFIED (schema 1.0 does not record how the sample IDs were established)" in out
    assert "RESULT: PASS WITH LIMITATIONS" in out
