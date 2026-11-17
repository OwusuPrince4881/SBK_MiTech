"""The UMaT/MN273 name tripwire and source manifests (audit N3 and N6).

The tripwire is a best-effort check on names. These tests pin what it catches, what it must
leave alone, and that it is applied to every string of a manifest and to source manifests too.
They do not, and cannot, show that arbitrary files are free of restricted material.
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import (
    ManifestLoadError,
    audit_manifests,
    find_restricted_name,
    load_source_views,
    source_view,
    validate_document,
)
from app.dataset_manifest import restricted_names as restricted_names_module
from app.dataset_manifest.restricted_names import normalise_for_matching, restricted_text_hits
from app.dataset_manifest.validation import ValidationResult
from dataset_builders import build_dataset
from scripts import audit_dataset_leakage, generate_dataset_splits, validate_dataset_manifest


def error_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.errors}


def write_json(path: Path, document: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def rich_manifest(root: Path) -> dict[str, Any]:
    """A valid manifest with every optional text field filled in, so every string can be probed."""
    manifest = build_dataset(root, sample_count=4, images_per_sample=2)
    dataset = manifest["dataset"]
    dataset["permissions"].update(
        commercial_training="permitted",
        commercial_training_basis="Clause 4.2 of the synthetic licence",
        authorisation_reference="fixture-authorisation-1",
        notes="fixture notes",
    )
    dataset["source"].update(
        url="https://example.invalid/source", doi="10.1234/fixture", custodian="Fixture custodian",
        citation="Fixture citation",
    )
    dataset["licence"].update(
        url="https://example.invalid/licence", usage_notes="fixture usage notes", spdx_id="fixture-1"
    )
    dataset["sample_identity"]["notes"] = "recorded by the collector"
    manifest["records"][0]["acquisition"] = {
        "captured_at": "2026-01-01T00:00:00Z", "lighting": "fixture value", "moisture_condition": "fixture value",
        "scale_reference": {"present": True, "description": "fixture value"},
        "camera": {"make": "fixture value", "model": "fixture value", "notes": "fixture value"},
        "viewpoint": "fixture value", "notes": "fixture value",
    }
    return manifest


# --- what the tripwire catches ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "UMaT practical", "umat", "UMAT", "umat_practicals", "umat-2024", "UMAT2024", "umatpracticals", "images/UMAT_practicals/x.jpg",
        "MN273", "mn273", "MN-273 team", "MN 273 team", "mn_273", "MN.273 dataset", "MN/273 dataset", "MN 0273",
        "M N 2 7 3", "see the MN273 brief",
        "U.M.a.T photos", "U M a T photos", "U-MaT photos",
        "ＵＭａＴ photos",  # full-width letters
        "UМаT photos",  # Cyrillic look-alikes
        "UM​aT photos",  # zero-width space
        "UM­aT",  # soft hyphen
        "MN２７３",  # full-width digits
        "University of Mines and Technology Tarkwa", "university  of  mines & technology",
    ],
)
def test_restricted_names_are_found_in_their_common_spellings(text: str) -> None:
    assert find_restricted_name(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Sumatra coal pit photographs", "Pneumatic hammer drill cores", "Humate-treated clay samples",
        "Umatilla County gravel survey", "Rheumatic-free fixture",  # the words named in the audit
        "Kumasi laterite", "Automatic grain counter", "Tarkwa laterite", "Formatted field photographs",
        "Automatic classification set", "Kumasi field set", "Thermatic",
        "mn2735 sample", "xmn273", "item 2 7 3", "autumn 2730", "University of Mines", "Technology University",
    ],
)
def test_innocent_words_are_left_alone(text: str) -> None:
    assert find_restricted_name(text) is None


# --- concatenated names (final audit finding M-4) ----------------------------------------------------------------
#
# The 2B.3 word-boundary rule let a restricted name through whenever it was glued to other letters: UMaTData,
# umatphotos, myumat. Contiguous `umat` is now refused anywhere, except inside a short list of innocent words
# that are masked out first (and only as pieces, so a name hidden next to one is still found). This is a
# deliberate trade: any other word that merely contains `umat` is refused too, and has to be renamed.


@pytest.mark.parametrize(
    "text",
    [
        "UMaTData", "umatphotos", "umatsamples", "umatlab", "UMaTField", "myumat", "images/UMaTphotos/a.jpg",
        "UMaTdata/2024", "datasetUMaT", "labumat", "umat1", "xumatx", "UMAT_DATA", "umat.jpg", "data-umat-2",
        # words that END in the letters, or hold them in a longer unknown word: refused, rename them
        "Plumat", "Kumat practical", "Dumat2024", "3umat", "Zumat-samples", "Dumatic",
        # a restricted name hidden next to an innocent word is still found, because the stems are masked as pieces
        "pneumaticumat", "SumatraUMaT", "umatSumatra", "Pneumatic-UMaT-Hammer", "HumateUMaTClay",
        # MN273 glued to a preceding word in capitals, or followed by a letter
        "ProjectMN273", "teamMN273", "MN273Data", "mn273photos", "images/MN273photos/a.jpg", "DataMN0273",
    ],
)
def test_a_restricted_name_glued_to_other_letters_is_found(text: str) -> None:
    assert find_restricted_name(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        # the words named in the audit, alone and as the start of a longer identifier
        "Sumatra", "pneumatic", "humate", "Umatilla", "rheumatic",
        "SumatraTrip", "PneumaticHammer", "RheumaticFever", "HumateClay", "UmatillaCounty",
        "images/Sumatra/a.jpg", "SumatraCoalPit", "pneumatic_drill_2024", "Rheumatology", "humates",
        # other words built on the listed stems
        "Thaumatin", "Sumatriptan", "Pneumatology", "Fumatori", "Cumate", "Thaumaturgy",
        # MN273 inside a longer word or number, or after a word that ends in mn
        "autumn 273", "column 273", "hymn273", "HYMN273", "Column273", "mn2735", "autumn273", "ColumnData273",
        # long words that merely look similar
        "Kumasi", "Automatic", "Formatted", "Tarkwa", "Thermatic", "Numerical", "Mathematical",
    ],
)
def test_innocent_words_containing_or_resembling_the_letters_are_still_accepted(text: str) -> None:
    assert find_restricted_name(text) is None


def test_a_known_limit_of_the_tripwire_is_pinned() -> None:
    """MN273 in lower case, glued to a word before it, is not split by the camel-case rule. Stated, not hidden."""
    assert find_restricted_name("teammn273") is None
    assert find_restricted_name("team MN273") == "MN273" and find_restricted_name("teamMN273") == "MN273"
    assert "best-effort" in (restricted_names_module.__doc__ or "").replace("\n", " ")
    assert "NOT proof" in (restricted_names_module.__doc__ or "")


def test_a_concatenated_name_in_a_record_path_is_refused_by_validation(tmp_path: Path) -> None:
    """The record path is a string in the manifest, so the tripwire sees it: no restricted-name path validates."""
    manifest = build_dataset(tmp_path / "files", sample_count=4, images_per_sample=2)
    assert validate_document(manifest).ok
    manifest["records"][0]["file"]["relative_path"] = "images/UMaTphotos/a.jpg"

    result = validate_document(manifest)

    assert "restricted-material-suspected" in error_codes(result) and result.cleared_uses == ()
    message = next(i.message for i in result.report.errors if i.code == "restricted-material-suspected")
    assert "records[0].file.relative_path" in message and message.isascii()


@pytest.mark.parametrize("name", ["UMaTData", "umatphotos", "umatsamples", "umatlab", "UMaTField", "myumat", "ProjectMN273"])
def test_a_concatenated_name_is_refused_in_a_dataset_name_and_in_every_context(tmp_path: Path, name: str) -> None:
    manifest = build_dataset(tmp_path / "files", sample_count=4, images_per_sample=2)
    manifest["dataset"]["name"] = name

    result = validate_document(manifest, dataset_dir=tmp_path / "files", uses=["training"])
    audit = audit_manifests([("m", manifest)])

    assert "restricted-material-suspected" in error_codes(result) and result.cleared_uses == ()
    assert "restricted-material-suspected" in {i.code for i in audit.report.errors} and not audit.passed()


@pytest.mark.parametrize("name", ["SumatraTrip", "PneumaticHammer", "RheumaticFever", "HumateClay", "UmatillaCounty"])
def test_an_innocent_compound_is_not_refused_in_a_dataset_name(tmp_path: Path, name: str) -> None:
    manifest = build_dataset(tmp_path / "files", sample_count=4, images_per_sample=2)
    manifest["dataset"]["name"] = name

    assert validate_document(manifest, dataset_dir=tmp_path / "files", uses=["training"]).ok


def test_normalisation_folds_spelling_tricks_but_leaves_ordinary_text_readable() -> None:
    assert normalise_for_matching("Café") == "cafe"
    assert normalise_for_matching("UM​aT") == "umat"
    assert normalise_for_matching("ＵＭａＴ") == "umat"
    assert normalise_for_matching("Plain ASCII") == "plain ascii"


def test_a_very_long_string_is_handled_without_a_traceback() -> None:
    assert find_restricted_name("a" * 5_000_000 + " umat") == "UMaT"
    assert find_restricted_name("é" * 1_000_000) is None


# --- every string in a manifest is checked ---------------------------------------------------------


def test_every_text_field_of_a_manifest_is_checked_for_restricted_names(tmp_path: Path) -> None:
    """The original gap: authorisation references, notes, licence names and sample IDs were not looked at."""
    base = rich_manifest(tmp_path / "files")
    assert validate_document(base).ok

    def leaves(node: Any, path: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        if isinstance(node, dict):
            return [leaf for key, child in node.items() for leaf in leaves(child, path + (key,))]
        if isinstance(node, list):
            return [leaf for index, child in enumerate(node) for leaf in leaves(child, path + (index,))]
        return [path] if isinstance(node, str) else []

    flagged: list[str] = []
    unflagged: list[str] = []
    for path in leaves(base):
        if path == ("schema_version",):
            continue  # not free text: any other value is an unsupported version, which stops validation first
        manifest = copy.deepcopy(base)
        node = manifest
        for part in path[:-1]:
            node = node[part]
        original = node[path[-1]]
        node[path[-1]] = original + ("-UMaT" if " " not in original else " UMaT")
        result = validate_document(manifest)
        label = ".".join(str(part) for part in path)
        if "schema" in error_codes(result):
            continue  # an enumeration, a date or a checksum: the edit is not a legal value
        (flagged if "restricted-material-suspected" in error_codes(result) else unflagged).append(label)

    assert unflagged == []
    for must in (
        "dataset.permissions.authorisation_reference", "dataset.permissions.notes", "dataset.licence.name",
        "dataset.licence.usage_notes", "dataset.sample_identity.notes", "dataset.description",
        "dataset.source.citation", "dataset.source.doi", "dataset.permissions.reviewed_by",
        "dataset.label_vocabulary.vocabulary_id", "records.0.record_id", "records.0.physical_sample_id",
        "records.0.capture_session_id", "records.0.acquisition.notes", "records.0.label.source.description",
    ):
        assert must in flagged, must


def test_a_public_dataset_that_names_mn273_in_its_authorisation_reference_is_refused(tmp_path: Path) -> None:
    manifest = rich_manifest(tmp_path / "files")
    manifest["dataset"]["permissions"]["authorisation_reference"] = "MN273 RED-zone exception granted"
    manifest["dataset"]["permissions"]["notes"] = "photographs from the UMaT practical"

    result = validate_document(manifest, dataset_dir=tmp_path / "files", uses=["training"])

    assert "restricted-material-suspected" in error_codes(result)
    assert result.cleared_uses == ()
    message = next(i.message for i in result.report.errors if i.code == "restricted-material-suspected")
    assert "dataset.permissions.authorisation_reference" in message and "best-effort" in message


def test_the_tripwire_message_is_plain_ascii_and_does_not_flood(tmp_path: Path) -> None:
    manifest = rich_manifest(tmp_path / "files")
    manifest["dataset"]["name"] = "UMaT 日本 " + "x" * 100
    for record in manifest["records"]:  # eight records, so nine hits in all
        record["capture_session_id"] = "umat-session"

    result = validate_document(manifest)

    message = next(i.message for i in result.report.errors if i.code == "restricted-material-suspected")
    assert message.isascii() and len(message) < 1500 and "and 4 more" in message


def test_hits_are_ordered_and_capped_so_results_do_not_depend_on_traversal(tmp_path: Path) -> None:
    manifest = rich_manifest(tmp_path / "files")
    manifest["dataset"]["name"] = "UMaT one"
    manifest["dataset"]["description"] = "MN273 two"

    first = restricted_text_hits(manifest)
    second = restricted_text_hits(json.loads(json.dumps(manifest, sort_keys=True)))

    assert [hit[0] for hit in first] == ["dataset.description", "dataset.name"] == [hit[0] for hit in second]
    assert len(restricted_text_hits(manifest, limit=1)) == 1


# --- source manifests are checked like any other manifest ----------------------------------------------


def derived_pair(tmp_path: Path, source_name: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """A derived manifest (dataset 'derived-b') whose first record derives from 'source-a'."""
    source = build_dataset(tmp_path / "source-files", sample_count=4, images_per_sample=2, dataset_id="source-a")
    if source_name is not None:
        source["dataset"]["name"] = source_name
    derived = build_dataset(tmp_path / "derived-files", sample_count=4, images_per_sample=2, dataset_id="derived-b")
    derived["records"][0]["provenance"] = {"derived_from": [{"dataset_id": "source-a", "record_id": source["records"][0]["record_id"]}]}
    for record in derived["records"] + source["records"]:
        record["split"] = "train"
    return source, derived


def test_a_source_that_names_itself_after_restricted_material_is_not_trusted(tmp_path: Path) -> None:
    """The original gap: only a source's declared category was looked at."""
    source, derived = derived_pair(tmp_path, source_name="UMaT practical sample photographs")
    assert source["dataset"]["category"] == "synthetic"

    result = validate_document(derived, provenance_sources={"source-a": source_view(source)})

    assert "derived-from-restricted-source" in error_codes(result)


def test_a_clean_source_still_works(tmp_path: Path) -> None:
    source, derived = derived_pair(tmp_path)

    bound = source_view(source, dataset_dir=tmp_path / "source-files")  # its files are verified there

    assert validate_document(derived, provenance_sources={"source-a": bound}).ok


def test_the_validator_cli_applies_the_tripwire_to_source_manifests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source, derived = derived_pair(tmp_path, source_name="MN 273 team photographs")
    source_path, derived_path = write_json(tmp_path / "m" / "source.json", source), write_json(tmp_path / "m" / "derived.json", derived)

    code = validate_dataset_manifest.main([str(derived_path), "--source-manifest", str(source_path)])

    assert code == 2 and "derived-from-restricted-source" in capsys.readouterr().err


def test_the_splitter_cli_applies_the_tripwire_to_source_manifests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source, derived = derived_pair(tmp_path, source_name="UMaT practical samples")
    for record in derived["records"]:
        record["split"] = "unassigned"
    source_path, derived_path = write_json(tmp_path / "m" / "source.json", source), write_json(tmp_path / "m" / "derived.json", derived)

    code = generate_dataset_splits.main(
        [str(derived_path), "--dataset-dir", str(tmp_path / "derived-files"), "--seed", "1",
         "--output", str(tmp_path / "s.json"), "--source-manifest", str(source_path)]
    )

    captured = capsys.readouterr()
    assert code == 2 and "derived-from-restricted-source" in captured.err
    assert not (tmp_path / "s.json").exists()


def test_the_audit_and_the_validator_agree_about_a_restricted_source(tmp_path: Path) -> None:
    source, derived = derived_pair(tmp_path, source_name="UMaT practical samples")

    audit = audit_manifests([("source", source), ("derived", derived)])
    validator = validate_document(derived, provenance_sources={"source-a": source_view(source)})

    assert "derived-from-restricted-source" in {i.code for i in audit.report.errors}
    assert "derived-from-restricted-source" in error_codes(validator)


# --- duplicate source manifests and argument order -----------------------------------------------------------


def test_two_source_manifests_with_one_dataset_id_are_refused_in_either_order(tmp_path: Path) -> None:
    source, derived = derived_pair(tmp_path)
    other = copy.deepcopy(source)
    for record in other["records"]:
        record["split"] = "independent_evaluation"
    other["dataset"]["permissions"]["permitted_uses"] = ["independent_evaluation"]
    first, second = write_json(tmp_path / "m" / "a.json", source), write_json(tmp_path / "m" / "b.json", other)

    messages = []
    for paths in ([first, second], [second, first]):
        with pytest.raises(ManifestLoadError) as caught:
            load_source_views(paths)
        messages.append(str(caught.value))

    assert messages[0] == messages[1]
    assert "same dataset_id ('source-a')" in messages[0] and "order of the arguments" in messages[0]


def test_the_validator_cli_refuses_duplicate_sources_whatever_the_argument_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The original gap: with two sources for one dataset, the first silently won."""
    source, derived = derived_pair(tmp_path)
    evaluation = copy.deepcopy(source)
    for record in evaluation["records"]:
        record["split"] = "independent_evaluation"
    evaluation["dataset"]["permissions"]["permitted_uses"] = ["independent_evaluation"]
    train, held_out = write_json(tmp_path / "m" / "train.json", source), write_json(tmp_path / "m" / "eval.json", evaluation)
    derived_path = write_json(tmp_path / "m" / "derived.json", derived)

    codes = []
    for order in ((train, held_out), (held_out, train)):
        arguments = [str(derived_path)]
        for path in order:
            arguments += ["--source-manifest", str(path)]
        codes.append(validate_dataset_manifest.main(arguments))
    errors = capsys.readouterr().err

    assert codes == [2, 2]
    assert errors.count("same dataset_id ('source-a')") == 2


def test_a_source_with_the_same_dataset_id_as_the_manifest_is_refused(tmp_path: Path) -> None:
    source, derived = derived_pair(tmp_path)
    path = write_json(tmp_path / "m" / "source.json", source)

    with pytest.raises(ManifestLoadError, match="not a source for itself"):
        load_source_views([path], own_dataset_id="source-a")


def test_distinct_sources_give_the_same_result_in_any_order(tmp_path: Path) -> None:
    source, derived = derived_pair(tmp_path)
    third = build_dataset(tmp_path / "third-files", sample_count=2, images_per_sample=1, dataset_id="third-c")
    paths = [write_json(tmp_path / "m" / "source.json", source), write_json(tmp_path / "m" / "third.json", third)]

    directories = {"source-a": tmp_path / "source-files", "third-c": tmp_path / "third-files"}
    forward = load_source_views(paths, dataset_dirs=directories)
    backward = load_source_views(list(reversed(paths)), dataset_dirs=directories)

    assert list(forward) == list(backward) == ["source-a", "third-c"]
    assert validate_document(derived, provenance_sources=forward).ok
    assert validate_document(derived, provenance_sources=backward).ok
