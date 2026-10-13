"""Conversion dates belong to artifact generations, not source refreshes."""

import json
import os
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tao2typ import pipeline
from tao2typ.ir import Node, Post
from tao2typ.source import BlogClient, FetchedPage, PostMetadata, RecentComment
from tao2typ.state import ArchiveState
from tao2typ.typst import render_post

URL = "https://terrytao.wordpress.com/2026/10/01/conversion-date/"
FIRST = datetime(2026, 10, 13, 12, 34, 56, tzinfo=UTC)


def post(body: str = "Original article.", *, manifest: str = "raw/first/manifest.json") -> Post:
    return Post(
        "Conversion date",
        URL,
        published_at="2026-10-01T10:00:00+00:00",
        body=[Node("paragraph", children=[Node("text", body)])],
        source_manifest=manifest,
    )


class Compiler:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, source: Path, pdf: Path, root: Path) -> None:
        self.calls += 1
        # The artifact bytes change if the rendered date changes unnecessarily.
        pdf.write_bytes(b"%PDF-test\n" + source.read_bytes())


@pytest.fixture
def compiler(monkeypatch: pytest.MonkeyPatch) -> Compiler:
    result = Compiler()
    monkeypatch.setattr(pipeline, "compile_typst", result)
    return result


def test_conversion_timestamp_round_trips_without_breaking_schema_one() -> None:
    legacy = post().to_dict()
    legacy.pop("converted_at", None)
    restored = Post.from_dict(legacy)
    assert restored.schema_version == 1
    assert restored.converted_at is None

    restored.converted_at = FIRST.isoformat()
    saved = json.loads(json.dumps(restored.to_dict()))
    assert saved["converted_at"] == FIRST.isoformat()
    assert Post.from_dict(saved) == restored
    assert saved["published_at"] != saved["converted_at"]


def test_top_provenance_displays_conversion_day_in_utc() -> None:
    article = post()
    # The local day differs from the UTC day: the printed date must match its label.
    article.converted_at = datetime(
        2026, 10, 14, 1, 15, tzinfo=timezone(timedelta(hours=2))
    ).isoformat()
    rendered = render_post(article)
    line = next(line for line in rendered.splitlines() if "converted to Typst" in line)
    assert '#text("tao2typ")' in line
    assert '#text(" · converted to Typst 2026-10-13 (UTC)")' in line
    assert rendered.index(line) < rendered.index("Original article.")
    size = re.search(r"size: ([\d.]+)pt", line)
    assert size is not None and float(size[1]) <= 7


def test_identical_next_day_refresh_keeps_artifact_date_bytes_and_mtime(
    tmp_path: Path, compiler: Compiler
) -> None:
    first = pipeline.save_post(post(), "conversion-date", tmp_path, None, converted_at=FIRST)
    assert first["converted_at"] == FIRST.isoformat()
    paths = {kind: tmp_path / first[kind] for kind in ("typst", "pdf")}
    original_bytes = {kind: path.read_bytes() for kind, path in paths.items()}
    for path in paths.values():
        os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    original_mtimes = {kind: path.stat().st_mtime_ns for kind, path in paths.items()}

    refreshed = post(manifest="raw/refreshed/manifest.json")
    next_day = FIRST + timedelta(days=1)
    again = pipeline.save_post(
        refreshed,
        "conversion-date",
        tmp_path,
        None,
        previous_digest=first["content_sha256"],
        converted_at=next_day,
    )
    assert compiler.calls == 1
    assert again["content_sha256"] == first["content_sha256"]
    assert again["converted_at"] == first["converted_at"]
    assert {kind: path.read_bytes() for kind, path in paths.items()} == original_bytes
    assert {kind: path.stat().st_mtime_ns for kind, path in paths.items()} == original_mtimes
    saved = Post.from_dict(json.loads((tmp_path / again["ir"]).read_text()))
    assert saved.converted_at == FIRST.isoformat()
    assert saved.source_manifest == refreshed.source_manifest

    changed_day = FIRST + timedelta(days=2)
    changed = pipeline.save_post(
        post("A revised article.", manifest="raw/revised/manifest.json"),
        "conversion-date",
        tmp_path,
        None,
        previous_digest=again["content_sha256"],
        converted_at=changed_day,
    )
    assert compiler.calls == 2
    assert changed["converted_at"] == changed_day.isoformat()
    assert changed["content_sha256"] != first["content_sha256"]
    assert paths["typst"].read_bytes() != original_bytes["typst"]
    assert paths["pdf"].read_bytes() != original_bytes["pdf"]
    saved = Post.from_dict(json.loads((tmp_path / changed["ir"]).read_text()))
    assert saved.converted_at == changed_day.isoformat()
    assert " · converted to Typst 2026-10-15 (UTC)" in paths["typst"].read_text()


def test_default_timestamp_records_actual_build_time(tmp_path: Path, compiler: Compiler) -> None:
    article = post()
    article.converted_at = "2000-01-01T00:00:00+00:00"
    before = datetime.now(UTC)
    artifacts = pipeline.save_post(article, "conversion-date", tmp_path, None)
    after = datetime.now(UTC)
    built = datetime.fromisoformat(artifacts["converted_at"])
    assert before <= built <= after
    assert compiler.calls == 1
    saved = Post.from_dict(json.loads((tmp_path / artifacts["ir"]).read_text()))
    assert saved.converted_at == artifacts["converted_at"]


class RefreshClient(BlogClient):
    """A real parser input, with all network calls replaced by local responses."""

    def __init__(self) -> None:
        self.body = "Original article."

    def discover_posts(
        self, *, page_size: int = 100, modified_after: datetime | None = None
    ) -> Iterator[PostMetadata]:
        published = datetime(2026, 10, 1, 10, tzinfo=UTC)
        yield PostMetadata(1, URL, "Conversion date", published, published)

    def recent_comments(self) -> list[RecentComment]:
        return []

    def fetch_post_pages(
        self, url: str, *, on_page: Callable[[FetchedPage], None] | None = None
    ) -> list[str]:
        html = (
            '<h1 class="entry-title">Conversion date</h1>'
            f'<div class="entry-content"><p>{self.body}</p></div>'
        )
        if on_page:
            on_page(FetchedPage(html, url, url, FIRST.isoformat()))
        return [html]


def test_batch_state_distinguishes_last_check_from_last_conversion(
    tmp_path: Path, compiler: Compiler
) -> None:
    client = RefreshClient()
    pipeline.run_batch(tmp_path, client, now=FIRST, max_posts=1)
    initial = ArchiveState.load(tmp_path / "state.json").posts["1"]
    assert initial.converted_at == FIRST.isoformat()

    refreshed_day = FIRST + timedelta(days=1)
    pipeline.run_batch(tmp_path, client, now=refreshed_day, max_posts=1, force=True)
    refreshed = ArchiveState.load(tmp_path / "state.json").posts["1"]
    assert refreshed.last_checked_at == refreshed_day.isoformat()
    assert refreshed.converted_at == initial.converted_at
    assert refreshed.source_manifest != initial.source_manifest
    assert compiler.calls == 1

    client.body = "A revised article."
    changed_day = FIRST + timedelta(days=2)
    pipeline.run_batch(tmp_path, client, now=changed_day, max_posts=1, force=True)
    changed = ArchiveState.load(tmp_path / "state.json").posts["1"]
    assert changed.last_checked_at == changed.converted_at == changed_day.isoformat()
    assert compiler.calls == 2
