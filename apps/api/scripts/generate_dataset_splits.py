import argparse
import os
import sys
import uuid
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
    describe_sources,
    dumps_canonical,
    ensure_not_restricted,
    generate_splits,
    load_manifest,
    load_source_views,
    parse_source_dataset_dirs,
)
from app.dataset_manifest.console import say  # noqa: E402
from app.dataset_manifest.sources import dataset_id_of  # noqa: E402

EXIT_FAILURE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate reproducible train/validation/test splits for a schema 1.1 manifest. "
            "Records are grouped by physical sample, so no sample crosses a split. "
            "Independent evaluation data is refused. Exit 0 on success, 2 on any failure. "
            "Output is written only after everything has been computed, and either all of it "
            "is written or none is."
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
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help=(
            "Where to write the split file. Never inside the dataset directory (so it cannot "
            "replace an image or the licence snapshot), never a source manifest"
        ),
    )
    parser.add_argument(
        "--output-manifest",
        type=Path,
        default=None,
        help=(
            "Also write a copy of the manifest with the splits filled in (never the input file, "
            "a source manifest, or a path inside the dataset directory)"
        ),
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
            "the manifest (repeatable). A pattern never hides a file recognised as an image, a "
            "video or an archive. See validate_dataset_manifest.py --help"
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
            "the split is refused. Two sources with one dataset_id are refused. Give its directory "
            "with --source-dataset-dir"
        ),
    )
    parser.add_argument(
        "--source-dataset-dir",
        dest="source_dataset_dirs",
        action="append",
        default=[],
        metavar="DATASET_ID=DIRECTORY",
        help=(
            "The dataset directory of a source given with --source-manifest (repeatable, always "
            "with the source's dataset_id). The source's record files and licence snapshot are "
            "checked there, and the split is refused unless every source, through the whole chain "
            "of derivations, verifies. The directory is never guessed, and no output is ever "
            "written inside it"
        ),
    )
    parser.add_argument(
        "--require-commercial-training",
        action="store_true",
        help="Require commercial_training to be 'permitted'",
    )
    return parser


def _resolved(path: Path) -> Path:
    """`path` made absolute with links followed (it need not exist)."""
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return Path(os.path.abspath(path))


def _inside(path: Path, directory: Path) -> bool:
    """True if `path` is `directory` or lies under it, ignoring letter case where the OS does."""
    inner, outer = os.path.normcase(str(path)), os.path.normcase(str(directory))
    try:
        return os.path.commonpath([inner, outer]) == outer
    except ValueError:  # on different drives
        return False


def _refuse_dangerous_outputs(
    arguments: argparse.Namespace, source_dirs: dict[str, Path] | None = None
) -> str | None:
    """A message if an output path is somewhere this tool must never write, else None.

    The split generator never writes into the dataset directory, or into the directory of
    any source dataset, so it cannot replace an image, a licence snapshot or any other
    source file with its output, with or without `--overwrite`. It also never replaces a
    source manifest. Links are followed first, so a folder outside a dataset directory that
    is really a junction into it is refused too. Source directories have already been
    checked against the restricted locations before they are resolved here.
    """
    options = [("--output", arguments.output)]
    if arguments.output_manifest is not None:
        options.append(("--output-manifest", arguments.output_manifest))
    roots = [("the dataset directory", arguments.dataset_dir, _resolved(arguments.dataset_dir))]
    for dataset_id, directory in sorted((source_dirs or {}).items()):
        roots.append((f"the directory of source {dataset_id!r}", directory, _resolved(directory)))
    sources = {_resolved(path): path for path in arguments.source_manifests}
    for option, path in options:
        target = _resolved(path)
        for what, directory, root in roots:
            if _inside(target, root):
                return (
                    f"{option} '{path}' is inside {what} '{directory}'. The split generator never "
                    "writes into a dataset directory, so it cannot overwrite an image, a licence "
                    "snapshot or any other source file. Choose a folder outside it."
                )
        if target in sources:
            return (
                f"{option} '{path}' is a source manifest, which is never overwritten. Choose "
                "another path."
            )
    return None


def _write_outputs(items: list[tuple[Path, str]], overwrite: bool) -> None:
    """Write every (path, text) or none of them.

    Without `overwrite`, each file is created exclusively (so a file that appeared after the
    existence check is never replaced) and, if a later file cannot be written, the files this
    run created are removed again. With `overwrite`, each file is first written completely
    beside its target and only then moved into place, so a failed write leaves the earlier
    version intact.
    """
    if not overwrite:
        created: list[Path] = []
        try:
            for path, text in items:
                with path.open("x", encoding="utf-8", newline="\n") as handle:
                    created.append(path)
                    handle.write(text)
        except OSError:
            for path in created:
                try:
                    path.unlink()
                except OSError:
                    pass
            raise
        return

    staged: list[tuple[Path, Path]] = []
    try:
        for path, text in items:
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                staged.append((temporary, path))
                handle.write(text)
        for temporary, path in staged:
            os.replace(temporary, path)
    except OSError:
        for temporary, _path in staged:
            try:
                temporary.unlink()
            except OSError:
                pass
        raise


def _run(arguments: argparse.Namespace) -> int:
    outputs = [arguments.output]
    if arguments.output_manifest is not None:
        outputs.append(arguments.output_manifest)
    for path in outputs:
        ensure_not_restricted(path, "Output file")
    source_dirs = parse_source_dataset_dirs(arguments.source_dataset_dirs)
    for dataset_id, directory in sorted(source_dirs.items()):
        ensure_not_restricted(directory, f"Source dataset directory for {dataset_id!r}")
    refusal = _refuse_dangerous_outputs(arguments, source_dirs)
    if refusal is not None:
        say(f"ERROR: {refusal}", file=sys.stderr)
        return EXIT_FAILURE

    loaded = load_manifest(arguments.manifest)
    sources = load_source_views(
        arguments.source_manifests, own_dataset_id=dataset_id_of(loaded.document), dataset_dirs=source_dirs
    )

    resolved = [path.resolve() for path in outputs]
    if arguments.manifest.resolve() in resolved:
        say("ERROR: an output path is the input manifest, which is never overwritten.", file=sys.stderr)
        return EXIT_FAILURE
    if len(set(resolved)) != len(resolved):
        say("ERROR: --output and --output-manifest are the same path.", file=sys.stderr)
        return EXIT_FAILURE
    for path in outputs:
        if path.exists() and not arguments.overwrite:
            say(f"ERROR: '{path}' already exists; pass --overwrite to replace it.", file=sys.stderr)
            return EXIT_FAILURE
        if not path.parent.is_dir():
            say(f"ERROR: the folder for '{path}' does not exist.", file=sys.stderr)
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
        say(f"ERROR: {error}", file=sys.stderr)
        if error.report is not None:
            say(error.report.format(), file=sys.stderr)
        return EXIT_FAILURE

    # Everything that can fail while preparing the output is done before the first file is written.
    items = [(arguments.output, dumps_canonical(result.to_document()))]
    if arguments.output_manifest is not None:
        updated = apply_splits(loaded.document, result.assignments)
        items.append((arguments.output_manifest, dumps_canonical(updated)))
    identity = (
        "sample identity ASSUMED, NOT VERIFIED"
        if result.sample_identity_assumed
        else f"sample identity basis: {result.sample_identity_basis}"
    )
    lines = [f"Splits written: {arguments.output}"]
    if arguments.output_manifest is not None:
        lines.append(f"Manifest with splits written: {arguments.output_manifest}")
    lines.append(f"  seed {result.seed}; ratios {result.ratios}; manifest SHA-256 {loaded.sha256}")
    lines.append(
        f"  {result.summary['records']} record(s) in {result.summary['physical_samples']} "
        f"physical sample(s) ({identity}), kept together as {result.summary['groups']} group(s)"
    )
    for name, entry in result.summary["per_split"].items():
        lines.append(f"  {name}: {entry['records']} record(s), {entry['physical_samples']} physical sample(s)")
    if result.ignored_files:
        lines.append(
            f"  {len(result.ignored_files)} file(s) were left out of the unlisted-file check by "
            f"{', '.join(result.ignored_file_patterns)}; they are listed in the split file"
        )
    lines.extend(f"  {line}" for line in describe_sources(sources, result.source_assessments))
    lines.extend(f"WARNING: {warning}" for warning in result.warnings)
    if result.warnings:
        lines.append(f"  ({len(result.warnings)} warning(s) are also recorded in the split file)")
    lines.append("Next: run audit_dataset_leakage.py on the manifest with the splits filled in.")

    try:
        _write_outputs(items, arguments.overwrite)
    except OSError as error:
        say(f"ERROR: could not write output: {error}", file=sys.stderr)
        return EXIT_FAILURE

    # The outputs are complete. A terminal that cannot take the summary (a closed pipe, for
    # example) must not turn a finished run into a reported failure.
    try:
        for line in lines:
            say(line)
    except (OSError, ValueError):
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        return _run(arguments)
    except (ManifestLoadError, RestrictedPathError) as error:
        say(f"ERROR: {error}", file=sys.stderr)
        return EXIT_FAILURE
    except Exception as error:  # noqa: BLE001 - report, with the documented exit code, not a traceback
        say(f"ERROR: unexpected failure ({type(error).__name__}): {error}", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
