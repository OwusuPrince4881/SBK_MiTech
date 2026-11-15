import argparse
import sys
from pathlib import Path

# Lets the script find the `app` package when run from the repository root.
_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.dataset_manifest import (  # noqa: E402
    ManifestLoadError,
    RestrictedPathError,
    SplitConfig,
    SplitGenerationError,
    apply_splits,
    dumps_canonical,
    ensure_not_restricted,
    generate_splits,
    load_manifest,
    source_view,
)

EXIT_FAILURE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate reproducible train/validation/test splits for a schema 1.1 manifest. "
            "Records are grouped by physical sample, so no sample crosses a split. "
            "Independent evaluation data is refused. Exit 0 on success, 2 on any failure."
        )
    )
    parser.add_argument("manifest", type=Path, help="Path to the JSON manifest (not modified)")
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        required=True,
        help="Directory that relative_path values are relative to; files and checksums are verified",
    )
    parser.add_argument("--seed", type=int, required=True, help="Integer seed; recorded in the output")
    parser.add_argument("--train", type=float, default=0.70, help="Train ratio (default 0.70)")
    parser.add_argument(
        "--validation", type=float, default=0.15, help="Validation ratio (default 0.15)"
    )
    parser.add_argument("--test", type=float, default=0.15, help="Test ratio (default 0.15)")
    parser.add_argument("--output", type=Path, required=True, help="Where to write the split file")
    parser.add_argument(
        "--output-manifest",
        type=Path,
        default=None,
        help="Also write a copy of the manifest with the splits filled in (never the input file)",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Replace output files that already exist"
    )
    parser.add_argument(
        "--group-capture-sessions",
        action="store_true",
        help="Keep physical samples that share a capture session in the same split",
    )
    parser.add_argument(
        "--allow-resplit",
        action="store_true",
        help="Discard existing train/validation/test assignments and generate them again",
    )
    parser.add_argument(
        "--accept-unverified-sample-identity",
        metavar="REASON",
        default=None,
        help=(
            "Proceed although the physical sample IDs are only ASSUMED (each image treated as its "
            "own sample), which means the splits are not known to keep a physical sample together. "
            "Needs a written reason, which is recorded in the split file and shown as a warning"
        ),
    )
    parser.add_argument(
        "--ignore-file",
        dest="ignore_patterns",
        action="append",
        default=[],
        metavar="PATTERN",
        help=(
            "Leave non-image files matching this pattern out of the check for files that are not in "
            "the manifest (repeatable). A pattern can never hide an image or a video. See "
            "validate_dataset_manifest.py --help"
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
            "The manifest of another dataset that records here derive from (repeatable). A "
            "derivation from a dataset whose manifest is not supplied cannot be established, and "
            "the split is refused"
        ),
    )
    parser.add_argument(
        "--require-commercial-training",
        action="store_true",
        help="Require commercial_training to be 'permitted'",
    )
    return parser


def _write(path: Path, text: str, overwrite: bool) -> None:
    with path.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _run(arguments: argparse.Namespace) -> int:
    outputs = [arguments.output]
    if arguments.output_manifest is not None:
        outputs.append(arguments.output_manifest)
    for path in outputs:
        ensure_not_restricted(path, "Output file")

    loaded = load_manifest(arguments.manifest)
    sources = {}
    for path in arguments.source_manifests:
        source = load_manifest(path)
        try:
            view = source_view(source.document)
        except ValueError as error:
            raise ManifestLoadError(f"'{path}' cannot be used as a source manifest: {error}") from error
        sources.setdefault(view.dataset_id, view)

    resolved = [path.resolve() for path in outputs]
    if arguments.manifest.resolve() in resolved:
        print("ERROR: an output path is the input manifest, which is never overwritten.", file=sys.stderr)
        return EXIT_FAILURE
    if len(set(resolved)) != len(resolved):
        print("ERROR: --output and --output-manifest are the same path.", file=sys.stderr)
        return EXIT_FAILURE
    for path in outputs:
        if path.exists() and not arguments.overwrite:
            print(f"ERROR: '{path}' already exists; pass --overwrite to replace it.", file=sys.stderr)
            return EXIT_FAILURE
        if not path.parent.is_dir():
            print(f"ERROR: the folder for '{path}' does not exist.", file=sys.stderr)
            return EXIT_FAILURE

    config = SplitConfig(
        seed=arguments.seed,
        ratios={
            "train": arguments.train,
            "validation": arguments.validation,
            "test": arguments.test,
        },
        group_capture_sessions=arguments.group_capture_sessions,
        allow_resplit=arguments.allow_resplit,
        unverified_sample_identity_reason=arguments.accept_unverified_sample_identity,
        require_commercial_training=arguments.require_commercial_training,
    )
    try:
        result = generate_splits(
            loaded.document,
            config,
            dataset_dir=arguments.dataset_dir,
            manifest_sha256=loaded.sha256,
            ignore_patterns=arguments.ignore_patterns,
            provenance_sources=sources,
        )
    except SplitGenerationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        if error.report is not None:
            print(error.report.format(), file=sys.stderr)
        return EXIT_FAILURE

    try:
        _write(arguments.output, dumps_canonical(result.to_document()), arguments.overwrite)
        if arguments.output_manifest is not None:
            updated = apply_splits(loaded.document, result.assignments)
            _write(arguments.output_manifest, dumps_canonical(updated), arguments.overwrite)
    except OSError as error:
        print(f"ERROR: could not write output: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"Splits written: {arguments.output}")
    if arguments.output_manifest is not None:
        print(f"Manifest with splits written: {arguments.output_manifest}")
    print(f"  seed {result.seed}; ratios {result.ratios}; manifest SHA-256 {loaded.sha256}")
    identity = (
        "sample identity ASSUMED, NOT VERIFIED"
        if result.sample_identity_assumed
        else f"sample identity basis: {result.sample_identity_basis}"
    )
    print(
        f"  {result.summary['records']} record(s) in {result.summary['physical_samples']} "
        f"physical sample(s) ({identity}), kept together as {result.summary['groups']} group(s)"
    )
    for name, entry in result.summary["per_split"].items():
        print(f"  {name}: {entry['records']} record(s), {entry['physical_samples']} physical sample(s)")
    if result.ignored_files:
        print(
            f"  {len(result.ignored_files)} file(s) were left out of the unlisted-file check by "
            f"{', '.join(result.ignored_file_patterns)}; they are listed in the split file"
        )
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    if result.warnings:
        print(f"  ({len(result.warnings)} warning(s) are also recorded in the split file)")
    print("Next: run audit_dataset_leakage.py on the manifest with the splits filled in.")
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
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
