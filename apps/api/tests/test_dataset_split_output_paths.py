"""The split generator must never write inside the dataset directory (final audit finding M-3).

The original gap: `--output` and `--output-manifest` were checked against the input manifest only. With
`--overwrite`, `--output` could be an image or the licence snapshot, and the tool would replace it with its own
JSON, destroying a source file. The generator now refuses any output path that resolves to, or inside, the
dataset directory (links and `..` are resolved first, and letter case is ignored where the OS ignores it), and
never replaces a source manifest. Every other overwrite protection is unchanged and is re-checked here.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import load_manifest
from dataset_builders import (
    LICENCE_SNAPSHOT_PATH,
    build_dataset,
    filesystem_is_case_sensitive,
    link_directory,
)
from scripts import generate_dataset_splits
from scripts.generate_dataset_splits import _inside

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_DOC = REPO_ROOT / "docs" / "dataset_workflow.md"
INSIDE_MESSAGE = "is inside the dataset directory"


class Workspace:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.dataset_dir = root / "files"
        self.manifest = build_dataset(self.dataset_dir, sample_count=20, images_per_sample=2)
        self.manifest_path = root / "manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")
        self.image = self.dataset_dir / self.manifest["records"][0]["file"]["relative_path"]
        self.snapshot = self.dataset_dir / LICENCE_SNAPSHOT_PATH

    def run(self, output: Path | str, *extra: str, dataset_dir: Path | str | None = None) -> int:
        return generate_dataset_splits.main(
            [
                str(self.manifest_path),
                "--dataset-dir",
                str(self.dataset_dir if dataset_dir is None else dataset_dir),
                "--seed",
                "7",
                "--output",
                str(output),
                *extra,
            ]
        )

    def state(self) -> dict[str, str]:
        """Every file under the dataset directory with its checksum: what 'untouched' means."""
        return {
            path.relative_to(self.dataset_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(self.dataset_dir.rglob("*"))
            if path.is_file()
        }


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path / "work")


# --- an output inside the dataset directory is refused -------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    ["splits.json", "licence/splits.json", "images/splits.json", "new-folder/splits.json", "a/b/c/splits.json"],
)
def test_output_inside_the_dataset_directory_is_refused_and_nothing_is_written(
    workspace: Workspace, capsys: pytest.CaptureFixture[str], relative: str
) -> None:
    before = workspace.state()
    target = workspace.dataset_dir / relative
    parent_existed = target.parent.exists()

    code = workspace.run(target)

    err = capsys.readouterr().err
    assert code == 2 and INSIDE_MESSAGE in err and "--output" in err and err.isascii()
    assert "never writes into a dataset directory" in err
    assert not target.exists() and workspace.state() == before
    assert target.parent.exists() == parent_existed  # no half-made folder either


def test_the_dataset_directory_itself_is_refused_as_an_output(workspace: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    code = workspace.run(workspace.dataset_dir)

    assert code == 2 and INSIDE_MESSAGE in capsys.readouterr().err


def test_output_manifest_inside_the_dataset_directory_is_refused_and_no_file_is_written(
    workspace: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    before = workspace.state()
    split_file = workspace.root / "splits.json"
    inside = workspace.dataset_dir / "licence" / "manifest-with-splits.json"

    code = workspace.run(split_file, "--output-manifest", str(inside))

    err = capsys.readouterr().err
    assert code == 2 and INSIDE_MESSAGE in err and "--output-manifest" in err
    assert not split_file.exists(), "the first file must not be written when the second is refused"
    assert not inside.exists() and workspace.state() == before


def test_both_outputs_inside_the_dataset_directory_are_refused(workspace: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    before = workspace.state()

    code = workspace.run(workspace.dataset_dir / "s.json", "--output-manifest", str(workspace.dataset_dir / "m.json"))

    assert code == 2 and INSIDE_MESSAGE in capsys.readouterr().err
    assert workspace.state() == before


# --- --overwrite cannot be used to destroy a source file ----------------------------------------------------


@pytest.mark.parametrize("which", ["image", "snapshot"])
def test_overwrite_cannot_replace_an_image_or_the_licence_snapshot(
    workspace: Workspace, capsys: pytest.CaptureFixture[str], which: str
) -> None:
    """The original gap: --output <an image> --overwrite replaced the image with the split file."""
    target = workspace.image if which == "image" else workspace.snapshot
    before = workspace.state()
    original = target.read_bytes()

    code = workspace.run(target, "--overwrite")

    assert code == 2 and INSIDE_MESSAGE in capsys.readouterr().err
    assert target.read_bytes() == original and workspace.state() == before


@pytest.mark.parametrize("which", ["image", "snapshot"])
def test_overwrite_cannot_replace_a_source_file_through_output_manifest(
    workspace: Workspace, capsys: pytest.CaptureFixture[str], which: str
) -> None:
    target = workspace.image if which == "image" else workspace.snapshot
    before = workspace.state()
    split_file = workspace.root / "splits.json"

    code = workspace.run(split_file, "--output-manifest", str(target), "--overwrite")

    assert code == 2 and INSIDE_MESSAGE in capsys.readouterr().err
    assert not split_file.exists() and workspace.state() == before


# --- the ways of writing "inside" that are not obvious --------------------------------------------------------


def test_a_path_that_climbs_out_and_back_in_is_refused(workspace: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    before = workspace.state()
    sneaky = workspace.root / "elsewhere" / ".." / "files" / "licence" / ".." / "splits.json"

    code = workspace.run(sneaky)

    assert code == 2 and INSIDE_MESSAGE in capsys.readouterr().err
    assert workspace.state() == before


def test_a_dotdot_path_that_stays_outside_is_accepted(workspace: Workspace) -> None:
    (workspace.root / "out").mkdir()

    code = workspace.run(workspace.root / "out" / "sub" / ".." / "splits.json")

    assert code == 0 and (workspace.root / "out" / "splits.json").is_file()


def test_relative_paths_are_resolved_before_they_are_compared(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = workspace.state()
    monkeypatch.chdir(workspace.dataset_dir)

    code = workspace.run("splits.json", dataset_dir=".")  # both relative, and the output is in the dataset directory

    assert code == 2 and INSIDE_MESSAGE in capsys.readouterr().err
    assert workspace.state() == before


def test_a_relative_output_beside_a_relative_dataset_directory_is_accepted(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(workspace.root)

    code = workspace.run("splits.json", dataset_dir="files")

    assert code == 0 and (workspace.root / "splits.json").is_file()


def test_a_folder_that_is_really_a_link_into_the_dataset_directory_is_refused(
    workspace: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    link = workspace.root / "innocent-looking-folder"
    if not link_directory(link, workspace.dataset_dir):
        pytest.skip("this environment cannot create a symbolic link or junction")
    before = workspace.state()

    code = workspace.run(link / "licence" / "licence-snapshot.txt", "--overwrite")

    assert code == 2 and INSIDE_MESSAGE in capsys.readouterr().err
    assert workspace.state() == before


def test_a_dataset_directory_given_as_a_link_is_judged_by_where_it_really_is(
    workspace: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    link = workspace.root / "dataset-alias"
    if not link_directory(link, workspace.dataset_dir):
        pytest.skip("this environment cannot create a symbolic link or junction")
    before = workspace.state()

    code = workspace.run(workspace.dataset_dir / "splits.json", dataset_dir=link)  # the real path, the alias as the root

    assert code == 2 and INSIDE_MESSAGE in capsys.readouterr().err
    assert workspace.state() == before


def test_a_different_letter_case_is_refused_where_the_file_system_ignores_case(
    workspace: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    if filesystem_is_case_sensitive(workspace.root):
        pytest.skip("on a case-sensitive file system a different case is a different folder")
    before = workspace.state()
    shouting = workspace.root / "FILES" / "LICENCE" / "splits.json"

    code = workspace.run(shouting)

    assert code == 2 and INSIDE_MESSAGE in capsys.readouterr().err
    assert workspace.state() == before


def test_a_folder_whose_name_merely_starts_like_the_dataset_directory_is_accepted(workspace: Workspace) -> None:
    sibling = workspace.root / "files-splits"
    sibling.mkdir()
    other = workspace.root / "files2"
    other.mkdir()

    assert workspace.run(sibling / "s.json", "--output-manifest", str(other / "m.json")) == 0

    assert (sibling / "s.json").is_file() and (other / "m.json").is_file()


def test_a_non_ascii_path_in_the_refusal_is_printed_as_plain_ascii(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = Workspace(tmp_path / "données")
    before = workspace.state()

    code = workspace.run(workspace.dataset_dir / "résumé.json")

    err = capsys.readouterr().err
    assert code == 2 and INSIDE_MESSAGE in err and err.isascii()
    assert workspace.state() == before


# --- ordinary use still works and writes nowhere near the sources ------------------------------------------------


def test_outputs_outside_the_dataset_directory_are_written_and_the_dataset_directory_is_untouched(
    workspace: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    before = workspace.state()
    split_file = workspace.root / "splits.json"
    manifest_copy = workspace.root / "with-splits.json"

    code = workspace.run(split_file, "--output-manifest", str(manifest_copy))

    assert code == 0 and "Splits written" in capsys.readouterr().out
    assert split_file.is_file() and manifest_copy.is_file()
    assert workspace.state() == before, "the generator must not add, change or remove anything in the dataset directory"
    assert load_manifest(manifest_copy).document["records"][0]["split"] in {"train", "validation", "test"}


def test_overwriting_an_earlier_output_outside_the_dataset_directory_still_works(workspace: Workspace) -> None:
    split_file = workspace.root / "splits.json"
    assert workspace.run(split_file) == 0
    split_file.write_text("older", encoding="utf-8")

    assert workspace.run(split_file, "--overwrite") == 0

    assert split_file.read_text(encoding="utf-8") != "older"


# --- the protections that existed before are unchanged ----------------------------------------------------------


def test_a_source_manifest_is_never_overwritten(workspace: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    source = build_dataset(workspace.root / "source-files", sample_count=20, images_per_sample=2, dataset_id="source-a")
    source_path = workspace.root / "source.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    before = source_path.read_bytes()

    first = workspace.run(source_path, "--source-manifest", str(source_path), "--overwrite")
    err_output = capsys.readouterr().err
    second = workspace.run(
        workspace.root / "splits.json", "--output-manifest", str(source_path), "--source-manifest", str(source_path), "--overwrite"
    )
    err_manifest = capsys.readouterr().err

    assert (first, second) == (2, 2)
    assert "is a source manifest" in err_output and "--output" in err_output
    assert "is a source manifest" in err_manifest and "--output-manifest" in err_manifest
    assert source_path.read_bytes() == before and not (workspace.root / "splits.json").exists()


def test_the_input_manifest_is_never_overwritten(workspace: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    before = workspace.manifest_path.read_bytes()

    code = workspace.run(workspace.root / "splits.json", "--output-manifest", str(workspace.manifest_path), "--overwrite")

    assert code == 2 and "never overwritten" in capsys.readouterr().err
    assert workspace.manifest_path.read_bytes() == before and not (workspace.root / "splits.json").exists()


def test_the_input_manifest_cannot_be_the_split_file_either(workspace: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    before = workspace.manifest_path.read_bytes()

    code = workspace.run(workspace.manifest_path, "--overwrite")

    assert code == 2 and "never overwritten" in capsys.readouterr().err
    assert workspace.manifest_path.read_bytes() == before


def test_two_outputs_may_not_be_one_path(workspace: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    target = workspace.root / "same.json"

    code = workspace.run(target, "--output-manifest", str(target))

    assert code == 2 and "same path" in capsys.readouterr().err and not target.exists()


def test_an_existing_output_is_kept_unless_overwrite_is_given(workspace: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    existing = workspace.root / "splits.json"
    existing.write_text("precious", encoding="utf-8")

    code = workspace.run(existing)

    assert code == 2 and "already exists" in capsys.readouterr().err
    assert existing.read_text(encoding="utf-8") == "precious"


def test_the_output_folder_must_exist(workspace: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    code = workspace.run(workspace.root / "no-such-folder" / "splits.json")

    assert code == 2 and "does not exist" in capsys.readouterr().err


def test_a_dataset_directory_that_does_not_exist_is_an_error_not_a_traceback(
    workspace: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = workspace.root / "no-such-dataset"

    outside = workspace.run(workspace.root / "splits.json", dataset_dir=missing)
    inside = workspace.run(missing / "splits.json", dataset_dir=missing)

    err = capsys.readouterr().err
    assert (outside, inside) == (2, 2) and "Traceback" not in err
    assert INSIDE_MESSAGE in err  # even a directory that is not there yet is never a place to write
    assert not (workspace.root / "splits.json").exists() and not missing.exists()


# --- the guard itself, and what is said about it --------------------------------------------------------------------


def test_the_containment_test_compares_whole_path_parts_not_text_prefixes(tmp_path: Path) -> None:
    directory = tmp_path / "files"

    assert _inside(directory, directory)
    assert _inside(directory / "a" / "b.json", directory)
    assert not _inside(tmp_path / "files-splits" / "a.json", directory)
    assert not _inside(tmp_path / "files2", directory)
    assert not _inside(tmp_path, directory)
    assert not _inside(tmp_path / "other" / "files", directory)
    assert _inside(Path(str(directory).upper()) / "x", directory) == (os.path.normcase("A") == os.path.normcase("a"))


def test_the_containment_test_does_not_fail_across_drives_or_mixed_paths() -> None:
    assert not _inside(Path("relative") / "x", Path(os.path.abspath(os.sep)) / "dataset")
    if os.name == "nt":
        assert not _inside(Path("C:\\elsewhere\\x"), Path("D:\\dataset"))


def test_the_help_text_says_where_outputs_may_not_go(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        generate_dataset_splits.main(["--help"])

    help_text = " ".join(capsys.readouterr().out.split())
    assert exit_info.value.code == 0
    assert "Never inside the dataset directory" in help_text and "never a source manifest" in help_text


def test_the_workflow_document_says_the_generator_never_writes_into_a_dataset_directory() -> None:
    text = " ".join(WORKFLOW_DOC.read_text(encoding="utf-8").split())

    assert "never writes into a dataset directory" in text
    assert "refuses any `--output` or `--output-manifest` that resolves to a path inside the dataset directory" in text
    assert "they never change a file in a dataset directory" not in text, "the old, over-broad claim must be gone"


def test_the_guard_is_applied_before_the_manifest_is_read(workspace: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    """An unreadable manifest must not hide the refusal: the destructive request is refused first."""
    workspace.manifest_path.write_text("{ not json", encoding="utf-8")
    before = workspace.state()

    code = workspace.run(workspace.image, "--overwrite")

    assert code == 2 and INSIDE_MESSAGE in capsys.readouterr().err
    assert workspace.state() == before


def test_outputs_are_not_written_when_the_second_output_is_refused_for_any_reason(
    workspace: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    split_file = workspace.root / "splits.json"
    missing_folder = workspace.root / "no-such-folder" / "m.json"

    code = workspace.run(split_file, "--output-manifest", str(missing_folder))

    assert code == 2 and not split_file.exists()
    assert "does not exist" in capsys.readouterr().err
