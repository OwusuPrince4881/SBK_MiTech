"""Unlisted files in a dataset directory: media, archives and housekeeping names (audit N1 and N2).

Everything is synthetic: a few bytes that carry a format's opening signature, written to a
temporary directory. No real photograph or restricted file is read or needed.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import files as files_module
from app.dataset_manifest import validate_document
from app.dataset_manifest.files import (
    CONTAINER,
    HEADER_BYTES,
    HOUSEKEEPING,
    MEDIA,
    OTHER,
    classify_file,
    looks_like_container,
    looks_like_media,
)
from app.dataset_manifest.validation import ValidationResult
from dataset_builders import build_dataset, sha256_hex
from scripts import validate_dataset_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF-synthetic"
TS = bytes([0x47]) + b"\x00" * 187 + bytes([0x47]) + b"\x00" * 187 + bytes([0x47]) + b"\x00" * 50

MEDIA_CONTENT: dict[str, bytes] = {
    "jpeg": JPEG,
    "png": b"\x89PNG\r\n\x1a\n-synthetic",
    "gif": b"GIF89a-synthetic",
    "webp": b"RIFF\x00\x00\x00\x00WEBP-syn",
    "tiff": b"II*\x00-synthetic",
    "bmp": b"BM" + b"\x36\x00\x00\x00" + b"\x00\x00\x00\x00" + b"\x36\x00\x00\x00" + b"\x28" + b"\x00" * 30,
    "ico": b"\x00\x00\x01\x00\x01\x00\x10\x10" + b"\x00" * 16,
    "cur": b"\x00\x00\x02\x00\x01\x00\x10\x10" + b"\x00" * 16,
    "ppm": b"P6\n1 1\n255\n\x00\x00\x00",
    "pgm-comment": b"P5\n# synthetic\n1 1\n255\n\x00",
    "psd": b"8BPS\x00\x01" + b"\x00" * 20,
    "jpeg2000": b"\x00\x00\x00\x0cjP  \r\n\x87\n" + b"\x00" * 8,
    "jpeg-xl": b"\xff\x0a" + b"synthetic",
    "openexr": b"v/1\x01" + b"\x00" * 12,
    "dds": b"DDS " + b"\x00" * 12,
    "qoi": b"qoif" + b"\x00" * 12,
    "icns": b"icns" + b"\x00" * 12,
    "radiance": b"#?RADIANCE\nsynthetic",
    "fuji-raw": b"FUJIFILMCCD-RAW 0201",
    "mp4": b"\x00\x00\x00\x18ftypisom-synth",
    "quicktime-atom": b"\x00\x00\x00\x08wide" + b"\x00" * 8,
    "matroska": b"\x1a\x45\xdf\xa3" + b"\x00" * 12,
    "avi": b"RIFF\x00\x00\x00\x00AVI LIST",
    "asf-wmv": b"\x30\x26\xb2\x75\x8e\x66\xcf\x11" + b"\x00" * 16,
    "flv": b"FLV\x01\x05" + b"\x00" * 12,
    "mpeg-ps": b"\x00\x00\x01\xba" + b"\x00" * 16,
    "mpeg-ts": TS,
}

TAR = b"a.txt" + b"\x00" * 252 + b"ustar\x0000" + b"\x00" * 200  # the marker sits at offset 257
CONTAINER_CONTENT: dict[str, bytes] = {
    "zip": b"PK\x03\x04" + b"\x00" * 20,
    "zip-empty": b"PK\x05\x06" + b"\x00" * 20,
    "gzip": b"\x1f\x8b\x08\x00" + b"\x00" * 12,
    "bzip2": b"BZh9" + b"\x00" * 12,
    "xz": b"\xfd7zXZ\x00" + b"\x00" * 12,
    "7z": b"7z\xbc\xaf\x27\x1c" + b"\x00" * 12,
    "rar": b"Rar!\x1a\x07\x00" + b"\x00" * 12,
    "zstd": b"\x28\xb5\x2f\xfd" + b"\x00" * 12,
    "lz4": b"\x04\x22\x4d\x18" + b"\x00" * 12,
    "pdf": b"%PDF-1.7\n" + b"\x00" * 12,
    "hdf5": b"\x89HDF\r\n\x1a\n" + b"\x00" * 12,
    "numpy": b"\x93NUMPY\x01\x00" + b"\x00" * 12,
    "sqlite": b"SQLite format 3\x00" + b"\x00" * 12,
    "parquet": b"PAR1" + b"\x00" * 12,
    "cab": b"MSCF\x00\x00\x00\x00" + b"\x00" * 12,
    "tar": TAR,
}


@pytest.fixture
def dataset(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    root = tmp_path / "dataset"
    return build_dataset(root, sample_count=4, images_per_sample=2), root


def write(root: Path, relative: str, data: bytes = b"stray") -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def error_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.errors}


def warning_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.warnings}


# --- N1: a housekeeping name does not make a file housekeeping ----------------------------------


@pytest.mark.parametrize("name", [".gitkeep", "Thumbs.db", "thumbs.db", "desktop.ini", ".DS_Store"])
@pytest.mark.parametrize("content", [JPEG, MEDIA_CONTENT["png"], MEDIA_CONTENT["mp4"]], ids=["jpeg", "png", "mp4"])
def test_media_named_like_a_housekeeping_file_is_still_media(
    dataset: tuple[dict[str, Any], Path], name: str, content: bytes
) -> None:
    """The original finding: a JPEG called `.gitkeep` was skipped as housekeeping."""
    manifest, root = dataset
    write(root, name, content)
    write(root, f"images/deep/{name}", content)

    result = validate_document(manifest, dataset_dir=root, ignore_patterns=["*"])

    assert error_codes(result) == {"unmanifested-image-file"}
    assert result.unmanifested_images == sorted([name, f"images/deep/{name}"])
    assert result.ignored_housekeeping == [] and result.ignored_by_pattern == []


@pytest.mark.parametrize("name", [".gitkeep", "Thumbs.db", "desktop.ini", ".DS_Store"])
def test_an_archive_named_like_a_housekeeping_file_is_still_an_archive(
    dataset: tuple[dict[str, Any], Path], name: str
) -> None:
    manifest, root = dataset
    write(root, f"x/{name}", CONTAINER_CONTENT["zip"])

    result = validate_document(manifest, dataset_dir=root)

    assert error_codes(result) == {"unmanifested-container-file"}
    assert result.unmanifested_containers == [f"x/{name}"] and result.ignored_housekeeping == []


@pytest.mark.parametrize(
    ("name", "content"),
    [
        (".gitkeep", b""),
        ("Thumbs.db", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32),  # the OLE header of a real Thumbs.db
        ("desktop.ini", b"[.ShellClassInfo]\r\nIconFile=x.ico\r\n"),
        (".DS_Store", b"\x00\x00\x00\x01Bud1" + b"\x00" * 20),
    ],
)
def test_genuine_housekeeping_files_are_still_skipped_and_listed(
    dataset: tuple[dict[str, Any], Path], name: str, content: bytes
) -> None:
    manifest, root = dataset
    write(root, name, content)

    result = validate_document(manifest, dataset_dir=root)

    assert result.ok and result.report.issues == []
    assert result.ignored_housekeeping == [name]


# --- N2: wider media coverage, and nothing hidden by an unsupported extension --------------------


@pytest.mark.parametrize("kind", sorted(MEDIA_CONTENT))
def test_recognised_media_signatures_are_errors_whatever_the_file_is_called(
    dataset: tuple[dict[str, Any], Path], kind: str
) -> None:
    manifest, root = dataset
    content = MEDIA_CONTENT[kind]
    write(root, "loose/blob.dat", content)

    plain = validate_document(manifest, dataset_dir=root)
    patterned = validate_document(manifest, dataset_dir=root, ignore_patterns=["*", "loose/*", "*.dat"])

    assert looks_like_media(content) and not looks_like_container(content)
    for result in (plain, patterned):
        assert error_codes(result) == {"unmanifested-image-file"}
        assert result.unmanifested_images == ["loose/blob.dat"] and result.ignored_by_pattern == []


@pytest.mark.parametrize(
    "extension",
    [".tga", ".svg", ".ico", ".psd", ".ppm", ".pgm", ".exr", ".dds", ".jxl", ".qoi", ".cr3", ".orf", ".raf", ".bmp",
     ".flv", ".3gp", ".asf", ".m2ts", ".ogv", ".divx"],
)
def test_more_image_and_video_extensions_are_recognised(
    dataset: tuple[dict[str, Any], Path], extension: str
) -> None:
    manifest, root = dataset
    write(root, f"loose/file{extension}", b"stray")

    result = validate_document(manifest, dataset_dir=root, ignore_patterns=["*"])

    assert error_codes(result) == {"unmanifested-image-file"}
    assert result.unmanifested_images == [f"loose/file{extension}"]


@pytest.mark.parametrize("kind", sorted(CONTAINER_CONTENT))
def test_archives_are_errors_whatever_the_file_is_called(
    dataset: tuple[dict[str, Any], Path], kind: str
) -> None:
    manifest, root = dataset
    content = CONTAINER_CONTENT[kind]
    write(root, "loose/notes.txt", content)

    plain = validate_document(manifest, dataset_dir=root, uses=["training"])
    patterned = validate_document(manifest, dataset_dir=root, ignore_patterns=["*", "loose/*"])

    assert looks_like_container(content) and not looks_like_media(content)
    for result in (plain, patterned):
        assert error_codes(result) == {"unmanifested-container-file"}
        assert result.unmanifested_containers == ["loose/notes.txt"] and result.ignored_by_pattern == []
    assert plain.cleared_uses == ()


@pytest.mark.parametrize(
    "extension",
    [".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".pdf", ".docx", ".xlsx", ".npy", ".npz", ".h5", ".pt", ".sqlite", ".parquet"],
)
def test_container_extensions_are_errors_even_with_unremarkable_content(
    dataset: tuple[dict[str, Any], Path], extension: str
) -> None:
    manifest, root = dataset
    write(root, f"loose/data{extension}", b"stray")

    result = validate_document(manifest, dataset_dir=root, ignore_patterns=["*"])

    assert error_codes(result) == {"unmanifested-container-file"}
    assert result.unmanifested_containers == [f"loose/data{extension}"]


@pytest.mark.parametrize(
    "content",
    [
        b"BM is the abbreviation used on the sample labels.",
        b"P1 is the first plate in the series.",
        b"P6 sample, taken after rain.",
        b"GIF is a format name, not a signature here.",
        b"II was the second visit.",
        b"PK is a nickname.",
        b"%PDF is mentioned in this note",
    ],
)
def test_ordinary_text_is_not_mistaken_for_media_or_an_archive(
    dataset: tuple[dict[str, Any], Path], content: bytes
) -> None:
    manifest, root = dataset
    write(root, "loose/readme", content)

    result = validate_document(manifest, dataset_dir=root)

    assert result.ok and warning_codes(result) == {"unmanifested-file"}
    assert result.unmanifested_other == ["loose/readme"]


def test_classification_order_is_housekeeping_name_then_extension_then_content() -> None:
    assert classify_file(".gitkeep", lambda: JPEG) == MEDIA
    assert classify_file(".gitkeep", lambda: b"") == HOUSEKEEPING
    assert classify_file("a.JPG", lambda: pytest.fail("the extension settles it")) == MEDIA
    assert classify_file("a.ZIP", lambda: pytest.fail("the extension settles it")) == CONTAINER
    assert classify_file("a.bin", lambda: JPEG) == MEDIA
    assert classify_file("a.bin", lambda: CONTAINER_CONTENT["zip"]) == CONTAINER
    assert classify_file("a.bin", lambda: b"hello") == OTHER


def test_the_header_read_covers_the_tar_marker_and_is_bounded(tmp_path: Path) -> None:
    big = write(tmp_path, "big.bin", b"\x00" * 5000)

    assert HEADER_BYTES >= 262
    assert len(files_module._read_header(big)) == HEADER_BYTES
    assert files_module._read_header(tmp_path / "missing") == b""


# --- supporting evidence that the manifest lists is not an unlisted archive --------------------------


def test_a_listed_document_is_not_an_unlisted_archive_but_it_is_not_accepted_as_a_licence_snapshot(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    """Listing a file stops it being an *unmanifested* container; it does not make a PDF a usable snapshot.

    Before the final audit a listed PDF was accepted in the snapshot slot. The slot now takes text only
    (see test_dataset_evidence_quality.py), so the listed PDF is refused for that reason, while a second,
    unlisted PDF is still reported as an unmanifested container.
    """
    manifest, root = dataset
    document = b"%PDF-1.7\nsynthetic licence document, not a real licence\n"
    write(root, "licence/licence.pdf", document)
    (root / "licence" / "licence-snapshot.txt").unlink()
    manifest["dataset"]["licence"]["evidence"]["snapshot"] = {
        "relative_path": "licence/licence.pdf",
        "sha256": sha256_hex(document),
    }

    listed = validate_document(manifest, dataset_dir=root, uses=["training"])
    write(root, "licence/other.pdf", document)
    unlisted = validate_document(manifest, dataset_dir=root)

    assert error_codes(listed) == {"licence-snapshot-not-text"} and listed.cleared_uses == ()
    assert listed.unmanifested_containers == []  # it is listed, so it is not an unmanifested container
    assert error_codes(unlisted) == {"licence-snapshot-not-text", "unmanifested-container-file"}
    assert unlisted.unmanifested_containers == ["licence/other.pdf"]


def test_a_listed_text_snapshot_is_still_accepted_next_to_an_unlisted_container(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset
    write(root, "licence/other.pdf", b"%PDF-1.7\nsynthetic licence document, not a real licence\n")

    result = validate_document(manifest, dataset_dir=root, uses=["training"])

    assert error_codes(result) == {"unmanifested-container-file"} and result.cleared_uses == ()
    assert result.unmanifested_containers == ["licence/other.pdf"]
    message = result.report.errors[0].message
    assert "accepted only when the manifest lists it" not in message  # the route that Fix 3 closed
    assert "the snapshot must be plain text" in message


def test_no_document_still_says_a_listed_licence_document_is_accepted() -> None:
    """After the final audit (M-1) a listed PDF or other document is refused as the snapshot; nothing may say otherwise."""
    old_claims = (
        "licence document is accepted only when",
        "the one container the manifest may carry",
        "the one container the manifest may list",
    )
    sources = (
        list((REPO_ROOT / "apps" / "api" / "app" / "dataset_manifest").glob("*.py"))
        + list((REPO_ROOT / "apps" / "api" / "scripts").glob("*.py"))
        + list((REPO_ROOT / "docs").glob("*.md"))
        + [REPO_ROOT / "CLAUDE.md"]
    )

    offenders = [
        (path.name, claim)
        for path in sources
        for claim in old_claims
        if claim in " ".join(path.read_text(encoding="utf-8").split()).lower()
    ]

    assert offenders == []


# --- what is and is not claimed ---------------------------------------------------------------------


def test_a_format_the_tool_does_not_know_is_only_a_warning_and_a_pattern_can_hide_it(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    """A pinned limitation: the lists are practical, not complete, and the messages say so."""
    manifest, root = dataset
    write(root, "loose/scan.xyz", b"\x00" * 18)  # a format with no listed extension or signature

    plain = validate_document(manifest, dataset_dir=root)
    hidden = validate_document(manifest, dataset_dir=root, ignore_patterns=["*.xyz"])

    assert plain.ok and warning_codes(plain) == {"unmanifested-file"}
    assert hidden.ok and hidden.ignored_by_pattern == ["loose/scan.xyz"]
    assert "format this tool does not recognise" in plain.report.warnings[0].message
    assert "can hide a file whose format this tool does not recognise" in hidden.report.warnings[0].message


def test_no_message_or_document_claims_that_every_image_or_video_is_detected() -> None:
    old_claims = ("can never hide an image or a video", "images and videos are never left out", "never hide an image")
    sources = (
        list((REPO_ROOT / "apps" / "api" / "scripts").glob("*.py"))
        + list((REPO_ROOT / "apps" / "api" / "app" / "dataset_manifest").glob("*.py"))
        + list((REPO_ROOT / "docs").glob("*.md"))
        + [REPO_ROOT / "CLAUDE.md", REPO_ROOT / "data" / "README.md"]
    )

    offenders = [
        (path.name, claim)
        for path in sources
        for claim in old_claims
        if claim in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
    assert "not complete" in files_module.__doc__ and "NOT recognised" in files_module.__doc__


# --- through the command line ----------------------------------------------------------------------


def test_the_validator_lists_unlisted_archives_in_full_and_fails(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest, root = dataset
    write(root, "extra/photos.zip", CONTAINER_CONTENT["zip"])
    write(root, "extra/.gitkeep", JPEG)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    code = validate_dataset_manifest.main([str(path), "--dataset-dir", str(root), "--ignore-file", "*"])

    err = capsys.readouterr().err
    assert code == 2
    assert "ERROR [unmanifested-container-file]" in err and "ERROR [unmanifested-image-file]" in err
    assert "Archive or container files in the dataset directory that are not in the manifest (left untouched):" in err
    assert "  'extra/photos.zip'" in err and "  'extra/.gitkeep'" in err
    assert err.isascii()
