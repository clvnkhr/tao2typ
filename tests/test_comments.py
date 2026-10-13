"""Regression cases for real WordPress comment ownership and pagination."""

from __future__ import annotations

from tao2typ.ir import Comment, Node, Post
from tao2typ.parser import parse_post

URL = "https://terrytao.wordpress.com/2007/02/27/a-post/"


def page(comments: str) -> str:
    return (
        '<h1 class="post-title">Fixture</h1><div class="post-content"><p>Post.</p></div>'
        f'<div id="comments">{comments}</div>'
    )


def comment(identifier: int, depth: int, body: str, *, metadata: bool = True) -> str:
    header = (
        f'<div class="comment-author"><img class="avatar avatar-32" '
        f'src="https://example.org/avatar-{identifier}.png" width="32" height="32">'
        f'<cite class="fn">Reader {identifier}</cite> says:</div>'
        f'<div class="comment-metadata"><a href="#comment-{identifier}">'
        f'<time datetime="2007-02-27T12:00:00Z">27 February, 2007 at 12:00 pm</time></a></div>'
        if metadata
        else ""
    )
    return (
        f'<div class="comment depth-{depth}" id="comment-{identifier}">{header}'
        f'<div class="comment-content">{body}</div></div>'
    )


def test_live_sibling_lists_preserve_multiple_reply_levels_and_top_level_comments() -> None:
    html = page(
        comment(1, 1, "<p>Parent.</p>")
        + '<!-- The live theme puts this after the closed parent div. --><ul class="children">'
        + comment(2, 2, "<p>Reply.</p>")
        + '<ul class="children">'
        + comment(3, 3, "<p>Reply to reply.</p>")
        + "</ul>"
        + comment(4, 2, "<p>Second reply.</p>")
        + "</ul>"
        + comment(5, 1, "<p>Another parent.</p>")
    )
    post = parse_post(html, URL)
    assert [item.id for item in post.comments] == ["comment-1", "comment-5"]
    assert [item.id for item in post.comments[0].replies] == ["comment-2", "comment-4"]
    assert post.comments[0].replies[0].replies[0].id == "comment-3"
    assert post.comments[1].replies == []
    assert post.comments[0].author == "Reader 1"


def test_nested_li_and_div_comment_body_do_not_split_one_comment() -> None:
    html = page(
        '<ol class="commentlist"><li class="comment depth-1" id="comment-1">'
        '<article class="comment-body" id="div-comment-1">'
        '<div class="comment-author"><cite class="fn">Parent</cite></div>'
        '<div class="comment-content"><p>Parent body.</p></div></article>'
        '<ul class="children"><li class="comment depth-2" id="comment-2">'
        '<article class="comment-body" id="div-comment-2">'
        '<div class="comment-author"><cite class="fn">Child</cite></div>'
        '<div class="comment-content"><p>Child body.</p></div></article>'
        "</li></ul></li></ol>"
    )
    post = parse_post(html, URL)
    assert len(post.comments) == 1
    assert post.comments[0].id == "comment-1"
    assert post.comments[0].replies[0].id == "comment-2"
    assert post.comments[0].replies[0].author == "Child"


def test_missing_parent_metadata_never_borrows_reply_author_date_or_avatar() -> None:
    html = page(
        '<li class="comment" id="comment-1"><div class="comment-content"><p>Parent.</p></div>'
        '<ul class="children">' + comment(2, 2, "<p>Reply.</p>") + "</ul></li>"
    )
    parent = parse_post(html, URL).comments[0]
    assert parent.author == "Anonymous"
    assert parent.published_at is None
    assert parent.url == URL + "#comment-1"
    assert parent.avatar is None
    assert parent.replies[0].author == "Reader 2"
    assert parent.replies[0].avatar is not None


def test_avatar_is_separate_from_prose_and_is_included_in_asset_walk() -> None:
    html = page(
        comment(
            1,
            1,
            '<img class="avatar" src="https://example.org/avatar-duplicate.png">'
            '<a href="https://example.org/profile"><img src="https://example.org/profile.png" '
            'alt="Reader’s avatar"></a><p>Content and '
            '<img src="https://example.org/diagram.png" alt="Diagram">.</p>',
        )
    )
    parent = parse_post(html, URL).comments[0]
    assert parent.avatar is not None
    assert parent.avatar.attrs["url"] == "https://example.org/avatar-1.png"
    assert parent.avatar.attrs["width"] == "32"
    assert [node.attrs["url"] for node in parent.walk() if node.kind == "image"] == [
        "https://example.org/avatar-1.png",
        "https://example.org/diagram.png",
    ]
    assert not any("profile" in node.attrs.get("url", "") for node in parent.walk())
    assert [
        node.attrs["url"] for body in parent.body for node in body.walk() if node.kind == "image"
    ] == ["https://example.org/diagram.png"]


def test_avatar_in_live_comment_content_is_captured_without_profile_link_fallback() -> None:
    html = page(
        comment(
            1,
            1,
            '<a href="https://example.org/profile"><img class="avatar" '
            'src="https://secure.gravatar.com/avatar/hash?s=32" alt="Reader avatar"></a>'
            "<p>Only prose remains.</p>",
            metadata=False,
        )
    )
    parent = parse_post(html, URL).comments[0]
    assert parent.avatar is not None
    assert parent.avatar.attrs["url"] == "https://secure.gravatar.com/avatar/hash?s=32"
    assert [node.text for body in parent.body for node in body.walk() if node.kind == "text"] == [
        "Only prose remains."
    ]


def test_paginated_duplicates_are_merged_once_and_orphan_can_acquire_parent() -> None:
    orphan_page = page(comment(2, 2, "<p>Reply appears on this page without its parent.</p>"))
    wrapped_page = page(
        comment(1, 1, "<p>Parent.</p>")
        + '<ul class="children">'
        + comment(2, 2, "<p>Repeated reply.</p>")
        + comment(3, 2, "<p>New reply.</p>")
        + "</ul>"
    )
    post = parse_post(orphan_page, URL, comment_pages=[wrapped_page, wrapped_page, orphan_page])
    assert [item.id for item in post.comments] == ["comment-1"]
    assert [item.id for item in post.comments[0].replies] == ["comment-2", "comment-3"]
    assert post.comments[0].replies[0].body[0].children[0].text.startswith("Reply appears")


def test_ir_avatar_roundtrip_and_older_schema_one_without_avatar() -> None:
    avatar = Node("image", attrs={"url": "https://example.org/avatar.png", "local": "assets/a.png"})
    post = Post("Fixture", URL, comments=[Comment("comment-1", "Reader", None, URL, avatar=avatar)])
    assert Post.from_dict(post.to_dict()) == post
    legacy = post.to_dict()
    legacy["comments"][0].pop("avatar")
    restored = Post.from_dict(legacy)
    assert restored.comments[0].avatar is None
    assert restored.schema_version == 1


def test_external_equation_numbers_accept_hierarchical_labels() -> None:
    html = page("").replace(
        "<p>Post.</p>",
        '<p align="center"><img class="latex" alt="x=y"> (2.3a)</p>',
    )
    post = parse_post(html, URL)
    equation = next(node for node in post.walk() if node.kind == "math")
    assert equation.attrs == {"display": "true", "number": "2.3a"}
    assert not any("(2.3a)" in node.text for node in post.walk())


def test_punctuated_external_number_is_extracted_without_changing_source_tex() -> None:
    latex = r"\nu(E) := \lambda(1_E)"
    html = page("").replace(
        "<p>Post.</p>",
        f'<p style="text-align: center"><img class="latex" alt="{latex}">. (11)</p>',
    )
    equation = next(node for node in parse_post(html, URL).walk() if node.kind == "math")
    assert equation.text == latex
    assert equation.attrs == {"display": "true", "number": "11", "punctuation": "."}


def test_punctuated_decimal_number_keeps_anchor_and_both_punctuation_positions() -> None:
    html = page("").replace(
        "<p>Post.</p>",
        '<a name="equation-2.3"><p align="center"><img class="latex" alt="x=y">. (2.3a),</p></a>',
    )
    post = parse_post(html, URL)
    equation = next(node for node in post.walk() if node.kind == "math")
    assert equation.text == "x=y"
    assert equation.attrs == {
        "display": "true",
        "number": "2.3a",
        "punctuation": ".",
        "number_suffix": ",",
    }
    assert any(node.kind == "anchor" and node.attrs["id"] == "equation-2.3" for node in post.walk())


def test_centered_math_with_words_and_numeric_reference_stays_inline() -> None:
    html = page("").replace(
        "<p>Post.</p>",
        '<p align="center"><img class="latex" alt="x=y"> in equation (11).</p>',
    )
    equation = next(node for node in parse_post(html, URL).walk() if node.kind == "math")
    assert equation.attrs == {"display": "false"}


def test_body_comment_links_are_not_mistaken_for_own_permalink() -> None:
    parent = parse_post(
        page(comment(1, 1, '<p>See <a href="#comment-99">this comment</a>.</p>', metadata=False)),
        URL,
    ).comments[0]
    assert parent.url == URL + "#comment-1"


def test_centered_prose_and_section_titles_keep_inline_math() -> None:
    html = page("").replace(
        "<p>Post.</p>",
        '<p style="text-align: center">— Linear functionals on <img class="latex" alt="L^p"> —</p>'
        '<p align="center">A centered sentence about <img class="latex" alt="x">.</p>'
        '<p align="center">Centered plain text.</p>',
    )
    post = parse_post(html, URL)
    assert post.body[0].kind == "heading"
    assert post.body[1].attrs["align"] == "center"
    assert post.body[2].attrs["align"] == "center"
    assert [node.attrs["display"] for node in post.walk() if node.kind == "math"] == [
        "false",
        "false",
    ]


def test_div_math_inline_markup_is_preserved_as_math() -> None:
    html = page("").replace("<p>Post.</p>", '<div class="math-inline">x^2</div>')
    equation = next(node for node in parse_post(html, URL).walk() if node.kind == "math")
    assert equation.text == "x^2"
    assert equation.attrs["display"] == "false"
