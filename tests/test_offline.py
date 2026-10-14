"""Local regeneration is checkpointed, cache-only, and preserves source freshness."""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Self

import httpx
import pytest

from tao2typ import offline, pipeline, raw
from tao2typ.ir import Node, Post
from tao2typ.offline import (
    _acknowledge_initial_capture,
    _asset_index,
    _cached_assets,
    reconcile_initial_checks,
    run_offline_regeneration,
)
from tao2typ.raw import RawCapture
from tao2typ.schedule import next_scheduled_check
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
        if identifier == "3":
            assert row.source_manifest is not None
            finished = read_json(output / row.source_manifest)["finished_at"]
            assert row.last_checked_at == finished
            assert (
                row.next_check_at
                == next_scheduled_check(
                    datetime.fromisoformat(row.published_at), datetime.fromisoformat(finished)
                ).isoformat()
            )
        else:
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
    recovered_reasons = next(entry for entry in queued if entry["id"] == 3)["reasons"]
    assert "initial" not in recovered_reasons and "recent-comment" in recovered_reasons
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


@pytest.fixture
def reconciliation_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, ArchiveState]:
    """Eleven recovered initial captures and one still-due monthly check."""
    fetched = datetime(2026, 10, 14, 11, tzinfo=UTC)
    now = datetime(2026, 10, 14, 16, tzinfo=UTC)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp(now.timestamp(), tz)

    monkeypatch.setattr(raw, "_now", lambda: fetched)
    monkeypatch.setattr(offline, "datetime", FixedDatetime)
    output = tmp_path / "archive"
    state = ArchiveState(daily_attempts={"2026-10-14": 10})
    entries = []
    for identifier in range(1, 13):
        url = f"https://terrytao.wordpress.com/2024/01/01/post-{identifier}/"
        published = "2024-01-01T00:00:00+00:00"
        if identifier == 12:
            published = "2026-09-14T15:00:00+00:00"
        capture = RawCapture(
            output, f"post-{identifier}", url, published_at=published, modified_at=published
        )
        capture.add('<h1>Saved</h1><div class="entry-content"><p>Saved article.</p></div>', url)
        capture.finish()
        post = Post(f"Post {identifier}", url, published, published)
        post.source_manifest = capture.manifest_path
        post.converted_at = now.isoformat()
        row = PostState(
            identifier,
            url,
            post.title,
            published,
            published,
            converted_at=post.converted_at,
            converted_modified_at=published,
            source_manifest=capture.manifest_path,
            pdf=f"pdfs/post-{identifier}.pdf",
            typst=f"typst/post-{identifier}.typ.gz",
            ir=f"ir/post-{identifier}.json.gz",
            next_check_at=published,
        )
        if identifier == 12:
            row.last_checked_at = "2026-10-14T11:00:01+00:00"
            row.next_check_at = "2026-10-14T15:00:00+00:00"
        assert row.pdf and row.typst and row.ir
        write_json(output / row.ir, post.to_dict())
        write_text(output / row.typst, "Saved Typst")
        (output / row.pdf).parent.mkdir(exist_ok=True)
        (output / row.pdf).write_bytes(b"%PDF-test")
        state.posts[str(identifier)] = row
        entries.append(
            {
                "id": identifier,
                "url": url,
                "title": post.title,
                "reasons": ["one-month" if identifier == 12 else "initial"],
            }
        )
    state.save(output / "state.json")
    write_json(output / "queue.json", {"posts": entries})
    return output, state


def test_reconciliation_clears_recovered_initials_keeps_monthly_and_is_idempotent(
    reconciliation_archive: tuple[Path, ArchiveState], monkeypatch: pytest.MonkeyPatch
) -> None:
    output, original = reconciliation_archive
    artifacts = {
        path: path.read_bytes()
        for directory in ("pdfs", "typst", "ir", "raw")
        for path in (output / directory).rglob("*")
        if path.is_file()
    }

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Reconciliation must not fetch or compile")

    monkeypatch.setattr(BlogClient, "_request", forbidden)
    monkeypatch.setattr(pipeline, "compile_typst", forbidden)
    report = reconcile_initial_checks(output)
    assert report["reconciled"] == 11 and report["remaining"] == 1
    assert report["network_requests"] == report["daily_attempts_consumed"] == 0
    current = ArchiveState.load(output / "state.json")
    for identifier, row in current.posts.items():
        expected = asdict(original.posts[identifier])
        if identifier != "12":
            expected["last_checked_at"] = "2026-10-14T11:00:00+00:00"
            expected["next_check_at"] = "2027-01-01T00:00:00+00:00"
        assert asdict(row) == expected
    assert current.daily_attempts == original.daily_attempts
    queue = read_json(output / "queue.json")
    assert [(entry["id"], entry["reasons"]) for entry in queue["posts"]] == [(12, ["one-month"])]
    assert all(path.read_bytes() == content for path, content in artifacts.items())
    state_bytes, queue_bytes = (
        (output / "state.json").read_bytes(),
        (output / "queue.json").read_bytes(),
    )
    reports = list((output / "jobs").glob("*.gz"))
    repeat = reconcile_initial_checks(output)
    assert repeat["reconciled"] == 0 and repeat["remaining"] == 1
    assert (output / "state.json").read_bytes() == state_bytes
    assert (output / "queue.json").read_bytes() == queue_bytes
    assert list((output / "jobs").glob("*.gz")) == reports


def test_reconciliation_preserves_uncovered_comment_events(
    reconciliation_archive: tuple[Path, ArchiveState],
) -> None:
    output, state = reconciliation_archive
    row = state.posts["1"]
    row.pending_comments = [row.url + "#comment-999"]
    row.seen_comments = [row.url + "#comment-9"]
    state.save(output / "state.json")
    report = reconcile_initial_checks(output)
    assert report["reconciled"] == 11 and report["remaining"] == 2
    current = ArchiveState.load(output / "state.json").posts["1"]
    assert current.pending_comments == row.pending_comments
    assert current.seen_comments == row.seen_comments
    assert next(entry for entry in read_json(output / "queue.json")["posts"] if entry["id"] == 1)[
        "reasons"
    ] == ["recent-comment"]


def test_reconciliation_recovers_queue_when_state_was_saved_before_interruption(
    reconciliation_archive: tuple[Path, ArchiveState],
) -> None:
    output, _ = reconciliation_archive
    stale_queue = read_json(output / "queue.json")
    reconcile_initial_checks(output)
    state_bytes = (output / "state.json").read_bytes()
    write_json(output / "queue.json", stale_queue)
    report = reconcile_initial_checks(output)
    assert report["reconciled"] == 0 and report["remaining"] == 1
    assert [entry["id"] for entry in read_json(output / "queue.json")["posts"]] == [12]
    assert (output / "state.json").read_bytes() == state_bytes


@pytest.mark.parametrize(
    "invalid",
    [
        "partial",
        "corrupt-page",
        "foreign-capture",
        "foreign-ir",
        "missing-finished",
        "missing-captured",
        "future-finished",
        "naive-finished",
        "null-finished",
        "invalid-finished",
        "stale-version",
        "stale-ir-version",
        "stale-converted-version",
        "stale-ir-converted-date",
        "wrong-published-date",
        "manifest-error",
        "failed-conversion",
        "missing-conversion-date",
        "missing-pdf",
        "missing-typst",
        "missing-ir",
        "missing-manifest",
        "symlink-pdf",
    ],
)
def test_reconciliation_rejects_unverified_or_stale_capture_without_changing_state(
    reconciliation_archive: tuple[Path, ArchiveState], invalid: str
) -> None:
    output, state = reconciliation_archive
    row = state.posts["1"]
    assert row.source_manifest and row.ir
    manifest_path = output / row.source_manifest
    metadata = read_json(manifest_path)
    ir_path = output / row.ir
    ir = read_json(ir_path)
    if invalid == "partial":
        metadata["complete"] = False
    elif invalid == "corrupt-page":
        write_text(output / metadata["pages"][0]["path"], "Altered source")
    elif invalid == "foreign-capture":
        metadata["post_url"] = "https://terrytao.wordpress.com/2024/01/01/other/"
    elif invalid == "foreign-ir":
        ir["url"] = "https://terrytao.wordpress.com/2024/01/01/other/"
    elif invalid in {"missing-finished", "missing-captured"}:
        metadata.pop("finished_at" if invalid == "missing-finished" else "captured_at")
    elif invalid == "future-finished":
        metadata["finished_at"] = "2027-10-14T11:00:00+00:00"
    elif invalid == "naive-finished":
        metadata["finished_at"] = "2026-10-14T11:00:00"
    elif invalid == "null-finished":
        metadata["finished_at"] = None
    elif invalid == "invalid-finished":
        metadata["finished_at"] = "Not a date"
    elif invalid == "stale-version":
        row.modified_at = "2026-10-14T14:00:00+00:00"
    elif invalid == "stale-ir-version":
        ir["modified_at"] = "2023-01-01T00:00:00+00:00"
    elif invalid == "stale-converted-version":
        row.converted_modified_at = "2023-01-01T00:00:00+00:00"
    elif invalid == "stale-ir-converted-date":
        ir["converted_at"] = "2026-10-14T15:00:00+00:00"
    elif invalid == "wrong-published-date":
        metadata["published_at"] = "2023-01-01T00:00:00+00:00"
    elif invalid == "manifest-error":
        metadata["error"] = "Incomplete fetch"
    elif invalid == "failed-conversion":
        row.last_error = "Compiler failed"
    elif invalid == "missing-conversion-date":
        row.converted_at = None
    elif invalid.startswith("missing-"):
        artifact = invalid.removeprefix("missing-")
        relative = row.source_manifest if artifact == "manifest" else getattr(row, artifact)
        (output / relative).unlink()
    elif invalid == "symlink-pdf":
        assert row.pdf
        pdf = output / row.pdf
        pdf.unlink()
        pdf.symlink_to(output / "pdfs/post-2.pdf")
    if manifest_path.is_file():
        write_json(manifest_path, metadata)
    if ir_path.is_file():
        write_json(ir_path, ir)
    before = asdict(row)
    assert not _acknowledge_initial_capture(row, output)
    assert asdict(row) == before


def test_reconciliation_keeps_existing_check_even_when_capture_is_newer(
    reconciliation_archive: tuple[Path, ArchiveState],
) -> None:
    output, state = reconciliation_archive
    row = state.posts["1"]
    row.last_checked_at = "2026-01-01T00:00:00+00:00"
    row.next_check_at = "2027-01-01T00:00:00+00:00"
    before = asdict(row)
    assert not _acknowledge_initial_capture(row, output)
    assert asdict(row) == before
