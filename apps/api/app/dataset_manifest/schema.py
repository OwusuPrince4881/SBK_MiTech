"""Manifest schemas, loaded and applied strictly offline.

The validator must never read an arbitrary file or open a network connection
because of what a schema says. Two things enforce that for a custom schema:

1. `assert_offline_schema` rejects, before anything is validated, every `$ref`,
   `$dynamicRef` or `$recursiveRef` that is not a reference to a place inside the
   same schema (`#...`), and every `$id` (which could re-base a local reference
   onto a remote address).
2. Validators are built with an empty reference registry that has no way to
   retrieve anything, so an unknown reference is an error and never a download or
   a file read, whatever jsonschema's defaults are.

A custom schema can only add requirements: the built-in schema always applies as
well, so it cannot weaken the structure or the governance rules built on it.
"""

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.dataset_manifest.issues import Report
from app.dataset_manifest.restricted import ensure_not_restricted

try:
    from referencing import Registry as _Registry
    from referencing.exceptions import Unresolvable as _Unresolvable
except ImportError:  # pragma: no cover - jsonschema >= 4.18 always brings `referencing`
    _Registry = None
    _Unresolvable = ()

# This file is app/dataset_manifest/schema.py, so parents[4] is the repository root.
DOCS_DIR = Path(__file__).resolve().parents[4] / "docs"

SCHEMA_PATHS: dict[str, Path] = {
    "1.0": DOCS_DIR / "dataset_manifest.schema.json",
    "1.1": DOCS_DIR / "dataset_manifest_1_1.schema.json",
}
SUPPORTED_VERSIONS = tuple(SCHEMA_PATHS)
CURRENT_VERSION = "1.1"
MAX_SCHEMA_BYTES = 8 * 1024 * 1024  # A custom schema is small; this bounds memory use.
_REFERENCE_KEYWORDS = frozenset({"$ref", "$dynamicRef", "$recursiveRef"})


class SchemaLoadError(ValueError):
    pass


def detect_version(manifest: Any) -> str | None:
    if isinstance(manifest, dict):
        version = manifest.get("schema_version")
        if isinstance(version, str):
            return version
    return None


def assert_offline_schema(schema: Any) -> None:
    """Raise SchemaLoadError if `schema` could make a validator look outside itself.

    Only local references (`"#"`, `"#/$defs/name"`, `"#anchor"`) are allowed, and
    `$id` is refused. A property that is merely *named* `$ref` or `$id` is refused
    too: it is cheaper to be strict than to tell the two uses apart.
    """
    stack: list[Any] = [schema]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            for key, child in value.items():
                if key in _REFERENCE_KEYWORDS and not (isinstance(child, str) and child.startswith("#")):
                    raise SchemaLoadError(
                        f"the custom schema uses {key} {ascii(child)[:80]}. Only references to a place "
                        "inside the same schema (starting with '#') are allowed: the validator is "
                        "offline and never fetches a remote or local file."
                    )
                if key == "$id":
                    raise SchemaLoadError(
                        "the custom schema sets $id, which could re-base its references onto another "
                        "address. Remove it: the validator is offline."
                    )
                stack.append(child)
        elif isinstance(value, list):
            stack.extend(value)


def load_schema(version: str, schema_path: Path | None = None) -> dict[str, Any]:
    """The built-in schema for `version`, or a custom schema read from `schema_path`.

    A custom path in a restricted location raises RestrictedPathError before the
    file is opened. A custom schema is also checked by `assert_offline_schema`.
    """
    if schema_path is None:
        path = SCHEMA_PATHS[version]
    else:
        ensure_not_restricted(schema_path, "Custom schema")
        path = schema_path
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_SCHEMA_BYTES + 1)
        if len(data) > MAX_SCHEMA_BYTES:
            raise SchemaLoadError(f"Manifest schema '{path}' is larger than {MAX_SCHEMA_BYTES} bytes.")
        schema = json.loads(data.decode("utf-8"))
    except SchemaLoadError:
        raise
    except (OSError, ValueError, RecursionError) as error:
        raise SchemaLoadError(f"Unable to read manifest schema '{path}': {error}") from error
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise SchemaLoadError(f"Manifest schema is invalid: {error.message}") from error
    except RecursionError as error:
        raise SchemaLoadError("Manifest schema is nested too deeply.") from error
    if schema_path is not None:
        assert_offline_schema(schema)
    return schema


def make_validator(schema: dict[str, Any]) -> Draft202012Validator:
    """A validator that cannot retrieve any reference: unknown references are errors."""
    if _Registry is None:  # pragma: no cover
        return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    return Draft202012Validator(
        schema, registry=_Registry(), format_checker=Draft202012Validator.FORMAT_CHECKER
    )


def _format_path(parts: Any) -> str:
    text = ""
    for part in parts:
        text += f"[{part}]" if isinstance(part, int) else f".{part}" if text else str(part)
    return text


def check_structure(manifest: Any, schema: dict[str, Any], report: Report) -> None:
    """Add one `schema` error per JSON Schema violation."""
    validator = make_validator(schema)
    try:
        errors = sorted(
            validator.iter_errors(manifest),
            key=lambda error: (list(map(str, error.absolute_path)), error.message),
        )
    except _Unresolvable as error:
        raise SchemaLoadError(
            f"the schema has a reference that cannot be resolved offline ({ascii(str(error))[:120]})."
        ) from error
    for error in errors:
        report.error("schema", error.message, _format_path(error.absolute_path))
