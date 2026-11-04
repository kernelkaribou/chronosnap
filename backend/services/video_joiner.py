"""Quality-preserving joining with explicitly approved selective conversion."""
from collections import deque
from contextlib import closing
from dataclasses import dataclass, field
from fractions import Fraction
import hashlib
import json
import logging
import os
import selectors
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Iterator

from fastapi import HTTPException

from .. import config
from ..database import get_db, dict_from_row
from ..helpers.file_helpers import (
    cleanup_empty_parents, make_relative, resolve_video_path,
    sanitize_directory_name, validate_path_within,
)
from ..models import VideoJoinCreate
from ..utils import get_now, to_iso
from .event_service import add_event
from .import_service import get_timelapses_path
from .video_processor import _ffmpeg_concat_escape
from .video_join_conversion import encode_args, encoded_size_bound, fit_dimensions, sample_aspect

logger = logging.getLogger(__name__)
_probe_slot = threading.BoundedSemaphore(1)
_operations_lock = threading.Lock()


class JoinError(ValueError):
    pass


class JoinCancelled(Exception):
    pass


class MediaCommandError(JoinError):
    pass


class MediaTimestampError(JoinError):
    pass


class MediaStorageError(JoinError):
    pass


class MediaStalledError(JoinError):
    pass


@dataclass
class JoinOperation:
    lock: threading.RLock = field(default_factory=threading.RLock)
    cancelled: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    process: subprocess.Popen | None = None
    started: bool = False
    video_ids: tuple[int, ...] = ()
    revisions: dict[int, str] = field(default_factory=dict)
    plan: "ConversionPlan | None" = None
    write_limit: int | None = None
    kind: str = "join"
    monitor_storage: bool = False


_operations: dict[int, JoinOperation] = {}


@dataclass(frozen=True)
class VideoInfo:
    signature: tuple
    fps: int
    time_base: Fraction
    frames: int
    width: int
    height: int
    profile: str
    stream: dict = field(default_factory=dict, compare=False, repr=False)
    copyable: bool = True

    @property
    def duration(self) -> Fraction:
        return Fraction(self.frames, self.fps)


@dataclass(frozen=True)
class Source:
    video_id: int
    name: str
    path: str
    revision: str
    size: int
    info: VideoInfo


@dataclass(frozen=True)
class ConversionPlan:
    mode: str
    target: VideoInfo
    actions: dict[int, str]
    bounds: dict[int, int]


_SIGNATURE_FIELDS = (
    "codec_name", "codec_tag_string", "profile", "level", "width", "height",
    "pix_fmt", "field_order", "has_b_frames", "sample_aspect_ratio",
    "extradata_hash", "color_range", "color_space", "color_transfer",
    "color_primaries", "chroma_location",
)


def _video_info(stream: dict, fps: int, time_base: Fraction, frames: int, *, copyable: bool = True) -> VideoInfo:
    signature = tuple(stream.get(key) for key in _SIGNATURE_FIELDS) + (fps, time_base)
    return VideoInfo(signature, fps, time_base, frames, int(stream["width"]),
                     int(stream["height"]), stream.get("profile", "Unknown"), stream, copyable)


def _check_cancel(operation: JoinOperation | None):
    if operation and operation.cancelled.is_set():
        raise JoinCancelled()


def _lines(args: list[str], operation: JoinOperation | None = None) -> Iterator[str]:
    """Drain both pipes without unbounded buffering or blocking readline calls."""
    _check_cancel(operation)
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if operation:
        with operation.lock:
            operation.process = process
            if operation.cancelled.is_set():
                process.kill()
    stderr_tail = deque(maxlen=32)
    timestamp_error = False
    buffer = b""
    last_activity = time.monotonic()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ, "out")
        selector.register(process.stderr, selectors.EVENT_READ, "err")
        try:
            while selector.get_map():
                _check_cancel(operation)
                events = selector.select(timeout=0.2)
                if time.monotonic() - last_activity > config.VIDEO_BUILD_STALL_TIMEOUT:
                    raise MediaStalledError("Media processing stalled; processing was stopped")
                for key, _ in events:
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    last_activity = time.monotonic()
                    if key.data == "err":
                        text = chunk.decode("utf-8", errors="replace")
                        previous_tail = stderr_tail[-1][-100:] if stderr_tail else ""
                        timestamp_error |= "non-monoton" in (previous_tail + text).lower()
                        stderr_tail.append(text[-4096:])
                    else:
                        buffer += chunk
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            yield line.decode("utf-8", errors="replace")
                        if len(buffer) > 65536:
                            raise MediaCommandError("Media inspection returned an oversized record")
            if buffer:
                yield buffer.decode("utf-8", errors="replace")
            process.wait(timeout=5)
            _check_cancel(operation)
            if process.returncode:
                # Diagnostics can contain private paths. Keep them out of client errors.
                logger.warning("Media command failed with exit code %s", process.returncode)
                raise MediaCommandError("FFmpeg could not read, copy, or validate the video")
            if timestamp_error:
                raise MediaTimestampError("Incompatible decoding timestamps; processing would alter playback")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
            process.stderr.close()
            if operation:
                with operation.lock:
                    operation.process = None


def _metadata(path: str, operation: JoinOperation | None) -> dict:
    fields = (
        "codec_type,codec_name,codec_tag_string,profile,level,width,height,pix_fmt,"
        "field_order,has_b_frames,sample_aspect_ratio,r_frame_rate,avg_frame_rate,"
        "time_base,start_pts,duration_ts,nb_frames,extradata_hash,nal_length_size,"
        "color_range,color_space,color_transfer,color_primaries,chroma_location"
    )
    args = [
        "ffprobe", "-v", "error", "-protocol_whitelist", "file",
        "-show_entries", f"stream={fields}:stream_disposition:stream_side_data:"
        "format=format_name:format_tags=major_brand",
        "-show_data_hash", "sha256", "-of", "json=compact=1", path,
    ]
    chunks = []
    size = 0
    with closing(_lines(args, operation)) as lines:
        for line in lines:
            size += len(line)
            if size > 65536:
                raise JoinError("Unsupported oversized video metadata")
            chunks.append(line)
    output = "\n".join(chunks)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise JoinError("Invalid video metadata") from exc


def _first_packet_is_idr(path: str, position: int, size: int) -> bool:
    if position < 0 or not 0 < size <= 32 * 1024 * 1024:
        return False
    with open(path, "rb") as source:
        source.seek(position)
        data = source.read(size)
    offset = 0
    found = False
    while offset + 4 < len(data):
        length = int.from_bytes(data[offset:offset + 4], "big")
        offset += 4
        if length < 1 or offset + length > len(data):
            return False
        nal_type = data[offset] & 31
        if nal_type in (1, 2, 3, 4):
            return False
        found |= nal_type == 5
        offset += length
    return found and offset == len(data)


def probe_video(path: str, operation: JoinOperation | None = None) -> VideoInfo:
    try:
        data = _metadata(path, operation)
        streams = data.get("streams", [])
        if len(streams) != 1 or streams[0].get("codec_type") != "video":
            raise JoinError("Only silent videos with one video stream are supported")
        stream = streams[0]
        brand = data.get("format", {}).get("tags", {}).get("major_brand", "").strip()
        if brand not in ("isom", "iso2", "mp41", "mp42", "avc1"):
            raise JoinError("Only MP4 containers are supported")
        if stream.get("codec_name") != "h264" or stream.get("codec_tag_string") != "avc1":
            raise JoinError("Only H.264/AVC MP4 videos are supported")
        if stream.get("pix_fmt") not in ("yuv420p", "yuvj420p") or stream.get("field_order") != "progressive":
            raise JoinError("Only progressive 8-bit 4:2:0 video is supported")
        if stream.get("color_transfer") in ("smpte2084", "arib-std-b67"):
            raise JoinError("HDR video is not supported")
        if stream.get("disposition", {}).get("attached_pic"):
            raise JoinError("Attached pictures are not supported")
        if stream.get("side_data_list"):
            raise JoinError("Videos with rotation or additional display metadata are not supported")
        fps = Fraction(stream["r_frame_rate"])
        time_base = Fraction(stream["time_base"])
        frame_ticks = 1 / (fps * time_base)
        frames = int(stream["nb_frames"])
        if (fps <= 0 or fps.denominator != 1 or time_base <= 0
                or frame_ticks.denominator != 1 or frames <= 0
                or Fraction(stream["avg_frame_rate"]) != fps):
            raise JoinError("Only constant integer frame rates with exact timing are supported")
        if int(stream["start_pts"]) != 0 or int(stream["duration_ts"]) != frames * frame_ticks:
            raise JoinError("Hidden preroll, edit lists, or irregular video duration are not supported")
        if int(stream.get("nal_length_size", 0)) != 4 or not stream.get("extradata_hash"):
            raise JoinError("Unsupported H.264 codec configuration")

        count = 0
        next_frame = 0
        pending = set()
        previous_dts = None
        args = [
            "ffprobe", "-v", "error", "-protocol_whitelist", "file",
            "-select_streams", "v:0", "-show_packets", "-show_entries",
            "packet=pts,dts,duration,flags,pos,size", "-of", "compact=p=0", path,
        ]
        with closing(_lines(args, operation)) as lines:
            for line in lines:
                if not line:
                    continue
                packet = dict(part.split("=", 1) for part in line.split("|") if "=" in part)
                pts, dts, duration = (int(packet[key]) for key in ("pts", "dts", "duration"))
                index = Fraction(pts) / frame_ticks
                if (duration != frame_ticks or index.denominator != 1 or not 0 <= index < frames
                        or (Fraction(dts) / frame_ticks).denominator != 1
                        or int(index) < next_frame or int(index) in pending
                        or (previous_dts is not None and dts - previous_dts != frame_ticks)
                        or "D" in packet["flags"]):
                    raise JoinError("Variable, discarded, duplicated, or discontinuous frames are not supported")
                if count == 0 and (
                    pts != 0 or "K" not in packet["flags"] or not -16 * frame_ticks <= dts <= 0
                    or not _first_packet_is_idr(path, int(packet["pos"]), int(packet["size"]))
                ):
                    raise JoinError("The video must begin with an independent H.264 keyframe")
                pending.add(int(index))
                while next_frame in pending:
                    pending.remove(next_frame)
                    next_frame += 1
                if len(pending) > 32:
                    raise JoinError("Unsupported frame reordering")
                previous_dts = dts
                count += 1
        if count != frames or next_frame != frames or pending:
            raise JoinError("Video packets do not match the declared frame count")

        width, height = int(stream["width"]), int(stream["height"])
        if min(width, height) <= 0:
            raise JoinError("Invalid video dimensions")
        return _video_info(stream, int(fps), time_base, frames)
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OSError) as exc:
        if isinstance(exc, JoinError):
            raise
        raise JoinError("The video is unreadable or has unsupported metadata/timestamps") from exc


def inspect_convertible(path: str, operation: JoinOperation | None = None) -> VideoInfo:
    try:
        return probe_video(path, operation)
    except JoinError:
        pass
    try:
        streams = _metadata(path, operation).get("streams", [])
        if len(streams) != 1 or streams[0].get("codec_type") != "video":
            raise JoinError("Only silent videos with one video stream are supported")
        stream = streams[0]
        if stream.get("field_order") not in (None, "unknown", "progressive"):
            raise JoinError("Interlaced video is not supported")
        if stream.get("pix_fmt") not in ("yuv420p", "yuvj420p", "yuv422p", "yuvj422p",
                                          "yuv444p", "yuvj444p", "nv12"):
            raise JoinError("Conversion supports progressive 8-bit SDR YUV video")
        if stream.get("color_transfer") in ("smpte2084", "arib-std-b67"):
            raise JoinError("HDR video is not supported")
        if stream.get("side_data_list") or stream.get("disposition", {}).get("attached_pic"):
            raise JoinError("Rotation or additional display metadata is not supported")
        fps = Fraction(stream["r_frame_rate"])
        time_base = Fraction(stream["time_base"])
        if fps <= 0 or fps.denominator != 1 or Fraction(stream["avg_frame_rate"]) != fps or time_base <= 0:
            raise JoinError("Only constant integer frame rates are supported")
        if min(int(stream["width"]), int(stream["height"])) < 2:
            raise JoinError("Invalid video dimensions")
        sample_aspect(stream)
        frames = 0
        first = None
        args = [
            "ffprobe", "-v", "error", "-protocol_whitelist", "file", "-select_streams", "v:0",
            "-show_frames", "-show_entries", "frame=best_effort_timestamp,interlaced_frame",
            "-of", "compact=p=0", path,
        ]
        with closing(_lines(args, operation)) as lines:
            for line in lines:
                values = dict(part.split("=", 1) for part in line.split("|") if "=" in part)
                if "best_effort_timestamp" not in values:
                    continue
                if values.get("interlaced_frame") == "1":
                    raise JoinError("Interlaced video is not supported")
                pts = int(values["best_effort_timestamp"])
                if first is None:
                    first = pts
                if abs(Fraction(pts - first) * time_base * fps - frames) > min(time_base * fps, Fraction(1, 4)):
                    raise JoinError("Variable or discontinuous frame timing is not supported")
                frames += 1
        declared = stream.get("nb_frames")
        if not frames or (declared not in (None, "N/A") and int(declared) != frames):
            raise JoinError("Decoded frames do not match the declared frame count")
        return _video_info(stream, int(fps), time_base, frames, copyable=False)
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OSError) as exc:
        if isinstance(exc, JoinError):
            raise
        raise JoinError("The video cannot be safely decoded for conversion") from exc


def _source_path(row: dict) -> str:
    try:
        path = validate_path_within(resolve_video_path(row["file_path"]), get_timelapses_path())
        if any(c in path for c in "\r\n\0") or not os.path.isfile(path) or not os.access(path, os.R_OK):
            raise ValueError("Invalid video file")
        return path
    except (ValueError, OSError) as exc:
        raise JoinError("Video file is missing, unreadable, or outside the timelapse directory") from exc


def _revision(row: dict, path: str) -> str:
    stat = os.stat(path)
    values = [row["id"], row["name"], row["file_path"], row["status"],
              row["completed_at"], stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def _retime_args(source: Source, fps: int, output: str, *, header_only: bool = False) -> list[str]:
    # Use the filter's actual input time base, not the MP4 track's original one.
    # Integer frame indices preserve B-frame presentation/decode ordering exactly.
    filters = (
        f"h264_metadata=tick_rate={fps * 2},"
        f"setts=pts=round(PTS*TB*{source.info.fps})*1024:"
        f"dts=round(DTS*TB*{source.info.fps})*1024:"
        f"duration=1024:time_base=1/{fps * 1024}"
    )
    return [
        "-protocol_whitelist", "file", "-i", source.path, "-map", "0:v:0",
        "-c", "copy", "-bsf:v", filters,
        *(["-frames:v", "1"] if header_only else []),
        "-video_track_timescale", str(fps * 1024), "-n", output,
    ]


def _retimed_info(source: Source, fps: int) -> VideoInfo:
    try:
        with tempfile.TemporaryDirectory(prefix="chronosnap-join-header-") as folder:
            path = os.path.join(folder, "header.mp4")
            with closing(_lines([
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning",
                "-xerror", *_retime_args(source, fps, path, header_only=True),
            ])) as lines:
                for _ in lines:
                    pass
            return probe_video(path)
    except OSError as exc:
        raise JoinError("Temporary storage is unavailable for frame-rate inspection") from exc


def _retimed_signature(source: Source, fps: int) -> tuple:
    return _retimed_info(source, fps).signature


def _needs_retiming(sources: list[Source], fps: int) -> bool:
    return any(s.info.fps != fps or s.info.signature != sources[0].info.signature for s in sources)


def _encoded_header(source: Source, target: dict, width: int, height: int, fps: int) -> VideoInfo:
    try:
        with tempfile.TemporaryDirectory(prefix="chronosnap-join-encode-") as folder:
            path = os.path.join(folder, "header.mp4")
            args = encode_args(source.path, source.info.stream, target, width, height, fps, path, header_only=True)
            with closing(_lines(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning",
                                 "-xerror", *args])) as lines:
                for _ in lines:
                    pass
            return probe_video(path)
    except (ValueError, OSError) as exc:
        if isinstance(exc, JoinError):
            raise
        raise JoinError("The selected encoding settings cannot be reproduced safely") from exc


def _conversion_plan(report: dict, sources: list[Source], resolution: str) -> ConversionPlan | None:
    unique = {source.video_id: source for source in sources}
    width, height = (int(value) for value in resolution.split("x"))
    fps = report["output_fps"]
    available = sorted({f"{s.info.width}x{s.info.height}" for s in sources
                        if not s.info.width % 2 and not s.info.height % 2},
                       key=lambda value: tuple(map(int, value.split("x"))))
    report["available_resolutions"] = available
    if resolution not in available:
        raise HTTPException(422, {
            "message": "Choose one of the selected clips' actual even-sized resolutions",
            "available_resolutions": available, "clips": report["clips"],
        })
    report["output_resolution"] = resolution
    if len(sources) != len(report["clips"]):
        return None
    if report["compatible"] and all((s.info.width, s.info.height) == (width, height) for s in sources):
        for clip in report["clips"]:
            clip["action"] = "retime" if _needs_retiming(sources, fps) else "copy"
        return None
    candidates = {}
    for source in unique.values():
        if source.info.copyable and (source.info.width, source.info.height) == (width, height):
            try:
                candidates[source.video_id] = _retimed_info(source, fps)
            except JoinError as exc:
                logger.info("Source requires encoding instead of timing copy: %s", exc)
    groups = {}
    for source in sources:
        if source.video_id in candidates:
            signature = candidates[source.video_id].signature
            groups[signature] = groups.get(signature, 0) + source.info.frames
    reference = max(groups, key=groups.get) if groups else None
    preserved = {video_id for video_id, info in candidates.items() if info.signature == reference}
    target = next((info for info in candidates.values() if info.signature == reference), None)
    mode = "selective"
    if target:
        for source in unique.values():
            if source.video_id not in preserved:
                try:
                    if _encoded_header(source, target.stream, width, height, fps).signature != reference:
                        target = None
                        break
                except JoinError as exc:
                    logger.info("Reference encoding cannot be reproduced: %s", exc)
                    target = None
                    break
    if target is None:
        mode = "all"
        preserved.clear()
        canvas = next(s.info.stream for s in sources if (s.info.width, s.info.height) == (width, height))
        template = {
            "sample_aspect_ratio": canvas.get("sample_aspect_ratio") or "1:1",
            "profile": "High", "pix_fmt": "yuv420p", "color_range": "tv",
            "color_space": "bt709", "color_transfer": "bt709", "color_primaries": "bt709",
            "chroma_location": "left",
        }
        try:
            target = _encoded_header(sources[0], template, width, height, fps)
            for source in list(unique.values())[1:]:
                if _encoded_header(source, target.stream, width, height, fps).signature != target.signature:
                    raise JoinError("The selected clips cannot be normalized to one encoding")
        except JoinError as exc:
            report["compatible"] = False
            for clip in report["clips"]:
                clip["error"] = str(exc)
            return None
        report["fallback_reason"] = "Converted clips cannot safely match a preserved stream."
    actions = {
        source.video_id: ("copy" if source.info.signature == target.signature else "retime")
        if source.video_id in preserved else "reencode"
        for source in unique.values()
    }
    bounds = {
        source.video_id: encoded_size_bound(target.stream, width, height, fps, source.info.frames)
        if actions[source.video_id] == "reencode" else
        source.size + max(65536, source.size // 20) if actions[source.video_id] == "retime" else source.size
        for source in unique.values()
    }
    for clip in report["clips"]:
        source = unique[clip["video_id"]]
        clip.pop("error", None)
        clip["action"] = actions[source.video_id]
        fitted = fit_dimensions(source.info.stream, target.stream, width, height)
        clip["padding"] = fitted != (width, height)
        if clip["action"] == "reencode":
            clip["reason"] = "Resolution differs" if (source.info.width, source.info.height) != (width, height) else "Encoding settings differ"
            clip["scaling"] = ("downscale" if fitted[0] < source.info.width or fitted[1] < source.info.height
                               else "upscale" if fitted != (source.info.width, source.info.height) else "none")
    report.update(
        compatible=True, mode=mode, warning=None,
        estimated_bytes=sum(bounds[source.video_id] for source in sources),
        temporary_bytes=sum(bounds[video_id] for video_id, action in actions.items() if action != "copy"),
    )
    token_data = [mode, resolution, fps, target.signature,
                  encode_args(sources[0].path, sources[0].info.stream, target.stream,
                              width, height, fps, "output.mp4"),
                  [(source.video_id, source.revision, actions[source.video_id]) for source in sources]]
    report["plan_token"] = hashlib.sha256(json.dumps(token_data, default=str).encode()).hexdigest()
    return ConversionPlan(mode, target, actions, bounds)


def analyze_join(video_ids: list[int], output_fps: int | None = None,
                 output_resolution: str | None = None) -> tuple[dict, list[Source]]:
    report, sources, _ = _analyze_join(video_ids, output_fps, output_resolution)
    return report, sources


def _analyze_join(video_ids: list[int], output_fps: int | None = None,
                  output_resolution: str | None = None) -> tuple[dict, list[Source], ConversionPlan | None]:
    if not _probe_slot.acquire(blocking=False):
        raise HTTPException(429, "Another compatibility check is running; try again shortly")
    try:
        with get_db() as conn:
            rows = {
                row["id"]: dict_from_row(row)
                for row in conn.execute(
                    f"SELECT * FROM processed_videos WHERE id IN ({','.join('?' for _ in video_ids)})",
                    video_ids,
                )
            }
        cached: dict[int, Source | str] = {}
        sources = []
        clips = []
        for position, video_id in enumerate(video_ids, 1):
            row = rows.get(video_id)
            if video_id not in cached:
                try:
                    if row is None:
                        raise JoinError("Video no longer exists")
                    if row["status"] != "completed":
                        raise JoinError("Video is not completed")
                    path = _source_path(row)
                    revision = _revision(row, path)
                    info = inspect_convertible(path) if output_resolution else probe_video(path)
                    if revision != _revision(row, path):
                        raise JoinError("Video changed during inspection; check it again")
                    cached[video_id] = Source(video_id, row["name"], path, revision, os.path.getsize(path), info)
                except (JoinError, OSError) as exc:
                    cached[video_id] = str(exc) if isinstance(exc, JoinError) else "Video changed during inspection"
            source = cached[video_id]
            clip = {"position": position, "video_id": video_id, "name": row["name"] if row else f"Video {video_id}"}
            if isinstance(source, str):
                clip["error"] = source
            else:
                sources.append(source)
                clip.update(revision=source.revision, duration_seconds=float(source.info.duration),
                            source_fps=source.info.fps, total_frames=source.info.frames,
                            resolution=f"{source.info.width}x{source.info.height}",
                            codec=source.info.stream.get("codec_name"))
            clips.append(clip)
        available_fps = sorted({source.info.fps for source in sources if source.info.fps * 1024 <= 2147483647})
        if sources and output_fps is not None and output_fps not in available_fps:
            raise HTTPException(422, {
                "message": "Choose one of the selected clips' actual frame rates",
                "available_fps": available_fps, "clips": clips,
            })
        fps = output_fps or (sources[0].info.fps if sources else None)
        timing_fps = fps if output_resolution else output_fps
        retime = bool(sources and timing_fps is not None and _needs_retiming(sources, timing_fps))
        signatures: dict[int, tuple | str] = {}
        reference = None
        for clip in clips:
            source = cached[clip["video_id"]]
            if isinstance(source, str):
                continue
            if source.video_id not in signatures:
                try:
                    signatures[source.video_id] = (
                        "Encoding requires conversion" if not source.info.copyable else
                        _retimed_signature(source, timing_fps)
                        if retime and timing_fps is not None else source.info.signature
                    )
                except JoinError as exc:
                    signatures[source.video_id] = str(exc)
            signature = signatures[source.video_id]
            if isinstance(signature, str):
                clip["error"] = signature
            else:
                if reference is None:
                    reference = signature
                if signature != reference:
                    clip["error"] = (
                        "Codec configuration, resolution, color, or display settings differ"
                        if output_fps is not None else
                        "Codec configuration, resolution, FPS, or timing differs from the first compatible clip"
                    )
            clip["output_duration_seconds"] = float(
                Fraction(source.info.frames, timing_fps) if timing_fps is not None else source.info.duration
            )
            clip["speed_ratio"] = float(Fraction(timing_fps, source.info.fps)) if timing_fps is not None else 1.0
            clip["action"] = "retime" if retime else "copy"
        compatible = all("error" not in clip for clip in clips)
        estimated_bytes = sum(source.size for source in sources)
        report = {
            "compatible": compatible, "clips": clips,
            "mode": "copy",
            "output_fps": fps, "available_fps": available_fps,
            "duration_seconds": sum(
                float(Fraction(source.info.frames, timing_fps)) if timing_fps is not None else float(source.info.duration)
                for source in sources
            ),
            "estimated_bytes": estimated_bytes,
            "temporary_bytes": sum(source.size for source in {s.video_id: s for s in sources}.values()) if retime else 0,
            "warning": (
                "Maximum-quality codec profiles may not play in every browser; the original profile is preserved."
                if any(source.info.profile == "High 4:4:4 Predictive" for source in sources) else None
            ),
        }
        plan = _conversion_plan(report, sources, output_resolution) if output_resolution and sources else None
        return report, sources, plan
    finally:
        _probe_slot.release()


def assert_videos_mutable(video_ids: list[int]):
    """Caller must hold BEGIN IMMEDIATE through both the check and mutation."""
    selected = set(video_ids)
    with _operations_lock:
        for output_id, operation in _operations.items():
            if not operation.done.is_set() and (
                output_id in selected or not selected.isdisjoint(operation.video_ids)
            ):
                raise HTTPException(409, "A selected video is in use by a join or trim; cancel or wait for it to finish")


def create_join(request: VideoJoinCreate) -> dict:
    name = request.name.strip()
    sanitized = sanitize_directory_name(name)
    if not sanitized:
        raise HTTPException(422, "Video name contains only invalid characters")
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM processed_videos WHERE build_source IN ('joined','trimmed') AND status='processing'").fetchone():
            raise HTTPException(409, "Joining or trimming is busy; try again when the current operation finishes")
    report, sources, plan = _analyze_join(request.video_ids, request.output_fps, request.output_resolution)
    if not report["compatible"]:
        raise HTTPException(422, {"message": "These videos cannot be joined without conversion", "clips": report["clips"]})
    if [source.revision for source in sources] != request.revisions:
        raise HTTPException(409, "A source changed; check compatibility again before joining")
    if request.mode != report["mode"] or (plan and request.plan_token != report["plan_token"]):
        raise HTTPException(409, {
            "message": "Confirm the current re-encoding plan before joining",
            "mode": report["mode"], "clips": report["clips"],
        })
    root = get_timelapses_path()
    working_bytes = report["estimated_bytes"] + report["temporary_bytes"]
    required = working_bytes + max(16 * 1024 * 1024, working_bytes // 20)
    try:
        free = shutil.disk_usage(root).free
    except OSError as exc:
        raise HTTPException(507, "Timelapse storage is unavailable or unreadable") from exc
    if free < required:
        raise HTTPException(507, "Not enough free space in the timelapse directory for a new joined video")
    info = plan.target if plan else sources[0].info
    quality = "partly_converted" if plan and plan.mode == "selective" else "high" if plan else "preserved"
    operation = JoinOperation(
        video_ids=tuple(request.video_ids),
        revisions={s.video_id: s.revision for s in sources},
        plan=plan,
    )
    video_id = None
    try:
        with get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM processed_videos WHERE build_source IN ('joined','trimmed') AND status='processing'"
            ).fetchone():
                raise HTTPException(409, "Joining or trimming is busy; try again shortly")
            if get_timelapses_path() != root:
                raise HTTPException(409, "Timelapse storage changed; check compatibility again")
            for source in sources:
                row = conn.execute("SELECT * FROM processed_videos WHERE id=?", (source.video_id,)).fetchone()
                try:
                    current = _revision(dict(row), _source_path(dict(row))) if row else None
                except (JoinError, OSError) as exc:
                    raise HTTPException(409, "A source changed; check compatibility again") from exc
                if current != source.revision:
                    raise HTTPException(409, "A source changed; check compatibility again")
            cursor = conn.execute("""
                INSERT INTO processed_videos
                (name, file_path, file_size, resolution, framerate, quality,
                 total_frames, duration_seconds, status, build_source, created_at)
                VALUES (?, '', 0, ?, ?, ?, 0, 0, 'processing', 'joined', ?)
            """, (name, f"{info.width}x{info.height}", report["output_fps"], quality, to_iso(get_now())))
            video_id = cursor.lastrowid
            rel_path = os.path.join("joined", f"{video_id}_{sanitized}", f"{sanitized}.mp4")
            conn.execute("UPDATE processed_videos SET file_path=? WHERE id=?", (rel_path, video_id))
            result = dict(conn.execute("SELECT * FROM processed_videos WHERE id=?", (video_id,)).fetchone())
            # Publish the claim before commit lets a waiting mutation take the write lock.
            with _operations_lock:
                _operations[video_id] = operation
    except Exception:
        if video_id is not None:
            with _operations_lock:
                if _operations.get(video_id) is operation:
                    _operations.pop(video_id)
        raise
    return result


def _owned_paths(row: dict) -> tuple[str, str, str, str]:
    root = get_timelapses_path()
    path = validate_path_within(resolve_video_path(row["file_path"]), root)
    path = validate_path_within(path, os.path.join(root, "joined"))
    if not os.path.basename(os.path.dirname(path)).startswith(f"{row['id']}_"):
        raise JoinError("Invalid joined output ownership")
    stem = os.path.splitext(path)[0]
    return path, stem + ".partial.mp4", stem + "_thumb.jpg", os.path.join(os.path.dirname(path), "join.ffconcat")


def _cleanup(row: dict):
    paths = _owned_paths(row)
    _remove_retimed(paths[0])
    for path in paths:
        if os.path.isfile(path):
            os.remove(path)
    cleanup_empty_parents(paths[0], get_timelapses_path())


def _remove_retimed(output: str):
    parent = os.path.dirname(output)
    raw_folder = os.path.join(parent, ".join-timing")
    if os.path.islink(raw_folder):
        raise JoinError("Invalid join working directory")
    folder = validate_path_within(raw_folder, parent)
    if not os.path.isdir(folder):
        return
    with os.scandir(folder) as entries:
        for entry in entries:
            if not entry.name.endswith(".mp4") or not entry.name.removesuffix(".mp4").isdigit():
                raise JoinError("Unexpected file in the join working directory")
            validate_path_within(entry.path, folder)
            os.remove(entry.path)
    os.rmdir(folder)


def _progress(video_id: int, value: float):
    with get_db() as conn:
        conn.execute("UPDATE processed_videos SET progress=MAX(progress,?) WHERE id=? AND status='processing'",
                     (min(99, value), video_id))


def _check_storage(root: str, output: str, limit: int | None):
    try:
        if shutil.disk_usage(root).free < 16 * 1024 * 1024:
            raise MediaStorageError("Timelapse storage is running out of space; processing stopped")
        if limit and os.path.isfile(output) and os.path.getsize(output) > limit:
            raise MediaStorageError("Video output exceeded its admitted storage bound")
    except OSError as exc:
        raise MediaStorageError("Timelapse storage is unavailable during processing") from exc


def _media(args: list[str], operation: JoinOperation, video_id: int,
           duration: Fraction, start: float, span: float) -> int:
    frames = 0
    last_update = 0.0
    last_change = time.monotonic()
    previous = None
    storage_root = get_timelapses_path() if operation.plan or operation.monitor_storage else None
    with closing(_lines(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning",
                         "-xerror", "-progress", "pipe:1", "-nostats", *args], operation)) as lines:
        for line in lines:
            key, _, value = line.partition("=")
            if key == "frame":
                frames = int(value.strip())
            if key == "out_time_us" and value.strip().lstrip("-").isdigit():
                current = int(value)
                if previous != current:
                    previous, last_change = current, time.monotonic()
                elif time.monotonic() - last_change > config.VIDEO_BUILD_STALL_TIMEOUT:
                    raise MediaStalledError("Media processing stopped making progress")
                if time.monotonic() - last_update >= 0.5:
                    if storage_root:
                        _check_storage(storage_root, args[-1], operation.write_limit)
                    _progress(video_id, start + span * max(0, float(Fraction(current, 1000000) / duration)))
                    last_update = time.monotonic()
    if storage_root:
        _check_storage(storage_root, args[-1], operation.write_limit)
    return frames


def process_join(video_id: int):
    with _operations_lock:
        operation = _operations.get(video_id)
    if operation is None:
        return
    with operation.lock:
        if operation.done.is_set():
            return
        operation.started = True
    row = None
    try:
        with get_db() as conn:
            row = dict(conn.execute("SELECT * FROM processed_videos WHERE id=?", (video_id,)).fetchone())
            source_rows = []
            for source_id in operation.video_ids:
                source_row = conn.execute("SELECT * FROM processed_videos WHERE id=?", (source_id,)).fetchone()
                if source_row is None:
                    raise JoinError("A source video was removed before joining")
                source_rows.append(dict(source_row))
        _check_cancel(operation)
        output, partial, thumbnail, manifest = _owned_paths(row)
        os.makedirs(os.path.dirname(output), exist_ok=True)
        sources = []
        inspected = {}
        for source_row in source_rows:
            _check_cancel(operation)
            path = _source_path(source_row)
            revision = _revision(source_row, path)
            if revision != operation.revisions[source_row["id"]]:
                raise JoinError("A source changed after joining was requested")
            if source_row["id"] not in inspected:
                inspected[source_row["id"]] = (inspect_convertible(path, operation) if operation.plan
                                                else probe_video(path, operation))
            info = inspected[source_row["id"]]
            sources.append(Source(source_row["id"], source_row["name"], path, revision, os.path.getsize(path), info))
        if not sources:
            raise JoinError("Source compatibility changed before joining")
        fps = row["framerate"]
        inputs = {s.video_id: (s.path, s.info) for s in sources}
        plan = operation.plan
        retime = not plan and _needs_retiming(sources, fps)
        if plan:
            unique = {s.video_id: s for s in sources}
            working = validate_path_within(os.path.join(os.path.dirname(output), ".join-timing"),
                                          os.path.dirname(output))
            os.mkdir(working)
            for index, source in enumerate(unique.values()):
                _check_cancel(operation)
                action = plan.actions[source.video_id]
                if action == "copy":
                    continue
                path = os.path.join(working, f"{source.video_id}.mp4")
                args = (encode_args(source.path, source.info.stream, plan.target.stream,
                                    plan.target.width, plan.target.height, fps, path)
                        if action == "reencode" else _retime_args(source, fps, path))
                operation.write_limit = plan.bounds[source.video_id]
                try:
                    _media(args, operation, video_id, Fraction(source.info.frames, fps),
                           5 + 50 * index / len(unique), 50 / len(unique))
                finally:
                    operation.write_limit = None
                info = probe_video(path, operation)
                if (info.signature != plan.target.signature or info.frames != source.info.frames
                        or os.path.getsize(path) > plan.bounds[source.video_id]):
                    raise JoinError("Converted stream does not match the approved plan; output was not published")
                inputs[source.video_id] = (path, info)
        elif retime:
            unique = {s.video_id: s for s in sources}
            timing_folder = validate_path_within(os.path.join(os.path.dirname(output), ".join-timing"),
                                                os.path.dirname(output))
            os.mkdir(timing_folder)
            for index, source in enumerate(unique.values()):
                _check_cancel(operation)
                path = os.path.join(timing_folder, f"{source.video_id}.mp4")
                _media(_retime_args(source, fps, path), operation, video_id,
                       Fraction(source.info.frames, fps), 5 + 25 * index / len(unique), 25 / len(unique))
                info = probe_video(path, operation)
                if info.fps != fps or info.frames != source.info.frames:
                    raise JoinError("Frame-rate timing copy changed the original frame count or output FPS")
                inputs[source.video_id] = (path, info)
        reference = inputs[sources[0].video_id][1].signature
        if any(info.signature != reference for _, info in inputs.values()):
            raise JoinError("Source compatibility changed before joining")
        duration = sum((Fraction(s.info.frames, fps) for s in sources), Fraction())
        frames = sum(s.info.frames for s in sources)
        with open(manifest, "x") as listing:
            listing.write("ffconcat version 1.0\n")
            elapsed = Fraction()
            previous_us = 0
            for source in sources:
                listing.write(f"file '{_ffmpeg_concat_escape(inputs[source.video_id][0])}'\n")
                # Round cumulative boundaries, not individual durations, so
                # microsecond concat timestamps cannot accumulate seam drift.
                elapsed += Fraction(source.info.frames, fps)
                boundary_us = round(elapsed * 1000000)
                duration_us = boundary_us - previous_us
                listing.write(f"duration {duration_us // 1000000}.{duration_us % 1000000:06d}\n")
                previous_us = boundary_us
        _media([
            "-protocol_whitelist", "file", "-f", "concat", "-safe", "0", "-i", manifest,
            "-map", "0:v:0", "-c", "copy",
            "-video_track_timescale", str(inputs[sources[0].video_id][1].time_base.denominator),
            "-movflags", "+faststart", "-n", partial,
        ], operation, video_id, duration, 60 if plan else 30 if retime else 5, 10 if plan else 40 if retime else 65)
        _progress(video_id, 70)
        result = probe_video(partial, operation)
        if result.signature != reference or result.frames != frames:
            raise JoinError("Joined output does not match the original stream configuration/frame count")
        decoded = _media([
            "-protocol_whitelist", "file", "-err_detect", "explode", "-i", partial,
            "-map", "0:v:0", "-fps_mode", "passthrough", "-f", "null", "-",
        ], operation, video_id, duration, 70, 25)
        if decoded != frames:
            raise JoinError("Decoded frame count differs from the original clips")
        _media([
            "-protocol_whitelist", "file", "-i", partial, "-frames:v", "1",
            "-q:v", "2", "-n", thumbnail,
        ], operation, video_id, duration, 95, 3)
        for source_row, source in zip(source_rows, sources):
            if _revision(source_row, _source_path(source_row)) != source.revision:
                raise JoinError("A source file changed while joining; the output was not published")
        _remove_retimed(output)
        with operation.lock:
            _check_cancel(operation)
            os.replace(partial, output)
            os.remove(manifest)
            with get_db() as conn:
                conn.execute("""
                    UPDATE processed_videos SET status='completed', progress=100,
                    file_size=?, total_frames=?, duration_seconds=?, completed_at=?,
                    thumbnail_path=?, error_message=NULL WHERE id=? AND status='processing'
                """, (os.path.getsize(output), result.frames, float(result.duration), to_iso(get_now()),
                      make_relative(thumbnail, get_timelapses_path()), video_id))
            operation.done.set()
        add_event(f"Video '{row['name']}' join completed", "video", {"video_id": video_id})
    except Exception as exc:
        # This background-task boundary always records failures and releases claims
        # only after the subprocess generator has killed/waited for its process.
        cancelled = isinstance(exc, JoinCancelled)
        message = str(exc) if isinstance(exc, JoinError) else "Joining failed; inspect the server log"
        logger.error("Video join %s %s: %s", video_id, "cancelled" if cancelled else "failed", type(exc).__name__)
        if row:
            try:
                _cleanup(row)
            except (OSError, ValueError) as cleanup_error:
                logger.error("Join %s cleanup failed: %s", video_id, type(cleanup_error).__name__)
                message += "; partial output cleanup failed"
                cancelled = False
        with get_db() as conn:
            if cancelled:
                conn.execute("DELETE FROM processed_videos WHERE id=?", (video_id,))
            else:
                conn.execute("UPDATE processed_videos SET status='failed', error_message=? WHERE id=?",
                             (message, video_id))
        add_event(f"Video join {'cancelled' if cancelled else 'failed'} (ID: {video_id})",
                  "video", {"video_id": video_id, "error": None if cancelled else message})
    finally:
        operation.done.set()
        with _operations_lock:
            _operations.pop(video_id, None)


def cancel_join(video_id: int) -> bool:
    with _operations_lock:
        operation = _operations.get(video_id)
    if operation is None or operation.kind != "join":
        return False
    with operation.lock:
        if operation.done.is_set():
            return False
        operation.cancelled.set()
        if operation.process and operation.process.poll() is None:
            operation.process.kill()
        if not operation.started:
            with get_db() as conn:
                row = conn.execute("SELECT * FROM processed_videos WHERE id=?", (video_id,)).fetchone()
                if row:
                    _cleanup(dict(row))
                    conn.execute("DELETE FROM processed_videos WHERE id=?", (video_id,))
            operation.done.set()
            with _operations_lock:
                _operations.pop(video_id, None)
            add_event(f"Video join cancelled (ID: {video_id})", "video", {"video_id": video_id})
    if not operation.done.wait(timeout=10):
        raise HTTPException(503, "Cancellation requested; the join is still stopping")
    return True


def recover_joins():
    with get_db() as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM processed_videos WHERE build_source='joined' AND status='processing'"
        )]
    for row in rows:
        message = "Joining was interrupted by an application restart"
        try:
            _cleanup(row)
        except (OSError, ValueError) as exc:
            logger.error("Interrupted join %s cleanup failed: %s", row["id"], type(exc).__name__)
            message += "; partial output cleanup failed"
        with get_db() as conn:
            conn.execute("UPDATE processed_videos SET status='failed', error_message=? WHERE id=?",
                         (message, row["id"]))
        add_event(f"Interrupted video join failed (ID: {row['id']})", "video", {"video_id": row["id"]})


def shutdown_joins():
    with _operations_lock:
        ids = [video_id for video_id, operation in _operations.items() if operation.kind == "join"]
    for video_id in ids:
        try:
            cancel_join(video_id)
        except HTTPException as exc:
            logger.error("Join %s is still stopping during shutdown: %s", video_id, exc.detail)
