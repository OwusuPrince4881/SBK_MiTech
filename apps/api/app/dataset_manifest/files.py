"""Read-only inspection of a dataset directory.

Two jobs: find the exact on-disk spelling of a path (so a manifest path that
differs only in letter case is caught on every operating system), and find
files that are on disk but not in the manifest.

Nothing here writes, deletes, moves or changes anything. The contents of a file
are never read, except the first 512 bytes of a file the manifest does not list,
to recognise an image, a video or an archive that has been given an unexpected
name. Links (symbolic links and Windows junctions) are never followed, so a link
cannot make the scan wander outside the dataset directory or loop. Sub-folders in
a restricted location are not entered.

What is recognised, and what is not. A file is treated as an image or video if
its extension is in `IMAGE_EXTENSIONS` / `VIDEO_EXTENSIONS` or its first bytes
match a signature in `looks_like_media`; as an archive or other container (a file
that can hold a dataset this tool cannot see into) if its extension is in
`CONTAINER_EXTENSIONS` or its first bytes match `looks_like_container`. These
lists are practical, not complete: a format with no listed extension and no
recognised signature (TGA files with an unlisted extension, for example, or any
format this module has never been told about) is NOT recognised and is reported
only as an "other" file.
"""

import fnmatch
import os
import unicodedata
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.dataset_manifest.restricted import lexical_restriction

# Extensions that a data loader could pick up as an image or video. Matched without regard to case.
IMAGE_EXTENSIONS = frozenset(
    {
        ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff",
        ".heic", ".heif", ".avif", ".jp2", ".j2k", ".jpf", ".jpx", ".jxl", ".dng", ".cr2", ".cr3",
        ".crw", ".nef", ".arw", ".orf", ".rw2", ".pef", ".srw", ".raf", ".raw", ".svg", ".svgz",
        ".ico", ".cur", ".tga", ".psd", ".ppm", ".pgm", ".pbm", ".pnm", ".pam", ".exr", ".hdr",
        ".dds", ".icns", ".qoi",
    }
)
VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".mpg", ".mpeg", ".3gp", ".3g2",
        ".flv", ".f4v", ".ogv", ".asf", ".vob", ".m2ts", ".mts", ".divx",
    }
)
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
# Archives and other containers: files that can hold images, videos or a whole dataset that
# this tool cannot see into. An unlisted one is an error, whatever its name looks like.
CONTAINER_EXTENSIONS = frozenset(
    {
        ".zip", ".tar", ".tgz", ".gz", ".bz2", ".xz", ".7z", ".rar", ".zst", ".lz4", ".cab", ".iso",
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp",
        ".npy", ".npz", ".h5", ".hdf5", ".hdf", ".pt", ".pth", ".pkl", ".pickle", ".parquet",
        ".feather", ".arrow", ".tfrecord", ".mat", ".db", ".sqlite", ".sqlite3", ".mdb",
    }
)
# Operating-system and version-control housekeeping files. They are skipped only when their
# content is neither a recognised media file nor a recognised archive; they are listed in the result.
IGNORED_NAMES = frozenset({".gitkeep", ".ds_store", "thumbs.db", "desktop.ini"})

HEADER_BYTES = 512  # Enough for every signature below, including the tar marker at offset 257.

_MEDIA_PREFIXES = (
    b"\xff\xd8\xff",  # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"GIF87a",
    b"GIF89a",
    b"II*\x00",  # TIFF, little endian (also DNG, CR2, NEF, ARW)
    b"MM\x00*",  # TIFF, big endian
    b"IIRO", b"IIRS", b"IIU\x00",  # Olympus and Panasonic raw
    b"\x1a\x45\xdf\xa3",  # Matroska / WebM
    b"\xff\x4f\xff\x51",  # JPEG 2000 codestream
    b"\x00\x00\x00\x0cjP  \r\n\x87\n",  # JPEG 2000 file
    b"\xff\x0a",  # JPEG XL codestream
    b"\x00\x00\x00\x0cJXL \r\n\x87\n",  # JPEG XL file
    b"8BPS\x00\x01", b"8BPS\x00\x02",  # Photoshop
    b"v/1\x01",  # OpenEXR
    b"DDS ", b"#?RADIANCE", b"#?RGBE", b"icns", b"qoif", b"FUJIFILMCCD-RAW",
    b"\x30\x26\xb2\x75\x8e\x66\xcf\x11",  # ASF / WMV
    b"FLV\x01",
    b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3",  # MPEG program stream / video
)
_CONTAINER_PREFIXES = (
    b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08",  # ZIP (and DOCX, XLSX, NPZ, PyTorch, ...)
    b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07",
    b"\x1f\x8b",  # gzip
    b"\xfd7zXZ\x00",
    b"\x28\xb5\x2f\xfd",  # zstd
    b"\x04\x22\x4d\x18",  # lz4
    b"%PDF-",
    b"\x89HDF\r\n\x1a\n",  # HDF5
    b"\x93NUMPY",
    b"SQLite format 3\x00",
    b"PAR1",  # Parquet
    b"MSCF\x00\x00\x00\x00",  # CAB
)

MEDIA = "media"
CONTAINER = "container"
OTHER = "other"
HOUSEKEEPING = "housekeeping"


def fold(value: str) -> str:
    """Letter case and Unicode form removed, so equal-looking names compare equal."""
    return unicodedata.normalize("NFKC", value).casefold()


def looks_like_media(header: bytes) -> bool:
    """True if the first bytes of a file match a recognised image or video signature.

    The list is practical, not complete. A format that is not listed here is not recognised.
    """
    if header.startswith(_MEDIA_PREFIXES):
        return True
    if header[:4] == b"RIFF" and header[8:12] in (b"WEBP", b"AVI "):
        return True
    if header[4:8] in (b"ftyp", b"moov", b"mdat", b"wide"):  # MP4, MOV, HEIC, AVIF, 3GP
        return True
    if header[:2] == b"BM" and len(header) >= 14 and header[6:10] == b"\x00\x00\x00\x00":  # BMP
        return True
    if header[:4] in (b"\x00\x00\x01\x00", b"\x00\x00\x02\x00") and len(header) >= 6:
        if header[4] > 0 and header[5] == 0:  # ICO / CUR holding 1 to 255 images
            return True
    if header[:1] == b"P" and header[1:2] in (b"1", b"2", b"3", b"4", b"5", b"6", b"7"):
        if header[2:3] in (b" ", b"\t", b"\r", b"\n"):
            rest = header[3:].lstrip()  # PBM / PGM / PPM / PAM: a number, a comment or a PAM tag follows
            if rest[:1].isdigit() or rest[:1] == b"#" or rest.startswith(b"WIDTH"):
                return True
    if len(header) >= 377 and header[0] == 0x47 and header[188] == 0x47 and header[376] == 0x47:
        return True  # MPEG transport stream
    return False


def looks_like_container(header: bytes) -> bool:
    """True if the first bytes match a recognised archive or other container format."""
    if header.startswith(_CONTAINER_PREFIXES):
        return True
    if len(header) >= 4 and header[:3] == b"BZh" and header[3:4] in b"123456789":  # bzip2
        return True
    return header[257:262] == b"ustar"  # tar


def classify_file(name: str, read_header: Callable[[], bytes]) -> str:
    """MEDIA, CONTAINER, HOUSEKEEPING or OTHER for an unlisted file.

    A housekeeping name (`.gitkeep`, `Thumbs.db`, ...) is judged by content alone, so a
    JPEG called `.gitkeep` is media, not housekeeping. Any other file is judged by its
    extension first and then, if that settles nothing, by its first bytes.
    """
    folded = name.casefold()
    if folded in IGNORED_NAMES:
        header = read_header()
        if looks_like_media(header):
            return MEDIA
        return CONTAINER if looks_like_container(header) else HOUSEKEEPING
    suffix = Path(folded).suffix
    if suffix in MEDIA_EXTENSIONS:
        return MEDIA
    if suffix in CONTAINER_EXTENSIONS:
        return CONTAINER
    header = read_header()
    if looks_like_media(header):
        return MEDIA
    return CONTAINER if looks_like_container(header) else OTHER


def _read_header(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read(HEADER_BYTES)
    except OSError:
        return b""


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
    containers: list[str] = field(default_factory=list)  # archives and other containers it does not list
    others: list[str] = field(default_factory=list)  # other files the manifest does not list
    ignored_by_pattern: list[str] = field(default_factory=list)  # other files left out by a pattern
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

    A file recognised as an image or video, or as an archive or container (by
    extension or by its first bytes; see the module notes for what that covers and
    what it does not), is always reported, whatever its name and whatever the ignore
    patterns say. A pattern can only leave out a file that was not recognised as
    either, and every file it leaves out is returned in `ignored_by_pattern`. A
    pattern is matched against the whole relative path, case-sensitively, and `*`
    also matches `/`. Housekeeping files (`.gitkeep`, `Thumbs.db`, ...) are skipped,
    and listed in `housekeeping`, only if their content is not recognised media or an
    archive.
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
        kind = classify_file(absolute.name, lambda path=absolute: _read_header(path))
        if kind == HOUSEKEEPING:
            result.housekeeping.append(relative)
        elif kind == MEDIA:
            result.images.append(relative)
        elif kind == CONTAINER:
            result.containers.append(relative)
        elif any(fnmatch.fnmatchcase(relative, pattern) for pattern in ignore_patterns):
            result.ignored_by_pattern.append(relative)
        else:
            result.others.append(relative)
    for names in (result.images, result.containers, result.others, result.ignored_by_pattern, result.housekeeping):
        names.sort()
    return result
