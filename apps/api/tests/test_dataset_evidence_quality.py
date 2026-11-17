"""Licence evidence must exist, be dated honestly and say something (audit N8).

The checks decide whether the record is plausible. None of them reads a licence, and none says that a
licence permits any use: whether it does is a human judgement.
"""

import io
import json
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import audit_manifests, validate_document
from app.dataset_manifest import validation as validation_module
from app.dataset_manifest.validation import ValidationResult
from dataset_builders import build_dataset, fake_image_bytes, sha256_hex
from scripts import validate_dataset_manifest

TODAY = date(2026, 6, 15)
SNAPSHOT = Path("licence") / "licence-snapshot.txt"


@pytest.fixture
def dataset(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    root = tmp_path / "dataset"
    return build_dataset(root, sample_count=4, images_per_sample=2), root


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(validation_module, "today_utc", lambda: TODAY)


def error_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.errors}


def replace_snapshot(manifest: dict[str, Any], root: Path, content: bytes) -> None:
    (root / SNAPSHOT).write_bytes(content)
    manifest["dataset"]["licence"]["evidence"]["snapshot"]["sha256"] = sha256_hex(content)


# --- empty snapshots ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [b"", b" ", b"\n", b"  \r\n\t \r\n", b"\x00\x00\x00", b" \x00 \n"],
    ids=["empty", "space", "newline", "whitespace", "nul", "mixed"],
)
def test_an_empty_or_blank_licence_snapshot_is_rejected_even_with_a_matching_checksum(
    dataset: tuple[dict[str, Any], Path], content: bytes
) -> None:
    """The original gap: a zero-byte file with its own checksum cleared training."""
    manifest, root = dataset
    replace_snapshot(manifest, root, content)

    result = validate_document(manifest, dataset_dir=root, uses=["training", "validation", "testing"])

    assert error_codes(result) == {"licence-snapshot-empty"}
    assert result.cleared_uses == ()
    issue = result.report.errors[0]
    assert issue.location == "dataset.licence.evidence.snapshot" and "licence-snapshot.txt" in issue.message
    assert "not what it says" in issue.message


@pytest.mark.parametrize(
    "content",
    [
        b"Terms of use: attribution is required and derivatives must be shared alike.",
        b"\xef\xbb\xbfTerms of use: attribution is required and derivatives must be shared alike.",  # UTF-8 with a BOM
        "Terms of use: attribution is required.".encode("utf-16"),  # UTF-16 with a BOM
        "Licence: éè 日本語 terms of use apply to this data.".encode("utf-8"),
    ],
    ids=["ascii", "utf8-bom", "utf16-bom", "non-ascii"],
)
def test_a_snapshot_that_is_real_text_is_accepted(dataset: tuple[dict[str, Any], Path], content: bytes) -> None:
    manifest, root = dataset
    replace_snapshot(manifest, root, content)

    result = validate_document(manifest, dataset_dir=root, uses=["training"])

    assert result.ok and result.cleared_uses == ("training",), result.report.format()


def test_a_snapshot_whose_content_is_blank_only_in_its_first_chunk_is_still_found(
    dataset: tuple[dict[str, Any], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, root = dataset
    monkeypatch.setattr(validation_module, "HASH_CHUNK_SIZE", 16)
    replace_snapshot(manifest, root, b" " * 100 + b"Terms of use: attribution is required")

    assert validate_document(manifest, dataset_dir=root).ok
    replace_snapshot(manifest, root, b" " * 100)
    assert error_codes(validate_document(manifest, dataset_dir=root)) == {"licence-snapshot-empty"}


def test_the_blank_check_does_not_apply_to_ordinary_files(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    (root / manifest["records"][0]["file"]["relative_path"]).write_bytes(b"\x00")
    manifest["records"][0]["file"]["sha256"] = sha256_hex(b"\x00")
    manifest["records"][0]["file"]["size_bytes"] = 1

    assert validate_document(manifest, dataset_dir=root).ok  # an image's content is not judged here


def test_a_missing_or_changed_snapshot_is_still_reported_as_before(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    (root / SNAPSHOT).write_bytes(b"changed after the review, with a longer text")
    assert error_codes(validate_document(manifest, dataset_dir=root)) == {"checksum-mismatch"}
    (root / SNAPSHOT).unlink()
    assert error_codes(validate_document(manifest, dataset_dir=root)) == {"file-missing"}


# --- dates -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "location"),
    [
        (("licence", "evidence", "captured_on"), "dataset.licence.evidence.captured_on"),
        (("permissions", "reviewed_on"), "dataset.permissions.reviewed_on"),
        (("source", "retrieved_on"), "dataset.source.retrieved_on"),
    ],
)
@pytest.mark.parametrize("value", ["2026-06-16", "2026-12-31", "2099-01-01"])
def test_evidence_review_and_retrieval_dates_cannot_be_in_the_future(
    dataset: tuple[dict[str, Any], Path], path: tuple[str, ...], location: str, value: str
) -> None:
    manifest, _root = dataset
    node = manifest["dataset"]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    if path[-1] != "reviewed_on":
        manifest["dataset"]["permissions"]["reviewed_on"] = "2099-12-31"  # keep the evidence-after-review rule out of it

    result = validate_document(manifest)

    assert (location, "date-in-future") in {(i.location, i.code) for i in result.report.errors}
    assert "(2026-06-15, UTC)" in next(i.message for i in result.report.errors if i.code == "date-in-future")


@pytest.mark.parametrize("value", ["2026-06-15", "2026-06-14", "2020-01-01"])
def test_today_and_earlier_dates_are_accepted(dataset: tuple[dict[str, Any], Path], value: str) -> None:
    manifest, _root = dataset
    manifest["dataset"]["licence"]["evidence"]["captured_on"] = value
    manifest["dataset"]["permissions"]["reviewed_on"] = "2026-06-15"
    manifest["dataset"]["source"]["retrieved_on"] = value

    assert validate_document(manifest).ok


def test_dates_that_are_not_review_or_evidence_dates_are_not_judged_against_today(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, _root = dataset
    manifest["records"][0]["acquisition"] = {"captured_at": "2099-01-01T00:00:00Z"}  # a camera clock
    manifest["records"][0]["label"]["source"]["annotated_on"] = "2099-01-01"

    assert validate_document(manifest).ok


def test_a_future_date_is_an_error_in_the_audit_and_blocks_clearance(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    manifest["dataset"]["licence"]["evidence"]["captured_on"] = "2099-01-01"
    manifest["dataset"]["permissions"]["reviewed_on"] = "2099-02-01"

    cleared = validate_document(manifest, dataset_dir=root, uses=["training"])
    audit = audit_manifests([("m.json", manifest)])

    assert cleared.cleared_uses == () and "date-in-future" in error_codes(cleared)
    assert "date-in-future" in {i.code for i in audit.report.errors} and not audit.passed()


def test_the_real_clock_is_used_when_nothing_is_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # drop the frozen date

    today = validation_module.today_utc()

    assert abs((today - date.today()).days) <= 1 and today.year >= 2026


# --- the commercial-training basis -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "basis",
    ["x", ".", "n/a", "N/A", "yes", "TBD", "tbc", "todo", "ok", "none", "unknown", "see above", "see licence", "same as above",
     "permitted", "the licence", "aaaaaaaaaa", "xx xx xx xx", "1111 1111", "a b c d e f g", "-- -- --"],
)
def test_a_placeholder_is_not_a_basis_for_commercial_training(dataset: tuple[dict[str, Any], Path], basis: str) -> None:
    """The original gap: a one-character basis cleared commercial training."""
    manifest, root = dataset
    manifest["dataset"]["permissions"].update(commercial_training="permitted", commercial_training_basis=basis)

    result = validate_document(manifest, dataset_dir=root, uses=["training"], require_commercial_training=True)

    assert error_codes(result) == {"commercial-training-basis-not-meaningful"}
    assert result.commercial_training_cleared is False
    issue = result.report.errors[0]
    assert issue.location == "dataset.permissions.commercial_training_basis"
    assert "does not establish that the licence permits commercial training" in issue.message


@pytest.mark.parametrize(
    "basis",
    ["Clause 4.2", "Clause 4.2.", "Clause 4.2 of the licence.", "Section 3(b) of the signed agreement", "Synthetic fixture.",
     "Email of 2026-03-02 from the data owner", "CC-BY 4.0 section 2(a)(1)"],
)
def test_a_basis_that_names_something_is_accepted(dataset: tuple[dict[str, Any], Path], basis: str) -> None:
    manifest, root = dataset
    manifest["dataset"]["permissions"].update(commercial_training="permitted", commercial_training_basis=basis)

    result = validate_document(manifest, dataset_dir=root, uses=["training"], require_commercial_training=True)

    assert result.ok and result.commercial_training_cleared is True


def test_a_missing_basis_is_still_its_own_error(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, _root = dataset
    manifest["dataset"]["permissions"]["commercial_training"] = "permitted"

    assert error_codes(validate_document(manifest)) == {"commercial-training-basis-missing"}


def test_the_basis_is_only_judged_when_commercial_training_is_permitted(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, _root = dataset
    manifest["dataset"]["permissions"].update(commercial_training="not_permitted", commercial_training_basis="x")

    assert validate_document(manifest).ok


def test_the_audit_applies_the_same_basis_check(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, _root = dataset
    manifest["dataset"]["permissions"].update(commercial_training="permitted", commercial_training_basis="n/a")

    audit = audit_manifests([("m.json", manifest)])

    assert "commercial-training-basis-not-meaningful" in {i.code for i in audit.report.errors}


# --- recorded evidence is not a legal determination -------------------------------------------------------------


def test_the_cli_still_says_it_does_not_judge_the_licence(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest, root = dataset
    manifest["dataset"]["permissions"].update(commercial_training="permitted", commercial_training_basis="Clause 4.2 of the licence.")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    code = validate_dataset_manifest.main(
        [str(path), "--dataset-dir", str(root), "--use", "training", "--require-commercial-training"]
    )

    out = capsys.readouterr().out.lower()
    assert code == 0
    assert "does not judge the licence" in out and "free text and is not verified" in out
    assert "legally" not in out and "lawful" not in out.replace("confirm that the use is lawful", "")
    assert "confirmed" not in out


# --- a licence snapshot must be usable text (final audit finding M-1) -------------------------------------------
#
# The original gap: the snapshot slot accepted anything with a checksum: a photograph, a ZIP holding two JPEGs, a
# byte-order mark alone, non-breaking spaces alone, zero-width spaces alone, a single dot, the single letter "x".
# It now has to be real UTF-8 (or BOM-marked UTF-16) text that is not an image, video, archive or other binary
# content. Recognition is by extension and a few known signatures: it is not complete, and a format that is not
# recognised is caught only because it is not valid text.


def zip_of_jpegs() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hidden/photo-1.jpg", fake_image_bytes("a"))
        archive.writestr("hidden/photo-2.jpg", fake_image_bytes("b"))
    return buffer.getvalue()


def set_snapshot(manifest: dict[str, Any], root: Path, relative_path: str, content: bytes) -> None:
    """Move the licence snapshot to another path, with its checksum, so only the new file is judged."""
    (root / SNAPSHOT).unlink()
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    manifest["dataset"]["licence"]["evidence"]["snapshot"] = {
        "relative_path": relative_path,
        "sha256": sha256_hex(content),
    }


NOT_TEXT = {
    "jpeg photograph": fake_image_bytes("snapshot"),
    "png": b"\x89PNG\r\n\x1a\nsynthetic licence terms of use apply to this data",
    "gif": b"GIF89a synthetic licence terms of use apply to this data",
    "mp4 video": b"\x00\x00\x00\x18ftypisom synthetic licence terms of use",
    "bmp": b"BM\x36\x00\x00\x00\x00\x00\x00\x00\x36\x00\x00\x00" + b"terms of use apply " * 3,
    "zip holding two jpegs": zip_of_jpegs(),
    "pdf document": b"%PDF-1.7\nsynthetic licence document, terms of use apply to this data\n",
    "gzip": b"\x1f\x8b\x08\x00" + b"\x00" * 32,
    "7z": b"7z\xbc\xaf\x27\x1c" + b"\x00" * 32,
    "rar": b"Rar!\x1a\x07\x00" + b"\x00" * 32,
    "tar": b"a.txt" + b"\x00" * 252 + b"ustar\x0000" + b"\x00" * 200,
    "sqlite": b"SQLite format 3\x00" + b"\x00" * 32,
}


@pytest.mark.parametrize("kind", sorted(NOT_TEXT))
def test_recognised_media_archives_and_documents_are_refused_as_a_snapshot_whatever_it_is_called(
    dataset: tuple[dict[str, Any], Path], kind: str
) -> None:
    manifest, root = dataset
    replace_snapshot(manifest, root, NOT_TEXT[kind])  # the name stays licence-snapshot.txt: the bytes give it away

    result = validate_document(manifest, dataset_dir=root, uses=["training", "validation", "testing"])

    assert error_codes(result) == {"licence-snapshot-not-text"} and result.cleared_uses == ()
    issue = result.report.errors[0]
    assert issue.location == "dataset.licence.evidence.snapshot" and "first bytes" in issue.message
    assert "not text" in issue.message and "not complete" in issue.message


@pytest.mark.parametrize(
    "extension",
    [".zip", ".7z", ".tar", ".gz", ".pdf", ".docx", ".jpg", ".JPG", ".png", ".svg", ".mp4", ".sqlite"],
)
def test_a_snapshot_named_like_media_or_an_archive_is_refused_even_if_its_content_is_text(
    dataset: tuple[dict[str, Any], Path], extension: str
) -> None:
    manifest, root = dataset
    set_snapshot(manifest, root, f"licence/licence{extension}", b"Terms of use: attribution is required and share alike.")

    result = validate_document(manifest, dataset_dir=root, uses=["training"])

    assert error_codes(result) == {"licence-snapshot-not-text"} and result.cleared_uses == ()
    assert f"by its extension ({extension.casefold()})" in result.report.errors[0].message


@pytest.mark.parametrize(
    "content",
    [
        b"Licence caf\xe9 terms of use apply to this data",  # a legacy code page, not UTF-8
        b"\xff\xff\xff\xffterms of use apply to this data",
        b"\xfe\xff\x00T\x00e\x00r\x00m\x00s\xd8\x00",  # UTF-16 with a broken surrogate
        bytes(range(128, 256)) * 4,
    ],
    ids=["cp1252", "invalid-bytes", "broken-utf16", "high-bytes"],
)
def test_content_that_is_not_valid_text_is_refused(dataset: tuple[dict[str, Any], Path], content: bytes) -> None:
    manifest, root = dataset
    replace_snapshot(manifest, root, content)

    result = validate_document(manifest, dataset_dir=root, uses=["training"])

    assert error_codes(result) == {"licence-snapshot-not-text"} and result.cleared_uses == ()
    assert "is not UTF-8 text" in result.report.errors[0].message


@pytest.mark.parametrize(
    "content",
    [
        b"\x00\x01\x02\x03binary content with some text in it",
        b"Terms of use apply\x00 to this data, with a NUL in the middle",
        b"Terms of use apply\x1b[0m to this data, with an escape character",
        b"Terms of use apply\x7f to this data, with a delete character",
        "Terms of use apply\x85 to this data, with a C1 control".encode("utf-8"),
    ],
    ids=["binary-prefix", "nul-in-text", "escape", "delete", "c1-control"],
)
def test_text_that_holds_control_characters_is_refused_as_binary(
    dataset: tuple[dict[str, Any], Path], content: bytes
) -> None:
    manifest, root = dataset
    replace_snapshot(manifest, root, content)

    result = validate_document(manifest, dataset_dir=root, uses=["training"])

    assert error_codes(result) == {"licence-snapshot-not-text"} and result.cleared_uses == ()
    assert "control characters" in result.report.errors[0].message


@pytest.mark.parametrize(
    "content",
    [
        b"\xef\xbb\xbf",  # a UTF-8 byte-order mark alone
        b"\xff\xfe",  # a UTF-16 byte-order mark alone
        b"\xfe\xff",
        b"\xef\xbb\xbf   \r\n",
        b"\xc2\xa0\xc2\xa0\xc2\xa0",  # non-breaking spaces alone
        b"\xe2\x80\x8b" * 5,  # zero-width spaces alone
        b"\xe2\x80\x8b\xe2\x80\x8d\xe2\x81\xa0",  # zero-width space, joiner and word joiner
        "   　".encode("utf-8"),  # line and paragraph separators, ideographic space
        b"\xc2\xad\xc2\xad",  # soft hyphens alone
    ],
    ids=["utf8-bom", "utf16-le-bom", "utf16-be-bom", "bom-and-space", "nbsp", "zero-width-spaces", "zero-width-mix", "unicode-spaces", "soft-hyphens"],
)
def test_a_snapshot_with_no_visible_text_is_refused(dataset: tuple[dict[str, Any], Path], content: bytes) -> None:
    manifest, root = dataset
    replace_snapshot(manifest, root, content)

    result = validate_document(manifest, dataset_dir=root, uses=["training"])

    assert error_codes(result) == {"licence-snapshot-empty"} and result.cleared_uses == ()
    assert "no visible text" in result.report.errors[0].message


@pytest.mark.parametrize(
    "content",
    [b".", b"x", b"Terms.", b"n/a", b"none", b"TBD", b"see licence", b"licence text", b"aaaaaaaaaaaa", b"- - - - - -", b"1234",
     b"yes yes yes"],
)
def test_a_snapshot_with_too_little_in_it_is_refused(dataset: tuple[dict[str, Any], Path], content: bytes) -> None:
    manifest, root = dataset
    replace_snapshot(manifest, root, content)

    result = validate_document(manifest, dataset_dir=root, uses=["training"])

    assert error_codes(result) == {"licence-snapshot-meaningless"} and result.cleared_uses == ()
    assert "not what it says" in result.report.errors[0].message


def test_the_snapshot_checks_do_not_replace_the_checksum_checks(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset
    (root / SNAPSHOT).write_bytes(zip_of_jpegs())  # replaced after the manifest recorded another checksum

    assert error_codes(validate_document(manifest, dataset_dir=root)) == {"checksum-mismatch", "licence-snapshot-not-text"}


def test_a_disguised_archive_cannot_hide_images_in_the_snapshot_slot(dataset: tuple[dict[str, Any], Path]) -> None:
    """The point of the finding: the licence-snapshot slot must not be a way to list a hidden dataset."""
    manifest, root = dataset
    replace_snapshot(manifest, root, zip_of_jpegs())

    result = validate_document(manifest, dataset_dir=root, uses=["training"])
    audit = audit_manifests([("m", manifest)], dataset_dirs={"m": root})

    assert not result.ok and result.cleared_uses == ()
    assert "licence-snapshot-not-text" in {i.code for i in audit.report.errors} and not audit.passed()


def test_the_snapshot_error_reaches_the_command_line(
    dataset: tuple[dict[str, Any], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest, root = dataset
    replace_snapshot(manifest, root, fake_image_bytes("photograph"))
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    code = validate_dataset_manifest.main([str(path), "--dataset-dir", str(root), "--use", "training"])

    err = capsys.readouterr().err
    assert code == 2 and "ERROR [licence-snapshot-not-text]" in err and "not complete" in err and err.isascii()


def test_only_the_first_megabyte_is_inspected_and_that_limit_is_the_documented_one(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    """A pinned limitation: the whole file is hashed, but binary content after the first megabyte is not seen."""
    manifest, root = dataset
    limit = validation_module.SNAPSHOT_INSPECT_BYTES
    replace_snapshot(manifest, root, b"Terms of use: attribution is required. " + b"a" * limit + b"\x00\x01binary tail")

    assert validate_document(manifest, dataset_dir=root, uses=["training"]).ok
    assert "first megabyte" in " ".join((validation_module._snapshot_problem.__doc__ or "").split())


def test_a_multibyte_character_cut_by_the_inspection_limit_is_not_mistaken_for_binary(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest, root = dataset
    limit = validation_module.SNAPSHOT_INSPECT_BYTES
    text = "Licence terms: attribution is required. " + "€" * limit  # three bytes each: the cut falls mid-character
    replace_snapshot(manifest, root, text.encode("utf-8"))

    assert validate_document(manifest, dataset_dir=root, uses=["training"]).ok


def test_the_licence_text_used_by_the_fixtures_still_passes(dataset: tuple[dict[str, Any], Path]) -> None:
    manifest, root = dataset

    result = validate_document(manifest, dataset_dir=root, uses=["training", "validation", "testing"])

    assert result.ok and result.cleared_uses == ("testing", "training", "validation")
