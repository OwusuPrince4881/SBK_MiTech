from pathlib import PurePosixPath
from typing import BinaryIO
from uuid import UUID, uuid4

from app.settings.uploads import UploadSettings

CHUNK_SIZE = 64 * 1024
IMAGE_FORMATS = {
    "image/jpeg": (".jpg",),
    "image/png": (".png",),
    "image/webp": (".webp",),
}


class UnsupportedImageFormatError(ValueError):
    pass


class ImageTooLargeError(ValueError):
    pass


def store_image(
    source: BinaryIO,
    sample_id: UUID,
    original_filename: str,
    declared_mime_type: str | None,
    settings: UploadSettings,
) -> tuple[str, str, int, str, str]:
    header = source.read(12)
    mime_type = _detect_image_type(header)
    if mime_type is None or declared_mime_type != mime_type:
        raise UnsupportedImageFormatError("Only JPEG, PNG, and WebP images are accepted")

    file_size = len(header)
    if file_size > settings.max_file_size_bytes:
        raise ImageTooLargeError("Image exceeds the maximum allowed file size")

    extension = IMAGE_FORMATS[mime_type][0]
    stored_filename = f"{uuid4().hex}{extension}"
    relative_path = PurePosixPath(str(sample_id), stored_filename).as_posix()
    destination = settings.directory / str(sample_id) / stored_filename
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        with destination.open("xb") as output:
            output.write(header)
            while chunk := source.read(
                min(CHUNK_SIZE, settings.max_file_size_bytes - file_size + 1)
            ):
                file_size += len(chunk)
                if file_size > settings.max_file_size_bytes:
                    raise ImageTooLargeError(
                        "Image exceeds the maximum allowed file size"
                    )
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        try:
            destination.parent.rmdir()
        except OSError:
            pass
        raise

    safe_original_filename = _safe_original_filename(original_filename)
    return stored_filename, safe_original_filename, file_size, relative_path, mime_type


def _detect_image_type(header: bytes) -> str | None:
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    return None


def _safe_original_filename(filename: str) -> str:
    basename = filename.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    safe_name = "".join(
        character
        for character in basename
        if ord(character) >= 32 and ord(character) != 127
    ).strip()
    return (safe_name or "upload")[:255]
