"""Local regeneration is checkpointed, cache-only, and preserves source freshness."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import httpx
import pytest

from tao2typ import pipeline
from tao2typ.ir import Node, Post
from tao2typ.offline import _asset_index, _cached_assets, run_offline_regeneration
from tao2typ.raw import RawCapture
from tao2typ.source import BlogClient
from tao2typ.state import ArchiveState, PostState
from tao2typ.storage import read_json, read_text, write_json, write_text

OLD = "2026-10-13T00:00:00+00:00"
SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    b'<rect width="10" height="10"/></svg>'
)


def seed(output: Path) -> ArchiveState:
    state = ArchiveState(daily_attempts={"2026-10-14": 10})
    for identifier in (1, 2, 3):
        url = f"https://terrytao.wordpress.com/2024/01/31/post-{identifier}/"
        capture = RawCapture(output, f"post-{identifier}", url, published_at=OLD, modified_at=OLD)
        capture.add(
            f'<h1>Post {identifier}</h1><div class="entry-content"><p>Saved text.</p>'
            '<img src="https://example.com/missing.png" alt="Diagram"></div>',
            url,
        )
        capture.finish()
        post = Post(f"Post {identifier}", url, OLD, OLD, body=[Node("text", "Saved text")])
        post.converted_at, post.source_manifest = OLD, capture.manifest_path
        row = PostState(
            identifier,
            url,
            post.title,
            OLD,
            OLD,
            last_checked_at=OLD,
            next_check_at="2027-10-13T00:00:00+00:00",
            pending_comments=[url + "#comment-10"],
            seen_comments=[url + "#comment-9"],
            source_manifest=capture.manifest_path,
        )
        if identifier != 3:
            row.ir, row.typst, row.pdf = (
                f"ir/post-{identifier}.json.gz",
                f"typst/post-{identifier}.typ.gz",
                f"pdfs/post-{identifier}.pdf",
            )
            row.converted_at, row.converted_modified_at = OLD, OLD
            write_json(output / row.ir, post.to_dict())
            write_text(output / row.typst, "Old Typst")
            (output / row.pdf).parent.mkdir(exist_ok=True)
            (output / row.pdf).write_bytes(b"old PDF")
        else:
            row.last_error = "Image unavailable"
            row.last_checked_at = None
        state.posts[str(identifier)] = row
    state.save(output / "state.json")
    return state


def test_resume_skips_successes_and_preserves_freshness_events_quota_and_raw_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "archive"
    original = seed(output)
    raw = {path: path.read_bytes() for path in (output / "raw").rglob("*.gz")}
    compiled: list[str] = []

    def compiler(source: Path, pdf: Path, root: Path) -> None:
        compiled.append(pdf.name)
        pdf.write_bytes(b"%PDF-test" + source.read_bytes())

    def no_http(*args: object, **kwargs: object) -> None:
        pytest.fail("Offline regeneration must never request source or image URLs")

    monkeypatch.setattr(pipeline, "compile_typst", compiler)
    monkeypatch.setattr(BlogClient, "_request", no_http)
    first = run_offline_regeneration(output, max_posts=1, workers=2)
    assert first["due"] == 3 and first["converted"] == 1 and first["remaining"] == 2
    assert "finished_at" not in first
    queued = read_json(output / "queue.json")["posts"]
    assert [entry["id"] for entry in queued if "local-regeneration" in entry["reasons"]] == [2, 3]
    assert "recent-comment" in next(entry for entry in queued if entry["id"] == 1)["reasons"]
    # Replay a durable successful job record when the process died before state save.
    first_state = ArchiveState.load(output / "state.json")
    first_state.posts["1"] = original.posts["1"]
    first_state.save(output / "state.json")
    resumed = run_offline_regeneration(output, workers=2)
    assert resumed["converted"] == 3 and resumed["failed"] == 0 and resumed["remaining"] == 0
    assert len(compiled) == len(set(compiled)) == 3
    current = ArchiveState.load(output / "state.json")
    assert current.daily_attempts == original.daily_attempts
    for identifier, row in current.posts.items():
        before = original.posts[identifier]
        assert row.last_checked_at == before.last_checked_at
        assert row.next_check_at == before.next_check_at
        assert (
            row.pending_comments == before.pending_comments
            and row.seen_comments == before.seen_comments
        )
        assert row.source_manifest == before.source_manifest
        assert row.converted_at != OLD and row.converter_fingerprint
    assert all(path.read_bytes() == content for path, content in raw.items())
    queued = read_json(output / "queue.json")["posts"]
    assert all("local-regeneration" not in entry["reasons"] for entry in queued)
    assert "initial" in next(entry for entry in queued if entry["id"] == 3)["reasons"]
    missing = current.posts["3"]
    assert missing.typst is not None
    text = read_text(output / missing.typst)
    assert "Image unavailable" in text and "https://example.com/missing.png" in text


def test_cache_rejects_wrong_extension_or_hash_and_recovers_a_stale_explicit_path(
    tmp_path: Path,
) -> None:
    output = tmp_path
    (output / "assets").mkdir()
    url = "https://example.com/diagram.jpg"
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    data = b"\xff\xd8\xffJFIF image bytes"
    name = url_hash + "-" + hashlib.sha256(data).hexdigest()
    wrong, good = output / "assets" / (name + ".png"), output / "assets" / (name + ".jpg")
    wrong.write_bytes(data)
    good.write_bytes(data)
    corrupt = output / "assets" / (url_hash + "-" + "0" * 64 + ".jpg")
    corrupt.write_bytes(data)
    os.utime(wrong, ns=(500, 500))
    os.utime(good, ns=(100, 100))
    os.utime(corrupt, ns=(1000, 1000))
    index = _asset_index(output)
    assert index[url_hash] == good
    image = Node(
        "image",
        attrs={
            "url": url,
            "asset_path": wrong.relative_to(output).as_posix(),
            "unavailable": "true",
        },
    )
    post = Post("Image", "https://example.com/", body=[image])
    assets = _cached_assets(post, output, index)
    assert assets[url] == "../" + good.relative_to(output).as_posix()
    assert "unavailable" not in image.attrs


def test_supplied_cached_assets_bypass_fetch_and_hash_the_actual_cached_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "assets/image.svg"
    image.parent.mkdir()
    image.write_bytes(SVG)
    cached, missing = "https://example.com/cached.svg", "https://example.com/missing.png"
    post = Post(
        "Assets",
        "https://example.com/",
        body=[Node("image", attrs={"url": cached}), Node("image", attrs={"url": missing})],
    )

    def compiler(source: Path, pdf: Path, root: Path) -> None:
        text = source.read_text()
        assert "../assets/image.svg" in text
        assert missing in text and "Image unavailable" in text
        pdf.write_bytes(b"%PDF-test")

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("Supplied cached assets must bypass all client downloads")

    monkeypatch.setattr(pipeline, "compile_typst", compiler)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        first = pipeline.save_post(
            post, "assets", tmp_path, client, assets={cached: "../assets/image.svg"}
        )
        image.write_bytes(SVG.replace(b"10", b"11"))
        second = pipeline.save_post(
            post, "assets", tmp_path, client, assets={cached: "../assets/image.svg"}
        )
    assert first["content_sha256"] != second["content_sha256"]


def test_subset_regeneration_keeps_unselected_existing_queue_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "archive"
    seed(output)
    entry = {
        "id": 3,
        "url": "https://example.com/",
        "title": "Due",
        "reasons": ["previous-failure"],
    }
    write_json(output / "queue.json", {"posts": [entry]})
    monkeypatch.setattr(
        pipeline, "compile_typst", lambda source, pdf, root: pdf.write_bytes(b"PDF")
    )
    report = run_offline_regeneration(output, post_ids=[1], workers=1)
    assert report["converted"] == 1
    assert entry in read_json(output / "queue.json")["posts"]
