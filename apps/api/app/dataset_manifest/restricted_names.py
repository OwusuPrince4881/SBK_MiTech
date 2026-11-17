"""A best-effort tripwire for UMaT/MN273 names in manifest text.

This is NOT proof that material is safe. A manifest can describe restricted
material without ever using these names, a file can be copied anywhere, and the
tools never look inside an image. The tripwire exists only to catch the common
careless case, in addition to the category check and the restricted-path guard:
a manifest that names itself after the restricted material is refused whatever
its declared category says.

Matching is explicit. Text is normalised first (compatibility forms such as
full-width letters are folded, invisible format characters and combining marks
are removed, case is folded, and a small table of look-alike Cyrillic, Greek and
Armenian letters is mapped to Latin). Then:

- `UMaT` is matched wherever the four letters `umat` run together, including inside a
  longer identifier (`UMaTData`, `umatphotos`, `myumat`, `images/UMaTphotos/a.jpg`), and
  when spaced or separated (`U.M.a.T`, `U-MaT`), which must then stand as its own word.
  A short, explicit list of innocent words that happen to contain `umat` is masked out
  first (`sumatra`, `sumatriptan`, `pneumat-`, `rheumat-`, `thaumat-`, `humate`,
  `umatilla`, `fumatori-`, `cumate`), so `Sumatra`, `pneumatic`, `humate`, `Umatilla`
  and `rheumatic` are left alone, in any compound (`PneumaticHammer`). The stems are
  matched as pieces, not as whole words, so a name hidden next to one (`pneumaticumat`)
  is still found. Any other word that contains `umat` is refused: rename it.
- `MN273` is matched with any separators and leading zeros (`MN 273`, `mn-273`,
  `MN.273`, `MN 0273`) as its own token, and also directly after a lower-case letter when
  it is written in capitals (`ProjectMN273`), but not inside a longer number or word
  (`autumn 273`, `column273`, `hymn273`, `mn2735`).
- The long form `University of Mines and Technology` is matched as a phrase.

The look-alike table and the list of innocent stems are deliberately small, so spellings
that depend on other look-alikes, words this module has never been told about, or a
lower-case `MN273` glued to a preceding word (`teammn273`) are not handled as you might
expect. Nothing here is proof that material is safe.
"""

import re
import unicodedata
from collections.abc import Iterator
from typing import Any

# Letters that look like Latin letters in other scripts, mapped after case folding.
_LOOKALIKES = str.maketrans(
    {
        # Cyrillic
        "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h",
        "о": "o", "п": "n", "р": "p", "с": "c", "т": "t", "у": "y",
        "х": "x", "і": "i", "ј": "j", "ѕ": "s",
        # Greek
        "α": "a", "β": "b", "ε": "e", "η": "n", "ι": "i", "κ": "k",
        "μ": "m", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "u",
        "χ": "x",
        # Armenian
        "ս": "u",
    }
)
# Zero-width, soft-hyphen and bidirectional-control characters (Unicode category Cf).
_INVISIBLE = dict.fromkeys(
    [0x00AD, 0x061C, 0x180E, 0xFEFF]
    + list(range(0x200B, 0x2010))
    + list(range(0x202A, 0x202F))
    + list(range(0x2060, 0x2070))
    + list(range(0xFFF9, 0xFFFC))
)
_COMBINING = re.compile("[̀-ͯ᪰-᫿᷀-᷿⃐-⃿︠-︯]")

_NAMES: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    (
        "UMaT",
        re.compile(
            r"(?<![a-z0-9])u[\W_]{0,2}m[\W_]{0,2}a[\W_]{0,2}t"
            r"(?:(?![a-z])|(?=practicals?(?![a-z])))"
        ),
    ),
    (
        "MN273",
        re.compile(r"(?<![a-z0-9])m[\W_]{0,2}n[\W_]{0,2}0*2[\W_]{0,2}7[\W_]{0,2}3(?![0-9])"),
    ),
    (
        "University of Mines and Technology",
        re.compile(r"university\s+of\s+mines\s+(?:and|&)\s+technology"),
    ),
)
_HEX = frozenset("0123456789abcdef")
_MIN_LENGTH = 4  # nothing shorter can hold a restricted name
_EXCERPT = 80
# Innocent words that contain the letters `umat`. They are masked out, as pieces, before a
# run of `umat` is looked for, so they never trigger it but never shield a real name either.
_INNOCENT_STEMS = re.compile(
    r"sumatra|sumatriptan|pneumat|rheumat|thaumat|humate|umatilla|fumatori|cumate"
)
# A lower-case letter or digit directly followed by a capital marks a word boundary in
# camel-case text (`ProjectMN273`).
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_MN273 = _NAMES[1][1]


def _clean(text: str) -> str:
    """Compatibility forms folded, invisible characters and combining marks removed; case kept."""
    if text.isascii():
        return text
    text = unicodedata.normalize("NFKC", text).translate(_INVISIBLE)
    return _COMBINING.sub("", unicodedata.normalize("NFKD", text))


def _fold(text: str) -> str:
    """Case folded, with look-alike letters from other scripts mapped to Latin."""
    return text.casefold() if text.isascii() else text.casefold().translate(_LOOKALIKES)


def normalise_for_matching(text: str) -> str:
    """Fold `text` so that spelling tricks do not change what a name looks like."""
    return _fold(_clean(text))


def find_restricted_name(text: str) -> str | None:
    """The restricted name that `text` contains, or None. See the module notes for the rules."""
    if len(text) < _MIN_LENGTH:
        return None
    cleaned = _clean(text)
    folded = _fold(cleaned)
    # `umat` run together, anywhere, once the known innocent words are masked out.
    if "umat" in folded and "umat" in _INNOCENT_STEMS.sub(" ", folded):
        return "UMaT"
    # Spaced or separated forms, and the other names, must stand on their own.
    for name, pattern in _NAMES:
        if pattern.search(folded):
            return name
    # Capitals after a lower-case letter are a word boundary: ProjectMN273.
    spaced = _fold(_CAMEL_BOUNDARY.sub(" ", cleaned))
    if spaced != folded and _MN273.search(spaced):
        return "MN273"
    return None


def _is_checksum(text: str) -> bool:
    return len(text) == 64 and _HEX.issuperset(text)


def _location(path: tuple[Any, ...]) -> str:
    rendered = ""
    for part in path:
        rendered += f"[{part}]" if isinstance(part, int) else (f".{part}" if rendered else str(part))
    return rendered or "$"


def _walk_strings(document: Any) -> Iterator[tuple[tuple[Any, ...], str]]:
    """Every string in a JSON document with its path, without recursion."""
    stack: list[tuple[Any, tuple[Any, ...]]] = [(document, ())]
    while stack:
        value, path = stack.pop()
        if isinstance(value, str):
            yield path, value
        elif isinstance(value, dict):
            stack.extend((child, path + (key,)) for key, child in value.items())
        elif isinstance(value, list):
            stack.extend((child, path + (index,)) for index, child in enumerate(value))


def restricted_text_hits(document: Any, limit: int = 50) -> tuple[tuple[str, str, str], ...]:
    """(location, excerpt, name) for each string in the manifest that carries a restricted name.

    Every string value is inspected: dataset ID, name, description, source and
    custodian, licence and permission text, authorisation references, notes,
    sample, record and session IDs, labels and file paths. Checksums are skipped.
    Results are in document order and capped at `limit`.
    """
    hits: list[tuple[str, str, str]] = []
    for path, text in _walk_strings(document):
        if _is_checksum(text):
            continue
        name = find_restricted_name(text)
        if name is not None:
            excerpt = text if len(text) <= _EXCERPT else text[:_EXCERPT] + "..."
            hits.append((_location(path), excerpt, name))
    hits.sort(key=lambda hit: hit[0])
    return tuple(hits[:limit])
