# Copyright 2026 Andreas Schneider
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""PNG to BMP conversion for clients that cannot decode PNG.

Microcontrollers such as the ESP8266 lack the contiguous RAM that a
PNG decoder's zlib window needs, while an uncompressed BMP can be
streamed row by row. A 1-bit BMP of a 296x128 display is about 5 KB.
"""

from __future__ import annotations

import io
from enum import StrEnum

from PIL import Image

# Pixels at or above this gray value become white in 1-bit output.
BMP_THRESHOLD = 128


class BmpDepth(StrEnum):
    """Bit depth requested for the BMP output."""

    # 1-bit when the image is pure black and white, else 8-bit.
    AUTO = "auto"
    # Always 1-bit; gray pixels are thresholded at BMP_THRESHOLD.
    MONO = "1"
    # Always 8-bit grayscale (palette of 256 gray levels).
    GRAY = "8"


def png_to_bmp(png_bytes: bytes, depth: BmpDepth = BmpDepth.AUTO) -> bytes:
    """Convert rendered PNG bytes to an uncompressed grayscale BMP.

    The rendered dashboard is already quantised to the display's gray
    levels, so a 2-level display yields an image with only black and
    white pixels; ``AUTO`` detects that and emits a compact 1-bit BMP.
    No dithering is applied in any mode, so pixel-exact layouts stay
    sharp.

    Args:
        png_bytes: Rendered dashboard image as PNG.
        depth: Requested output bit depth.

    Returns:
        The image encoded as a Windows BMP (1 or 8 bits per pixel).
    """
    img = Image.open(io.BytesIO(png_bytes))
    if img.mode != "L":
        img = img.convert("L")

    mono = depth == BmpDepth.MONO
    if depth == BmpDepth.AUTO:
        # getcolors() returns None once more than maxcolors distinct
        # values occur, so this stays cheap on gray images.
        colors = img.getcolors(maxcolors=2)
        mono = colors is not None and all(
            value in (0, 255) for _count, value in colors
        )

    if mono:
        img = img.point(lambda v: 255 if v >= BMP_THRESHOLD else 0, mode="1")

    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()
