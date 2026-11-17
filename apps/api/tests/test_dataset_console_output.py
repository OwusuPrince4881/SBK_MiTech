"""Console output is safe for non-ASCII paths, and a finished split run cannot be mis-reported (audit N7).

The original failure: a valid manifest under a Japanese folder name, with output in a legacy code page
(cp1252), made the tool exit 2 with `UnicodeEncodeError` after the work was done, and the split generator
had already written its files. The paths and text here are synthetic.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import safe_text
from dataset_builders import build_dataset
from scripts import generate_dataset_splits

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
JAPANESE = "日本語"
LEGACY = {"PYTHONIOENCODING": "cp1252"}  # as on a Windows terminal or a redirected stream


def run_script(name: str, *arguments: object, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    environment = {key: value for key, value in os.environ.items() if key not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    environment.update(env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / name), *map(str, arguments)],
        capture_output=True, env=environment, check=False,
    )


def write_json(path: Path, document: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# --- safe_text -------------------------------------------------------------------------------------------


def test_safe_text_escapes_everything_outside_printable_ascii() -> None:
    assert safe_text("plain text, 123 / \\ path") == "plain text, 123 / \\ path"
    assert safe_text(JAPANESE) == "\\u65e5\\u672c\\u8a9e"
    assert safe_text("café") == "caf\\xe9"
    assert safe_text("emoji \U0001F600") == "emoji \\U0001f600"
    assert safe_text("a\x07b\x85c\x7f") == "a\\x07b\\x85c\\x7f"
    assert safe_text("bad \udc80 surrogate") == "bad \\udc80 surrogate"
    assert safe_text("line one\nline two\tend") == "line one\nline two\tend"
    assert safe_text(Path(JAPANESE) / "m.json").isascii()
    assert safe_text(None) == "None" and safe_text(12) == "12"


# --- the three tools, on a Japanese path, in a legacy code page -----------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    """(manifest path, dataset directory), both under a Japanese folder name."""
    folder = tmp_path / JAPANESE
    dataset_dir = folder / f"{JAPANESE}-files"
    manifest = build_dataset(dataset_dir, sample_count=40, images_per_sample=2)
    return write_json(folder / f"manifest-{JAPANESE}.json", manifest), dataset_dir


def test_the_validator_succeeds_and_prints_ascii_for_a_non_latin_path(workspace: tuple[Path, Path]) -> None:
    manifest_path, dataset_dir = workspace

    done = run_script("validate_dataset_manifest.py", manifest_path, "--dataset-dir", dataset_dir, "--use", "training", env=LEGACY)

    assert done.returncode == 0, done.stderr
    text = done.stdout.decode("ascii")  # raises if anything non-ASCII was printed
    assert "Manifest is valid:" in text and "\\u65e5\\u672c\\u8a9e" in text
    assert "recorded permission accepted for: training" in text


def test_the_validator_failure_report_is_ascii_too(workspace: tuple[Path, Path], tmp_path: Path) -> None:
    manifest_path, dataset_dir = workspace
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["dataset"]["name"] = f"UMaT {JAPANESE}"
    (dataset_dir / f"{JAPANESE}.jpg").write_bytes(b"stray")
    bad = write_json(tmp_path / JAPANESE / "bad.json", document)

    done = run_script("validate_dataset_manifest.py", bad, "--dataset-dir", dataset_dir, env=LEGACY)

    assert done.returncode == 2
    assert "restricted-material-suspected" in done.stderr.decode("ascii")
    assert "\\u65e5\\u672c\\u8a9e.jpg" in done.stderr.decode("ascii")


def test_the_audit_succeeds_and_prints_ascii_for_a_non_latin_path(workspace: tuple[Path, Path]) -> None:
    manifest_path, dataset_dir = workspace

    done = run_script("audit_dataset_leakage.py", manifest_path, "--dataset-dir", dataset_dir, env=LEGACY)

    assert done.returncode == 0, done.stderr
    assert "RESULT:" in done.stdout.decode("ascii") and "\\u65e5\\u672c\\u8a9e" in done.stdout.decode("ascii")


def test_the_split_generator_writes_complete_files_and_exits_zero_for_a_non_latin_path(
    workspace: tuple[Path, Path], tmp_path: Path
) -> None:
    """The original failure: files were written, then the summary raised and the exit code was 2."""
    manifest_path, dataset_dir = workspace
    output = tmp_path / JAPANESE / f"split-{JAPANESE}.json"
    output_manifest = tmp_path / JAPANESE / f"with-splits-{JAPANESE}.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in document["records"]:
        record["split"] = "unassigned"
    manifest_path = write_json(manifest_path, document)

    done = run_script(
        "generate_dataset_splits.py", manifest_path, "--dataset-dir", dataset_dir, "--seed", 3,
        "--output", output, "--output-manifest", output_manifest, env=LEGACY,
    )

    assert done.returncode == 0, done.stderr
    assert "Splits written:" in done.stdout.decode("ascii")
    split_file = json.loads(output.read_text(encoding="utf-8"))
    assert split_file["split_format_version"] == "1.0" and len(split_file["assignments"]) == 80
    assert {r["split"] for r in json.loads(output_manifest.read_text(encoding="utf-8"))["records"]} == {
        "train", "validation", "test",
    }


# --- the split generator writes all of its output or none of it -------------------------------------------------


@pytest.fixture
def split_inputs(tmp_path: Path) -> tuple[Path, Path]:
    dataset_dir = tmp_path / "files"
    manifest = build_dataset(dataset_dir, sample_count=40, images_per_sample=2)
    return write_json(tmp_path / "manifest.json", manifest), dataset_dir


def run_split(manifest_path: Path, dataset_dir: Path, *extra: str) -> int:
    return generate_dataset_splits.main(
        [str(manifest_path), "--dataset-dir", str(dataset_dir), "--seed", "1", *extra]
    )


def test_a_failure_writing_the_second_file_removes_the_first(
    split_inputs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, dataset_dir = split_inputs
    real_open = Path.open

    def failing_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == "with-splits.json":
            raise PermissionError("synthetic failure")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)

    code = run_split(manifest_path, dataset_dir, "--output", str(tmp_path / "split.json"), "--output-manifest", str(tmp_path / "with-splits.json"))

    assert code == 2 and "could not write output" in capsys.readouterr().err
    assert not (tmp_path / "split.json").exists() and not (tmp_path / "with-splits.json").exists()


def test_a_failure_while_overwriting_leaves_the_earlier_files_untouched_and_no_debris(
    split_inputs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, dataset_dir = split_inputs
    (tmp_path / "split.json").write_text("precious split", encoding="utf-8")
    (tmp_path / "with-splits.json").write_text("precious manifest", encoding="utf-8")
    real_open = Path.open

    def failing_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name.startswith(".with-splits.json."):
            raise OSError("synthetic failure")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)

    code = run_split(
        manifest_path, dataset_dir, "--output", str(tmp_path / "split.json"),
        "--output-manifest", str(tmp_path / "with-splits.json"), "--overwrite",
    )

    assert code == 2 and "could not write output" in capsys.readouterr().err
    assert (tmp_path / "split.json").read_text(encoding="utf-8") == "precious split"
    assert (tmp_path / "with-splits.json").read_text(encoding="utf-8") == "precious manifest"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")) == []


def test_overwrite_replaces_both_files_completely(split_inputs: tuple[Path, Path], tmp_path: Path) -> None:
    manifest_path, dataset_dir = split_inputs
    (tmp_path / "split.json").write_text("old", encoding="utf-8")

    code = run_split(manifest_path, dataset_dir, "--output", str(tmp_path / "split.json"), "--overwrite")

    assert code == 0 and json.loads((tmp_path / "split.json").read_text(encoding="utf-8"))["algorithm"]
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_a_terminal_that_cannot_take_the_summary_does_not_turn_success_into_failure(
    split_inputs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path, dataset_dir = split_inputs

    def closed_terminal(*args: Any, **kwargs: Any) -> None:
        raise OSError("the output stream was closed")

    monkeypatch.setattr(generate_dataset_splits, "say", closed_terminal)

    code = run_split(manifest_path, dataset_dir, "--output", str(tmp_path / "split.json"))

    assert code == 0
    assert json.loads((tmp_path / "split.json").read_text(encoding="utf-8"))["split_format_version"] == "1.0"


def test_nothing_is_written_when_a_non_latin_manifest_is_refused(workspace: tuple[Path, Path], tmp_path: Path) -> None:
    manifest_path, dataset_dir = workspace
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["records"][0]["file"]["sha256"] = "0" * 64  # the file no longer matches its checksum
    write_json(manifest_path, document)
    output = tmp_path / JAPANESE / "never.json"

    done = run_script("generate_dataset_splits.py", manifest_path, "--dataset-dir", dataset_dir, "--seed", 1, "--output", output, env=LEGACY)

    assert done.returncode == 2 and not output.exists()
    assert "checksum-mismatch" in done.stderr.decode("ascii")  # the refusal is printable too
