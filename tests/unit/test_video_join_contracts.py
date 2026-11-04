"""Fast metadata, conversion geometry, and resource-bound contracts for joining."""
from copy import deepcopy
from fractions import Fraction

import pytest

from backend.services import video_join_conversion as conversion
from backend.services import video_joiner as joiner


@pytest.mark.parametrize("source,target,size,expected", [
    ((1280, 720, "1:1"), "1:1", (640, 360), (640, 360)),
    ((640, 360, "1:1"), "1:1", (1280, 720), (1280, 720)),
    ((1280, 720, "9:16"), "1:1", (640, 360), (360, 360)),
    ((640, 480, "1:1"), "1:1", (1920, 1080), (1440, 1080)),
    ((1080, 1920, "1:1"), "1:1", (1920, 1080), (606, 1080)),
    ((1920, 1080, "1:1"), "1:1", (1080, 1920), (1080, 606)),
    ((1280, 720, "9:16"), "9:16", (1280, 720), (1280, 720)),
    ((640, 360, None), "N/A", (1280, 720), (1280, 720)),
])
def test_fit_uses_display_aspect_and_even_dimensions(source, target, size, expected):
    width, height, sar = source
    fitted = conversion.fit_dimensions(
        {"width": width, "height": height, "sample_aspect_ratio": sar},
        {"sample_aspect_ratio": target}, *size,
    )
    assert fitted == expected
    assert all(value >= 2 and value % 2 == 0 for value in fitted)
    assert all(value <= limit for value, limit in zip(fitted, size))


@pytest.mark.parametrize("value", ["0:1", "-1:2", "1:0", "invalid"])
def test_invalid_pixel_aspect_is_rejected(value):
    with pytest.raises((ValueError, ZeroDivisionError)):
        conversion.sample_aspect({"sample_aspect_ratio": value})


@pytest.mark.parametrize("profile,expected", [
    ("High", 62_500_000), ("Main", 50_000_000), ("Constrained Baseline", 50_000_000),
])
def test_bitrate_is_limited_by_target_profile_and_level(profile, expected):
    target = {"profile": profile, "level": 41}
    assert conversion.bitrate_limit(target, 3840, 2160, 60) == expected
    small = conversion.encoded_size_bound(target, 3840, 2160, 60, 120)
    large = conversion.encoded_size_bound(target, 3840, 2160, 60, 240)
    assert small == int(Fraction(expected * 3, 8) * Fraction(6, 5)) + 1024 * 1024
    assert large > small


@pytest.mark.parametrize("size", [(1, 96), (160, 1), (161, 96), (160, 95), (0, 96), (-2, 96)])
def test_encoding_rejects_invalid_output_dimensions(size):
    stream = {"width": 160, "height": 96}
    with pytest.raises(ValueError, match="even"):
        conversion.encode_args("input.mp4", stream, stream, *size, 5, "output.mp4")


def test_encoding_arguments_keep_paths_separate_and_pin_required_settings():
    source = {"width": 160, "height": 96, "sample_aspect_ratio": "1:1"}
    target = {**source, "has_b_frames": 0, "level": 31, "profile": "High"}
    path = "/timelapses/a name's [video].mp4"
    args = conversion.encode_args(path, source, target, 160, 96, 5, "output.mp4", header_only=True)
    assert args[args.index("-i") + 1] == path
    assert args[args.index("-protocol_whitelist") + 1] == "file"
    assert args[args.index("-map") + 1] == "0:v:0"
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-crf") + 1] == "18"
    assert args[args.index("-bf") + 1] == "0"
    assert args[args.index("-frames:v") + 1] == "1"
    assert args[args.index("-video_track_timescale") + 1] == "5120"
    assert args[-2:] == ["-n", "output.mp4"]
    assert "setpts=N*1024" in args[args.index("-vf") + 1]


@pytest.fixture
def metadata(monkeypatch):
    data = {
        "format": {"tags": {"major_brand": "isom"}},
        "streams": [{
            "codec_type": "video", "codec_name": "h264", "codec_tag_string": "avc1",
            "pix_fmt": "yuv420p", "field_order": "progressive", "profile": "High",
            "width": 160, "height": 96, "r_frame_rate": "5/1", "avg_frame_rate": "5/1",
            "time_base": "1/5120", "nb_frames": "2", "duration_ts": 2048,
            "start_pts": 0, "nal_length_size": 4, "extradata_hash": "SHA256:fixture",
        }],
    }
    monkeypatch.setattr(joiner, "_metadata", lambda *args: deepcopy(data))
    monkeypatch.setattr(joiner, "_first_packet_is_idr", lambda *args: True)
    return data


@pytest.mark.parametrize("changes", [
    {"pix_fmt": "yuv420p10le"}, {"field_order": "tt"}, {"color_transfer": "smpte2084"},
    {"color_transfer": "arib-std-b67"}, {"disposition": {"attached_pic": 1}},
    {"side_data_list": [{"rotation": 90}]}, {"r_frame_rate": "30000/1001"},
    {"time_base": "0/1"}, {"avg_frame_rate": "6/1"}, {"nb_frames": "0"},
    {"start_pts": 1024}, {"duration_ts": 2047}, {"nal_length_size": 2},
    {"extradata_hash": None},
])
def test_preservation_rejects_unsupported_headers_before_packet_scan(metadata, monkeypatch, changes):
    metadata["streams"][0].update(changes)
    scans = []

    def packets(*args):
        scans.append(args)
        yield from ()

    monkeypatch.setattr(joiner, "_lines", packets)
    with pytest.raises(joiner.JoinError):
        joiner.probe_video("fixture.mp4")
    assert not scans


@pytest.mark.parametrize("changes", [
    {"pix_fmt": "yuv420p10le"}, {"field_order": "bb"}, {"color_transfer": "smpte2084"},
    {"color_transfer": "arib-std-b67"}, {"disposition": {"attached_pic": 1}},
    {"side_data_list": [{"rotation": 180}]}, {"sample_aspect_ratio": "0:1"},
    {"avg_frame_rate": "6/1"},
])
def test_conversion_does_not_weaken_unsupported_input_checks(metadata, monkeypatch, changes):
    metadata["streams"][0].update(codec_name="mpeg4", **changes)
    scans = []

    def frames(*args):
        scans.append(args)
        yield from ()

    monkeypatch.setattr(joiner, "_lines", frames)
    with pytest.raises(joiner.JoinError):
        joiner.inspect_convertible("fixture.mp4")
    assert not scans


@pytest.mark.parametrize("second", [
    "pts=0|dts=1024|duration=1024|flags=__",
    "pts=1024|dts=2048|duration=1024|flags=__",
    "pts=1024|dts=1024|duration=1023|flags=__",
    "pts=1024|dts=1024|duration=1024|flags=_D",
    "pts=N/A|dts=1024|duration=1024|flags=__",
])
def test_packet_inspection_rejects_discontinuity_discard_and_bad_timing(metadata, monkeypatch, second):
    def packets(*args):
        yield "pts=0|dts=0|duration=1024|flags=K_|pos=0|size=6"
        yield second

    monkeypatch.setattr(joiner, "_lines", packets)
    with pytest.raises(joiner.JoinError):
        joiner.probe_video("fixture.mp4")


def test_metadata_is_bounded_even_when_probe_returns_many_short_lines(monkeypatch):
    def output(*args):
        for _ in range(66):
            yield " " * 1024

    monkeypatch.setattr(joiner, "_lines", output)
    with pytest.raises(joiner.JoinError, match="oversized"):
        joiner._metadata("fixture.mp4", None)


@pytest.mark.parametrize("payload,expected", [
    (b"\x00\x00\x00\x02\x65\x88", True),
    (b"\x00\x00\x00\x02\x06\x00\x00\x00\x00\x02\x65\x88", True),
    (b"\x00\x00\x00\x02\x41\x88\x00\x00\x00\x02\x65\x88", False),
    (b"\x00\x00\x00\x08\x65\x88", False),
    (b"\x00\x00\x00\x00\x65", False),
])
def test_idr_parser_requires_a_complete_independent_first_picture(tmp_path, payload, expected):
    path = tmp_path / "packet.bin"
    path.write_bytes(payload)
    assert joiner._first_packet_is_idr(str(path), 0, len(payload)) is expected
    assert not joiner._first_packet_is_idr(str(path), -1, len(payload))
    assert not joiner._first_packet_is_idr(str(path), 0, 32 * 1024 * 1024 + 1)


def test_selective_plan_weights_repetitions_and_preflights_only_distinct_sources(metadata, monkeypatch):
    def source(video_id, frames, size=(160, 96)):
        stream = {**metadata["streams"][0], "width": size[0], "height": size[1],
                  "extradata_hash": f"SHA256:group-{video_id}"}
        info = joiner._video_info(stream, 5, Fraction(1, 5120), frames)
        return joiner.Source(video_id, f"Source {video_id}", f"/timelapses/{video_id}.mp4",
                             str(video_id) * 64, 1024, info)

    a, b, outlier = source(1, 20), source(2, 30), source(3, 8, (96, 64))
    sources = [a, a, b, outlier]
    timing_calls = []
    encoding_calls = []

    def retime(source, fps):
        timing_calls.append(source.video_id)
        return source.info

    def encode(source, target, width, height, fps):
        encoding_calls.append(source.video_id)
        return joiner._video_info(target, fps, Fraction(1, fps * 1024), source.info.frames)

    monkeypatch.setattr(joiner, "_retimed_info", retime)
    monkeypatch.setattr(joiner, "_encoded_header", encode)
    report = {"compatible": False, "output_fps": 5,
              "clips": [{"video_id": item.video_id} for item in sources]}
    plan = joiner._conversion_plan(report, sources, "160x96")
    assert plan.mode == report["mode"] == "selective"
    assert plan.target.signature == a.info.signature  # 40 ordered frames beat B's 30.
    assert plan.actions == {1: "copy", 2: "reencode", 3: "reencode"}
    assert timing_calls == [1, 2]
    assert encoding_calls == [2, 3]
    assert report["temporary_bytes"] == plan.bounds[2] + plan.bounds[3]
