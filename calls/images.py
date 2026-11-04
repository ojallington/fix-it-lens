"""Frames are downscaled to 1024 px (long edge) before they reach any request."""

from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps


@dataclass(frozen=True)
class PreparedImage:
    jpeg: bytes
    width: int
    height: int
    sha256: str

    @property
    def pixels(self) -> int:
        return self.width * self.height

    def data_url(self) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(self.jpeg).decode()


def prepare(data: bytes, max_px: int = 1024, quality: int = 85) -> PreparedImage:
    """Apply EXIF rotation, downscale so the long edge is at most max_px, and encode as JPEG.

    An image already within max_px that is a JPEG with no rotation is passed through unchanged, so a frame the
    phone downscaled keeps its hash.
    """
    with Image.open(io.BytesIO(data)) as im:
        upright = im.getexif().get(0x0112, 1) == 1
        w, h = im.size
        if upright and max(w, h) <= max_px and im.format == "JPEG":
            return PreparedImage(data, w, h, hashlib.sha256(data).hexdigest())
        rotated = ImageOps.exif_transpose(im)
        w, h = rotated.size
        if max(w, h) > max_px:
            scale = max_px / max(w, h)
            rotated = rotated.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        rotated.convert("RGB").save(out, format="JPEG", quality=quality)
        jpeg = out.getvalue()
        return PreparedImage(jpeg, rotated.size[0], rotated.size[1], hashlib.sha256(jpeg).hexdigest())
