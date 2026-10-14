"""Compression must preserve source bytes and leave complete atomic artifacts."""

import gzip
from pathlib import Path

import pytest

from tao2typ.storage import read_bytes, read_json, read_text, write_bytes, write_json, write_text


@pytest.mark.parametrize("suffix", [".txt", ".txt.gz"])
def test_text_round_trips_exact_utf8_and_line_endings(tmp_path: Path, suffix: str) -> None:
    text = "café π 😄\r\nsecond\nlast\r"
    path = tmp_path / "nested" / f"artifact{suffix}"
    write_text(path, text)
    assert read_text(path) == text
    assert read_bytes(path) == text.encode("utf-8")
    if suffix == ".txt":
        assert path.read_bytes() == text.encode("utf-8")
    else:
        assert gzip.decompress(path.read_bytes()) == text.encode("utf-8")


def test_gzip_is_deterministic_without_timestamp_or_filename(tmp_path: Path) -> None:
    content = b"<p>Repeated HTML</p>\r\n" * 1000
    first, second = tmp_path / "first.html.gz", tmp_path / "second.html.gz"
    write_bytes(first, content)
    write_bytes(second, content)
    encoded = first.read_bytes()
    assert encoded == second.read_bytes()
    assert encoded[4:8] == bytes(4)
    assert encoded[3] & 8 == 0  # The FNAME header flag is absent.
    assert len(encoded) < len(content) / 10
    assert read_bytes(first) == content


@pytest.mark.parametrize("suffix", [".json", ".json.gz"])
def test_json_retains_existing_encoding_and_reads_legacy_files(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"metadata{suffix}"
    value = {"title": "π", "items": [1, None, True]}
    write_json(path, value)
    assert read_json(path) == value
    assert (
        read_text(path) == '{\n  "title": "π",\n  "items": [\n    1,\n    null,\n    true\n  ]\n}\n'
    )


def test_failed_atomic_replace_leaves_old_artifact_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "artifact.json.gz"
    write_json(path, {"original": True})
    before = path.read_bytes()

    def fail_replace(self: Path, target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_json(path, {"replacement": True})
    assert path.read_bytes() == before
    assert sorted(tmp_path.iterdir()) == [path]
