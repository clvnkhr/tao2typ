"""Asset failures distinguish optional avatars from required article diagrams."""

from pathlib import Path

import pytest

from tao2typ import pipeline
from tao2typ.ir import Comment, Node, Post
from tao2typ.pipeline import asset_extension, resolve_assets
from tao2typ.source import BlogClient, SourceError


class AssetClient(BlogClient):
    def __init__(self, responses: dict[str, bytes | Exception]) -> None:
        self.responses = responses

    def fetch_asset(self, url: str) -> bytes:
        result = self.responses[url]
        if isinstance(result, Exception):
            raise result
        return result


def test_html_error_is_not_accepted_as_a_png_by_filename() -> None:
    with pytest.raises(ValueError, match="Unrecognized image data"):
        asset_extension("https://example.org/photo.png", b"<html>Not found</html>")
    assert asset_extension("https://example.org/avatar", b"\x89PNG\r\n\x1a\nbytes") == ".png"


def test_unavailable_avatar_does_not_lose_comment_or_required_diagram(tmp_path: Path) -> None:
    avatar = "https://example.org/avatar.png"
    diagram = "https://example.org/diagram"
    comment = Comment(
        "comment-1",
        "Reader",
        None,
        "https://example.org/#comment-1",
        body=[Node("paragraph", children=[Node("text", "The discussion remains.")])],
        avatar=Node("image", attrs={"url": avatar}),
    )
    post = Post(
        "Title",
        "https://example.org/",
        comments=[comment],
        body=[Node("image", attrs={"url": diagram})],
    )
    assets = resolve_assets(
        post,
        AssetClient({avatar: b"<html>Error</html>", diagram: b"\x89PNG\r\n\x1a\nbytes"}),
        tmp_path,
    )
    assert set(assets) == {diagram}
    assert "The discussion remains." == comment.body[0].children[0].text
    assert len(post.warnings) == 1 and "avatar unavailable" in post.warnings[0]
    assert post.body[0].attrs["asset_path"].startswith("assets/")


def test_avatar_asset_is_materialized_for_nested_replies(tmp_path: Path) -> None:
    avatar = Node("image", attrs={"url": "https://example.org/avatar"})
    reply = Comment("r", "Reply", None, "https://example.org/#r", avatar=avatar)
    parent = Comment("p", "Parent", None, "https://example.org/#p", replies=[reply])
    post = Post("Title", "https://example.org/", comments=[parent])
    assets = resolve_assets(
        post, AssetClient({avatar.attrs["url"]: b"\x89PNG\r\n\x1a\nbytes"}), tmp_path
    )
    assert avatar.attrs["asset_path"].startswith("assets/")
    assert (tmp_path / avatar.attrs["asset_path"]).is_file()
    assert assets[avatar.attrs["url"]] == "../" + avatar.attrs["asset_path"]


def test_image_shared_by_avatar_and_prose_remains_required(tmp_path: Path) -> None:
    url = "https://example.org/shared"
    avatar = Node("image", attrs={"url": url})
    post = Post(
        "Title",
        "https://example.org/",
        body=[Node("image", attrs={"url": url})],
        comments=[Comment("p", "Parent", None, "https://example.org/#p", avatar=avatar)],
    )
    with pytest.raises(SourceError, match="Unavailable"):
        resolve_assets(post, AssetClient({url: SourceError("Unavailable")}), tmp_path)


def test_fetching_and_asset_pipeline_fixes_invalidate_existing_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "ir.py",
        "parser.py",
        "typst.py",
        "tex.py",
        "alignment.py",
        "pipeline.py",
        "source.py",
        "raw.py",
    ):
        (tmp_path / name).write_text(name)
    monkeypatch.setattr(pipeline, "__file__", str(tmp_path / "pipeline.py"))
    original = pipeline.converter_fingerprint()
    (tmp_path / "source.py").write_text("Comment fetching fixed")
    after_fetch_fix = pipeline.converter_fingerprint()
    assert after_fetch_fix != original
    (tmp_path / "pipeline.py").write_text("Assets fixed")
    assert pipeline.converter_fingerprint() != after_fetch_fix
    after_asset_fix = pipeline.converter_fingerprint()
    (tmp_path / "tex.py").write_text("Math argument boundaries fixed")
    assert pipeline.converter_fingerprint() != after_asset_fix
    after_tex_fix = pipeline.converter_fingerprint()
    (tmp_path / "alignment.py").write_text("Shared display alignment fixed")
    assert pipeline.converter_fingerprint() != after_tex_fix
