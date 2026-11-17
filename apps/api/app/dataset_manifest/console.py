"""Console-safe text for the command-line tools.

Paths, manifest text and names read from disk can contain characters that a
Windows console or a redirected stream in a legacy code page cannot encode
(for example a Japanese folder name under cp1252). Printing such text raises
`UnicodeEncodeError`, which would turn a successful run into a failure after the
work was done. Everything the tools print goes through `safe_text`, so the output
is plain ASCII: characters outside ASCII and control characters are shown as
escapes such as `\\u65e5` or `\\x85`.
"""

import sys
from typing import TextIO


def safe_text(value: object) -> str:
    """`value` as ASCII text. Newlines and tabs are kept; every other special character is escaped."""
    parts: list[str] = []
    for character in str(value):
        code = ord(character)
        if character in "\n\t" or 32 <= code < 127:
            parts.append(character)
        elif code < 256:
            parts.append(f"\\x{code:02x}")
        elif code < 0x10000:
            parts.append(f"\\u{code:04x}")
        else:
            parts.append(f"\\U{code:08x}")
    return "".join(parts)


def say(text: object = "", *, file: TextIO | None = None) -> None:
    """Print `text` as safe ASCII to `file` (standard output by default)."""
    print(safe_text(text), file=file if file is not None else sys.stdout)
