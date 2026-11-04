"""Whole-second trimming with verified stream copies and bounded H.264 fallback."""
from contextlib import closing
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import logging
import os
import shutil
import threading

from fastapi import HTTPException

from ..database import get_db
from ..helpers.db_helpers import fetch_tags_for_videos, set_video_tags
from ..helpers.file_helpers import (
    cleanup_empty_parents, make_relative, resolve_path,
    sanitize_directory_name, validate_path_within,
)
from ..models import VideoTrimCreate, VideoTrimRequest
from ..utils import get_now, to_iso
from . import video_joiner as media
from . import video_join_conversion as encoding
from .event_service import add_event
from .import_service import get_timelapses_path

logger = logging.getLogger(__name__)
_RESERVE = 16 * 1024 * 1024
_WORK_FILES = ("start.mp4", "middle.mp4", "end.mp4", "parts.ffconcat")


class TrimCompatibilityError(media.JoinError):
    pass


@dataclass(frozen=True)
class TrimPlan:
    source: media.Source
    start: int
    end: int
    copy_start: int
    copy_end: int

    @property
    def frames(self):
        return self.end - self.start

    @property
    def copied_frames(self):
        return self.copy_end - self.copy_start

    @property
    def mode(self):
        return "copy" if self.copied_frames == self.frames else "smart" if self.copied_frames else "encode"


@dataclass
class TrimOperation(media.JoinOperation):
    trim: TrimPlan | None = None
    root: str = ""
    bound: int = 0
    worker: threading.Thread | None = None


def _copy_range(source: media.Source, start: int, end: int) -> tuple[int, int]:
    if not source.info.copyable:
        return start, start
    frame_ticks = 1 / (source.info.fps * source.info.time_base)
    copy_start = None
    copy_end = start
    maximum = -1
    args = [
        "ffprobe", "-v", "error", "-protocol_whitelist", "file",
        "-select_streams", "v:0", "-show_packets", "-show_entries",
        "packet=pts,flags,pos,size", "-of", "compact=p=0", source.path,
    ]
    with closing(media._lines(args)) as lines:
        for count, line in enumerate(lines, 1):
            packet = dict(part.split("=", 1) for part in line.split("|") if "=" in part)
            frame = int(Fraction(int(packet["pts"])) / frame_ticks)
            # A closed prefix before an actual IDR excludes leading/open-GOP B frames.
            if (copy_start is None and start <= frame < end and frame == count - 1
                    and maximum == count - 2 and "K" in packet["flags"]
                    and media._first_packet_is_idr(source.path, int(packet["pos"]), int(packet["size"]))):
                copy_start = frame
            maximum = max(maximum, frame)
            if copy_start is not None and copy_start < count <= end and maximum == count - 1:
                copy_end = count
    if copy_start is None or copy_end <= copy_start:
        return start, start
    return copy_start, copy_end


def _inspect(video_id: int, request: VideoTrimRequest) -> tuple[TrimPlan, dict]:
    if not media._probe_slot.acquire(blocking=False):
        raise HTTPException(429, "Another video inspection is running; try again shortly")
    try:
        with get_db() as conn:
            row = conn.execute("SELECT * FROM processed_videos WHERE id=?", (video_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Video not found")
        row = dict(row)
        if row["status"] != "completed":
            raise HTTPException(409, "Only completed videos can be trimmed")
        path = media._source_path(row)
        revision = media._revision(row, path)
        info = media.inspect_convertible(path)
        if (info.fps > 240 or info.width % 2 or info.height % 2
                or max(info.width, info.height) > 8192 or info.width * info.height > 8192 * 4096):
            raise media.JoinError("Trimming supports even-sized SDR video up to 32 megapixels, 8192 pixels per side, and 240 FPS")
        start = request.trim_start_seconds * info.fps
        end = info.frames - request.trim_end_seconds * info.fps
        if end - start < info.fps:
            raise HTTPException(422, "Keep at least one second of video")
        source = media.Source(video_id, row["name"], path, revision, os.path.getsize(path), info)
        copy_start, copy_end = _copy_range(source, start, end)
        if revision != media._revision(row, path):
            raise HTTPException(409, "The video changed during inspection; open Trim again")
        return TrimPlan(source, start, end, copy_start, copy_end), row
    except media.JoinError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(409, "The video file changed or became unavailable; open Trim again") from exc
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise HTTPException(422, "The video has unsupported or unreadable trim metadata") from exc
    finally:
        media._probe_slot.release()


def analyze_trim(video_id: int, request: VideoTrimRequest) -> dict:
    plan, _ = _inspect(video_id, request)
    info = plan.source.info
    return {
        "revision": plan.source.revision, "source_seconds": float(info.duration),
        "total_frames": info.frames, "framerate": info.fps,
        "resolution": f"{info.width}x{info.height}",
        "duration_seconds": plan.frames / info.fps,
        "max_trim_seconds": (info.frames - info.fps) // info.fps,
        "mode": plan.mode, "copied_frames": plan.copied_frames,
        "reencoded_frames": plan.frames - plan.copied_frames,
    }


def _require_storage(root: str, required: int):
    try:
        if shutil.disk_usage(root).free < required:
            raise HTTPException(507, "Not enough free space for the trimmed video and working files")
    except OSError as exc:
        raise HTTPException(507, "Timelapse storage is unavailable") from exc


def _encoding_target(stream: dict) -> dict:
    return stream if stream.get("codec_name") == "h264" else {}


def create_trim(video_id: int, request: VideoTrimCreate) -> dict:
    name = request.name.strip()
    sanitized = sanitize_directory_name(name)
    if not sanitized:
        raise HTTPException(422, "Video name contains only invalid characters")
    if not request.trim_start_seconds and not request.trim_end_seconds:
        raise HTTPException(422, "Remove at least one second from the start or end")
    with get_db() as conn:
        if conn.execute(
            "SELECT 1 FROM processed_videos WHERE build_source IN ('joined','trimmed') AND status='processing'"
        ).fetchone():
            raise HTTPException(409, "Joining or trimming is busy; try again when the current operation finishes")
    plan, _ = _inspect(video_id, request)
    source = plan.source
    if request.revision != source.revision:
        raise HTTPException(409, "The video changed; open Trim again")
    root = get_timelapses_path()
    bound = encoding.encoded_size_bound(
        _encoding_target(source.info.stream), source.info.width, source.info.height, source.info.fps, plan.frames,
    )
    required = 3 * (source.size + bound) + _RESERVE
    _require_storage(root, required)
    operation = TrimOperation(
        kind="trim", monitor_storage=True, trim=plan, root=root, bound=bound,
        video_ids=(video_id,), revisions={video_id: source.revision},
    )
    output_id = None
    try:
        with get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM processed_videos WHERE build_source IN ('joined','trimmed') AND status='processing'"
            ).fetchone():
                raise HTTPException(409, "Joining or trimming is busy; try again shortly")
            row = conn.execute("SELECT * FROM processed_videos WHERE id=?", (video_id,)).fetchone()
            try:
                current = media._revision(dict(row), media._source_path(dict(row))) if row else None
            except (media.JoinError, OSError) as exc:
                raise HTTPException(409, "The source video changed; open Trim again") from exc
            if current != source.revision or get_timelapses_path() != root:
                raise HTTPException(409, "The source video or storage location changed; open Trim again")
            _require_storage(root, required)
            quality = "preserved" if plan.mode == "copy" else "partly_converted" if plan.mode == "smart" else "high"
            cursor = conn.execute("""
                INSERT INTO processed_videos
                (job_id, job_name, name, file_path, file_size, resolution, framerate,
                 quality, total_frames, duration_seconds, status, build_source, created_at)
                VALUES (?, ?, ?, '', 0, ?, ?, ?, 0, 0, 'processing', 'trimmed', ?)
            """, (row["job_id"], row["job_name"], name, f"{source.info.width}x{source.info.height}",
                  source.info.fps, quality, to_iso(get_now())))
            output_id = cursor.lastrowid
            relative = os.path.join("trimmed", f"{output_id}_{sanitized}", f"{sanitized}.mp4")
            conn.execute("UPDATE processed_videos SET file_path=? WHERE id=?", (relative, output_id))
            tags = fetch_tags_for_videos(conn.cursor(), [video_id]).get(video_id, [])
            set_video_tags(conn.cursor(), output_id, [tag["id"] for tag in tags])
            result = dict(conn.execute("SELECT * FROM processed_videos WHERE id=?", (output_id,)).fetchone())
            with media._operations_lock:
                media._operations[output_id] = operation
    except Exception:
        if output_id is not None:
            with media._operations_lock:
                if media._operations.get(output_id) is operation:
                    media._operations.pop(output_id)
        raise
    return result


def _paths(row: dict, root: str) -> tuple[str, str, str, str]:
    output = validate_path_within(resolve_path(row["file_path"], root), root)
    output = validate_path_within(output, os.path.join(root, "trimmed"))
    if not os.path.basename(os.path.dirname(output)).startswith(f"{row['id']}_"):
        raise media.JoinError("Invalid trimmed output ownership")
    stem = os.path.splitext(output)[0]
    working = os.path.join(os.path.dirname(output), ".trim-work")
    if os.path.islink(working):
        raise media.JoinError("Invalid trim working directory")
    return output, stem + ".partial.mp4", stem + "_thumb.jpg", validate_path_within(working, os.path.dirname(output))


def _clear_working(working: str):
    if not os.path.isdir(working):
        return
    with os.scandir(working) as entries:
        for entry in entries:
            if entry.name not in _WORK_FILES:
                raise media.JoinError("Unexpected file in the trim working directory")
            os.remove(validate_path_within(entry.path, working))
    os.rmdir(working)


def _cleanup(row: dict, root: str):
    output, partial, thumbnail, working = _paths(row, root)
    _clear_working(working)
    for path in (partial, thumbnail, output):
        if os.path.isfile(path):
            os.remove(path)
    cleanup_empty_parents(output, root)


def _range_filter(source: media.Source, start: int, end: int) -> tuple[list[str], str]:
    # Seek to an integer second, then count frames within that second. This avoids
    # rounding away a frame at fractional keyframe positions and bounds preroll.
    second, offset = divmod(start, source.info.fps)
    return (
        ["-ss", str(second), "-protocol_whitelist", "file", "-i", source.path],
        f"trim=start_frame={offset}:end_frame={offset + end - start},setpts=PTS-STARTPTS",
    )


def _encode_args(source: media.Source, start: int, end: int, output: str) -> list[str]:
    info = source.info
    stream = info.stream
    target = _encoding_target(stream)
    inputs, filters = _range_filter(source, start, end)
    rate = encoding.bitrate_limit(target, info.width, info.height, info.fps)
    chroma = encoding._CHROMA.get(stream.get("chroma_location"), encoding._CHROMA["left"])[0]
    args = [
        *inputs, "-map", "0:v:0", "-an",
        "-vf", filters, "-frames:v", str(end - start),
        "-r", str(info.fps), "-fps_mode", "cfr", "-c:v", "libx264",
        "-preset", "medium", "-crf", "18", "-threads", "2", "-filter_threads", "1",
        "-maxrate", str(rate), "-bufsize", str(rate),
        "-pix_fmt", "yuvj420p" if stream.get("color_range") == "pc" else "yuv420p",
        "-profile:v", encoding._PROFILES.get(target.get("profile"), "high"),
        "-x264-params", f"chromaloc={chroma}:nal-hrd=none",
        "-video_track_timescale", str(info.fps * 1024),
    ]
    for field, option in [
        ("color_range", "-color_range"), ("color_space", "-colorspace"),
        ("color_primaries", "-color_primaries"), ("color_transfer", "-color_trc"),
    ]:
        if stream.get(field) not in (None, "unknown"):
            args += [option, str(stream[field])]
    if target.get("level") in encoding._LEVEL_BITRATES:
        args += ["-level:v", str(target["level"] / 10)]
    if stream.get("has_b_frames") == 0:
        args += ["-bf", "0"]
    return [*args, "-movflags", "+faststart", "-n", output]


def _copy_args(source: media.Source, start: int, end: int, output: str) -> list[str]:
    fps = source.info.fps
    microseconds = (start * 1_000_000 + fps - 1) // fps
    seek = f"{microseconds // 1_000_000}.{microseconds % 1_000_000:06d}"
    return [
        "-ss", seek, "-protocol_whitelist", "file", "-i", source.path,
        "-map", "0:v:0", "-c:v", "copy", "-frames:v", str(end - start),
        "-bsf:v", f"setts=pts=round(PTS*TB*{fps})*1024:"
        f"dts=round(DTS*TB*{fps})*1024:duration=1024:time_base=1/{fps * 1024}",
        "-video_track_timescale", str(fps * 1024), "-movflags", "+faststart", "-n", output,
    ]


def _digest(path: str, operation: TrimOperation, combined=None,
            start_packet: int = 0, end_packet: int | None = None) -> tuple[int, bytes]:
    """Verify coded picture data, ignoring only non-picture muxer-inserted NALs.

    This avoids decoding the source and every working segment again. The final
    result still receives one complete, error-checked decode before publication.
    """
    digest = hashlib.sha256()
    count = 0
    args = [
        "ffprobe", "-v", "error", "-protocol_whitelist", "file", "-select_streams", "v:0",
        "-show_packets", "-show_entries", "packet=pos,size", "-of", "compact=p=0", path,
    ]
    with open(path, "rb") as source, closing(media._lines(args, operation)) as lines:
        for index, line in enumerate(lines):
            if end_packet is not None and index >= end_packet:
                break
            if index < start_packet:
                continue
            packet = dict(part.split("=", 1) for part in line.split("|") if "=" in part)
            position, size = int(packet["pos"]), int(packet["size"])
            if position < 0 or not 0 < size <= 32 * 1024 * 1024:
                raise media.JoinError("Unsupported coded-picture size")
            source.seek(position)
            data = source.read(size)
            offset = 0
            picture = False
            while offset + 4 < len(data):
                length = int.from_bytes(data[offset:offset + 4], "big")
                offset += 4
                if length < 1 or offset + length > len(data):
                    raise media.JoinError("Invalid coded-picture data")
                if (data[offset] & 31) in (1, 2, 3, 4, 5):
                    value = length.to_bytes(4, "big") + data[offset:offset + length]
                    digest.update(value)
                    if combined is not None:
                        combined.update(value)
                    picture = True
                offset += length
            if offset != size or len(data) != size or not picture:
                raise media.JoinError("Video packets do not contain complete coded pictures")
            count += 1
    return count, digest.digest()


def _part_info(path: str, operation: TrimOperation):
    try:
        return media.probe_video(path, operation)
    except (media.MediaCommandError, media.MediaStalledError, media.MediaStorageError):
        raise
    except media.JoinError as exc:
        raise TrimCompatibilityError(f"Boundary cannot be safely stitched: {exc}") from exc


def _render(plan: TrimPlan, operation: TrimOperation, video_id: int,
            partial: str, working: str, *, smart: bool) -> tuple[media.VideoInfo, str]:
    source = plan.source
    duration = Fraction(plan.frames, source.info.fps)
    pieces = []
    if smart:
        for name, start, end, copy in [
            ("start.mp4", plan.start, plan.copy_start, False),
            ("middle.mp4", plan.copy_start, plan.copy_end, True),
            ("end.mp4", plan.copy_end, plan.end, False),
        ]:
            if end <= start:
                continue
            path = os.path.join(working, name)
            args = _copy_args(source, start, end, path) if copy else _encode_args(source, start, end, path)
            operation.write_limit = source.size + operation.bound if copy else operation.bound
            media._media(args, operation, video_id, Fraction(end - start, source.info.fps), 5, 45)
            info = _part_info(path, operation)
            if info.frames != end - start or info.fps != source.info.fps:
                raise TrimCompatibilityError("A trim boundary did not preserve the selected frame interval")
            pieces.append((path, start, end, copy, info))
        reference = pieces[0][4].signature
        if any(piece[4].signature != reference for piece in pieces):
            fields = (*media._SIGNATURE_FIELDS, "fps", "time_base")
            differences = [field for index, field in enumerate(fields)
                           if any(piece[4].signature[index] != reference[index] for piece in pieces)]
            raise TrimCompatibilityError(
                f"Boundary codec configurations differ in {', '.join(differences)}; the selection needs re-encoding")
        manifest = os.path.join(working, "parts.ffconcat")
        with open(manifest, "x") as listing:
            listing.write("ffconcat version 1.0\n")
            elapsed = Fraction()
            previous = 0
            for path, start, end, _, _ in pieces:
                elapsed += Fraction(end - start, source.info.fps)
                boundary = round(elapsed * 1_000_000)
                micros = boundary - previous
                listing.write(f"file '{os.path.basename(path)}'\nduration {micros // 1_000_000}.{micros % 1_000_000:06d}\n")
                previous = boundary
        operation.write_limit = source.size + operation.bound
        # Tiny encoded tails may contain no B pictures despite advertising the
        # same reorder depth. Rebuild uniform DTS rather than inheriting their
        # different local decode lead-in; keep every presentation timestamp.
        delay = int(pieces[0][4].stream.get("has_b_frames", 0))
        timestamps = (
            f"setts=pts=round(PTS*TB*{source.info.fps})*1024:"
            f"dts=(N-{delay})*1024:duration=1024:time_base=1/{source.info.fps * 1024}"
        )
        media._media([
            "-protocol_whitelist", "file", "-f", "concat", "-safe", "1", "-i", manifest,
            "-map", "0:v:0", "-c:v", "copy", "-bsf:v", timestamps,
            "-video_track_timescale", str(source.info.fps * 1024),
            "-movflags", "+faststart", "-n", partial,
        ], operation, video_id, duration, 50, 5)
        result = _part_info(partial, operation)
        if result.signature != reference:
            raise TrimCompatibilityError("Stitching changed the video configuration")
    else:
        operation.write_limit = operation.bound
        media._media(_encode_args(source, plan.start, plan.end, partial),
                     operation, video_id, duration, 5, 60)
        result = media.probe_video(partial, operation)
    if result.frames != plan.frames or result.fps != source.info.fps:
        raise TrimCompatibilityError("Trimmed output does not match the selected duration")
    if (result.width != source.info.width or result.height != source.info.height
            or encoding.sample_aspect(result.stream) != encoding.sample_aspect(source.info.stream)):
        raise media.JoinError("Trimming changed the video dimensions or display aspect ratio")
    for field in ("color_range", "color_space", "color_transfer", "color_primaries", "chroma_location"):
        if source.info.stream.get(field) not in (None, "unknown") and result.stream.get(field) != source.info.stream[field]:
            raise media.JoinError("Trimming changed the video's color information")
    media._progress(video_id, 70)
    if smart:
        combined = hashlib.sha256()
        for path, start, end, copy, _ in pieces:
            count, digest = _digest(path, operation, combined)
            if count != end - start:
                raise TrimCompatibilityError("A trimmed segment contains an unexpected number of coded pictures")
            if copy and (count, digest) != _digest(
                source.path, operation, start_packet=start, end_packet=end,
            ):
                raise TrimCompatibilityError("Copied picture data differs from the selected source interval")
        count, digest = _digest(partial, operation)
        if count != plan.frames or digest != combined.digest():
            raise TrimCompatibilityError("Stitching did not preserve every segment's picture data")
    decoded = media._media([
        "-protocol_whitelist", "file", "-err_detect", "explode", "-i", partial,
        "-map", "0:v:0", "-fps_mode", "passthrough", "-f", "null", "-",
    ], operation, video_id, duration, 70, 18)
    if decoded != plan.frames:
        raise TrimCompatibilityError("The trimmed video decoded an unexpected number of frames")
    return result, "preserved" if smart and plan.mode == "copy" else "partly_converted" if smart else "high"


def _operation(video_id: int) -> TrimOperation | None:
    with media._operations_lock:
        operation = media._operations.get(video_id)
    return operation if isinstance(operation, TrimOperation) else None


def process_trim(video_id: int):
    operation = _operation(video_id)
    if operation is None:
        return
    with operation.lock:
        if operation.done.is_set() or operation.started:
            return
        operation.started = True
    row = None
    try:
        plan = operation.trim
        if plan is None:
            raise media.JoinError("The trim request is no longer available")
        with get_db() as conn:
            row = dict(conn.execute("SELECT * FROM processed_videos WHERE id=?", (video_id,)).fetchone())
            source_row = dict(conn.execute("SELECT * FROM processed_videos WHERE id=?", (plan.source.video_id,)).fetchone())
        media._check_cancel(operation)
        if media._revision(source_row, media._source_path(source_row)) != plan.source.revision:
            raise media.JoinError("The source changed after trimming was requested")
        output, partial, thumbnail, working = _paths(row, operation.root)
        os.makedirs(working)
        try:
            result, quality = _render(plan, operation, video_id, partial, working, smart=bool(plan.copied_frames))
        except (TrimCompatibilityError, media.MediaTimestampError) as exc:
            if not plan.copied_frames:
                raise
            media._check_cancel(operation)
            if media._revision(source_row, media._source_path(source_row)) != plan.source.revision:
                raise media.JoinError("The source changed during trimming") from exc
            logger.info("Trim %s is re-encoding the selection after boundary compatibility validation failed: %s",
                        video_id, exc)
            _clear_working(working)
            if os.path.isfile(partial):
                os.remove(partial)
            os.mkdir(working)
            result, quality = _render(plan, operation, video_id, partial, working, smart=False)
        media._progress(video_id, 90)
        operation.write_limit = operation.bound
        media._media([
            "-protocol_whitelist", "file", "-i", partial,
            "-frames:v", "1", "-q:v", "2", "-n", thumbnail,
        ], operation, video_id, Fraction(plan.frames, plan.source.info.fps), 90, 8)
        if (media._revision(source_row, media._source_path(source_row)) != plan.source.revision
                or get_timelapses_path() != operation.root):
            raise media.JoinError("The source or storage changed during trimming; output was not published")
        _clear_working(working)
        with operation.lock:
            media._check_cancel(operation)
            os.replace(partial, output)
            with get_db() as conn:
                conn.execute("""
                    UPDATE processed_videos SET status='completed', progress=100,
                    file_size=?, total_frames=?, duration_seconds=?, completed_at=?,
                    thumbnail_path=?, quality=?, error_message=NULL WHERE id=? AND status='processing'
                """, (os.path.getsize(output), result.frames, float(result.duration), to_iso(get_now()),
                      make_relative(thumbnail, operation.root), quality, video_id))
            operation.done.set()
        add_event(f"Video '{row['name']}' trim completed", "video", {"video_id": video_id})
    except Exception as exc:
        # A detached-from-request worker must persist every failure and release its
        # source claim only after the bounded subprocess runner has stopped.
        cancelled = isinstance(exc, media.JoinCancelled)
        message = str(exc) if isinstance(exc, media.JoinError) else "Trimming failed; inspect the server log"
        logger.error("Video trim %s %s: %s", video_id, "cancelled" if cancelled else "failed", type(exc).__name__)
        if row:
            try:
                _cleanup(row, operation.root)
            except (OSError, ValueError, media.JoinError) as cleanup_error:
                logger.error("Trim %s cleanup failed: %s", video_id, type(cleanup_error).__name__)
                message += "; partial output cleanup failed"
                cancelled = False
        with get_db() as conn:
            if cancelled:
                conn.execute("DELETE FROM processed_videos WHERE id=?", (video_id,))
            else:
                conn.execute("UPDATE processed_videos SET status='failed', error_message=? WHERE id=?", (message, video_id))
        add_event(f"Video trim {'cancelled' if cancelled else 'failed'} (ID: {video_id})",
                  "video", {"video_id": video_id, "error": None if cancelled else message})
    finally:
        operation.done.set()
        with media._operations_lock:
            media._operations.pop(video_id, None)


def start_trim(video_id: int):
    operation = _operation(video_id)
    if operation is None:
        raise HTTPException(409, "The trim request was cancelled before it could start")
    operation.worker = threading.Thread(target=process_trim, args=(video_id,), name=f"video-trim-{video_id}", daemon=True)
    try:
        operation.worker.start()
    except RuntimeError as exc:
        cancel_trim(video_id)
        logger.error("Trim %s worker could not start: %s", video_id, type(exc).__name__)
        raise HTTPException(503, "Trimming could not start; try again shortly") from exc


def cancel_trim(video_id: int) -> bool:
    operation = _operation(video_id)
    if operation is None:
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
                    _cleanup(dict(row), operation.root)
                    conn.execute("DELETE FROM processed_videos WHERE id=?", (video_id,))
            operation.done.set()
            with media._operations_lock:
                media._operations.pop(video_id, None)
            add_event(f"Video trim cancelled (ID: {video_id})", "video", {"video_id": video_id})
    if not operation.done.wait(timeout=10):
        raise HTTPException(503, "Cancellation requested; the trim is still stopping")
    return True


def recover_trims():
    with get_db() as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM processed_videos WHERE build_source='trimmed' AND status='processing'"
        )]
    for row in rows:
        message = "Trimming was interrupted by an application restart"
        try:
            _cleanup(row, get_timelapses_path())
        except (OSError, ValueError, media.JoinError) as exc:
            logger.error("Interrupted trim %s cleanup failed: %s", row["id"], type(exc).__name__)
            message += "; partial output cleanup failed"
        with get_db() as conn:
            conn.execute("UPDATE processed_videos SET status='failed', error_message=? WHERE id=?", (message, row["id"]))
        add_event(f"Interrupted video trim failed (ID: {row['id']})", "video", {"video_id": row["id"]})


def shutdown_trims():
    with media._operations_lock:
        operations = [(video_id, operation) for video_id, operation in media._operations.items()
                      if isinstance(operation, TrimOperation)]
    for video_id, operation in operations:
        try:
            cancel_trim(video_id)
        except HTTPException as exc:
            logger.error("Trim %s is still stopping during shutdown: %s", video_id, exc.detail)
        if operation.worker and operation.worker is not threading.current_thread():
            operation.worker.join(timeout=10)
            if operation.worker.is_alive():
                logger.error("Trim %s worker did not exit within the shutdown deadline", video_id)
