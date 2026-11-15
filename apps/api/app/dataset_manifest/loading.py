import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.dataset_manifest.restricted import ensure_not_restricted

MAX_MANIFEST_BYTES = 64 * 1024 * 1024  # A real manifest is far smaller; this bounds memory use.
MAX_NESTING = 50  # A manifest is about six levels deep; anything deeper is refused.
MAX_INTEGER_DIGITS = 30  # Larger integers are refused rather than converted.


class ManifestLoadError(ValueError):
    """The file could not be read as a strict JSON document."""


@dataclass(frozen=True)
class LoadedManifest:
    path: Path
    document: Any
    sha256: str


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    # json.loads silently keeps the last of two equal keys, which would let a
    # second "split" or "sha256" entry override the first without any warning.
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestLoadError(f"Duplicate JSON key {key!r}; each key may appear once.")
        result[key] = value
    return result


def _reject_constant(name: str) -> Any:
    raise ManifestLoadError(f"Non-standard JSON constant {name} is not allowed.")


def _parse_integer(text: str) -> int:
    if len(text.lstrip("-")) > MAX_INTEGER_DIGITS:
        raise ManifestLoadError(f"A number with more than {MAX_INTEGER_DIGITS} digits is not allowed.")
    return int(text)


def _parse_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ManifestLoadError("A number too large to represent is not allowed.")
    return value


def _check_nesting(document: Any) -> None:
    """Refuse documents nested deeper than MAX_NESTING (checked without recursion)."""
    stack: list[tuple[Any, int]] = [(document, 1)]
    while stack:
        value, depth = stack.pop()
        if isinstance(value, (dict, list)):
            if depth > MAX_NESTING:
                raise ManifestLoadError(f"Manifest is nested more than {MAX_NESTING} levels deep.")
            children = value.values() if isinstance(value, dict) else value
            stack.extend((child, depth + 1) for child in children)


def parse_manifest_bytes(data: bytes) -> Any:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ManifestLoadError(f"Manifest is not valid UTF-8: {error}") from error
    try:
        document = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
            parse_int=_parse_integer,
            parse_float=_parse_float,
        )
    except ManifestLoadError:
        raise
    except json.JSONDecodeError as error:
        raise ManifestLoadError(f"Manifest is not valid JSON: {error}") from error
    except RecursionError as error:
        raise ManifestLoadError(f"Manifest is nested more than {MAX_NESTING} levels deep.") from error
    except ValueError as error:
        raise ManifestLoadError(f"Manifest contains a value that cannot be read: {error}") from error
    _check_nesting(document)
    return document


def load_manifest(path: Path) -> LoadedManifest:
    """Read a manifest file and record the SHA-256 of the exact bytes that were read.

    Raises RestrictedPathError, before anything is read, for a path in a restricted location.
    """
    ensure_not_restricted(path, "Manifest")
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_MANIFEST_BYTES + 1)
    except OSError as error:
        raise ManifestLoadError(f"Unable to read '{path}': {error}") from error
    if len(data) > MAX_MANIFEST_BYTES:
        raise ManifestLoadError(f"'{path}' is larger than {MAX_MANIFEST_BYTES} bytes.")
    try:
        document = parse_manifest_bytes(data)
    except ManifestLoadError as error:
        raise ManifestLoadError(f"'{path}': {error}") from error
    return LoadedManifest(path=path, document=document, sha256=hashlib.sha256(data).hexdigest())


def dumps_canonical(document: Any) -> str:
    """Deterministic JSON text: sorted keys, two-space indent, LF newline at the end."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
