"""Pillow security floor and real preview/thumbnail processing regressions."""
import base64
from io import BytesIO
import struct

from packaging.version import Version
import PIL
from PIL import Image
import pytest

from backend.services.text_overlay import render_preview_bytes
from backend.services.thumbnail_generator import generate_thumbnail, get_thumbnail_path


def test_pillow_security_floor():
    assert Version(PIL.__version__) >= Version("13.0.0")


@pytest.mark.parametrize("image_format,mode", [
    ("JPEG", "RGB"),
    ("PNG", "RGBA"),
    ("GIF", "P"),
])
@pytest.mark.parametrize("data_uri", [False, True])
def test_overlay_preview_formats_and_downscaling(image_format, mode, data_uri):
    image = Image.new("RGB", (2000, 1000), (30, 90, 150)).convert(mode)
    source = BytesIO()
    image.save(source, format=image_format)
    encoded = base64.b64encode(source.getvalue()).decode("ascii")
    if data_uri:
        encoded = f"data:image/{image_format.lower()};base64,{encoded}"

    result = render_preview_bytes(
        image_data=encoded,
        config={"enabled": True, "text": "Pillow {job_name}", "bold": True},
        variables={"job_name": "Regression"},
    )
    with Image.open(BytesIO(result)) as preview:
        preview.load()
        assert preview.format == "JPEG"
        assert preview.mode == "RGB"
        assert preview.size == (1920, 960)


def test_benign_psd_preview():
    # Uncompressed 1x1 RGB PSD, with empty metadata sections and valid pixels.
    source = struct.pack(
        ">4sH6sHIIHHIIIH",
        b"8BPS", 1, b"\0" * 6, 3, 1, 1, 8, 3, 0, 0, 0, 0,
    ) + bytes((30, 90, 150))
    result = render_preview_bytes(
        image_data=base64.b64encode(source).decode("ascii"),
        config={"enabled": False},
    )
    with Image.open(BytesIO(result)) as preview:
        preview.load()
        assert preview.format == "JPEG"
        assert preview.mode == "RGB"
        assert preview.size == (1, 1)


@pytest.mark.parametrize("image_format,mode", [
    ("JPEG", "RGB"),
    ("PNG", "RGBA"),
    ("GIF", "P"),
])
def test_capture_thumbnail_formats(tmp_path, image_format, mode):
    source = tmp_path / f"capture.{image_format.lower()}"
    Image.new("RGB", (1600, 900), (30, 90, 150)).convert(mode).save(
        source, format=image_format,
    )

    success, error = generate_thumbnail(str(source))
    assert success, error
    assert error is None
    with Image.open(get_thumbnail_path(str(source))) as thumbnail:
        thumbnail.load()
        assert thumbnail.format == "WEBP"
        assert thumbnail.size == (384, 216)


@pytest.mark.skipif(
    Version(PIL.__version__) < Version("13.0.0"),
    reason="Do not exercise scaled lossless-JPEG decoding on a vulnerable Pillow",
)
def test_scaled_lossless_jpeg_thumbnail(tmp_path):
    source = tmp_path / "lossless.jpg"

    def segment(marker, payload):
        return b"\xff" + bytes((marker,)) + struct.pack(">H", len(payload) + 2) + payload

    # Constant-gray 8-bit SOF3 JPEG: each prediction difference is zero.
    source.write_bytes(
        b"\xff\xd8"
        + segment(0xc4, b"\x00\x01" + b"\x00" * 15 + b"\x00")
        + segment(0xc3, struct.pack(">BHHB", 8, 1024, 1024, 1) + b"\x01\x11\x00")
        + segment(0xda, b"\x01\x01\x00\x01\x00\x00")
        + b"\x00" * (1024 * 1024 // 8) + b"\xff\xd9"
    )
    assert b"\xff\xc3" in source.read_bytes(), "The fixture must use lossless JPEG (SOF3)"

    success, error = generate_thumbnail(str(source))
    assert success, error
    with Image.open(get_thumbnail_path(str(source))) as thumbnail:
        thumbnail.load()
        assert thumbnail.format == "WEBP"
        assert thumbnail.size == (216, 216)
