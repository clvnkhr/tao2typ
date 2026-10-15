"""The live preview reads local artifacts without exporting or mutating the batch."""

import hashlib
import shutil
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import tao2typ.catalogue as catalogue
import tao2typ.preview as preview_module
from tao2typ.preview import Preview
from tao2typ.state import ArchiveState, PostState
from tao2typ.storage import write_bytes, write_json, write_text


def setup_preview(tmp_path: Path) -> Preview:
    archive = tmp_path / "archive"
    post = PostState(
        9, "https://terrytao.wordpress.com/post/", "A post", "2009-01-01", "2009-01-01"
    )
    post.pdf = "pdfs/a-post.pdf"
    post.typst = "typst/a-post.typ.gz"
    post.ir = "ir/a-post.json.gz"
    post.converted_at = "2026-10-14T10:20:00Z"
    ArchiveState(posts={"9": post}).save(archive / "state.json")
    write_json(archive / "queue.json", {"updated_at": "2026-10-14T10:20:00Z", "posts": []})
    write_json(
        archive / "jobs/job.json",
        {"started_at": post.converted_at, "results": [{"status": "converted"}]},
    )
    write_bytes(archive / post.pdf, b"%PDF-local-test")
    write_text(archive / post.typst, "Some Typst café")
    write_json(archive / post.ir, {"title": "A post"})
    return Preview(archive, tmp_path / "site", "http://127.0.0.1:8765")


def test_preview_links_local_artifacts_without_copying_them(tmp_path: Path) -> None:
    preview = setup_preview(tmp_path)
    original_state = (preview.archive / "state.json").read_bytes()
    page = preview.index().decode()
    assert 'data-download-url="http://127.0.0.1:8765/pdfs/a-post.pdf"' in page
    assert 'data-filename="a-post.pdf"' in page
    assert 'href="http://127.0.0.1:8765/pdfs/a-post.pdf"' not in page
    soup = BeautifulSoup(page, "html.parser")
    assert not soup.select(".post-actions a:not(.original)")
    for kind, folder, extension, download_format in (
        ("pdf", "pdfs", "pdf", "pdf"),
        ("typst", "typst", "typ", "typst"),
        ("ir", "ir", "json", "json"),
    ):
        button = soup.select_one(f"button.download.{kind}")
        assert button is not None
        assert button["data-download-url"] == (f"http://127.0.0.1:8765/{folder}/a-post.{extension}")
        assert button["data-filename"] == f"a-post.{extension}"
        assert button["data-download-format"] == download_format
        assert button["type"] == "button" and button["aria-haspopup"] == "dialog"
    assert soup.select_one("dialog#download-dialog") is not None
    assert 'href="https://github.com/clvnkhr/tao2typ"' in page
    assert not (preview.output / "pdfs").exists()
    assert not (preview.output / "typst").exists()
    assert preview.artifact("/pdfs/a-post.pdf") == (b"%PDF-local-test", "application/pdf")
    assert preview.artifact("/typst/a-post.typ") == (
        "Some Typst café".encode(),
        "text/plain; charset=utf-8",
    )
    assert preview.artifact("/typst/a-post.typ.gz") == (
        (preview.archive / "typst/a-post.typ.gz").read_bytes(),
        "application/gzip",
    )
    assert (preview.archive / "state.json").read_bytes() == original_state


def test_preview_tracks_new_checkpoints_and_finish(tmp_path: Path) -> None:
    preview = setup_preview(tmp_path)
    first = preview.snapshot()
    assert first["converted"] == 1 and first["running"]
    assert first["revision"].encode() in preview.index()
    write_json(preview.archive / "queue.json", {"posts": [{"id": 9, "reasons": ["birthday"]}]})
    assert preview.snapshot()["revision"] != first["revision"]
    assert preview.snapshot()["remaining"] == 1
    assert 'data-queue-position="0"' in preview.index().decode()
    write_json(
        preview.archive / "jobs/job.json",
        {
            "started_at": "2026-10-14T10:20:00Z",
            "finished_at": "2026-10-14T10:21:00Z",
            "results": [{"status": "converted"}, {"status": "failed"}],
        },
    )
    finished = preview.snapshot()
    assert finished["attempted"] == 2 and finished["failed"] == 1 and not finished["running"]


def test_preview_rebuilds_faq_after_markdown_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = tmp_path / "web"
    shutil.copytree(catalogue._ASSETS, assets)
    monkeypatch.setattr(catalogue, "_ASSETS", assets)
    monkeypatch.setattr(preview_module, "_ASSETS", assets)
    preview = setup_preview(tmp_path)
    before = preview.snapshot()["revision"]
    preview.index()
    assert (preview.output / "faq.html").is_file()
    (assets / "faq.md").write_text("# FAQ\n\nWords I edited myself.\n", encoding="utf-8")
    assert preview.snapshot()["revision"] != before
    preview.index()
    assert "Words I edited myself." in (preview.output / "faq.html").read_text()


def test_compressing_an_older_report_does_not_replace_latest_progress(tmp_path: Path) -> None:
    preview = setup_preview(tmp_path)
    reports = preview.archive / "jobs"
    (reports / "job.json").rename(reports / "20261014T102000Z.json")
    write_json(
        reports / "20261013T102000Z.json.gz",
        {"started_at": "2026-10-13T10:20:00Z", "results": [{"status": "failed"}]},
    )
    snapshot = preview.snapshot()
    assert snapshot["converted"] == 1 and snapshot["failed"] == 0


def test_preview_rejects_escape_paths_and_working_state(tmp_path: Path) -> None:
    preview = setup_preview(tmp_path)
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"secret")
    (preview.archive / "pdfs/linked.pdf").symlink_to(secret)
    for url in (
        "/state.json",
        "/queue.json",
        "/pdfs/../state.json",
        "/pdfs/%2e%2e/secret.pdf",
        "/pdfs/linked.pdf",
        "/raw/pages/capture.html",
        "/pdfs/missing.pdf",
    ):
        assert preview.artifact(url) is None


def test_local_failure_downloads_are_plain_text_and_read_only(tmp_path: Path) -> None:
    preview = setup_preview(tmp_path)
    state = ArchiveState.load(preview.archive / "state.json")
    state.posts["9"].last_error = "Typst compilation failed: unknown command: \\badcommand"
    state.posts["9"].last_attempted_at = "2026-10-14T11:00:00Z"
    html = b"<script>alert('raw')</script>"
    digest = hashlib.sha256(html).hexdigest()
    html_path = f"raw/pages/{digest}.html.gz"
    write_bytes(preview.archive / html_path, html)
    manifest_path = "raw/post/job/manifest.json.gz"
    write_json(
        preview.archive / manifest_path,
        {
            "schema_version": 1,
            "complete": True,
            "post_url": state.posts["9"].url,
            "pages": [{"path": html_path, "bytes_sha256": digest, "bytes": len(html)}],
        },
    )
    state.posts["9"].source_manifest = manifest_path
    state.save(preview.archive / "state.json")
    write_text(preview.archive / "failures/post.typ.gz", "Failed source")
    write_json(preview.archive / "failures/post.json.gz", {"title": "Failed post"})
    original_state = (preview.archive / "state.json").read_bytes()
    original_queue = (preview.archive / "queue.json").read_bytes()
    page = preview.index().decode()
    assert 'data-failed="true"' in page and "Unrecognized TeX command" in page
    assert "/__tao2typ_failure/9/typst" in page
    soup = BeautifulSoup(page, "html.parser")
    assert not soup.select(".failure-actions a")
    actions = soup.select(".failure-actions button.download")
    assert len(actions) == 4
    for key, filename, download_format in (
        ("typst", "post.typ", "typst"),
        ("ir", "post.json", "json"),
        ("capture", "manifest.json", "json"),
        ("html/0", f"{digest}.html", "text"),
    ):
        button = soup.select_one(
            f'.failure-actions button[data-download-url$="/__tao2typ_failure/9/{key}"]'
        )
        assert button is not None
        assert button["data-filename"] == filename
        assert button["data-download-format"] == download_format
        assert button["type"] == "button" and button["aria-haspopup"] == "dialog"
        assert not button.has_attr("href")
    assert preview.diagnostic("/__tao2typ_failure/9/typst") == (
        b"Failed source",
        "text/plain; charset=utf-8",
    )
    for url in (
        "/__tao2typ_failure/9/../../state.json",
        "/__tao2typ_failure/9/%2e%2e",
        "/__tao2typ_failure/unknown/typst",
        "/__tao2typ_failure/999/typst",
        "/__tao2typ_failure/9/html/nope",
    ):
        assert preview.diagnostic(url) is None
    assert (preview.archive / "state.json").read_bytes() == original_state
    assert (preview.archive / "queue.json").read_bytes() == original_queue
    state.posts["9"].last_error = None
    state.save(preview.archive / "state.json")
    assert preview.diagnostic("/__tao2typ_failure/9/typst") is None
