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

from __future__ import annotations

import io
import struct

from PIL import Image

from custom_components.eink_dashboard.bmp import BmpDepth, png_to_bmp


def _png(img: Image.Image) -> bytes:
    """Encode ``img`` as PNG bytes."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _bw_png(width: int = 296, height: int = 128) -> bytes:
    """Return a pure black/white PNG with a black left half."""
    img = Image.new("L", (width, height), 255)
    img.paste(0, (0, 0, width // 2, height))
    return _png(img)


def _gray_png(width: int = 64, height: int = 32) -> bytes:
    """Return a PNG with black, gray and white columns."""
    img = Image.new("L", (width, height), 255)
    img.paste(0, (0, 0, width // 3, height))
    img.paste(120, (width // 3, 0, 2 * width // 3, height))
    return _png(img)


def _bits_per_pixel(bmp: bytes) -> int:
    """Read biBitCount from a BMP's BITMAPINFOHEADER."""
    return struct.unpack_from("<H", bmp, 28)[0]


class TestPngToBmp:
    def test_output_is_bmp(self) -> None:
        # Output starts with the BMP magic and keeps the dimensions.
        bmp = png_to_bmp(_bw_png())
        assert bmp[:2] == b"BM"
        img = Image.open(io.BytesIO(bmp))
        assert img.size == (296, 128)

    def test_auto_bw_is_1bit(self) -> None:
        # A pure black/white image becomes a compact 1-bit BMP.
        bmp = png_to_bmp(_bw_png())
        assert _bits_per_pixel(bmp) == 1
        # 296 px -> 37 bytes, padded to 40 per row; plus headers.
        assert len(bmp) < 40 * 128 + 200

    def test_auto_bw_preserves_pixels(self) -> None:
        # Black and white pixels keep their positions after conversion.
        img = Image.open(io.BytesIO(png_to_bmp(_bw_png()))).convert("L")
        assert img.getpixel((0, 0)) == 0
        assert img.getpixel((147, 64)) == 0
        assert img.getpixel((148, 64)) == 255
        assert img.getpixel((295, 127)) == 255

    def test_auto_all_white_is_1bit(self) -> None:
        # A single-color image is still detected as black/white.
        bmp = png_to_bmp(_png(Image.new("L", (16, 8), 255)))
        assert _bits_per_pixel(bmp) == 1

    def test_auto_gray_is_8bit(self) -> None:
        # Gray pixels keep 8-bit depth so no levels are lost.
        bmp = png_to_bmp(_gray_png())
        assert _bits_per_pixel(bmp) == 8
        img = Image.open(io.BytesIO(bmp)).convert("L")
        assert img.getpixel((32, 16)) == 120

    def test_force_mono_thresholds_gray(self) -> None:
        # bits=1 forces 1-bit output, thresholding gray without dither.
        bmp = png_to_bmp(_gray_png(), BmpDepth.MONO)
        assert _bits_per_pixel(bmp) == 1
        img = Image.open(io.BytesIO(bmp)).convert("L")
        assert img.getpixel((5, 5)) == 0
        assert img.getpixel((32, 16)) == 0
        assert img.getpixel((60, 16)) == 255

    def test_force_gray_on_bw(self) -> None:
        # bits=8 keeps 8-bit output even for black/white images.
        bmp = png_to_bmp(_bw_png(), BmpDepth.GRAY)
        assert _bits_per_pixel(bmp) == 8

    def test_rgb_input_converted(self) -> None:
        # Non-grayscale PNG input is converted to grayscale first.
        img = Image.new("RGB", (8, 8), (255, 255, 255))
        bmp = png_to_bmp(_png(img))
        assert _bits_per_pixel(bmp) == 1
