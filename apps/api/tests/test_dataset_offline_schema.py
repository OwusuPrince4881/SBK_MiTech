"""The validator is offline: a custom schema cannot make it fetch, open or connect (audit N4).

An audit hook records every network call and every file open while a test runs, so these tests show
that nothing was attempted, not just that an error came back.
"""

import contextlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import schema as schema_module
from app.dataset_manifest import validate_document
from app.dataset_manifest.issues import Report
from app.dataset_manifest.schema import SchemaLoadError, assert_offline_schema, check_structure, make_validator
from dataset_builders import build_dataset
from scripts import validate_dataset_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
NETWORK_EVENTS = ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "urllib.Request")
_recorded: list[tuple[str, str]] = []
_recording = [False]


def _hook(event: str, args: tuple[Any, ...]) -> None:
    if not _recording[0]:
        return
    if event in NETWORK_EVENTS:
        _recorded.append((event, str(args[:2])))
    elif event == "open" and isinstance(args[0], (str, bytes, Path)):
        _recorded.append((event, str(args[0])))


sys.addaudithook(_hook)  # a hook cannot be removed; it records only inside `watching()`


@contextlib.contextmanager
def watching() -> Iterator[list[tuple[str, str]]]:
    _recorded.clear()
    _recording[0] = True
    try:
        yield _recorded
    finally:
        _recording[0] = False


def attempts(events: list[tuple[str, str]], *probes: str) -> list[tuple[str, str]]:
    """Network events, and opens of any file whose name contains one of the probe names."""
    return [
        event
        for event in events
        if event[0] in NETWORK_EVENTS or (event[0] == "open" and any(probe in event[1] for probe in probes))
    ]


def error_codes(result: Any) -> set[str]:
    return {issue.code for issue in result.report.errors}


def schema_file(folder: Path, document: Any, name: str = "custom.json") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.fixture
def manifest(tmp_path: Path) -> dict[str, Any]:
    return build_dataset(tmp_path / "dataset", sample_count=4, images_per_sample=2)


# --- external references are refused before anything is touched -------------------------------------


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef", "$recursiveRef"])
@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1:9/schema.json",
        "https://example.invalid/schema.json",
        "//example.invalid/schema.json",
        "urn:example:remote-schema",
        "other.json",
        "./other.json",
        "other.json#/definitions/x",
        "file:///C:/anything/at/all.json",
        "",
    ],
)
def test_no_external_reference_is_followed(
    manifest: dict[str, Any], tmp_path: Path, keyword: str, target: str
) -> None:
    custom = schema_file(tmp_path / "schemas", {keyword: target})

    with watching() as events:
        result = validate_document(manifest, schema_path=custom)

    assert error_codes(result) == {"schema-unavailable"}
    assert "Only references to a place inside the same schema" in result.report.errors[0].message
    assert attempts(events, "other.json", "all.json") == []


def test_a_file_reference_to_a_real_local_file_is_not_opened(manifest: dict[str, Any], tmp_path: Path) -> None:
    """The original gap: a file: reference made the validator read the file it named."""
    canary = schema_file(tmp_path / "secret", {"type": "object"}, "canary-secret.json")
    custom = schema_file(tmp_path / "schemas", {"$ref": canary.as_uri()})

    with watching() as events:
        result = validate_document(manifest, schema_path=custom)

    assert error_codes(result) == {"schema-unavailable"}
    assert attempts(events, "canary-secret.json") == []


def test_a_network_reference_makes_no_connection(manifest: dict[str, Any], tmp_path: Path) -> None:
    """The original gap: an http: reference made the validator open a socket."""
    custom = schema_file(tmp_path / "schemas", {"$ref": "http://127.0.0.1:9/schema.json"})

    with watching() as events:
        result = validate_document(manifest, schema_path=custom)

    assert error_codes(result) == {"schema-unavailable"}
    assert [event for event in events if event[0] in NETWORK_EVENTS] == []


@pytest.mark.parametrize(
    "document",
    [
        {"properties": {"records": {"items": {"$ref": "https://example.invalid/record.json"}}}},
        {"$defs": {"a": {"allOf": [{"$ref": "https://example.invalid/a.json"}]}}},
        {"anyOf": [{"type": "object"}, {"$dynamicRef": "https://example.invalid/d.json"}]},
    ],
)
def test_a_nested_external_reference_is_found_too(
    manifest: dict[str, Any], tmp_path: Path, document: dict[str, Any]
) -> None:
    custom = schema_file(tmp_path / "schemas", document)

    with watching() as events:
        result = validate_document(manifest, schema_path=custom)

    assert error_codes(result) == {"schema-unavailable"}
    assert attempts(events) == []


def test_an_id_cannot_rebase_local_references_onto_a_remote_address(
    manifest: dict[str, Any], tmp_path: Path
) -> None:
    custom = schema_file(
        tmp_path / "schemas",
        {"$id": "http://127.0.0.1:9/base/", "$defs": {"x": {"type": "object"}}, "$ref": "#/$defs/x"},
    )

    with watching() as events:
        result = validate_document(manifest, schema_path=custom)

    assert error_codes(result) == {"schema-unavailable"} and "$id" in result.report.errors[0].message
    assert attempts(events) == []


def test_local_references_still_work_and_still_add_requirements(
    manifest: dict[str, Any], tmp_path: Path
) -> None:
    custom = schema_file(
        tmp_path / "schemas",
        {
            "$defs": {"short": {"type": "string", "maxLength": 5}},
            "properties": {"dataset": {"properties": {"name": {"$ref": "#/$defs/short"}}}},
        },
    )

    result = validate_document(manifest, schema_path=custom)

    assert error_codes(result) == {"schema"}
    assert "is too long" in result.report.errors[0].message


def test_an_unresolvable_local_reference_is_reported_not_raised(manifest: dict[str, Any], tmp_path: Path) -> None:
    custom = schema_file(tmp_path / "schemas", {"properties": {"dataset": {"$ref": "#/$defs/missing"}}})

    with watching() as events:
        result = validate_document(manifest, schema_path=custom)

    assert error_codes(result) == {"schema-unavailable"} and "cannot be resolved offline" in result.report.errors[0].message
    assert attempts(events) == []


def test_even_if_the_up_front_scan_were_skipped_the_validator_cannot_retrieve_anything() -> None:
    """Second line of defence: validators are built with a registry that cannot retrieve."""
    schema = {"$ref": "http://127.0.0.1:9/schema.json"}

    with watching() as events:
        with pytest.raises(SchemaLoadError, match="cannot be resolved offline"):
            check_structure({"a": 1}, schema, Report())

    assert [event for event in events if event[0] in NETWORK_EVENTS] == []
    assert make_validator({"type": "object"}).is_valid({})


def test_the_scan_is_strict_about_what_counts_as_local() -> None:
    for fine in ("#", "#/$defs/x", "#anchor"):
        assert_offline_schema({"$ref": fine})
    for bad in ("x#", "./#", "http://a/#", " #", 5, None, {"nested": "#"}):
        with pytest.raises(SchemaLoadError):
            assert_offline_schema({"$ref": bad})
    with pytest.raises(SchemaLoadError):
        assert_offline_schema({"properties": {"$id": {"type": "string"}}})  # strict: a property named $id too


def test_the_built_in_schemas_still_work_without_any_retrieval() -> None:
    for name in ("example_dataset_manifest.json", "example_dataset_manifest_1_1.json"):
        document = json.loads((REPO_ROOT / "docs" / "examples" / name).read_text(encoding="utf-8"))
        with watching() as events:
            result = validate_document(document)
        assert result.ok, name
        assert [event for event in events if event[0] in NETWORK_EVENTS] == []


# --- a custom schema in a restricted location is refused unread ------------------------------------------


@pytest.mark.parametrize("parts", [("data", "evaluation"), ("data", "uploads"), ("data", "umat_practicals"), ("x", "umat_practicals")])
def test_a_custom_schema_in_a_restricted_location_is_refused_and_never_opened(
    manifest: dict[str, Any], tmp_path: Path, parts: tuple[str, ...]
) -> None:
    custom = schema_file(tmp_path.joinpath(*parts), {"type": "object"}, "restricted-probe-schema.json")

    with watching() as events:
        result = validate_document(manifest, schema_path=custom)

    assert error_codes(result) == {"restricted-schema"}
    assert "restricted location" in result.report.errors[0].message
    assert attempts(events, "restricted-probe-schema.json") == []


def test_the_validator_cli_refuses_a_restricted_schema_path(
    manifest: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = schema_file(tmp_path / "m", manifest, "manifest.json")
    custom = schema_file(tmp_path / "data" / "evaluation", {"type": "object"}, "restricted-probe-schema.json")

    with watching() as events:
        code = validate_dataset_manifest.main([str(manifest_path), "--schema", str(custom)])

    assert code == 2
    assert "restricted location" in capsys.readouterr().err
    assert attempts(events, "restricted-probe-schema.json") == []


def test_the_validator_cli_refuses_a_remote_schema_reference_without_a_traceback(
    manifest: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = schema_file(tmp_path / "m", manifest, "manifest.json")
    custom = schema_file(tmp_path / "s", {"$ref": "https://example.invalid/schema.json"})

    with watching() as events:
        code = validate_dataset_manifest.main([str(manifest_path), "--schema", str(custom)])

    err = capsys.readouterr().err
    assert code == 2 and "offline" in err and "Traceback" not in err
    assert attempts(events) == []


def test_the_legacy_structure_check_is_offline_for_a_custom_schema_too(tmp_path: Path) -> None:
    custom = schema_file(tmp_path, {"$ref": "http://127.0.0.1:9/schema.json"})
    document = json.loads((REPO_ROOT / "docs" / "examples" / "example_dataset_manifest.json").read_text(encoding="utf-8"))

    with watching() as events:
        with pytest.raises(validate_dataset_manifest.ManifestValidationError, match="offline"):
            validate_dataset_manifest.validate_manifest(document, schema_path=custom)

    assert attempts(events) == []


# --- a custom schema can only add requirements ----------------------------------------------------------------


def test_a_permissive_custom_schema_does_not_lift_any_built_in_rule(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    umat = build_dataset(root, sample_count=4, images_per_sample=2)
    umat["dataset"]["category"] = "umat_practical"
    split_twice = build_dataset(tmp_path / "other", sample_count=4, images_per_sample=2)
    split_twice["records"][0]["split"], split_twice["records"][1]["split"] = "train", "test"
    extra_key = build_dataset(tmp_path / "third", sample_count=4, images_per_sample=2)
    extra_key["unexpected"] = 1

    for permissive in ({}, {"$comment": "nothing here"}, {"additionalProperties": True}, {"type": ["object", "array"]}):
        custom = schema_file(tmp_path / "schemas", permissive)
        assert "restricted-category" in error_codes(validate_document(umat, schema_path=custom))
        assert "sample-in-multiple-splits" in error_codes(validate_document(split_twice, schema_path=custom))
        assert "schema" in error_codes(validate_document(extra_key, schema_path=custom))


def test_an_unusable_schema_file_is_reported_not_raised(
    manifest: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    not_utf8 = tmp_path / "not-utf8.json"
    not_utf8.write_bytes(b"\xff\xfe{ not utf-8")
    too_deep = tmp_path / "too-deep.json"
    too_deep.write_text("[" * 100_000, encoding="utf-8")
    large = schema_file(tmp_path, {"description": "x" * 100}, "large.json")

    for path in (not_utf8, too_deep, tmp_path):  # tmp_path is a directory, not a file
        assert error_codes(validate_document(manifest, schema_path=path)) == {"schema-unavailable"}, path
    monkeypatch.setattr(schema_module, "MAX_SCHEMA_BYTES", 50)
    result = validate_document(manifest, schema_path=large)
    assert error_codes(result) == {"schema-unavailable"} and "larger than 50 bytes" in result.report.errors[0].message
