"""A settled archive shrinks without losing replay inputs or PDF provenance."""

import hashlib
from pathlib import Path

import pytest

from tao2typ.compression import compress_archive, compress_completed_reports
from tao2typ.ir import Node, Post
from tao2typ.raw import RawCapture, load_raw_capture
from tao2typ.state import ArchiveState, PostState
from tao2typ.storage import read_bytes, read_json, read_text, write_bytes, write_json


def test_finished_reports_compress_without_touching_live_reports(tmp_path: Path) -> None:
    finished = tmp_path / "jobs/finished.json"
    live = tmp_path / "jobs/live.json"
    payload = b'{"finished_at":"2026-10-14T09:00:00Z", "error":"caf\xc3\xa9"}\r\n'
    write_bytes(finished, payload)
    write_json(live, {"started_at": "2026-10-14T09:00:00Z"})
    assert compress_completed_reports(tmp_path) == 1
    assert read_bytes(finished.with_suffix(".json.gz")) == payload
    assert not finished.exists()
    assert live.exists() and not live.with_suffix(".json.gz").exists()
    assert compress_completed_reports(tmp_path) == 0


def test_conflicting_finished_report_is_retained(tmp_path: Path) -> None:
    source = tmp_path / "jobs/finished.json"
    target = source.with_suffix(".json.gz")
    write_json(source, {"finished_at": "2026-10-14", "results": [1]})
    write_json(target, {"finished_at": "2026-10-14", "results": [2]})
    with pytest.raises(ValueError, match="Conflicting"):
        compress_completed_reports(tmp_path)
    assert read_json(source)["results"] == [1]
    assert read_json(target)["results"] == [2]


def archive_fixture(output: Path) -> tuple[bytes, str, str]:
    url = "https://terrytao.wordpress.com/2009/01/09/lp-spaces/"
    original = ('<p title="L^p">café π &amp;nbsp;</p>\r\n' * 200).encode()
    capture = RawCapture(output, "lp-spaces", url)
    capture.add(original.decode(), url)
    capture.finish()
    metadata = read_json(output / capture.manifest_path)
    page = metadata["pages"][0]
    plain_page = page["path"].removesuffix(".gz")
    write_bytes(output / plain_page, original)
    (output / page["path"]).unlink()
    page["path"] = plain_page
    manifest = capture.manifest_path.removesuffix(".gz")
    write_json(output / manifest, metadata)
    (output / capture.manifest_path).unlink()
    converted = "2026-10-13T15:00:00+00:00"
    post = Post("L^p spaces", url, body=[Node("math", r"L^p([0,1])")])
    post.source_manifest = manifest
    post.converted_at = converted
    write_json(output / "ir/lp-spaces.json", post.to_dict())
    write_bytes(output / "typst/lp-spaces.typ", b"// L^p stays literal\n" * 200)
    pdf = output / "pdfs/lp-spaces.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-immutable")
    record = PostState(1, url, post.title, "2009-01-09T12:00:00+00:00", "2009-01-09T12:00:00+00:00")
    record.pdf = "pdfs/lp-spaces.pdf"
    record.ir = "ir/lp-spaces.json"
    record.typst = "typst/lp-spaces.typ"
    record.source_manifest = manifest
    record.converted_at = converted
    state = ArchiveState(posts={"1": record}, daily_attempts={"2026-10-13": 10})
    state.save(output / "state.json")
    write_json(
        output / "jobs/completed.json",
        {"finished_at": converted, "results": [{"source_manifest": manifest, "ir": record.ir}]},
    )
    write_json(output / "jobs/live.json", {"started_at": converted})
    write_bytes(output / "queue.json", b'{"posts":[]}\n')
    return original, plain_page, manifest


def test_compression_preserves_original_inputs_dates_pdfs_and_relinks_history(
    tmp_path: Path,
) -> None:
    original, plain_page, manifest = archive_fixture(tmp_path)
    before = ArchiveState.load(tmp_path / "state.json")
    assert before.posts["1"].pdf is not None
    pdf_digest = hashlib.sha256((tmp_path / before.posts["1"].pdf).read_bytes()).hexdigest()
    result = compress_archive(tmp_path)
    after = ArchiveState.load(tmp_path / "state.json")
    assert after.posts["1"].pdf is not None
    assert result["bytes_saved"] > 0
    assert result["unfinished_reports_retained"] == 1
    assert after.daily_attempts == before.daily_attempts
    assert after.posts["1"].converted_at == before.posts["1"].converted_at
    assert after.posts["1"].source_manifest == manifest + ".gz"
    assert after.posts["1"].ir == "ir/lp-spaces.json.gz"
    assert after.posts["1"].typst == "typst/lp-spaces.typ.gz"
    assert hashlib.sha256((tmp_path / after.posts["1"].pdf).read_bytes()).hexdigest() == pdf_digest
    _, pages, _ = load_raw_capture(tmp_path / after.posts["1"].source_manifest)
    assert pages == [original.decode()]
    assert read_json(tmp_path / after.posts["1"].ir)["body"][0]["text"] == r"L^p([0,1])"
    assert read_text(tmp_path / after.posts["1"].typst) == "// L^p stays literal\n" * 200
    history = read_json(tmp_path / "jobs/completed.json.gz")
    assert history["results"][0] == {
        "source_manifest": manifest + ".gz",
        "ir": "ir/lp-spaces.json.gz",
    }
    assert (tmp_path / "jobs/live.json").exists()
    assert (tmp_path / "queue.json").read_bytes() == b'{"posts":[]}\n'
    assert not (tmp_path / plain_page).exists()
    second = compress_archive(tmp_path)
    assert (
        second["files_created"]
        == second["files_rewritten"]
        == second["plaintext_files_removed"]
        == 0
    )


def test_state_referenced_gzip_supersedes_an_obsolete_plain_refresh_copy(tmp_path: Path) -> None:
    archive_fixture(tmp_path)
    compress_archive(tmp_path)
    canonical = read_bytes(tmp_path / "ir/lp-spaces.json.gz")
    write_json(tmp_path / "ir/lp-spaces.json", {"title": "old version", "body": "old" * 2000})
    compress_archive(tmp_path)
    assert read_bytes(tmp_path / "ir/lp-spaces.json.gz") == canonical
    assert not (tmp_path / "ir/lp-spaces.json").exists()


def test_checksum_failure_aborts_before_replacing_any_file_or_state(tmp_path: Path) -> None:
    _, page, _ = archive_fixture(tmp_path)
    (tmp_path / page).write_bytes(b"tampered")
    before = (tmp_path / "state.json").read_bytes()
    with pytest.raises(ValueError, match="checksum mismatch"):
        compress_archive(tmp_path)
    assert (tmp_path / "state.json").read_bytes() == before
    assert (tmp_path / "ir/lp-spaces.json").exists()
    assert not (tmp_path / "ir/lp-spaces.json.gz").exists()


def test_artifact_symlink_is_rejected_without_following_it(tmp_path: Path) -> None:
    archive_fixture(tmp_path)
    source = tmp_path / "typst/lp-spaces.typ"
    source.unlink()
    source.symlink_to(tmp_path / "state.json")
    with pytest.raises(ValueError, match="Unsafe compression input"):
        compress_archive(tmp_path)
