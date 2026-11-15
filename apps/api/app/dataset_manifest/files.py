"""Read-only inspection of a dataset directory.

Two jobs: find the exact on-disk spelling of a path (so a manifest path that
differs only in letter case is caught on every operating system), and find
files that are on disk but not in the manifest.

Nothing here writes, deletes, moves or changes anything. The contents of a file
are never read, except the first 16 bytes of a file the manifest does not list,
to recognise an image or video that has been given an unexpected name. Links
(symbolic links and Windows junctions) are never followed, so a link cannot make
the scan wander outside the dataset directory or loop. Sub-folders in a
restricted location are not entered.
"""

import fnmatch
import os
import unicodedata
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.dataset_manifest.restricted import lexical_restriction

# Extensions that a data loader could pick up as an image or video. Matched without regard to case.
IMAGE_EXTENSIONS = frozenset(
    {
        ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff",
        ".heic", ".heif", ".avif", ".jp2", ".dng", ".cr2", ".nef", ".arw", ".raw",
    }
)
VIDEO_EXTENSIONS = frozenset(
    {".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".mpg", ".mpeg"}
)
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
# Operating-system and version-control housekeeping files. They are skipped, but listed in the result.
IGNORED_NAMES = frozenset({".gitkeep", ".ds_store", "thumbs.db", "desktop.ini"})

_SIGNATURES = (
    b"\xff\xd8\xff",  # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"GIF87a",
    b"GIF89a",
    b"II*\x00",  # TIFF, little endian
    b"MM\x00*",  # TIFF, big endian
    b"\x1a\x45\xdf\xa3",  # Matroska / WebM
)
HEADER_BYTES = 16


def fold(value: str) -> str:
    """Letter case and Unicode form removed, so equal-looking names compare equal."""
    return unicodedata.normalize("NFKC", value).casefold()


def looks_like_media(header: bytes) -> bool:
    """True if the first bytes of a file match a common image or video format."""
    if header.startswith(_SIGNATURES):
        return True
    if header[:4] == b"RIFF" and header[8:12] in (b"WEBP", b"AVI "):
        return True
    return header[4:8] == b"ftyp"  # MP4, MOV, HEIC, AVIF


def _has_media_content(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return looks_like_media(handle.read(HEADER_BYTES))
    except OSError:
        return False


@dataclass
class DirectoryScan:
    root: Path
    files: dict[str, Path] = field(default_factory=dict)  # exact relative path -> file
    links: list[str] = field(default_factory=list)  # links that were not followed
    unreadable: list[str] = field(default_factory=list)  # directories that could not be listed
    restricted: list[str] = field(default_factory=list)  # restricted sub-folders, not entered
    _folded: dict[str, list[str]] | None = field(default=None, repr=False)

    def case_variants(self, relative_path: str) -> list[str]:
        """Files on disk whose name equals `relative_path` apart from case or Unicode form."""
        if self._folded is None:
            folded: dict[str, list[str]] = {}
            for path in self.files:
                folded.setdefault(fold(path), []).append(path)
            self._folded = {key: sorted(paths) for key, paths in folded.items()}
        return [p for p in self._folded.get(fold(relative_path), []) if p != relative_path]

    def link_on_path(self, relative_path: str) -> str | None:
        """The link that `relative_path` runs through, if any."""
        for link in self.links:
            if relative_path == link or relative_path.startswith(link + "/"):
                return link
        return None


def _looks_like_junction(entry: "os.DirEntry[str]") -> bool:
    # Fallback for Python before 3.12, which has no DirEntry.is_junction(): a
    # junction is a directory whose real path differs from where it sits.
    if os.name != "nt" or not entry.is_dir(follow_symlinks=False):
        return False
    parent = os.path.realpath(os.path.dirname(entry.path))
    expected = os.path.join(parent, entry.name)
    return os.path.normcase(os.path.realpath(entry.path)) != os.path.normcase(expected)


def _is_link(entry: "os.DirEntry[str]") -> bool:
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)
    return bool(is_junction()) if is_junction is not None else _looks_like_junction(entry)


def scan_directory(root: Path) -> DirectoryScan:
    """List every regular file under `root` with its exact on-disk relative path (using `/`)."""
    scan = DirectoryScan(root)
    pending: list[tuple[Path, str]] = [(root, "")]
    while pending:
        directory, prefix = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError:
            scan.unreadable.append(prefix.rstrip("/") or ".")
            continue
        for entry in entries:
            relative = prefix + entry.name
            try:
                if _is_link(entry):
                    scan.links.append(relative)
                elif entry.is_dir(follow_symlinks=False):
                    if lexical_restriction(entry.path) is not None:
                        scan.restricted.append(relative)  # never entered
                    else:
                        pending.append((Path(entry.path), relative + "/"))
                elif entry.is_file(follow_symlinks=False):
                    scan.files[relative] = Path(entry.path)
            except OSError:
                scan.unreadable.append(relative)
    scan.links.sort()
    scan.unreadable.sort()
    scan.restricted.sort()
    return scan


def _is_same_file(path: Path, other: Path) -> bool:
    try:
        return os.path.samefile(path, other)
    except OSError:
        return False


@dataclass
class Unmanifested:
    images: list[str] = field(default_factory=list)  # image or video files the manifest does not list
    others: list[str] = field(default_factory=list)  # other files the manifest does not list
    ignored_by_pattern: list[str] = field(default_factory=list)  # non-media files left out by a pattern
    housekeeping: list[str] = field(default_factory=list)  # housekeeping files skipped


def find_unmanifested(
    scan: DirectoryScan,
    declared: Collection[str],
    *,
    case_counterparts: Collection[str] = (),
    manifest_path: Path | None = None,
    ignore_patterns: Sequence[str] = (),
) -> Unmanifested:
    """Files on disk that the manifest does not list.

    Not reported at all: files the manifest lists (record files and the licence
    snapshot), disk files that are the wrongly-cased spelling of a listed file
    (reported as a case mismatch instead), and the manifest file itself.

    An image or video, recognised by its extension or by its first bytes, is
    always reported, whatever its name and whatever the ignore patterns say. A
    pattern can only leave out a file that is not an image or video, and every
    file it leaves out is returned in `ignored_by_pattern`. A pattern is
    matched against the whole relative path, case-sensitively, and `*` also
    matches `/`. Housekeeping files (`.gitkeep`, `Thumbs.db`, ...) are skipped
    and listed in `housekeeping`.
    """
    declared_paths = set(declared)
    counterparts = set(case_counterparts)
    manifest_abs: Path | None = None
    if manifest_path is not None:
        try:
            manifest_abs = manifest_path.resolve()
        except OSError:
            manifest_abs = None

    result = Unmanifested()
    for relative, absolute in scan.files.items():
        if relative in declared_paths or relative in counterparts:
            continue
        if (
            manifest_abs is not None
            and absolute.name.casefold() == manifest_abs.name.casefold()
            and _is_same_file(absolute, manifest_abs)
        ):
            continue
        if absolute.name.casefold() in IGNORED_NAMES:
            result.housekeeping.append(relative)
        elif absolute.suffix.casefold() in MEDIA_EXTENSIONS or _has_media_content(absolute):
            result.images.append(relative)
        elif any(fnmatch.fnmatchcase(relative, pattern) for pattern in ignore_patterns):
            result.ignored_by_pattern.append(relative)
        else:
            result.others.append(relative)
    for names in (result.images, result.others, result.ignored_by_pattern, result.housekeeping):
        names.sort()
    return result
