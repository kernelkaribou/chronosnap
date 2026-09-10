"""
Regression tests for the 7z archive extraction fix in import_service.py.

The pre-fix implementation called `py7zr.SevenZipFile.read()`, which does
not exist in the pinned py7zr==1.1.3 API at all -- every 7z import attempt
raised AttributeError (caught by extract_archive()'s outer try/except and
surfaced as a generic "Extraction error", so 7z import was completely
non-functional, not just missing its bomb check).

Separately, even a hypothetical `.read()`-based implementation would
decompress the *entire* archive into memory before ever checking per-entry
extraction limits -- unlike zip/tar/rar, which check each entry's declared
size before decompressing it. The fix: read entry metadata via list() (a
pure header read, no decompression), apply the same validation/limit
checks as the other formats to build an allow-list, then extract() only
the allowed entries -- so anything that would blow the limits is filtered
out and never decompressed.
"""
import os
import sys
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import py7zr

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.services import import_service  # noqa: E402


def _make_7z(tmp_path, entries: dict) -> str:
    """Build a real 7z archive at tmp_path/'test.7z' with entries {arcname: bytes}."""
    archive_path = str(tmp_path / "test.7z")
    with py7zr.SevenZipFile(archive_path, 'w') as sz:
        for arcname, data in entries.items():
            sz.writestr(data, arcname)
    return archive_path


@pytest.fixture
def dest_dir(tmp_path):
    d = tmp_path / "dest"
    d.mkdir()
    return str(d)


def test_extract_7z_valid_archive_extracts_all_files(tmp_path, dest_dir):
    archive_path = _make_7z(tmp_path, {
        "hello.txt": b"hello world",
        "sub/nested.txt": b"nested content",
    })
    stats = import_service.ExtractionStats(os.path.getsize(archive_path))
    errors, nested = [], []

    import_service._extract_7z(archive_path, dest_dir, stats, errors, nested)

    assert errors == []
    assert (Path(dest_dir) / "hello.txt").read_bytes() == b"hello world"
    assert (Path(dest_dir) / "sub" / "nested.txt").read_bytes() == b"nested content"
    assert stats.file_count == 2
    assert stats.total_extracted == len(b"hello world") + len(b"nested content")


def test_extract_7z_oversized_entry_is_never_decompressed(tmp_path, dest_dir):
    """A highly-compressible huge file (classic decompression-bomb shape)
    must be excluded from extract()'s targets -- i.e. never decompressed --
    not merely deleted after the fact."""
    bomb_bytes = b"\x00" * (2 * 1024 * 1024)  # 2MB of zeros compresses to a few hundred bytes
    archive_path = _make_7z(tmp_path, {
        "normal.txt": b"small and fine",
        "bomb.bin": bomb_bytes,
    })
    archive_size = os.path.getsize(archive_path)
    assert (len(bomb_bytes) / archive_size) > import_service.MAX_EXTRACTION_RATIO, \
        "test fixture must actually exceed the ratio limit to be meaningful"

    stats = import_service.ExtractionStats(archive_size)
    errors, nested = [], []

    real_extract = py7zr.SevenZipFile.extract
    captured_targets = {}

    def spy_extract(self, path=None, targets=None, **kwargs):
        captured_targets['targets'] = list(targets) if targets else None
        return real_extract(self, path=path, targets=targets, **kwargs)

    with patch.object(py7zr.SevenZipFile, "extract", spy_extract):
        import_service._extract_7z(archive_path, dest_dir, stats, errors, nested)

    assert "bomb.bin" not in (captured_targets.get('targets') or []), \
        "oversized entry must be excluded from extract() targets, not decompressed then discarded"
    assert not (Path(dest_dir) / "bomb.bin").exists()
    assert (Path(dest_dir) / "normal.txt").read_bytes() == b"small and fine"
    assert any("bomb.bin" in e and "exceeds extraction limits" in e for e in errors)


def test_extract_7z_rejects_path_traversal_entry(tmp_path, dest_dir):
    # py7zr's own writer refuses to write a path-traversal arcname
    # (check_archive_path), so a real py7zr-authored archive can never
    # contain one. A maliciously-crafted archive from another tool could
    # still contain one, though -- bypass the writer-side guard here so
    # this test proves *our* extraction-side validation (_validate_archive
    # _entry) is what actually blocks it, not just py7zr's own guard.
    with patch("py7zr.py7zr.check_archive_path", return_value=True):
        archive_path = _make_7z(tmp_path, {
            "../../etc/evil.txt": b"escape attempt",
            "safe.txt": b"safe content",
        })
    stats = import_service.ExtractionStats(os.path.getsize(archive_path))
    errors, nested = [], []

    import_service._extract_7z(archive_path, dest_dir, stats, errors, nested)

    # Nothing must exist outside dest_dir.
    escaped_path = Path(dest_dir).parent.parent / "etc" / "evil.txt"
    assert not escaped_path.exists()
    assert (Path(dest_dir) / "safe.txt").read_bytes() == b"safe content"
    assert stats.file_count == 1


def test_extract_7z_respects_max_file_count(tmp_path, dest_dir, monkeypatch):
    monkeypatch.setattr(import_service, "MAX_FILE_COUNT", 2)
    archive_path = _make_7z(tmp_path, {
        "a.txt": b"a", "b.txt": b"b", "c.txt": b"c",
    })
    stats = import_service.ExtractionStats(os.path.getsize(archive_path))
    errors, nested = [], []

    import_service._extract_7z(archive_path, dest_dir, stats, errors, nested)

    assert stats.file_count == 2
    extracted_names = {p.name for p in Path(dest_dir).iterdir()}
    assert len(extracted_names) == 2


def test_extract_7z_flags_nested_archive_for_reprocessing(tmp_path, dest_dir):
    inner_zip_path = tmp_path / "inner.zip"
    import zipfile
    with zipfile.ZipFile(inner_zip_path, 'w') as zf:
        zf.writestr("deep.txt", "deep content")

    archive_path = _make_7z(tmp_path, {
        "inner.zip": inner_zip_path.read_bytes(),
    })
    stats = import_service.ExtractionStats(os.path.getsize(archive_path))
    errors, nested = [], []

    import_service._extract_7z(archive_path, dest_dir, stats, errors, nested)

    assert nested == [str(Path(dest_dir) / "inner.zip")]
    assert (Path(dest_dir) / "inner.zip").exists()


def test_extract_7z_missing_py7zr_records_error_without_raising(tmp_path, dest_dir, monkeypatch):
    monkeypatch.setitem(sys.modules, "py7zr", None)
    stats = import_service.ExtractionStats(100)
    errors, nested = [], []

    import_service._extract_7z("/nonexistent.7z", dest_dir, stats, errors, nested)

    assert any("py7zr" in e for e in errors)


def test_extract_archive_dispatches_7z_extension_to_extract_7z(tmp_path, dest_dir):
    """End-to-end via the public extract_archive() dispatcher, matching how
    the real import flow invokes this (not calling _extract_7z directly)."""
    archive_path = _make_7z(tmp_path, {"hello.txt": b"hi"})
    # extract_archive() dispatches on the actual archive_path's extension.
    real_archive_path = str(tmp_path / "test_import.7z")
    shutil.copy(archive_path, real_archive_path)

    result = import_service.extract_archive(real_archive_path, dest_dir)

    assert result['errors'] == []
    assert result['extracted_count'] == 1
    assert (Path(dest_dir) / "hello.txt").read_bytes() == b"hi"
