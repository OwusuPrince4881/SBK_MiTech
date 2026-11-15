import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.dataset_manifest.issues import Report

# This file is app/dataset_manifest/schema.py, so parents[4] is the repository root.
DOCS_DIR = Path(__file__).resolve().parents[4] / "docs"

SCHEMA_PATHS: dict[str, Path] = {
    "1.0": DOCS_DIR / "dataset_manifest.schema.json",
    "1.1": DOCS_DIR / "dataset_manifest_1_1.schema.json",
}
SUPPORTED_VERSIONS = tuple(SCHEMA_PATHS)
CURRENT_VERSION = "1.1"


class SchemaLoadError(ValueError):
    pass


def detect_version(manifest: Any) -> str | None:
    if isinstance(manifest, dict):
        version = manifest.get("schema_version")
        if isinstance(version, str):
            return version
    return None


def load_schema(version: str, schema_path: Path | None = None) -> dict[str, Any]:
    path = schema_path or SCHEMA_PATHS[version]
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SchemaLoadError(f"Unable to read manifest schema '{path}': {error}") from error
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise SchemaLoadError(f"Manifest schema is invalid: {error.message}") from error
    return schema


def _format_path(parts: Any) -> str:
    text = ""
    for part in parts:
        text += f"[{part}]" if isinstance(part, int) else f".{part}" if text else str(part)
    return text


def check_structure(manifest: Any, schema: dict[str, Any], report: Report) -> None:
    """Add one `schema` error per JSON Schema violation."""
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    errors = sorted(
        validator.iter_errors(manifest),
        key=lambda error: (list(map(str, error.absolute_path)), error.message),
    )
    for error in errors:
        report.error("schema", error.message, _format_path(error.absolute_path))
