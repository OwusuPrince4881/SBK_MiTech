import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO

# Lets `python apps\api\scripts\validate_dataset_manifest.py` find the `app`
# package when run from the repository root. It does nothing under pytest.
_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from jsonschema import Draft202012Validator  # noqa: E402
from jsonschema.exceptions import SchemaError  # noqa: E402

from app.dataset_manifest import (  # noqa: E402
    ManifestLoadError,
    RestrictedPathError,
    ValidationResult,
    load_manifest,
    source_view,
    validate_document,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "docs" / "dataset_manifest.schema.json"
USE_CHOICES = ("training", "validation", "testing", "independent_evaluation")
EXIT_FAILURE = 2  # Any failure exits 2, with a message and no traceback.
MAX_LISTED = 1000  # The most file names printed in full.


class ManifestValidationError(ValueError):
    pass


def validate_manifest(
    manifest: Any,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
) -> None:
    """Schema-1.0 structural validation only. Kept as is for existing callers.

    `validate_document` in `app.dataset_manifest` checks both schema versions
    and adds the cross-record, permission and file checks.
    """
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a MineMat AI dataset manifest (schema 1.0 or 1.1): structure, unique "
            "IDs, one split per physical sample, evaluation isolation, the UMaT/MN273 "
            "prohibition, licence and permission consistency, derivations, and optionally the "
            "files and their SHA-256 checksums. Exit 0 if valid, 2 on any failure. Valid means "
            "consistent: this tool does not judge whether a licence permits a use."
        )
    )
    parser.add_argument("manifest", type=Path, help="Path to the JSON manifest")
    parser.add_argument(
        "--schema",
        type=Path,
        default=None,
        help=(
            "An extra schema the manifest must also satisfy. The built-in schema always applies, "
            "so this can only add to it"
        ),
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=None,
        help=(
            "Directory that relative_path values are relative to. Every file must exist inside it, "
            "match its SHA-256 checksum, and nothing unlisted may be there. Needed for --use. "
            "Restricted locations (data/evaluation, data/umat_practicals, data/uploads) are refused"
        ),
    )
    parser.add_argument(
        "--ignore-file",
        dest="ignore_patterns",
        action="append",
        default=[],
        metavar="PATTERN",
        help=(
            "With --dataset-dir: leave non-image files matching this pattern out of the check for "
            "files that are not in the manifest, for example 'README.md' or 'notes/*' (repeatable). "
            "A pattern can never hide an image or a video, and every file it leaves out is listed. "
            "The pattern matches the whole relative path, case-sensitively; '*' also matches '/'. "
            "The manifest file itself and the licence snapshot are left out automatically"
        ),
    )
    parser.add_argument(
        "--source-manifest",
        dest="source_manifests",
        action="append",
        type=Path,
        default=[],
        metavar="PATH",
        help=(
            "The manifest of another dataset that records in this one derive from (repeatable). "
            "A derivation from a dataset whose manifest is not supplied cannot be established "
            "and is an error"
        ),
    )
    parser.add_argument(
        "--use",
        dest="uses",
        action="append",
        choices=USE_CHOICES,
        default=[],
        help=(
            "A use you propose (repeatable). The manifest must record the permission, "
            "--dataset-dir must be given, and the files and licence snapshot must verify"
        ),
    )
    parser.add_argument(
        "--require-commercial-training",
        action="store_true",
        help=(
            "Also require commercial_training to be 'permitted'. This is separate from "
            "--use training and needs the same verification"
        ),
    )
    return parser


def _describe_permissions(document: dict[str, Any]) -> str:
    permissions = document["dataset"]["permissions"]
    uses = ", ".join(permissions["permitted_uses"]) or "none"
    return (
        f"status {permissions['status']}; permitted uses: {uses}; "
        f"commercial training: {permissions['commercial_training']} (as the manifest declares)"
    )


def _describe_identity(basis: str | None) -> str:
    if basis is None:
        return "(sample identity NOT VERIFIED: schema 1.0 does not record how it was established)"
    if basis == "assumed_each_image_distinct":
        return "(sample identity ASSUMED, NOT VERIFIED: each image is treated as its own sample)"
    return f"(sample identity basis: {basis})"


def _print_listing(title: str, names: list[str], stream: TextIO) -> None:
    """Print every name (escaped to ASCII, as they come from disk), up to a generous cap."""
    if not names:
        return
    print(title, file=stream)
    for name in names[:MAX_LISTED]:
        print(f"  {ascii(name)}", file=stream)
    if len(names) > MAX_LISTED:
        print(f"  ... and {len(names) - MAX_LISTED} more", file=stream)


def _print_file_findings(result: ValidationResult, stream: TextIO) -> None:
    _print_listing(
        "Image or video files in the dataset directory that are not in the manifest (left untouched):",
        result.unmanifested_images,
        stream,
    )
    _print_listing(
        "Other files in the dataset directory that are not in the manifest (left untouched):",
        result.unmanifested_other,
        stream,
    )
    _print_listing(
        f"Links in the dataset directory (refused, not followed, left untouched):",
        result.links_not_followed,
        stream,
    )
    if result.ignored_by_pattern:
        _print_listing(
            f"{len(result.ignored_by_pattern)} file(s) left out of the unlisted-file check by an "
            "ignore pattern (images and videos are never left out):",
            result.ignored_by_pattern,
            stream,
        )
    if result.ignored_housekeeping:
        _print_listing(
            f"{len(result.ignored_housekeeping)} housekeeping file(s) skipped:",
            result.ignored_housekeeping,
            stream,
        )


def _load_sources(paths: list[Path]) -> dict[str, Any]:
    sources: dict[str, Any] = {}
    for path in paths:
        loaded = load_manifest(path)
        try:
            view = source_view(loaded.document)
        except ValueError as error:
            raise ManifestLoadError(f"'{path}' cannot be used as a source manifest: {error}") from error
        sources.setdefault(view.dataset_id, view)
    return sources


def _run(arguments: argparse.Namespace) -> int:
    loaded = load_manifest(arguments.manifest)
    sources = _load_sources(arguments.source_manifests)

    result = validate_document(
        loaded.document,
        dataset_dir=arguments.dataset_dir,
        uses=arguments.uses,
        require_commercial_training=arguments.require_commercial_training,
        schema_path=arguments.schema,
        manifest_path=arguments.manifest,
        ignore_patterns=arguments.ignore_patterns,
        provenance_sources=sources,
    )
    if not result.ok:
        print(result.report.format(), file=sys.stderr)
        _print_file_findings(result, sys.stderr)
        print(
            f"Manifest is NOT valid: {arguments.manifest} "
            f"({len(result.report.errors)} error(s), {len(result.report.warnings)} warning(s))",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    print(f"Manifest is valid: {arguments.manifest}")
    print(
        f"  schema {result.version}; {result.record_count} record(s); "
        f"{result.sample_count} physical sample(s) {_describe_identity(result.sample_identity_basis)}"
    )
    if result.single_image_samples:
        print(
            "  note: every physical sample has exactly one image, so grouping by sample protects "
            "nothing; treat the sample identity as unverified"
        )
    if result.version == "1.1":
        print(f"  recorded permissions: {_describe_permissions(loaded.document)}")
    if result.files_verified:
        if result.version == "1.1":
            print("  files: every file exists, with the recorded spelling, and was checked against the manifest")
        else:
            print("  files: every listed file exists (schema 1.0 records no checksums, so contents were not verified)")
    else:
        print("  files: NOT checked (pass --dataset-dir to verify files and SHA-256 checksums)")
    if result.ignored_by_pattern:
        print(
            f"  {len(result.ignored_by_pattern)} file(s) were left out of the unlisted-file check by: "
            f"{', '.join(arguments.ignore_patterns)}"
        )
    if result.cleared_uses:
        print(f"  recorded permission accepted for: {', '.join(result.cleared_uses)}")
    if result.commercial_training_cleared:
        print(
            "  commercial training: the manifest declares it permitted (its basis is free text "
            "and is not verified; this is separate from the training permission)"
        )
    if result.cleared_uses or result.commercial_training_cleared:
        print(
            "  the files and the licence snapshot match their recorded checksums. This is the "
            "manifest's own declaration: this tool does not judge the licence or confirm that "
            "the use is lawful"
        )
    else:
        print(
            "  no use was requested: valid means consistent, and nothing was cleared for any use "
            "(pass --use training together with --dataset-dir to check the recorded permission)"
        )
    for issue in result.report.warnings:
        print(issue.format())
    _print_file_findings(result, sys.stdout)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.ignore_patterns and arguments.dataset_dir is None:
        parser.error("--ignore-file needs --dataset-dir")
    if any(not pattern for pattern in arguments.ignore_patterns):
        parser.error("--ignore-file needs a non-empty pattern")

    try:
        return _run(arguments)
    except (ManifestLoadError, RestrictedPathError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return EXIT_FAILURE
    except Exception as error:  # noqa: BLE001 - report, with the documented exit code, not a traceback
        print(f"ERROR: unexpected failure ({type(error).__name__}): {error}", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
