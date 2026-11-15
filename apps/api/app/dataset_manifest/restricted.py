"""Refusal of restricted locations.

UMaT/MN273 restricted material, and the application's upload folder, must not
be read, hashed, scanned or written by these tools, whatever a manifest says
about its category. This guard is deliberately independent of any manifest
field: it looks only at the path.

The check is lexical first: it compares the path's components with the list of
restricted locations without touching the file system, so a restricted
location is refused before anything inside it is listed, opened or hashed. Only
if that passes is the path resolved, to catch an alias (a symbolic link or
Windows junction) that points into a restricted location.

The locations are matched anywhere in a path, case-insensitively, so a copy of
the data tree in another folder or on another drive is refused too. There is no
option to switch the guard off.
"""

import os
from pathlib import PurePath

# Runs of consecutive path components that mark a restricted location.
RESTRICTED_SEQUENCES: tuple[tuple[str, ...], ...] = (
    ("data", "evaluation"),
    ("data", "umat_practicals"),
    ("data", "uploads"),
)
# Component names that are restricted wherever they appear in a path.
RESTRICTED_NAMES: tuple[str, ...] = ("umat_practicals",)


class RestrictedPathError(ValueError):
    """A path is inside a restricted location and was refused."""


def lexical_restriction(path: "str | os.PathLike[str]") -> str | None:
    """Name the restricted location `path` is inside, using no file system access."""
    absolute = os.path.normpath(os.path.abspath(os.fspath(path)))
    parts = [part.casefold() for part in PurePath(absolute).parts]
    for name in RESTRICTED_NAMES:
        if name in parts:
            return name
    for sequence in RESTRICTED_SEQUENCES:
        size = len(sequence)
        for start in range(len(parts) - size + 1):
            if tuple(parts[start : start + size]) == sequence:
                return "/".join(sequence)
    return None


def restriction_of(path: "str | os.PathLike[str]") -> str | None:
    """The restricted location `path` is in or resolves into, or None.

    The lexical check runs first. Resolution, which does touch the file system
    (only to follow links along this one path), runs only for paths that
    passed it.
    """
    found = lexical_restriction(path)
    if found is not None:
        return found
    try:
        resolved = os.path.realpath(os.fspath(path))
    except (OSError, ValueError, RuntimeError):
        return None
    found = lexical_restriction(resolved)
    return f"{found} (reached through a link)" if found is not None else None


def refusal_message(path: "str | os.PathLike[str]", role: str, location: str) -> str:
    return (
        f"{role} {ascii(os.fspath(path))} is in a restricted location ({location}). These tools "
        "never read, hash, scan or write there: UMaT/MN273 material must not be used for "
        "training, validation, testing or independent AI evaluation, and no manifest field "
        "can change that."
    )


def ensure_not_restricted(path: "str | os.PathLike[str]", role: str) -> None:
    """Raise RestrictedPathError if `path` is in or resolves into a restricted location."""
    location = restriction_of(path)
    if location is not None:
        raise RestrictedPathError(refusal_message(path, role, location))
