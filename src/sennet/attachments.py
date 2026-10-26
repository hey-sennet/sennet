"""Images sent with a message: checked, stored under the data dir, and handed to agents by path.

A device shrinks an image before uploading it (to a long side of about 1600 px), so what arrives
is a JPEG, PNG or WebP of a few hundred KB. It sends a thumbnail too, for the timeline, which
would otherwise load the whole image to show it a few hundred pixels wide; only the thumbnail
is never shown to an agent. It is uploaded on its own first, and a message, typed
or spoken, then names it by id: a spoken turn can't carry a file, and an upload can start as
soon as the image is picked. An image no message took is removed after a day.

Nothing here decodes an image: its header has to be one of these formats, with a size that
makes sense. Agents read the file themselves, and the browser only shows it from an `<img>`, as
the type stored here, under `nosniff`.
"""
from __future__ import annotations

import os
import struct
import time
from dataclasses import dataclass
from pathlib import Path

from .db import Database

MAX_BYTES = 10 * 1024 * 1024    # well above a shrunk image; a phone's full photo is refused
MAX_PER_MESSAGE = 8
MAX_SIDE = 16384                # pixels; a header claiming more isn't a real image
THUMBNAIL_MAX_BYTES = 256 * 1024
THUMBNAIL_MAX_SIDE = 1024
UNSENT_TTL_S = 24 * 60 * 60     # an upload no message took

EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


class AttachmentError(ValueError):
    pass


@dataclass(frozen=True)
class Sniffed:
    media_type: str
    width: int
    height: int


def sniff(data: bytes) -> Sniffed:
    """The image's type and size, from its header; AttachmentError if it isn't one we take."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return _sized("image/png", width, height)
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return _webp(data)
    if data.startswith(b"\xff\xd8\xff"):
        return _jpeg(data)
    raise AttachmentError("that isn't a JPEG, PNG or WebP image")


def _sized(media_type: str, width: int, height: int) -> Sniffed:
    if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
        raise AttachmentError("that image's size doesn't make sense")
    return Sniffed(media_type, width, height)


def _webp(data: bytes) -> Sniffed:
    chunk = data[12:16]
    if chunk == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
        width, height = struct.unpack("<HH", data[26:30])
        return _sized("image/webp", width & 0x3FFF, height & 0x3FFF)
    if chunk == b"VP8L" and data[20:21] == b"\x2f":
        bits = int.from_bytes(data[21:25], "little")
        return _sized("image/webp", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    if chunk == b"VP8X":
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return _sized("image/webp", width, height)
    raise AttachmentError("that WebP image is damaged")


# JPEG markers that start a frame, and so carry its size: SOF0 to SOF15 but DHT, JPG and DAC.
_FRAMES = set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def _jpeg(data: bytes) -> Sniffed:
    at = 2
    while at + 9 < len(data):
        if data[at] != 0xFF:
            break
        marker = data[at + 1]
        if marker == 0xFF:  # padding
            at += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # no length
            at += 2
            continue
        (length,) = struct.unpack(">H", data[at + 2:at + 4])
        if marker in _FRAMES:
            height, width = struct.unpack(">HH", data[at + 5:at + 9])
            return _sized("image/jpeg", width, height)
        at += 2 + length
    raise AttachmentError("that JPEG image is damaged")


class Attachments:
    """The stored images: files under `folder`, rows in the database."""

    def __init__(self, db: Database, folder: Path):
        self.db = db
        self.folder = folder

    def add(self, data: bytes, device_id: int | None) -> dict:
        if len(data) > MAX_BYTES:
            raise AttachmentError(f"that image is over {MAX_BYTES // (1024 * 1024)} MB")
        found = sniff(data)
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        attachment = self.db.add_attachment(device_id, found.media_type, found.width, found.height, len(data))
        self._write(self.path(attachment), data)
        return attachment

    def path(self, attachment: dict) -> Path:
        return self.folder / f"{attachment['id']}{EXTENSIONS[attachment['media_type']]}"

    def thumbnail_path(self, attachment: dict) -> Path | None:
        if not attachment.get("thumbnail"):
            return None
        return self.folder / f"{attachment['id']}-thumbnail{EXTENSIONS[attachment['thumbnail']]}"

    def add_thumbnail(self, attachment: dict, data: bytes) -> dict:
        """The image's thumbnail, for an image not yet sent; it replaces any before it."""
        if attachment["message_id"] is not None:
            raise AttachmentError("that image was already sent")
        if len(data) > THUMBNAIL_MAX_BYTES:
            raise AttachmentError(f"a thumbnail is up to {THUMBNAIL_MAX_BYTES // 1024} KB")
        found = sniff(data)
        if max(found.width, found.height) > THUMBNAIL_MAX_SIDE:
            raise AttachmentError(f"a thumbnail is up to {THUMBNAIL_MAX_SIDE} pixels on a side")
        if (old := self.thumbnail_path(attachment)) is not None:
            old.unlink(missing_ok=True)
        updated = {**attachment, "thumbnail": found.media_type}
        self._write(self.thumbnail_path(updated), data)
        self.db.set_thumbnail(attachment["id"], found.media_type)
        return updated

    def _write(self, path: Path, data: bytes):
        # Kept to this user, like the database: a screenshot may hold anything.
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "wb") as file:
            file.write(data)

    def unsent(self, ids: list[int]) -> list[dict]:
        """The images with these ids, in that order, for a message to take; AttachmentError if one
        is missing or another message took it."""
        if len(ids) > MAX_PER_MESSAGE:
            raise AttachmentError(f"a message takes up to {MAX_PER_MESSAGE} images")
        if len(set(ids)) != len(ids):
            raise AttachmentError("the same image is in the message twice")
        found = []
        for attachment_id in ids:
            attachment = self.db.attachment(attachment_id)
            if attachment is None or not self.path(attachment).is_file():
                raise AttachmentError("an image in the message is gone; add it again")
            if attachment["message_id"] is not None:
                raise AttachmentError("an image in the message was already sent")
            found.append(attachment)
        return found

    def remove(self, attachments: list[dict]):
        for attachment in attachments:
            self.path(attachment).unlink(missing_ok=True)
            if (thumbnail := self.thumbnail_path(attachment)) is not None:
                thumbnail.unlink(missing_ok=True)

    def sweep(self):
        """Uploads no message took within a day, and files whose row is gone."""
        self.remove(self.db.take_unsent_attachments(time.time() - UNSENT_TTL_S))
        if not self.folder.is_dir():
            return
        rows = self.db.query("SELECT id, media_type, thumbnail FROM attachments")
        known = {self.path(a).name for a in rows} | {t.name for a in rows if (t := self.thumbnail_path(a))}
        for file in self.folder.iterdir():
            if file.is_file() and file.name not in known:
                file.unlink(missing_ok=True)
