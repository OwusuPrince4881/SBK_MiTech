import argparse
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "docs" / "dataset_manifest.schema.json"


class ManifestValidationError(ValueError):
    pass


def validate_manifest(
    manifest: Any,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
) -> None:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestValidationError(
            f"Unable to read manifest schema '{schema_path}': {error}"
        ) from error

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise ManifestValidationError(
            f"Manifest schema is invalid: {error.message}"
        ) from error

    validator = Draft202012Validator(
        schema,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    errors = sorted(
        validator.iter_errors(manifest),
        key=lambda error: (list(map(str, error.absolute_path)), error.message),
    )
    if errors:
        details = "\n".join(
            f"{'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}"
            for error in errors
        )
        raise ManifestValidationError(details)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a MineMat AI dataset manifest against its JSON Schema."
    )
    parser.add_argument("manifest", type=Path, help="Path to the JSON manifest")
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help=f"Schema path (default: {DEFAULT_SCHEMA_PATH})",
    )
    arguments = parser.parse_args()

    try:
        manifest = json.loads(arguments.manifest.read_text(encoding="utf-8"))
        validate_manifest(manifest, arguments.schema)
    except (OSError, json.JSONDecodeError, ManifestValidationError) as error:
        parser.error(str(error))

    print(f"Manifest is valid: {arguments.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
