import json
import os
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import validate_document
from app.dataset_manifest import files as files_module
from app.dataset_manifest.files import (
    DirectoryScan,
    _looks_like_junction,
    find_unmanifested,
    scan_directory,
)
from app.dataset_manifest.validation import ValidationResult
from dataset_builders import (
    build_dataset,
    clone,
    filesystem_is_case_sensitive,
    link_directory,
    sha256_hex,
)
from scripts import generate_dataset_splits, validate_dataset_manifest


@pytest.fixture
def dataset(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    root = tmp_path / "dataset"
    return build_dataset(root, sample_count=4, images_per_sample=2), root


def error_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.errors}


def warning_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.warnings}


def assert_fails_only_with(result: ValidationResult, *codes: str) -> None:
    assert not result.ok, "expected the manifest to be rejected"
    assert error_codes(result) == set(codes), result.report.format()


def tree_state(root: Path) -> dict[str, str | None]:
    """Every path under `root` with a content hash (None for directories)."""
    state: dict[str, str | None] = {}
    for path in sorted(root.rglob("*")):
        state[path.relative_to(root).as_posix()] = (
            sha256_hex(path.read_bytes()) if path.is_file() else None
        )
    return state


def write(root: Path, relative: str, data: bytes = b"stray") -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


# --- files that are not in the manifest ---------------------------------------------------


def test_a_dataset_directory_with_exactly_the_listed_files_is_clean(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    result = validate_document(dataset[0], dataset_dir=dataset[1])

    assert result.ok
    assert result.report.issues == []
    assert result.unmanifested_images == [] and result.unmanifested_other == []


def test_unmanifested_images_are_reported_by_name_and_left_untouched(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset
    write(root, "stray.jpg")
    write(root, "images/sample-000/extra/deep.PNG")
    before_tree = tree_state(root)
    before_manifest = clone(manifest)

    result = validate_document(manifest, dataset_dir=root)

    assert_fails_only_with(result, "unmanifested-image-file")
    assert result.unmanifested_images == ["images/sample-000/extra/deep.PNG", "stray.jpg"]
    message = result.report.errors[0].message
    assert "'stray.jpg'" in message and "'images/sample-000/extra/deep.PNG'" in message
    assert "this tool does neither" in message
    assert tree_state(root) == before_tree  # nothing added, removed, renamed or changed
    assert manifest == before_manifest  # nothing was added to the manifest


@pytest.mark.parametrize(
    "name",
    [
        "a.jpg", "a.JPG", "a.jpeg", "a.png", "a.PNG", "a.webp", "a.gif", "a.bmp",
        "a.tif", "a.TIFF", "a.heic", "a.avif", "a.dng", "a.jfif",
    ],
)
def test_every_common_image_extension_counts_as_an_image(
    dataset: tuple[dict[str, Any], Path], name: str
) -> None:
    manifest, root = dataset
    write(root, f"loose/{name}")

    result = validate_document(manifest, dataset_dir=root)

    assert_fails_only_with(result, "unmanifested-image-file")
    assert result.unmanifested_images == [f"loose/{name}"]


def test_other_unmanifested_files_are_a_warning_not_an_error(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset
    write(root, "notes.txt")
    write(root, "labels/labels.csv")
    before = tree_state(root)

    result = validate_document(manifest, dataset_dir=root)

    assert result.ok
    assert warning_codes(result) == {"unmanifested-file"}
    assert result.unmanifested_other == ["labels/labels.csv", "notes.txt"]
    assert result.unmanifested_images == []
    assert tree_state(root) == before


def test_a_long_list_of_unmanifested_files_is_shortened_in_the_message_but_kept_in_full(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset
    for index in range(25):
        write(root, f"loose/extra-{index:02d}.jpg")

    result = validate_document(manifest, dataset_dir=root)

    assert len(result.unmanifested_images) == 25
    assert "and 5 more" in result.report.errors[0].message
    assert "25 image or video file(s)" in result.report.errors[0].message


def test_without_a_dataset_directory_nothing_is_scanned(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    write(root, "stray.jpg")

    result = validate_document(manifest)

    assert result.ok
    assert result.files_verified is False
    assert result.unmanifested_images == []


def test_the_manifest_file_itself_is_left_out_when_it_sits_in_the_dataset_directory(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset
    manifest_file = write(root, "manifest.json", json.dumps(manifest).encode("utf-8"))

    left_out = validate_document(manifest, dataset_dir=root, manifest_path=manifest_file)
    not_told = validate_document(manifest, dataset_dir=root)

    assert left_out.report.issues == []
    assert not_told.unmanifested_other == ["manifest.json"]


@pytest.mark.parametrize("name", [".gitkeep", "Thumbs.db", "thumbs.db", "desktop.ini", ".DS_Store"])
def test_housekeeping_files_are_never_reported(
    dataset: tuple[dict[str, Any], Path], name: str
) -> None:
    manifest, root = dataset
    write(root, name)
    write(root, f"images/{name}")

    assert validate_document(manifest, dataset_dir=root).report.issues == []


def test_documented_metadata_can_be_left_out_with_ignore_patterns(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset
    write(root, "README.md")
    write(root, "notes/ethics.txt")
    write(root, "notes/deep/more.txt")

    unfiltered = validate_document(manifest, dataset_dir=root)
    filtered = validate_document(manifest, dataset_dir=root, ignore_patterns=["README.md", "notes/*"])

    assert unfiltered.unmanifested_other == ["README.md", "notes/deep/more.txt", "notes/ethics.txt"]
    assert filtered.ok
    assert filtered.unmanifested_other == []
    # Every file an ignore pattern leaves out is reported, with the number.
    assert filtered.ignored_by_pattern == ["README.md", "notes/deep/more.txt", "notes/ethics.txt"]
    assert warning_codes(filtered) == {"files-ignored-by-pattern"}
    assert "3 file(s) were left out" in filtered.report.warnings[0].message


def test_ignore_patterns_only_hide_what_they_match_and_match_case_sensitively(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset
    write(root, "README.md")
    write(root, "other.txt")

    result = validate_document(manifest, dataset_dir=root, ignore_patterns=["readme.md", "nomatch*"])

    assert result.unmanifested_other == ["README.md", "other.txt"]


@pytest.mark.parametrize("pattern", ["*", "*.png", "sketches/*", "sketches/diagram.png", "**/*"])
def test_an_ignore_pattern_can_never_hide_an_image(
    dataset: tuple[dict[str, Any], Path], pattern: str
) -> None:
    manifest, root = dataset
    write(root, "sketches/diagram.png")

    result = validate_document(manifest, dataset_dir=root, ignore_patterns=[pattern])

    assert_fails_only_with(result, "unmanifested-image-file")
    assert result.unmanifested_images == ["sketches/diagram.png"]
    assert result.ignored_by_pattern == []


@pytest.mark.parametrize("name", ["clip.mp4", "clip.MOV", "recording.webm"])
def test_a_video_is_treated_like_an_image_and_cannot_be_ignored(
    dataset: tuple[dict[str, Any], Path], name: str
) -> None:
    manifest, root = dataset
    write(root, name)

    result = validate_document(manifest, dataset_dir=root, ignore_patterns=["*"])

    assert_fails_only_with(result, "unmanifested-image-file")


def test_a_star_pattern_hides_only_non_image_files_and_reports_each_one(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset
    write(root, "stray-a.jpg")
    write(root, "x/stray-b.PNG")
    write(root, "notes.txt")
    write(root, "x/labels.csv")

    result = validate_document(manifest, dataset_dir=root, ignore_patterns=["*"])

    assert_fails_only_with(result, "unmanifested-image-file")
    assert result.unmanifested_images == ["stray-a.jpg", "x/stray-b.PNG"]
    assert result.ignored_by_pattern == ["notes.txt", "x/labels.csv"]
    assert warning_codes(result) == {"files-ignored-by-pattern"}


def test_housekeeping_files_are_skipped_but_listed(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    write(root, ".gitkeep")
    write(root, "images/Thumbs.db")

    result = validate_document(manifest, dataset_dir=root)

    assert result.ok
    assert result.ignored_housekeeping == [".gitkeep", "images/Thumbs.db"]


@pytest.mark.parametrize("name", ["photo", "photo.dat", "IMG_0001.bin", "image.txt"])
def test_an_image_with_an_unexpected_name_is_recognised_by_its_content(
    dataset: tuple[dict[str, Any], Path], name: str
) -> None:
    manifest, root = dataset
    write(root, f"loose/{name}", b"\xff\xd8\xff\xe0\x00\x10JFIF-synthetic")

    result = validate_document(manifest, dataset_dir=root, ignore_patterns=["*"])

    assert_fails_only_with(result, "unmanifested-image-file")
    assert result.unmanifested_images == [f"loose/{name}"]


@pytest.mark.parametrize(
    "content",
    [
        b"\x89PNG\r\n\x1a\n-synthetic",
        b"GIF89a-synthetic",
        b"RIFF\x00\x00\x00\x00WEBP-syn",
        b"\x00\x00\x00\x18ftypisom-synth",
        b"II*\x00-synthetic",
    ],
)
def test_common_image_and_video_signatures_are_recognised(
    dataset: tuple[dict[str, Any], Path], content: bytes
) -> None:
    manifest, root = dataset
    write(root, "loose/blob", content)

    assert validate_document(manifest, dataset_dir=root).unmanifested_images == ["loose/blob"]


def test_a_plain_text_file_is_not_mistaken_for_an_image(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    write(root, "loose/readme", b"This is just a note about the dataset.")

    result = validate_document(manifest, dataset_dir=root)

    assert result.ok
    assert result.unmanifested_other == ["loose/readme"]

def test_an_unmanifested_image_makes_the_dataset_unusable_until_resolved(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset
    write(root, "stray.jpg")

    result = validate_document(manifest, dataset_dir=root, uses=["training"])

    assert not result.ok
    assert result.cleared_uses == ()


def test_disk_derived_names_are_escaped_so_they_are_safe_to_print(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset
    write(root, "ünmanifested-é-写真.jpg")

    result = validate_document(manifest, dataset_dir=root)

    assert result.unmanifested_images == ["ünmanifested-é-写真.jpg"]
    assert all(issue.message.isascii() for issue in result.report.issues)
    assert "\\xfc" in result.report.errors[0].message


@pytest.mark.skipif(os.name == "nt", reason="Windows file names cannot contain a newline")
def test_a_newline_in_a_file_name_cannot_forge_report_lines(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset
    write(root, "bad\nERROR [forged] pretend finding.jpg")

    result = validate_document(manifest, dataset_dir=root)

    message = result.report.errors[0].message
    assert "\n" not in message
    assert "\\n" in message


def test_scanning_a_dataset_directory_writes_nothing(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    write(root, "stray.jpg")
    write(root, "notes.txt")
    before = {path: (path.stat().st_mtime_ns, path.stat().st_size) for path in root.rglob("*") if path.is_file()}

    validate_document(manifest, dataset_dir=root)

    after = {path: (path.stat().st_mtime_ns, path.stat().st_size) for path in root.rglob("*") if path.is_file()}
    assert after == before


# --- links are not followed ---------------------------------------------------------------


def test_a_link_to_a_directory_outside_is_not_followed(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    manifest, root = dataset
    outside = tmp_path / "outside-the-dataset"
    write(outside, "secret.jpg", b"x")
    if not link_directory(root / "linked", outside):
        pytest.skip("this environment cannot create a symbolic link or junction")

    result = validate_document(manifest, dataset_dir=root)

    assert_fails_only_with(result, "link-in-dataset-dir")
    assert result.links_not_followed == ["linked"]
    assert result.unmanifested_images == []  # nothing outside the directory was looked at
    assert (outside / "secret.jpg").read_bytes() == b"x"


def test_a_manifest_path_that_runs_through_a_link_is_refused(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset
    if not link_directory(root / "images" / "alias", root / "images" / "sample-000"):
        pytest.skip("this environment cannot create a symbolic link or junction")
    manifest["records"][0]["file"]["relative_path"] = "images/alias/rec-000-0.jpg"
    manifest["records"][0]["file"]["filename"] = "rec-000-0.jpg"

    result = validate_document(manifest, dataset_dir=root)

    assert "file-through-link" in error_codes(result)


def test_directory_scan_never_reports_files_behind_a_link(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write(root, "real.txt")
    write(tmp_path / "elsewhere", "hidden.jpg")
    if not link_directory(root / "alias", tmp_path / "elsewhere"):
        pytest.skip("this environment cannot create a symbolic link or junction")

    scan = scan_directory(root)

    assert sorted(scan.files) == ["real.txt"]
    assert scan.links == ["alias"]


@pytest.mark.skipif(os.name != "nt", reason="the junction fallback only applies on Windows")
def test_the_junction_fallback_for_older_python_recognises_a_junction(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    (tmp_path / "target").mkdir()
    if not link_directory(tmp_path / "junction", tmp_path / "target"):
        pytest.skip("this environment cannot create a junction")

    with os.scandir(tmp_path) as iterator:
        entries = {entry.name: entry for entry in iterator}

    assert _looks_like_junction(entries["junction"]) is True
    assert _looks_like_junction(entries["plain"]) is False
    assert _looks_like_junction(entries["target"]) is False


# --- directories that cannot be listed ----------------------------------------------------


def test_a_directory_that_cannot_be_listed_is_an_error_not_a_silent_gap(
    dataset: tuple[dict[str, Any], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, root = dataset
    real_scandir = os.scandir

    def refusing_scandir(path: Any) -> Any:
        if Path(path).name == "sample-001":
            raise PermissionError("denied for the test")
        return real_scandir(path)

    monkeypatch.setattr(files_module.os, "scandir", refusing_scandir)

    result = validate_document(manifest, dataset_dir=root)

    assert "directory-unreadable" in error_codes(result)
    assert any("images/sample-001" in issue.message for issue in result.report.errors)


# --- relative paths whose case differs from the disk ---------------------------------------


def test_a_file_whose_case_differs_from_the_manifest_is_reported(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset
    original = root / "images" / "sample-000" / "rec-000-0.jpg"
    original.rename(original.with_name("REC-000-0.JPG"))
    if "REC-000-0.JPG" not in os.listdir(original.parent):
        pytest.skip("this file system does not preserve the case of file names")

    result = validate_document(manifest, dataset_dir=root)

    # Exactly one finding: not "missing", and the wrongly-cased file is not "unmanifested" too.
    assert_fails_only_with(result, "path-case-mismatch")
    issue = result.report.errors[0]
    assert issue.location == "records[0]"
    assert "'images/sample-000/rec-000-0.jpg'" in issue.message
    assert "'images/sample-000/REC-000-0.JPG'" in issue.message
    assert result.unmanifested_images == []


def test_a_directory_whose_case_differs_from_the_manifest_is_reported_for_each_file(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset
    original = root / "images" / "sample-001"
    original.rename(original.with_name("Sample-001"))
    if "Sample-001" not in os.listdir(original.parent):
        pytest.skip("this file system does not preserve the case of directory names")

    result = validate_document(manifest, dataset_dir=root)

    assert_fails_only_with(result, "path-case-mismatch")
    assert len(result.report.errors) == 2
    assert all("Sample-001" in issue.message for issue in result.report.errors)
    assert result.unmanifested_images == []


def test_the_case_check_does_not_depend_on_how_the_file_system_treats_case(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    """On a case-insensitive system the OS still finds the wrongly-cased path; the check must not."""
    manifest, root = dataset
    original = root / "images" / "sample-000" / "rec-000-0.jpg"
    original.rename(original.with_name("Rec-000-0.jpg"))
    if "Rec-000-0.jpg" not in os.listdir(original.parent):
        pytest.skip("this file system does not preserve the case of file names")
    os_finds_it = (root / "images" / "sample-000" / "rec-000-0.jpg").exists()
    assert os_finds_it == (not filesystem_is_case_sensitive(tmp_path))

    result = validate_document(manifest, dataset_dir=root)

    assert error_codes(result) == {"path-case-mismatch"}


def test_matching_case_is_accepted_on_every_system(dataset: tuple[dict[str, Any], Path]) -> None:
    assert validate_document(dataset[0], dataset_dir=dataset[1]).report.issues == []


def test_a_second_file_differing_only_in_case_is_unmanifested_on_a_case_sensitive_system(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    if not filesystem_is_case_sensitive(tmp_path):
        pytest.skip("this file system cannot hold two names that differ only in case")
    manifest, root = dataset
    write(root, "images/sample-000/REC-000-0.jpg", b"a different image")

    result = validate_document(manifest, dataset_dir=root)

    assert_fails_only_with(result, "unmanifested-image-file")
    assert result.unmanifested_images == ["images/sample-000/REC-000-0.jpg"]


def test_case_variants_are_found_by_name_not_by_the_operating_system() -> None:
    scan = DirectoryScan(
        root=Path("."),
        files={
            "Images/A.JPG": Path("Images/A.JPG"),
            "images/a.jpg": Path("images/a.jpg"),
            "other.txt": Path("other.txt"),
            "é.jpg": Path("é.jpg"),
        },
        links=["linked"],
    )

    assert scan.case_variants("images/A.jpg") == ["Images/A.JPG", "images/a.jpg"]
    assert scan.case_variants("images/a.jpg") == ["Images/A.JPG"]  # the exact name is not a variant
    assert scan.case_variants("OTHER.TXT") == ["other.txt"]
    assert scan.case_variants("é.jpg") == ["é.jpg"]  # same name in another Unicode form
    assert scan.case_variants("nothing.jpg") == []
    assert unicodedata.normalize("NFC", "é") != "é"
    assert scan.link_on_path("linked/x.jpg") == "linked"
    assert scan.link_on_path("linked") == "linked"
    assert scan.link_on_path("linkedx/y.jpg") is None


def test_the_case_mismatch_message_is_safe_to_print(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    original = root / "images" / "sample-000" / "rec-000-0.jpg"
    original.rename(original.with_name("RÉC-000-0.JPG"))
    manifest["records"][0]["file"]["relative_path"] = "images/sample-000/réc-000-0.jpg"
    manifest["records"][0]["file"]["filename"] = "réc-000-0.jpg"

    result = validate_document(manifest, dataset_dir=root)

    assert error_codes(result) == {"path-case-mismatch"}
    assert result.report.errors[0].message.isascii()


def test_find_unmanifested_classifies_and_excludes_by_the_documented_rules() -> None:
    scan = DirectoryScan(
        root=Path("."),
        files={name: Path(name) for name in [
            "listed.jpg", "Counterpart.JPG", "stray.JPEG", "stray.txt", "docs/readme.md",
            ".gitkeep", "sub/Thumbs.db", "noextension",
        ]},
    )

    found = find_unmanifested(
        scan, {"listed.jpg"}, case_counterparts={"Counterpart.JPG"}, ignore_patterns=["docs/*"]
    )

    assert found.images == ["stray.JPEG"]
    assert found.others == ["noextension", "stray.txt"]
    assert found.ignored_by_pattern == ["docs/readme.md"]
    assert found.housekeeping == [".gitkeep", "sub/Thumbs.db"]


# --- through the command-line tools ---------------------------------------------------------


def write_manifest(manifest: dict[str, Any], folder: Path) -> Path:
    path = folder / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def test_validate_cli_lists_unmanifested_images_in_full_and_fails(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest, root = dataset
    for index in range(25):
        write(root, f"loose/extra-{index:02d}.jpg")
    manifest_path = write_manifest(manifest, tmp_path)

    code = validate_dataset_manifest.main([str(manifest_path), "--dataset-dir", str(root)])

    err = capsys.readouterr().err
    assert code == 2
    assert "ERROR [unmanifested-image-file]" in err
    assert "Image or video files in the dataset directory that are not in the manifest (left untouched):" in err
    assert "  'loose/extra-24.jpg'" in err  # the full list, beyond the 20 named in the message
    assert err.isascii()


def test_validate_cli_lists_other_unmanifested_files_but_succeeds(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest, root = dataset
    write(root, "notes.txt")
    manifest_path = write_manifest(manifest, tmp_path)

    code = validate_dataset_manifest.main([str(manifest_path), "--dataset-dir", str(root)])

    out = capsys.readouterr().out
    assert code == 0
    assert "WARNING [unmanifested-file]" in out
    assert "Other files in the dataset directory that are not in the manifest (left untouched):" in out
    assert "  'notes.txt'" in out


def test_validate_cli_ignore_file_leaves_documented_metadata_out(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest, root = dataset
    write(root, "README.md")
    write(root, "notes/ethics.txt")
    manifest_path = write_manifest(manifest, tmp_path)

    code = validate_dataset_manifest.main(
        [str(manifest_path), "--dataset-dir", str(root), "--ignore-file", "README.md", "--ignore-file", "notes/*"]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "WARNING [unmanifested-file]" not in out
    assert "WARNING [files-ignored-by-pattern]" in out
    assert "2 file(s) were left out of the unlisted-file check by: README.md, notes/*" in out
    assert "  'README.md'" in out and "  'notes/ethics.txt'" in out  # every ignored file is listed


def test_validate_cli_ignore_file_needs_a_dataset_directory(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    manifest_path = write_manifest(dataset[0], tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        validate_dataset_manifest.main([str(manifest_path), "--ignore-file", "README.md"])

    assert exit_info.value.code == 2


def test_validate_cli_leaves_the_manifest_alone_when_it_sits_in_the_dataset_directory(
    dataset: tuple[dict[str, Any], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    manifest, root = dataset
    manifest_path = write_manifest(manifest, root)

    code = validate_dataset_manifest.main([str(manifest_path), "--dataset-dir", str(root)])

    assert code == 0
    assert "unmanifested-file" not in capsys.readouterr().out


def test_validate_cli_reports_a_case_mismatch(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest, root = dataset
    original = root / "images" / "sample-000" / "rec-000-0.jpg"
    original.rename(original.with_name("REC-000-0.JPG"))
    manifest_path = write_manifest(manifest, tmp_path)

    code = validate_dataset_manifest.main([str(manifest_path), "--dataset-dir", str(root)])

    assert code == 2
    assert "ERROR [path-case-mismatch] records[0]:" in capsys.readouterr().err


def test_split_cli_refuses_a_dataset_directory_with_an_unmanifested_image_even_if_ignored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "files"
    manifest = build_dataset(root, sample_count=30, images_per_sample=1)
    write(root, "stray.jpg")
    manifest_path = write_manifest(manifest, tmp_path)
    output = tmp_path / "splits.json"
    arguments = [str(manifest_path), "--dataset-dir", str(root), "--seed", "1", "--output", str(output)]

    plain = generate_dataset_splits.main(arguments)
    plain_err = capsys.readouterr().err
    ignored = generate_dataset_splits.main([*arguments, "--ignore-file", "stray.*"])

    assert (plain, ignored) == (2, 2)
    assert "unmanifested-image-file" in plain_err
    assert not output.exists()


def test_split_cli_records_the_files_an_ignore_pattern_left_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "files"
    manifest = build_dataset(root, sample_count=30, images_per_sample=1)
    write(root, "NOTES.txt")
    manifest_path = write_manifest(manifest, tmp_path)
    output = tmp_path / "splits.json"

    code = generate_dataset_splits.main(
        [str(manifest_path), "--dataset-dir", str(root), "--seed", "1", "--output", str(output), "--ignore-file", "NOTES.*"]
    )

    assert code == 0, capsys.readouterr().err
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["ignored_file_patterns"] == ["NOTES.*"]
    assert document["ignored_files"] == ["NOTES.txt"]
    assert "1 file(s) were left out" in capsys.readouterr().out

def test_split_cli_leaves_the_manifest_out_when_it_sits_in_the_dataset_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "files"
    manifest = build_dataset(root, sample_count=30, images_per_sample=1)
    manifest_path = write_manifest(manifest, root)  # inside the dataset directory
    output = tmp_path / "splits.json"

    code = generate_dataset_splits.main(
        [str(manifest_path), "--dataset-dir", str(root), "--seed", "1", "--output", str(output)]
    )

    assert code == 0, capsys.readouterr().err
    assert output.exists()
