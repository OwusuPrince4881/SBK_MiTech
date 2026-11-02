import copy
import json
from pathlib import Path

import pytest

from scripts.validate_dataset_manifest import (
    DEFAULT_SCHEMA_PATH,
    ManifestValidationError,
    validate_manifest,
)

EXAMPLE_MANIFEST_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "examples"
    / "example_dataset_manifest.json"
)


@pytest.fixture
def valid_manifest() -> dict[str, object]:
    return json.loads(EXAMPLE_MANIFEST_PATH.read_text(encoding="utf-8"))


def test_valid_example_manifest_passes_schema(valid_manifest: dict[str, object]) -> None:
    validate_manifest(valid_manifest)


def test_manifest_missing_required_field_is_rejected(
    valid_manifest: dict[str, object],
) -> None:
    invalid_manifest = copy.deepcopy(valid_manifest)
    del invalid_manifest["dataset"]["name"]

    with pytest.raises(
        ManifestValidationError,
        match="'name' is a required property",
    ):
        validate_manifest(invalid_manifest)


def test_manifest_with_invalid_split_is_rejected(
    valid_manifest: dict[str, object],
) -> None:
    invalid_manifest = copy.deepcopy(valid_manifest)
    invalid_manifest["images"][0]["split"] = "development"

    with pytest.raises(ManifestValidationError, match="development"):
        validate_manifest(invalid_manifest)


def test_manifest_missing_licence_information_is_rejected(
    valid_manifest: dict[str, object],
) -> None:
    invalid_manifest = copy.deepcopy(valid_manifest)
    del invalid_manifest["dataset"]["source"]["licence"]

    with pytest.raises(
        ManifestValidationError,
        match="'licence' is a required property",
    ):
        validate_manifest(invalid_manifest)


@pytest.mark.parametrize(
    "measurement",
    [
        {
            "measurement": "fictional-placeholder",
            "value": "not-a-number",
            "unit": "fictional-placeholder",
            "method": "fictional-placeholder",
        },
        {
            "measurement": "fictional-placeholder",
            "value": "not-a-number",
            "method": "fictional-placeholder",
        },
    ],
)
def test_manifest_with_invalid_laboratory_measurement_is_rejected(
    valid_manifest: dict[str, object],
    measurement: dict[str, object],
) -> None:
    invalid_manifest = copy.deepcopy(valid_manifest)
    invalid_manifest["images"][0]["laboratory_measurements"] = [measurement]

    with pytest.raises(ManifestValidationError):
        validate_manifest(invalid_manifest)


def test_manifest_rejects_parent_directory_paths(
    valid_manifest: dict[str, object],
) -> None:
    invalid_manifest = copy.deepcopy(valid_manifest)
    invalid_manifest["images"][0]["relative_path"] = "../outside/image.jpg"

    with pytest.raises(ManifestValidationError):
        validate_manifest(invalid_manifest)


def test_manifest_schema_is_json_schema_2020_12() -> None:
    schema = json.loads(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
