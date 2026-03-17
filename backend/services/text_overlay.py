"""
Text overlay service — renders text overlays on timelapse frames using PIL.
Supports per-frame dynamic variables and 9-position grid placement.
"""
import os
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Font search paths inside the Docker container
FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype/liberation",
    "/usr/share/fonts/truetype",
    "/usr/share/fonts",
]

# Map of display name → (filename, bold_filename)
FONT_MAP = {
    "DejaVu Sans": ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    "DejaVu Sans Mono": ("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf"),
    "DejaVu Serif": ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf"),
    "Liberation Sans": ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
    "Liberation Mono": ("LiberationMono-Regular.ttf", "LiberationMono-Bold.ttf"),
    "Liberation Serif": ("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf"),
}

# 9-position grid padding (percentage of image dimension)
PADDING_PERCENT = 0.02


def get_available_fonts() -> list[dict]:
    """Return list of available fonts with their display names."""
    fonts = []
    for name, (regular, bold) in FONT_MAP.items():
        path = _find_font(regular)
        if path:
            fonts.append({
                "name": name,
                "has_bold": _find_font(bold) is not None,
            })
    return fonts


def _find_font(filename: str) -> Optional[str]:
    """Find a font file by filename across search paths."""
    for d in FONT_DIRS:
        path = os.path.join(d, filename)
        if os.path.isfile(path):
            return path
    return None


def _load_font(font_name: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load a PIL font by display name and size."""
    entry = FONT_MAP.get(font_name)
    if entry:
        filename = entry[1] if bold else entry[0]
        path = _find_font(filename)
        if not path and bold:
            path = _find_font(entry[0])
        if path:
            return ImageFont.truetype(path, size)
    # Fallback to default
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _hex_to_rgba(hex_color: str, opacity: float = 1.0) -> Tuple[int, int, int, int]:
    """Convert hex color string to RGBA tuple."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 6:
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    elif len(hex_color) == 8:
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        opacity = int(hex_color[6:8], 16) / 255.0
    else:
        r, g, b = 255, 255, 255
    return (r, g, b, int(opacity * 255))


def resolve_template(text: str, variables: Dict[str, str]) -> str:
    """Resolve template variables in text.

    Supported variables:
        {job_name}      - Job name
        {date}          - Capture date (YYYY-MM-DD)
        {time}          - Capture time (HH:MM:SS)
        {datetime}      - Capture date and time
        {frame}         - Current frame number
        {total_frames}  - Total frame count
    """
    try:
        return text.format(**variables)
    except (KeyError, IndexError, ValueError):
        # If template has unknown vars, do partial replacement
        result = text
        for key, val in variables.items():
            result = result.replace('{' + key + '}', str(val))
        return result


def _calculate_position(
    position: str,
    image_size: Tuple[int, int],
    text_size: Tuple[int, int],
) -> Tuple[int, int]:
    """Calculate x, y coordinates for the 9-position grid."""
    img_w, img_h = image_size
    txt_w, txt_h = text_size
    pad_x = int(img_w * PADDING_PERCENT)
    pad_y = int(img_h * PADDING_PERCENT)

    positions = {
        'top-left':      (pad_x, pad_y),
        'top-center':    ((img_w - txt_w) // 2, pad_y),
        'top-right':     (img_w - txt_w - pad_x, pad_y),
        'middle-left':   (pad_x, (img_h - txt_h) // 2),
        'middle-center': ((img_w - txt_w) // 2, (img_h - txt_h) // 2),
        'middle-right':  (img_w - txt_w - pad_x, (img_h - txt_h) // 2),
        'bottom-left':   (pad_x, img_h - txt_h - pad_y),
        'bottom-center': ((img_w - txt_w) // 2, img_h - txt_h - pad_y),
        'bottom-right':  (img_w - txt_w - pad_x, img_h - txt_h - pad_y),
    }
    return positions.get(position, positions['bottom-left'])


def _get_text_bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> Tuple[int, int, int, int]:
    """Get bounding box of rendered text. Returns (width, height, x_offset, y_offset)."""
    bbox = draw.multiline_textbbox((0, 0), text, font=font)
    return (bbox[2] - bbox[0], bbox[3] - bbox[1], bbox[0], bbox[1])


def render_overlay(
    image: Image.Image,
    config: Dict[str, Any],
    variables: Optional[Dict[str, str]] = None,
) -> Image.Image:
    """Render text overlay on an image.

    Args:
        image: PIL Image to draw on (will be modified in-place if RGBA, otherwise copied)
        config: Overlay configuration dict with keys:
            text, font, font_size, color, position, bold,
            background, background_color, background_opacity
        variables: Template variables for text resolution

    Returns:
        Image with overlay rendered
    """
    if not config.get('enabled') or not config.get('text'):
        return image

    text = config['text']
    if variables:
        text = resolve_template(text, variables)

    if not text.strip():
        return image

    font_name = config.get('font', 'DejaVu Sans')
    font_size_pct = config.get('font_size', 5.0)
    bold = config.get('bold', False)
    color = config.get('color', '#FFFFFF')
    color_opacity = config.get('color_opacity', 1.0)
    position = config.get('position', 'bottom-left')
    bg_enabled = config.get('background', True)
    bg_color = config.get('background_color', '#000000')
    bg_opacity = config.get('background_opacity', 0.5)

    # Convert percentage to pixels based on image height
    font_size_px = max(8, int(image.height * font_size_pct / 100))

    font = _load_font(font_name, font_size_px, bold)
    color_rgba = _hex_to_rgba(color, color_opacity)

    # Work on RGBA copy for transparency support
    if image.mode != 'RGBA':
        image = image.convert('RGBA')

    # Create overlay layer for semi-transparent background
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    text_w, text_h, _, _ = _get_text_bbox(draw, text, font)
    bg_pad = max(int(font_size_px * 0.2), 2)
    box_w = text_w + bg_pad * 2
    box_h = text_h + bg_pad * 2

    x, y = _calculate_position(position, image.size, (box_w, box_h))

    if bg_enabled:
        bg_rgba = _hex_to_rgba(bg_color, bg_opacity)
        draw.rounded_rectangle(
            [x, y, x + box_w, y + box_h],
            radius=int(font_size_px * 0.15),
            fill=bg_rgba,
        )

    # Draw text centered in the background box using anchor
    center_x = x + box_w / 2
    center_y = y + box_h / 2
    draw.multiline_text(
        (center_x, center_y), text, font=font, fill=color_rgba, anchor='mm',
    )

    # Composite overlay onto image
    result = Image.alpha_composite(image, overlay)
    return result


def render_preview_bytes(
    image_path: Optional[str] = None,
    image_data: Optional[str] = None,
    config: Dict[str, Any] = None,
    variables: Optional[Dict[str, str]] = None,
    max_width: int = 1920,
) -> bytes:
    """Render a preview image with overlay and return as JPEG bytes.

    Args:
        image_path: Path to source image (file on disk)
        image_data: Base64-encoded image (e.g. from test-url)
        config: Overlay config dict
        variables: Template variables
        max_width: Max width for preview (downscale for performance)

    Returns:
        JPEG image bytes
    """
    if image_data:
        # Decode base64 — strip data URI prefix if present
        import base64
        b64 = image_data.split(',', 1)[-1] if ',' in image_data else image_data
        img = Image.open(BytesIO(base64.b64decode(b64)))
    else:
        img = Image.open(image_path)

    # Downscale for preview performance
    if img.width > max_width:
        ratio = max_width / img.width
        new_h = int(img.height * ratio)
        img = img.resize((max_width, new_h), Image.LANCZOS)
        # font_size is percentage-based, so it scales automatically with image size

    result = render_overlay(img, config, variables)

    # Convert to RGB for JPEG output
    if result.mode == 'RGBA':
        bg = Image.new('RGB', result.size, (0, 0, 0))
        bg.paste(result, mask=result.split()[3])
        result = bg

    buf = BytesIO()
    result.save(buf, format='JPEG', quality=85)
    return buf.getvalue()


def process_frames_with_overlay(
    captures: list,
    config: Dict[str, Any],
    job_name: str,
    temp_dir: str,
    total_frames: int,
    progress_callback=None,
) -> list[str]:
    """Process capture images with text overlay for video building.

    For each capture, resolves per-frame variables and renders overlay,
    saving the result to temp_dir.

    Args:
        captures: List of capture tuples/rows from DB
        config: Overlay configuration dict
        job_name: Job name for {job_name} variable
        temp_dir: Directory to save processed frames
        total_frames: Total frame count for {total_frames} variable
        progress_callback: Optional callback(frame_num, total) for progress

    Returns:
        List of paths to processed frame images in order
    """
    from ..utils import parse_iso
    from ..helpers.file_helpers import resolve_capture_path

    output_paths = []
    has_dynamic = _has_dynamic_variables(config.get('text', ''))

    for i, capture in enumerate(captures):
        file_path = resolve_capture_path(capture[2])  # file_path column
        captured_at = capture[4]  # captured_at column

        if not os.path.isfile(file_path):
            logger.warning(f"Frame {i}: file not found: {file_path}")
            continue

        # Build per-frame variables
        variables = {
            'job_name': job_name,
            'frame': str(i + 1),
            'total_frames': str(total_frames),
        }

        if captured_at:
            try:
                dt = parse_iso(captured_at)
                variables['date'] = dt.strftime('%Y-%m-%d')
                variables['time'] = dt.strftime('%H:%M:%S')
                variables['datetime'] = dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                variables['date'] = ''
                variables['time'] = ''
                variables['datetime'] = ''

        out_name = f"frame_{i:06d}.jpg"
        out_path = os.path.join(temp_dir, out_name)

        if has_dynamic or i == 0:
            # Dynamic text or first frame — must render per-frame
            img = Image.open(file_path)
            result = render_overlay(img, config, variables)
            if result.mode == 'RGBA':
                bg = Image.new('RGB', result.size, (0, 0, 0))
                bg.paste(result, mask=result.split()[3])
                result = bg
            result.save(out_path, format='JPEG', quality=95)
        else:
            # Static text — reuse first frame's overlay placement
            # Still need to open/render each frame since images differ
            img = Image.open(file_path)
            result = render_overlay(img, config, variables)
            if result.mode == 'RGBA':
                bg = Image.new('RGB', result.size, (0, 0, 0))
                bg.paste(result, mask=result.split()[3])
                result = bg
            result.save(out_path, format='JPEG', quality=95)

        output_paths.append(out_path)

        if progress_callback and (i + 1) % 50 == 0:
            progress_callback(i + 1, total_frames)

    return output_paths


def _has_dynamic_variables(text: str) -> bool:
    """Check if text contains per-frame dynamic variables."""
    dynamic_vars = ['{date}', '{time}', '{datetime}', '{frame}', '{total_frames}']
    return any(v in text for v in dynamic_vars)
