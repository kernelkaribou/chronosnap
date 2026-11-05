"""Deterministic, bounded H.264 normalization for explicitly approved joins."""
from fractions import Fraction


_LEVEL_BITRATES = {
    10: 64_000, 11: 192_000, 12: 384_000, 13: 768_000,
    20: 2_000_000, 21: 4_000_000, 22: 4_000_000, 30: 10_000_000,
    31: 14_000_000, 32: 20_000_000, 40: 20_000_000, 41: 50_000_000,
    42: 50_000_000, 50: 135_000_000, 51: 240_000_000, 52: 240_000_000,
    60: 240_000_000, 61: 480_000_000, 62: 800_000_000,
}
_PROFILES = {
    "Constrained Baseline": "baseline", "Baseline": "baseline",
    "Main": "main", "High": "high", "High 4:4:4 Predictive": "high444",
}
_CHROMA = {
    "left": (0, 0, 128), "center": (1, 128, 128),
    "topleft": (2, 0, 0), "top": (3, 128, 0),
    "bottomleft": (4, 0, 256), "bottom": (5, 128, 256),
}


def sample_aspect(stream: dict) -> Fraction:
    value = stream.get("sample_aspect_ratio")
    ratio = Fraction(value.replace(":", "/")) if value not in (None, "N/A") else Fraction(1)
    if ratio <= 0:
        raise ValueError("Invalid pixel aspect ratio")
    return ratio


def fit_dimensions(source: dict, target: dict, width: int, height: int) -> tuple[int, int]:
    source_aspect = Fraction(source["width"], source["height"]) * sample_aspect(source)
    target_sar = sample_aspect(target)
    if source_aspect >= Fraction(width, height) * target_sar:
        return width, max(2, int(Fraction(width) * target_sar / source_aspect) // 2 * 2)
    return max(2, int(Fraction(height) * source_aspect / target_sar) // 2 * 2), height


def bitrate_limit(target: dict, width: int, height: int, fps: int) -> int:
    rate = max(1_000_000, width * height * fps)
    level_rate = _LEVEL_BITRATES.get(target.get("level"))
    if level_rate:
        factor = Fraction(5, 4) if target.get("profile", "").startswith("High") else Fraction(1)
        rate = min(rate, int(level_rate * factor))
    return rate


def encoded_size_bound(target: dict, width: int, height: int, fps: int, frames: int) -> int:
    rate = bitrate_limit(target, width, height, fps)
    # Capped CRF includes one VBV buffer of burst allowance, plus muxing headroom.
    bits = Fraction(rate * frames, fps) + rate
    return int(bits / 8 * Fraction(6, 5)) + 1024 * 1024


def _colors(stream: dict, height: int) -> tuple[str, str, str, str]:
    return (
        stream.get("color_space") or ("bt709" if height >= 720 else "bt470bg"),
        stream.get("color_primaries") or "bt709",
        stream.get("color_transfer") or "bt709",
        "pc" if stream.get("color_range") == "pc" else "tv",
    )


def encode_args(path: str, source: dict, target: dict, width: int, height: int,
                fps: int, output: str, *, header_only: bool = False) -> list[str]:
    if min(width, height) < 2 or width % 2 or height % 2:
        raise ValueError("Conversion requires positive even output dimensions")
    sw, sh = fit_dimensions(source, target, width, height)
    sar = sample_aspect(target)
    encoded_sar = (f"{sar.numerator}/{sar.denominator}"
                   if target.get("sample_aspect_ratio") not in (None, "N/A") else "0/1")
    input_space, input_primaries, input_transfer, input_range = _colors(source, source["height"])
    output_space, output_primaries, output_transfer, output_range = _colors(target, height)
    chroma, chroma_x, chroma_y = _CHROMA.get(target.get("chroma_location"), _CHROMA["left"])
    color_filter = (
        f"colorspace=ispace={input_space}:iprimaries={input_primaries}:itrc={input_transfer}:"
        f"irange={input_range}:space={output_space}:primaries={output_primaries}:"
        f"trc={output_transfer}:range={output_range}:format=yuv420p"
    )
    filters = [
        f"scale={sw}:{sh}:flags=lanczos:out_h_chr_pos={chroma_x}:out_v_chr_pos={chroma_y}",
        "format=yuv420p", color_filter,
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
        f"setsar={encoded_sar}",
        f"setparams=range={target.get('color_range') or 0}:"
        f"color_primaries={target.get('color_primaries') or 2}:"
        f"color_trc={target.get('color_transfer') or 2}:colorspace={target.get('color_space') or 2}",
        f"settb=1/{fps * 1024}", "setpts=N*1024",
    ]
    rate = bitrate_limit(target, width, height, fps)
    args = [
        "-protocol_whitelist", "file", "-i", path, "-map", "0:v:0",
        "-vf", ",".join(filters), "-r", str(fps), "-fps_mode", "cfr",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-maxrate", str(rate), "-bufsize", str(rate),
        "-pix_fmt", target.get("pix_fmt") or "yuv420p",
        "-profile:v", _PROFILES.get(target.get("profile"), "high"),
        "-x264-params", f"chromaloc={chroma}:nal-hrd=none",
        "-video_track_timescale", str(fps * 1024),
    ]
    if target.get("level"):
        args += ["-level:v", str(target["level"] / 10)]
    if target.get("has_b_frames") == 0:
        args += ["-bf", "0"]
    if header_only:
        args += ["-frames:v", "1"]
    return [*args, "-movflags", "+faststart", "-n", output]
