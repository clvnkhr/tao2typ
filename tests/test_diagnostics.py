"""Failure evidence stays confined, checked, and inert in local previews."""

import hashlib
from pathlib import Path

import pytest

from tao2typ.diagnostics import failure_artifacts, failure_stems, read_failure_artifact
from tao2typ.storage import read_json, write_json, write_text

POST = {
    "id": 9,
    "title": "A post",
    "url": "https://example.org/a-post/",
    "published_at": "2009-01-01",
}


def capture(archive: Path, *, compressed: bool = True, complete: bool = False) -> dict:
    suffix = ".gz" if compressed else ""
    records = []
    for html in ("<script>alert('raw')</script>\r\ncafé", "<p>Comments</p>"):
        data = html.encode()
        digest = hashlib.sha256(data).hexdigest()
        relative = f"raw/pages/{digest}.html{suffix}"
        write_text(archive / relative, html)
        records.append({"path": relative, "bytes_sha256": digest, "bytes": len(data)})
    manifest = f"raw/a-post/job/manifest.json{suffix}"
    write_json(
        archive / manifest,
        {"schema_version": 1, "post_url": POST["url"], "complete": complete, "pages": records},
    )
    return {**POST, "source_manifest": manifest}


@pytest.mark.parametrize("compressed", [False, True])
def test_partial_capture_pages_and_gzip_diagnostics_are_served_as_plain_text(
    tmp_path: Path, compressed: bool
) -> None:
    archive = tmp_path / "archive"
    post = capture(archive, compressed=compressed)
    suffix = ".gz" if compressed else ""
    write_text(archive / f"failures/a-post.typ{suffix}", "Typst café")
    write_json(archive / f"failures/a-post.json{suffix}", {"title": "A post"})
    artifacts = failure_artifacts(archive, post, "a-post")
    assert set(artifacts) == {"typst", "ir", "capture", "html/0", "html/1"}
    assert read_failure_artifact(archive, post, "a-post", "typst") == (
        "Typst café".encode(),
        "text/plain; charset=utf-8",
    )
    assert read_failure_artifact(archive, post, "a-post", "html/0") == (
        "<script>alert('raw')</script>\r\ncafé".encode(),
        "text/plain; charset=utf-8",
    )
    assert read_failure_artifact(archive, post, "a-post", "missing") is None


def test_canonical_names_precede_legacy_date_id_fallback(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    write_text(archive / "failures/2009-01-01-9.typ", "legacy")
    write_text(archive / "failures/a-post.typ.gz", "canonical")
    write_json(archive / "failures/2009-01-01-9.json.gz", {"legacy": True})
    paths = failure_artifacts(archive, POST, "a-post")
    assert paths["typst"].name == "a-post.typ.gz"
    assert paths["ir"].name == "2009-01-01-9.json.gz"


@pytest.mark.parametrize("alteration", ["content", "checksum", "count", "bool_count"])
def test_raw_page_is_checked_only_when_selected(tmp_path: Path, alteration: str) -> None:
    archive = tmp_path / "archive"
    post = capture(archive)
    manifest = archive / post["source_manifest"]
    metadata = read_json(manifest)
    page = metadata["pages"][0]
    if alteration == "content":
        write_text(archive / page["path"], "tampered")
    elif alteration == "checksum":
        page["bytes_sha256"] = "0" * 64
    else:
        page["bytes"] = True if alteration == "bool_count" else page["bytes"] + 1
    write_json(manifest, metadata)
    assert "html/0" in failure_artifacts(archive, post, "a-post")
    assert read_failure_artifact(archive, post, "a-post", "html/0") is None
    assert read_failure_artifact(archive, post, "a-post", "html/1") is not None


@pytest.mark.parametrize(
    "relative",
    [
        "../secret.json",
        "/secret.json",
        "raw/a-post/../manifest.json",
        r"raw\a-post\job\manifest.json",
    ],
)
def test_manifest_cannot_escape_archive(tmp_path: Path, relative: str) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    (tmp_path / "secret.json").write_text("secret")
    assert failure_artifacts(archive, {**POST, "source_manifest": relative}, "a-post") == {}
    assert failure_artifacts(archive, POST, "../../secret") == {}


@pytest.mark.parametrize("component", ["failures", "manifest", "capture-dir", "page", "pages-dir"])
def test_symlink_components_are_never_discovered(tmp_path: Path, component: str) -> None:
    archive = tmp_path / "archive"
    post = capture(archive)
    write_text(archive / "failures/a-post.typ", "Typst")
    manifest = archive / post["source_manifest"]
    page = archive / read_json(manifest)["pages"][0]["path"]
    targets = {
        "failures": archive / "failures",
        "manifest": manifest,
        "capture-dir": manifest.parent,
        "page": page,
        "pages-dir": page.parent,
    }
    target = targets[component]
    saved = target.with_name(target.name + "-saved")
    target.rename(saved)
    target.symlink_to(saved, target_is_directory=saved.is_dir())
    artifacts = failure_artifacts(archive, post, "a-post")
    if component == "failures":
        assert "typst" not in artifacts
    elif component in {"manifest", "capture-dir"}:
        assert "capture" not in artifacts and "html/0" not in artifacts
    else:
        assert "html/0" not in artifacts
    assert (
        read_failure_artifact(archive, post, "a-post", "html/0") is None or component == "failures"
    )


def test_manifest_page_paths_and_invalid_gzip_are_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    post = capture(archive)
    manifest = archive / post["source_manifest"]
    metadata = read_json(manifest)
    metadata["pages"][0]["path"] = "raw/pages/../a-post/job/manifest.json.gz"
    write_json(manifest, metadata)
    assert "html/0" not in failure_artifacts(archive, post, "a-post")
    assert read_failure_artifact(archive, post, "a-post", "html/0") is None
    manifest.write_bytes(b"not gzip")
    assert set(failure_artifacts(archive, post, "a-post")) == {"capture"}
    assert read_failure_artifact(archive, post, "a-post", "capture") is None


def test_failure_stems_resolve_collisions_and_skip_bad_ids() -> None:
    posts = [
        {**POST, "id": 2},
        {**POST, "id": "9"},
        {**POST, "id": 10, "url": "https://example.org/a-post-9/"},
        {**POST, "id": None},
        {**POST, "id": True},
        {**POST, "id": "../9"},
        {"title": "Missing id"},
    ]
    assert failure_stems(posts) == {"2": "a-post", "9": "a-post-9-2", "10": "a-post-9"}
