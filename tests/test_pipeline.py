"""Exercise durable batch progress without HTTP requests or a Typst binary."""

import json
import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tao2typ import pipeline
from tao2typ.ir import Node, Post
from tao2typ.source import BlogClient, PostMetadata, RecentComment
from tao2typ.state import ArchiveState


def instant(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, tzinfo=UTC)


def metadata(identifier: int, published: datetime) -> PostMetadata:
    # Deliberately use another URL date: WordPress metadata is the source of truth.
    return PostMetadata(
        identifier,
        f"https://terrytao.wordpress.com/2010/01/01/post-{identifier}/",
        f"Post {identifier}",
        published,
        published,
    )


class FakeBlogClient(BlogClient):
    def __init__(self, posts: list[PostMetadata]) -> None:
        self.posts = posts
        self.events: list[RecentComment] = []
        self.fetched: list[str] = []
        self.html = "Version one"
        self.discovery_error: Exception | None = None
        self.recent_error: Exception | None = None

    def discover_posts(
        self, *, page_size: int = 100, modified_after: datetime | None = None
    ) -> Iterator[PostMetadata]:
        yield from self.posts
        if self.discovery_error is not None:
            raise self.discovery_error

    def recent_comments(self) -> list[RecentComment]:
        if self.recent_error is not None:
            raise self.recent_error
        return self.events

    def fetch_post_pages(self, url: str) -> list[str]:
        self.fetched.append(url)
        return [self.html, "Comment page"]


@dataclass
class FakeCompiler:
    calls: int = 0
    fail: bool = False

    def __call__(self, source: Path, pdf: Path, root: Path) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("mock compiler failed")
        # Include the rendered text so a changed document creates different bytes.
        pdf.write_bytes(b"%PDF-test\n" + source.read_bytes())


@pytest.fixture
def compiler(monkeypatch: pytest.MonkeyPatch) -> FakeCompiler:
    compiler = FakeCompiler()
    monkeypatch.setattr(pipeline, "compile_typst", compiler)

    def parse(
        html: str,
        url: str,
        *,
        comment_pages: Sequence[str] = (),
        published_at: str | None = None,
        modified_at: str | None = None,
    ) -> Post:
        assert comment_pages == ["Comment page"]
        return Post(
            title=url,
            url=url,
            published_at=published_at,
            modified_at=modified_at,
            body=[Node("paragraph", children=[Node("text", html)])],
        )

    monkeypatch.setattr(pipeline, "parse_post", parse)
    return compiler


def archive(output: Path) -> ArchiveState:
    return ArchiveState.load(output / "state.json")


def test_inventory_is_complete_even_when_conversion_is_limited(
    tmp_path: Path, compiler: FakeCompiler
) -> None:
    posts = [metadata(identifier, instant(2007, 1, identifier)) for identifier in range(1, 4)]
    client = FakeBlogClient(posts)
    now = instant(2026, 10, 13)
    report = pipeline.run_batch(tmp_path, client, now=now, max_posts=1)
    state = archive(tmp_path)
    assert report["discovered"] == 3
    assert report["due"] == 3
    assert report["converted"] == 1
    assert report["remaining"] == 2
    assert len(state.posts) == 3
    assert len(client.fetched) == compiler.calls == 1
    assert state.last_discovered_at == now.isoformat()
    assert state.posts["1"].last_checked_at == now.isoformat()
    assert state.posts["2"].last_checked_at is None
    assert state.posts["3"].last_checked_at is None
    # A subsequent batch continues the unconverted inventory rather than rediscovering
    # only a narrow recent-post window or repeating a successfully checked old post.
    report = pipeline.run_batch(tmp_path, client, now=now + timedelta(days=1), max_posts=1)
    assert report["due"] == 2
    assert report["remaining"] == 1
    assert report["results"][0]["id"] == 2


def test_unlimited_initial_batch_persists_source_dates_and_timestamped_report(
    tmp_path: Path, compiler: FakeCompiler
) -> None:
    published = datetime(2026, 10, 1, 13, tzinfo=timezone(timedelta(hours=1)))
    client = FakeBlogClient([metadata(1, published), metadata(2, instant(2007, 4, 15))])
    now = instant(2026, 10, 13)
    report = pipeline.run_batch(tmp_path, client, now=now, max_posts=0)
    state = archive(tmp_path)
    first = state.posts["1"]
    assert report["converted"] == compiler.calls == 2
    assert report["remaining"] == 0
    assert report["started_at"] == now.isoformat()
    assert first.published_at == published.isoformat()
    assert first.last_checked_at == first.converted_at == now.isoformat()
    assert first.next_check_at == instant(2026, 11, 1).isoformat()
    assert state.posts["2"].next_check_at == instant(2027, 4, 15).isoformat()
    assert first.ir is not None
    ir = json.loads((tmp_path / first.ir).read_text())
    assert ir["published_at"] == first.published_at
    assert ir["modified_at"] == first.modified_at
    assert first.pdf is not None and (tmp_path / first.pdf).exists()
    jobs = list((tmp_path / "jobs").glob("*.json"))
    assert len(jobs) == 1
    assert jobs[0].name == "20261013T120000.000000Z.json"
    assert json.loads(jobs[0].read_text()) == report
    assert first.pdf in (tmp_path / "README.md").read_text()


def test_unchanged_post_is_skipped_until_week_boundary(
    tmp_path: Path, compiler: FakeCompiler
) -> None:
    published = instant(2026, 10, 1)
    client = FakeBlogClient([metadata(1, published)])
    pipeline.run_batch(tmp_path, client, now=published)
    report = pipeline.run_batch(tmp_path, client, now=published + timedelta(days=1))
    assert report["due"] == 0
    assert report["results"] == []
    assert compiler.calls == 1
    report = pipeline.run_batch(tmp_path, client, now=published + timedelta(days=7))
    assert report["due"] == 1
    assert report["results"][0]["reasons"] == ["one-week"]
    assert compiler.calls == 1
    assert len(client.fetched) == 2
    assert (
        archive(tmp_path).posts["1"].last_checked_at == (published + timedelta(days=7)).isoformat()
    )


def test_missed_checks_are_collapsed_into_one_refresh(
    tmp_path: Path, compiler: FakeCompiler
) -> None:
    published = instant(2026, 9, 1)
    client = FakeBlogClient([metadata(1, published)])
    pipeline.run_batch(tmp_path, client, now=published)
    later = instant(2028, 2, 1)
    report = pipeline.run_batch(tmp_path, client, now=later)
    assert report["converted"] == 1
    assert compiler.calls == 1
    assert len(client.fetched) == 2
    assert archive(tmp_path).posts["1"].next_check_at == instant(2028, 9, 1).isoformat()
    assert pipeline.run_batch(tmp_path, client, now=later + timedelta(days=1))["due"] == 0


def test_pending_comment_events_survive_limits_and_are_deduplicated(
    tmp_path: Path, compiler: FakeCompiler
) -> None:
    client = FakeBlogClient([metadata(1, instant(2007, 4, 15)), metadata(2, instant(2008, 4, 15))])
    now = instant(2026, 10, 13)
    pipeline.run_batch(tmp_path, client, now=now, max_posts=0)
    first = RecentComment(client.posts[0].url + "#comment-101", client.posts[0].url)
    second = RecentComment(client.posts[1].url + "#comment-202", client.posts[1].url)
    client.events = [first, first, second]
    report = pipeline.run_batch(tmp_path, client, now=now + timedelta(days=1), max_posts=1)
    state = archive(tmp_path)
    assert report["due"] == 2 and report["remaining"] == 1
    assert state.posts["1"].seen_comments == [first.event_url]
    assert state.posts["1"].pending_comments == []
    assert state.posts["2"].pending_comments == [second.event_url]
    # The second comment falls out of the sidebar before its limited batch slot.
    client.events = []
    report = pipeline.run_batch(tmp_path, client, now=now + timedelta(days=2), max_posts=1)
    assert report["results"][0]["id"] == 2
    assert report["results"][0]["reasons"] == ["recent-comment"]
    assert archive(tmp_path).posts["2"].seen_comments == [second.event_url]
    client.events = [first, second]
    report = pipeline.run_batch(tmp_path, client, now=now + timedelta(days=3))
    assert report["due"] == 0
    assert compiler.calls == 2
    assert len(client.fetched) == 4


def test_compile_failure_preserves_outputs_check_dates_and_comment_retry(
    tmp_path: Path, compiler: FakeCompiler
) -> None:
    published = instant(2026, 10, 1)
    client = FakeBlogClient([metadata(1, published)])
    pipeline.run_batch(tmp_path, client, now=published)
    original = archive(tmp_path).posts["1"]
    assert original.pdf is not None and original.ir is not None and original.typst is not None
    old_outputs = {
        path: (tmp_path / path).read_bytes() for path in (original.pdf, original.ir, original.typst)
    }
    client.html = "Version two"
    client.posts = [replace(client.posts[0], modified_at=published + timedelta(days=1))]
    event = RecentComment(client.posts[0].url + "#comment-303", client.posts[0].url)
    client.events = [event]
    compiler.fail = True
    report = pipeline.run_batch(tmp_path, client, now=published + timedelta(days=1))
    failed = archive(tmp_path).posts["1"]
    assert report["failed"] == 1 and report["converted"] == 0
    assert failed.last_checked_at == original.last_checked_at
    assert failed.converted_at == original.converted_at
    assert failed.converted_modified_at == original.converted_modified_at
    assert failed.content_sha256 == original.content_sha256
    assert failed.last_error == "mock compiler failed"
    assert failed.pending_comments == [event.event_url]
    assert failed.seen_comments == []
    assert all((tmp_path / path).read_bytes() == data for path, data in old_outputs.items())
    assert list(tmp_path.rglob("*.tmp.*")) == []
    compiler.fail = False
    client.events = []
    checked = published + timedelta(days=2)
    report = pipeline.run_batch(tmp_path, client, now=checked)
    retried = archive(tmp_path).posts["1"]
    assert report["converted"] == 1
    assert retried.last_checked_at == checked.isoformat()
    assert retried.converted_modified_at == client.posts[0].modified_at.isoformat()
    assert retried.last_error is None and retried.pending_comments == []
    assert retried.seen_comments == [event.event_url]
    assert (tmp_path / original.pdf).read_bytes() != old_outputs[original.pdf]


def test_discovery_failure_is_logged_without_partial_inventory(
    tmp_path: Path, compiler: FakeCompiler
) -> None:
    client = FakeBlogClient([metadata(1, instant(2026, 10, 1))])
    client.discovery_error = RuntimeError("metadata page unavailable")
    report = pipeline.run_batch(tmp_path, client, now=instant(2026, 10, 13))
    assert report["errors"] == [{"stage": "discovery", "error": "metadata page unavailable"}]
    assert report["converted"] == report["failed"] == compiler.calls == 0
    state = archive(tmp_path)
    assert state.posts == {} and state.last_discovered_at is None
    jobs = list((tmp_path / "jobs").glob("*.json"))
    assert len(jobs) == 1
    assert json.loads(jobs[0].read_text())["errors"] == report["errors"]


def test_recent_comments_failure_is_logged_while_schedule_still_runs(
    tmp_path: Path, compiler: FakeCompiler
) -> None:
    client = FakeBlogClient([metadata(1, instant(2026, 10, 1))])
    client.recent_error = RuntimeError("widget changed")
    report = pipeline.run_batch(tmp_path, client, now=instant(2026, 10, 13))
    assert report["errors"] == [{"stage": "recent-comments", "error": "widget changed"}]
    assert report["converted"] == compiler.calls == 1


def test_failed_post_cannot_starve_unconverted_inventory(
    tmp_path: Path, compiler: FakeCompiler
) -> None:
    client = FakeBlogClient([metadata(1, instant(2007, 4, 15)), metadata(2, instant(2008, 4, 15))])
    now = instant(2026, 10, 13)
    compiler.fail = True
    report = pipeline.run_batch(tmp_path, client, now=now, max_posts=1)
    assert report["results"][0]["id"] == 1 and report["failed"] == 1
    assert archive(tmp_path).posts["1"].last_attempted_at == now.isoformat()
    compiler.fail = False
    report = pipeline.run_batch(tmp_path, client, now=now + timedelta(days=1), max_posts=1)
    assert report["results"][0]["id"] == 2 and report["converted"] == 1
    report = pipeline.run_batch(tmp_path, client, now=now + timedelta(days=2), max_posts=1)
    assert report["results"][0]["id"] == 1 and report["converted"] == 1


def test_missing_pdf_is_repaired_before_schedule_due(
    tmp_path: Path, compiler: FakeCompiler
) -> None:
    published = instant(2026, 10, 1)
    client = FakeBlogClient([metadata(1, published)])
    pipeline.run_batch(tmp_path, client, now=published)
    post = archive(tmp_path).posts["1"]
    assert post.pdf is not None
    (tmp_path / post.pdf).unlink()
    report = pipeline.run_batch(tmp_path, client, now=published + timedelta(days=1))
    assert report["results"][0]["reasons"] == ["missing-pdf"]
    assert report["converted"] == 1 and compiler.calls == 2
    assert (tmp_path / post.pdf).exists()


def test_soft_deadline_checkpoints_success_and_counts_unattempted_posts(
    tmp_path: Path, compiler: FakeCompiler, monkeypatch: pytest.MonkeyPatch
) -> None:
    posts = [metadata(identifier, instant(2007, 1, identifier)) for identifier in range(1, 4)]
    client = FakeBlogClient(posts)
    # Start at zero, allow one post, then cross the deadline before the second.
    ticks = iter([0.0, 0.0, 10.0])
    monkeypatch.setattr(pipeline, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    now = instant(2026, 10, 13)
    report = pipeline.run_batch(tmp_path, client, now=now, max_seconds=5)
    state = archive(tmp_path)
    assert report["time_budget_reached"] is True
    assert report["due"] == 3 and report["converted"] == 1 and report["remaining"] == 2
    assert len(report["results"]) == len(client.fetched) == compiler.calls == 1
    assert state.posts["1"].last_checked_at == now.isoformat()
    assert state.posts["2"].last_attempted_at is None
    assert state.posts["3"].last_attempted_at is None
    job = json.loads(next((tmp_path / "jobs").glob("*.json")).read_text())
    assert job == report
    assert job["finished_at"] == now.isoformat()
    # Checkpointed successes stay covered while the following run completes backlog.
    monkeypatch.setattr(pipeline, "time", SimpleNamespace(monotonic=lambda: 0.0))
    report = pipeline.run_batch(tmp_path, client, now=now + timedelta(days=1), max_seconds=5)
    assert report["due"] == report["converted"] == 2
    assert report["remaining"] == 0
    assert "time_budget_reached" not in report
    assert compiler.calls == 3


def test_same_url_image_bytes_invalidate_pdf_cache_without_rewriting_unchanged_assets(
    tmp_path: Path, compiler: FakeCompiler, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeBlogClient([metadata(1, instant(2026, 10, 1))])
    image_url = "https://terrytao.files.wordpress.com/2026/10/figure.png"
    fetched_assets: list[str] = []
    image_bytes = b"image version one"

    def fetch_asset(url: str) -> bytes:
        fetched_assets.append(url)
        return image_bytes

    monkeypatch.setattr(client, "fetch_asset", fetch_asset)

    def parse(
        html: str,
        url: str,
        *,
        comment_pages: Sequence[str] = (),
        published_at: str | None = None,
        modified_at: str | None = None,
    ) -> Post:
        return Post(
            title="Image post",
            url=url,
            published_at=published_at,
            modified_at=modified_at,
            body=[Node("figure", children=[Node("image", attrs={"url": image_url})])],
        )

    monkeypatch.setattr(pipeline, "parse_post", parse)
    published = instant(2026, 10, 1)
    pipeline.run_batch(tmp_path, client, now=published)
    original = archive(tmp_path).posts["1"]
    asset = next((tmp_path / "assets").glob("*.png"))
    # An explicit old modification time makes rewrites observable without sleeps.
    os.utime(asset, ns=(1_000_000_000, 1_000_000_000))
    unchanged_mtime = asset.stat().st_mtime_ns
    report = pipeline.run_batch(tmp_path, client, now=published + timedelta(days=1), force=True)
    assert report["converted"] == 1
    assert compiler.calls == 1
    assert asset.stat().st_mtime_ns == unchanged_mtime
    assert archive(tmp_path).posts["1"].content_sha256 == original.content_sha256
    image_bytes = b"image version two"
    report = pipeline.run_batch(tmp_path, client, now=published + timedelta(days=2), force=True)
    assert report["converted"] == 1 and compiler.calls == 2
    assert fetched_assets == [image_url, image_url, image_url]
    assert asset.read_bytes() == b"image version one"
    assert asset.stat().st_mtime_ns == unchanged_mtime
    assert any(path.read_bytes() == image_bytes for path in (tmp_path / "assets").glob("*.png"))
    assert archive(tmp_path).posts["1"].content_sha256 != original.content_sha256
