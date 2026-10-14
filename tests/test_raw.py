"""Raw fetches remain replayable independently of parser or renderer changes."""

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tao2typ import raw
from tao2typ.cli import main
from tao2typ.raw import RawCapture, load_raw_capture
from tao2typ.storage import read_bytes, read_json, read_text, write_json, write_text


def manifest(output: Path, capture: RawCapture) -> dict[str, Any]:
    return read_json(output / capture.manifest_path)


def test_captures_exact_utf8_parser_input_and_metadata(tmp_path: Path) -> None:
    html = '<!doctype html>\r\n<p title="L^p">café π 😄 &amp;nbsp;</p>\r\n'
    capture = RawCapture(
        tmp_path,
        "lp-spaces",
        "https://example.com/lp-spaces/",
        published_at="2009-01-09T03:04:05+00:00",
        modified_at="2010-01-09T03:04:05+00:00",
    )
    assert manifest(tmp_path, capture)["pages"] == []
    assert manifest(tmp_path, capture)["complete"] is False
    capture.add(
        html,
        "https://example.com/lp-spaces/",
        requested_url="http://example.com/lp-spaces/",
        fetched_at="2026-10-13T12:00:00+00:00",
    )
    capture.finish()
    result = manifest(tmp_path, capture)
    assert result["schema_version"] == 1
    assert result["post_url"] == "https://example.com/lp-spaces/"
    assert result["published_at"] == "2009-01-09T03:04:05+00:00"
    assert result["modified_at"] == "2010-01-09T03:04:05+00:00"
    assert result["complete"] is True
    page = result["pages"][0]
    assert read_bytes(tmp_path / page["path"]) == html.encode("utf-8")
    assert page["path"].endswith(".html.gz")
    assert capture.manifest_path.endswith("manifest.json.gz")
    assert page["bytes_sha256"] == hashlib.sha256(html.encode("utf-8")).hexdigest()
    assert page["bytes"] == len(html.encode("utf-8"))
    assert page["requested_url"] == "http://example.com/lp-spaces/"
    assert page["url"] == "https://example.com/lp-spaces/"
    assert page["fetched_at"] == "2026-10-13T12:00:00+00:00"
    _, loaded, root = load_raw_capture(tmp_path / capture.manifest_path)
    assert loaded == [html] and root == tmp_path.resolve()


def test_replay_rejects_modified_bytes_or_partial_capture(tmp_path: Path) -> None:
    capture = RawCapture(tmp_path, "post", "https://example.com/post/")
    capture.add("original", "https://example.com/post/")
    with pytest.raises(ValueError, match="complete"):
        load_raw_capture(tmp_path / capture.manifest_path)
    capture.finish()
    page = manifest(tmp_path, capture)["pages"][0]
    write_text(tmp_path / page["path"], "altered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_raw_capture(tmp_path / capture.manifest_path)


def test_parse_raw_replays_all_pages_and_keeps_literal_source_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://terrytao.wordpress.com/2009/01/09/245b-notes-3-lp-spaces/"
    capture = RawCapture(tmp_path, "lp-spaces", url, published_at="2009-01-09T12:00:00+00:00")
    first = r"""<h1 class="post-title">L^p spaces</h1><div class="post-content">
      <p><img class="latex" alt="L^p"></p></div><div id="comments">
      <div class="comment" id="comment-1"><div class="comment-content">First.</div></div>
      </div>"""
    second = (
        '<div id="comments"><div class="comment" id="comment-2">'
        '<div class="comment-content">Second.</div></div></div>'
    )
    capture.add(first, url)
    capture.add(second, url + "comment-page-1/")
    capture.finish()
    replay = tmp_path / "replay.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["tao2typ", "parse-raw", str(tmp_path / capture.manifest_path), "--output", str(replay)],
    )
    assert main() == 0
    result = json.loads(replay.read_text())
    assert result["title"] == "L^p spaces"
    assert "title_nodes" not in result
    assert [c["id"] for c in result["comments"]] == ["comment-1", "comment-2"]
    assert result["source_manifest"] == capture.manifest_path


def test_same_bytes_share_blob_without_overwriting_prior_capture(tmp_path: Path) -> None:
    first = RawCapture(tmp_path, "post", "https://example.com/post/")
    first.add("same HTML", "https://example.com/post/")
    first.finish()
    before = (tmp_path / first.manifest_path).read_bytes()
    first_page = manifest(tmp_path, first)["pages"][0]
    before_blob = (tmp_path / first_page["path"]).stat()

    second = RawCapture(tmp_path, "post", "https://example.com/post/")
    second.add("same HTML", "https://example.com/post/")
    second.finish()
    assert first.manifest_path != second.manifest_path
    assert (tmp_path / first.manifest_path).read_bytes() == before
    assert manifest(tmp_path, second)["pages"][0]["path"] == first_page["path"]
    after_blob = (tmp_path / first_page["path"]).stat()
    assert after_blob.st_ino == before_blob.st_ino
    assert after_blob.st_mtime_ns == before_blob.st_mtime_ns
    assert len(list((tmp_path / "raw" / "pages").glob("*.html.gz"))) == 1


def test_page_order_and_urls_preserve_comment_pagination(tmp_path: Path) -> None:
    capture = RawCapture(tmp_path, "post", "https://example.com/post/")
    for index, html in enumerate(["article", "first comments", "second comments"]):
        capture.add(html, f"https://example.com/post/comment-page-{index + 1}/")
    capture.finish()
    pages = manifest(tmp_path, capture)["pages"]
    assert [page["url"] for page in pages] == [
        f"https://example.com/post/comment-page-{index}/" for index in (1, 2, 3)
    ]
    assert [read_text(tmp_path / page["path"]) for page in pages] == [
        "article",
        "first comments",
        "second comments",
    ]


def test_failed_fetch_retains_partial_pages_and_error(tmp_path: Path) -> None:
    capture = RawCapture(tmp_path, "post", "https://example.com/post/")
    capture.add("article", "https://example.com/post/")
    capture.fail("Timed out fetching comment page 2")
    result = manifest(tmp_path, capture)
    assert result["complete"] is False
    assert result["error"] == "Timed out fetching comment page 2"
    assert len(result["pages"]) == 1
    assert read_text(tmp_path / result["pages"][0]["path"]) == "article"
    assert "finished_at" in result


@pytest.mark.parametrize("finalizer", ["finish", "fail"])
def test_finalized_manifest_cannot_be_mutated(tmp_path: Path, finalizer: str) -> None:
    capture = RawCapture(tmp_path, "post", "https://example.com/post/")
    if finalizer == "finish":
        capture.finish()
    else:
        capture.fail("failed")
    before = (tmp_path / capture.manifest_path).read_bytes()
    with pytest.raises(RuntimeError, match="finalized"):
        capture.add("new", "https://example.com/post/")
    with pytest.raises(RuntimeError, match="finalized"):
        capture.finish()
    with pytest.raises(RuntimeError, match="finalized"):
        capture.fail("another error")
    assert (tmp_path / capture.manifest_path).read_bytes() == before


def test_coincident_timestamps_create_separate_manifests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(raw, "_now", lambda: datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC))
    first = RawCapture(tmp_path, "post", "https://example.com/post/")
    second = RawCapture(tmp_path, "post", "https://example.com/post/")
    assert first.manifest_path == "raw/post/20261013T120000.000000Z/manifest.json.gz"
    assert second.manifest_path == "raw/post/20261013T120000.000001Z/manifest.json.gz"
    assert (tmp_path / first.manifest_path).is_file()
    assert (tmp_path / second.manifest_path).is_file()


@pytest.mark.parametrize("stem", ["", ".", "..", "../post", "a/post", r"a\post", "a\0b"])
def test_rejects_stems_outside_capture_namespace(tmp_path: Path, stem: str) -> None:
    with pytest.raises(ValueError, match="simple filename"):
        RawCapture(tmp_path, stem, "https://example.com/post/")
    assert not (tmp_path / "raw").exists()


def test_rejects_corrupted_existing_blob_instead_of_replacing_it(tmp_path: Path) -> None:
    html = "article"
    digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
    pages = tmp_path / "raw" / "pages"
    pages.mkdir(parents=True)
    blob = pages / f"{digest}.html.gz"
    blob.write_bytes(b"damaged data")
    capture = RawCapture(tmp_path, "post", "https://example.com/post/")
    with pytest.raises(ValueError, match="content hash"):
        capture.add(html, "https://example.com/post/")
    assert blob.read_bytes() == b"damaged data"
    assert manifest(tmp_path, capture)["pages"] == []
    assert not list(pages.glob("*.tmp"))


def test_legacy_plain_capture_replays_without_rewriting_it(tmp_path: Path) -> None:
    capture = RawCapture(tmp_path, "post", "https://example.com/post/")
    html = "original\r\nπ"
    capture.add(html, "https://example.com/post/")
    capture.finish()
    metadata = manifest(tmp_path, capture)
    page = metadata["pages"][0]
    compressed_blob = tmp_path / page["path"]
    page["path"] = page["path"].removesuffix(".gz")
    plain_blob = tmp_path / page["path"]
    plain_blob.write_bytes(read_bytes(compressed_blob))
    compressed_blob.unlink()
    plain_manifest = tmp_path / capture.manifest_path.removesuffix(".gz")
    write_json(plain_manifest, metadata)
    (tmp_path / capture.manifest_path).unlink()
    before = plain_manifest.read_bytes(), plain_blob.read_bytes()

    loaded_metadata, pages, output = load_raw_capture(plain_manifest)

    assert loaded_metadata == metadata
    assert pages == [html] and output == tmp_path.resolve()
    assert (plain_manifest.read_bytes(), plain_blob.read_bytes()) == before


@pytest.mark.parametrize("damage", ["invalid-gzip", "different-content"])
def test_compressed_blob_replay_checks_original_bytes(tmp_path: Path, damage: str) -> None:
    capture = RawCapture(tmp_path, "post", "https://example.com/post/")
    capture.add("article", "https://example.com/post/")
    capture.finish()
    blob = tmp_path / manifest(tmp_path, capture)["pages"][0]["path"]
    if damage == "invalid-gzip":
        blob.write_bytes(b"not gzip")
    else:
        write_text(blob, "changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_raw_capture(tmp_path / capture.manifest_path)


def test_rejects_raw_blob_symlink_even_with_matching_bytes(tmp_path: Path) -> None:
    capture = RawCapture(tmp_path, "post", "https://example.com/post/")
    capture.add("article", "https://example.com/post/")
    capture.finish()
    blob = tmp_path / manifest(tmp_path, capture)["pages"][0]["path"]
    elsewhere = tmp_path / "elsewhere.gz"
    blob.rename(elsewhere)
    blob.symlink_to(elsewhere)
    with pytest.raises(ValueError, match="escapes"):
        load_raw_capture(tmp_path / capture.manifest_path)
    duplicate = RawCapture(tmp_path, "post", "https://example.com/post/")
    with pytest.raises(ValueError, match="content hash"):
        duplicate.add("article", "https://example.com/post/")


def test_rejects_manifest_symlink(tmp_path: Path) -> None:
    capture = RawCapture(tmp_path, "post", "https://example.com/post/")
    capture.add("article", "https://example.com/post/")
    capture.finish()
    path = tmp_path / capture.manifest_path
    elsewhere = tmp_path / "elsewhere.gz"
    path.rename(elsewhere)
    path.symlink_to(elsewhere)
    with pytest.raises(ValueError, match="symlink"):
        load_raw_capture(path)


def test_rejects_compressed_page_path_traversal(tmp_path: Path) -> None:
    capture = RawCapture(tmp_path, "post", "https://example.com/post/")
    capture.add("article", "https://example.com/post/")
    capture.finish()
    path = tmp_path / capture.manifest_path
    metadata = manifest(tmp_path, capture)
    metadata["pages"][0]["path"] = "raw/pages/../escape.html.gz"
    write_json(path, metadata)
    with pytest.raises(ValueError, match="Invalid raw page path"):
        load_raw_capture(path)
