import argparse
import re
import sys
from pathlib import Path

# Lets the script find the `app` package when run from the repository root.
_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.dataset_manifest import (  # noqa: E402
    ManifestLoadError,
    RestrictedPathError,
    audit_manifests,
    load_manifest,
)
from app.dataset_manifest.audit import NOT_CHECKED  # noqa: E402

EXIT_FAILURE = 2
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit one or more dataset manifests for data leakage. Every manifest is fully "
            "validated first (permissions, the UMaT/MN273 prohibition, evaluation isolation, "
            "split consistency, sample identity, licence evidence and, with --dataset-dir, "
            "directory integrity), so an invalid manifest is an audit error. Across manifests "
            "it checks identical image content in several splits or datasets, derivations "
            "between the manifests, and capture sessions that straddle splits. Pass every "
            "manifest that should be kept apart, and the manifest of every dataset that records "
            "derive from, in one run. Exit 0 if no errors, 2 otherwise."
        )
    )
    parser.add_argument("manifests", nargs="+", type=Path, help="Manifest JSON files, split fields filled in")
    parser.add_argument(
        "--dataset-dir",
        dest="dataset_dirs",
        action="append",
        default=[],
        metavar="[DATASET_ID=]DIRECTORY",
        help=(
            "The dataset directory for a manifest, so its files, checksums and licence snapshot "
            "are verified and unlisted files, links and restricted folders are reported. Repeat "
            "it with DATASET_ID=DIRECTORY for several manifests. Without it the manifest's files "
            "are not verified, which the result states. Restricted locations are refused"
        ),
    )
    parser.add_argument(
        "--ignore-file",
        dest="ignore_patterns",
        action="append",
        default=[],
        metavar="PATTERN",
        help=(
            "Leave non-image files matching this pattern out of the unlisted-file check "
            "(repeatable). A pattern can never hide an image or a video"
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings (for example a capture session spanning splits) as failures",
    )
    return parser


def _describe_identity(basis: str | None, single_image: bool) -> str:
    if basis is None:
        return "NOT VERIFIED (schema 1.0 does not record how the sample IDs were established)"
    if basis == "assumed_each_image_distinct":
        return "ASSUMED, NOT VERIFIED (each image is treated as its own physical sample)"
    if single_image:
        return (
            f"basis recorded as {basis}, but every sample has exactly one image, so grouping "
            "protects nothing: NOT VERIFIED"
        )
    return f"basis recorded as {basis}"


def _map_dataset_dirs(
    parser: argparse.ArgumentParser, values: list[str], labels: dict[str, str]
) -> dict[str, Path]:
    """Match each --dataset-dir to a manifest label; `labels` maps dataset_id -> label."""
    mapping: dict[str, Path] = {}
    for value in values:
        head, separator, tail = value.partition("=")
        if separator and IDENTIFIER.fullmatch(head):
            if head not in labels:
                parser.error(f"--dataset-dir names dataset_id {head!r}, which no manifest has")
            mapping[labels[head]] = Path(tail)
        elif len(set(labels.values())) == 1:
            mapping[next(iter(labels.values()))] = Path(value)
        else:
            parser.error("with several manifests, give each directory as DATASET_ID=DIRECTORY")
    return mapping


def _run(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> int:
    inputs = []
    labels: dict[str, str] = {}
    manifest_paths: dict[str, Path] = {}
    for path in arguments.manifests:
        loaded = load_manifest(path)
        label = str(path)
        inputs.append((label, loaded.document))
        manifest_paths[label] = path
        document = loaded.document
        dataset = document.get("dataset") if isinstance(document, dict) else None
        dataset_id = dataset.get("dataset_id") if isinstance(dataset, dict) else None
        if isinstance(dataset_id, str):
            labels.setdefault(dataset_id, label)
    dataset_dirs = _map_dataset_dirs(parser, arguments.dataset_dirs, labels)
    if any(not pattern for pattern in arguments.ignore_patterns):
        parser.error("--ignore-file needs a non-empty pattern")
    if arguments.ignore_patterns and not dataset_dirs:
        parser.error("--ignore-file needs --dataset-dir")

    result = audit_manifests(
        inputs,
        dataset_dirs=dataset_dirs,
        manifest_paths=manifest_paths,
        ignore_patterns=arguments.ignore_patterns,
    )
    print(f"Leakage audit of {len(inputs)} manifest(s)")
    for summary in result.summaries:
        splits = ", ".join(f"{name} {count}" for name, count in summary.split_counts.items())
        category = summary.category or "not recorded (schema 1.0)"
        print(f"  {summary.label}")
        print(f"    schema {summary.version}; dataset {summary.dataset_id!r}; category {category}")
        print(
            "    sample identity: "
            + _describe_identity(summary.sample_identity_basis, summary.single_image_samples)
        )
        print(
            "    files: "
            + (
                "verified against the dataset directory"
                if summary.files_verified
                else "NOT verified (no usable --dataset-dir)"
            )
        )
        print(
            f"    {summary.records} record(s); {summary.samples} physical sample(s); "
            f"{summary.sessions} capture session(s); {summary.records_without_session} "
            "record(s) without a session"
        )
        print(f"    records per split: {splits}")

    report = result.report
    print(f"Findings: {len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    if report.issues:
        print(report.format())
    print("Declared checksums are trusted unless a dataset directory is supplied for the manifest.")
    print("Not checked: " + "; ".join(NOT_CHECKED) + ".")
    print(
        "This audit reads what manifests declare; it does not judge whether any licence "
        "permits a use."
    )

    if result.unverified_identity:
        print(
            "LIMITATION: the physical sample IDs are NOT verified for: "
            + "; ".join(result.unverified_identity)
            + ". Images of one physical sample can sit in different splits without this audit "
            "noticing, so sample-level leakage cannot be ruled out for them."
        )
    if result.files_not_verified:
        print(
            "LIMITATION: files and licence snapshots were NOT verified against their checksums "
            "for: "
            + "; ".join(result.files_not_verified)
            + ". Pass --dataset-dir to verify them."
        )

    passed = result.passed(strict=arguments.strict)
    verdict = "FAIL"
    if passed:
        verdict = "PASS WITH LIMITATIONS" if result.limitations else "PASS"
    suffix = " (strict: warnings count as failures)" if arguments.strict else ""
    print(f"RESULT: {verdict}{suffix}")
    return 0 if passed else EXIT_FAILURE


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        return _run(parser, arguments)
    except (ManifestLoadError, RestrictedPathError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return EXIT_FAILURE
    except Exception as error:  # noqa: BLE001 - report, with the documented exit code, not a traceback
        print(f"ERROR: unexpected failure ({type(error).__name__}): {error}", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
