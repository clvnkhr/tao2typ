"""Regression tests for the HTML -> IR -> Typst boundary."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tao2typ.ir import Node, Post
from tao2typ.parser import parse_post
from tao2typ.typst import MITEX_VERSION, render_post

URL = "https://terrytao.wordpress.com/2020/02/29/a-test/"

HTML = r"""<!doctype html><html><head>
<meta property="article:published_time" content="2020-02-29T12:30:00Z">
<meta property="article:modified_time" content="2020-03-01T01:02:03Z">
</head><body>
<h1 class="entry-title">A title with [brackets] &amp; #hash</h1>
<div class="entry-content">
<p>Unicode α ≥ 0. <strong>Bold <em>and emphasis</em></strong>.
<custom-element>A wrapper <img class="latex" alt="{x^2 + y^2}"/></custom-element></p>
<p align="center"><a name="eq-one"></a><img class="latex extra"
 alt="\displaystyle \frac{1}{2} \quad (7)"/></p>
<p>See <a href="#eq-one">(7)</a> and <a href="https://example.org/?x=1&amp;y=2">a link</a>.</p>
<p style="text-align: center"><b>1. A section <img class="latex" alt="\alpha"/></b></p>
<blockquote><p><b>Theorem 3 (Example)</b> A theorem.</p><p>Its second paragraph.</p></blockquote>
<ol start="3"><li><p>An item.</p><ul><li>A nested item.</li></ul></li><li>Another.</li></ol>
<table><thead><tr><th colspan="2">Heading</th></tr></thead>
<tbody><tr><td rowspan="2">A</td><td>B</td></tr><tr><td>C</td></tr></tbody></table>
<figure><img src="/image.png" alt="A #diagram" width="600">
<figcaption>Caption.</figcaption></figure>
<pre><code class="language-python">print("#panic(\"unsafe\")")</code></pre>
<p><del>Removed</del> H<sub>2</sub>O and x<sup>2</sup>.</p>
<div class="sharedaddy"><p>Share this!</p></div><script>panic()</script>
</div>
<div id="comments"><ol class="commentlist"><li class="comment" id="comment-1">
<div class="comment-author"><cite class="fn">First Author</cite></div>
<div class="comment-metadata"><a href="#comment-1">
<time datetime="2020-03-01T12:00:00Z">Time</time></a></div>
<div class="comment-content"><p>A comment with <img class="latex" alt="\mathbb{R}"/>.</p></div>
<ul class="children"><li class="comment" id="comment-2">
<div class="comment-author"><cite class="fn">Reply Author</cite></div>
<div class="comment-content"><p>A reply.</p></div></li></ul></li></ol></div>
</body></html>"""


def _kinds(post: Post, kind: str) -> list[Node]:
    return [node for node in post.walk() if node.kind == kind]


def test_rich_html_produces_output_independent_ir() -> None:
    post = parse_post(HTML, URL)
    assert post.title == "A title with [brackets] & #hash"
    assert post.published_at == "2020-02-29T12:30:00+00:00"
    assert post.modified_at == "2020-03-01T01:02:03+00:00"
    assert len(_kinds(post, "quote")) == 1
    assert len(_kinds(post, "list")) == 2
    assert len(_kinds(post, "table_row")) == 3
    assert _kinds(post, "heading")[0].children[0].kind == "strong"
    assert _kinds(post, "image")[0].attrs["url"] == "https://terrytao.wordpress.com/image.png"
    assert all("Share this!" not in node.text for node in post.walk())
    assert all("panic()" != node.text for node in post.walk())
    # Unknown wrappers retain the rich descendants, including TeX.
    assert "{x^2 + y^2}" in [node.text for node in _kinds(post, "math")]


def test_ir_json_round_trip_retains_comments_dates_and_tex() -> None:
    original = parse_post(HTML, URL)
    saved = json.loads(json.dumps(original.to_dict(), ensure_ascii=False))
    restored = Post.from_dict(saved)
    assert restored == original
    assert render_post(restored) == render_post(original)
    saved["schema_version"] = 99
    with pytest.raises(ValueError, match="schema version"):
        Post.from_dict(saved)


def test_dates_from_api_override_html_without_coupling_to_rendering() -> None:
    post = parse_post(
        HTML, URL, published_at="2020-02-29T00:00:00Z", modified_at="2026-10-13T12:34:00Z"
    )
    assert post.published_at == "2020-02-29T00:00:00Z"
    assert post.modified_at == "2026-10-13T12:34:00Z"
    assert "29 February 2020" in render_post(post)


def test_comments_retain_threading_and_deduplicate_paginated_wrappers() -> None:
    next_page = HTML.replace('id="comment-2"', 'id="comment-3"').replace(
        "A reply.", "Second reply."
    )
    post = parse_post(HTML, URL, comment_pages=[HTML, next_page])
    assert len(post.comments) == 1
    parent = post.comments[0]
    assert parent.id == "comment-1"
    assert parent.author == "First Author"
    assert parent.published_at == "2020-03-01T12:00:00+00:00"
    assert [reply.id for reply in parent.replies] == ["comment-2", "comment-3"]
    assert "Comments (3)" in render_post(post)


def test_tex_url_decoding_and_alt_have_distinct_encoding_rules() -> None:
    html = r"""<h1 class="post-title">Math</h1><div class="post-content"><p>
    <img src="https://s0.wp.com/latex.php?latex=x%2By%5E2&amp;bg=fff">
    <img class="latex" alt="x+y%20z" src="https://s0.wp.com/latex.php?latex=unused">
    <script type="math/tex; mode=display">\int_0^1 x\,dx</script>
    </p></div>"""
    maths = _kinds(parse_post(html, URL), "math")
    assert [math.text for math in maths] == ["x+y^2", "x+y%20z", r"\int_0^1 x\,dx"]
    assert maths[-1].attrs["display"] == "true"


def test_unambiguous_math_delimiters_leave_dollars_as_prose() -> None:
    html = r"""<div class="post-content"><p>Cost $5. Inline \(x + 1\).
    Display \[\int x\,dx\] and $$y^2$$.</p></div>"""
    post = parse_post(html, URL)
    assert [node.text for node in _kinds(post, "math")] == ["x + 1", r"\int x\,dx", "y^2"]
    assert [node.attrs["display"] for node in _kinds(post, "math")] == ["false", "true", "true"]
    assert any("Cost $5." in node.text for node in _kinds(post, "text"))


def test_original_numbers_and_internal_link_text_survive_rendering() -> None:
    post = parse_post(HTML, URL)
    typst = render_post(post)
    assert f"@preview/mitex:{MITEX_VERSION}" in typst
    assert '#mitex("\\\\frac{1}{2}")' in typst
    assert '#text("(7)")' in typst
    assert "#link(<tao-" in typst
    assert '#text("Theorem 3 (Example)")' in typst
    assert URL in typst


def test_source_text_cannot_execute_typst_and_unsafe_urls_are_dropped() -> None:
    html = """<h1 class="post-title">#panic("title") [end]</h1>
    <div class="post-content"><p>#panic("body") ] &lt;label&gt; $money$
    <a href="javascript:alert(1)">Visible words</a></p></div>"""
    post = parse_post(html, URL)
    typst = render_post(post)
    assert '#text("#panic(\\"body\\") ] &lt;label&gt; $money$' not in typst
    assert '#text("#panic(\\"body\\") ] <label> $money$' in typst
    assert "javascript:" not in typst
    assert "Visible words" in typst


def test_renderer_resolves_only_explicit_asset_mapping_and_preserves_table_spans() -> None:
    post = parse_post(HTML, URL)
    typst = render_post(post, {"https://terrytao.wordpress.com/image.png": "../assets/image.png"})
    assert '#image("../assets/image.png", width: 90%, alt: "A #diagram")' in typst
    assert "table.cell(colspan: 2, rowspan: 1)" in typst
    assert "table.cell(colspan: 1, rowspan: 2)" in typst
    assert "#enum(start: 3," in typst
    assert "#sub[" in typst
    assert "#super[" in typst


def test_legacy_malformed_table_rows_and_wrapped_display_equations_survive() -> None:
    html = r"""<div class="post-content"><table>
    <th><td>First</td><td><img class="latex" alt="\lambda"/></td></th>
    <tr><td>Second</td><td>Last</td></tr></table>
    <a name="eq-old"><p align="center"><img class="latex" alt="x=1"/> (3)</p></a>
    </div>"""
    post = parse_post(html, URL)
    assert len(_kinds(post, "table_row")) == 2
    assert _kinds(post, "math")[-1].attrs == {"display": "true", "number": "3"}
    assert _kinds(post, "anchor")[-1].attrs["id"] == "eq-old"


def test_json_ld_publication_and_url_date_fallback() -> None:
    html = """<script type="application/ld+json">{"@graph": [{"@type": "BlogPosting",
    "datePublished": "2020-02-29T10:00:00Z"}]}</script><div class="post-content">Words.</div>"""
    assert parse_post(html, URL).published_at == "2020-02-29T10:00:00+00:00"
    assert parse_post('<div class="post-content">Words.</div>', URL).published_at == "2020-02-29"


def test_parser_fails_loudly_on_error_pages_instead_of_overwriting_posts() -> None:
    with pytest.raises(ValueError, match="No WordPress post content"):
        parse_post("<h1>Service unavailable</h1>", URL)


def test_renderer_rejects_unknown_nodes_instead_of_silently_losing_content() -> None:
    post = Post("Title", URL, body=[Node("future-format", "Important words")])
    with pytest.raises(ValueError, match="unknown IR node"):
        render_post(post)


def test_rich_fixture_compiles_with_typst(tmp_path: Path) -> None:
    compiler = shutil.which("typst")
    if compiler is None:
        pytest.skip("Typst is not installed; CI installs it for this compilation test")
    post = parse_post(HTML, URL)
    diagram = Path(__file__).resolve().parents[1] / "examples" / "demo-diagram.svg"
    shutil.copyfile(diagram, tmp_path / "diagram.svg")
    source = tmp_path / "post.typ"
    pdf = tmp_path / "post.pdf"
    source.write_text(
        render_post(post, {"https://terrytao.wordpress.com/image.png": "diagram.svg"}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [compiler, "compile", str(source), str(pdf)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert pdf.read_bytes().startswith(b"%PDF-")


def test_tao_theme_comments_separate_avatars_from_prose_and_preserve_replies() -> None:
    # Match the older Tao theme's comment structure, using self-authored prose.
    html = r"""<h1 class="post-title">Fixture</h1><div class="post-content">
    <p><img class="avatar" src="https://secure.gravatar.com/a" alt="Post image"></p></div>
    <div id="comments"><div class="comment depth-1 vcard" id="comment-30">
      <div class="comment-metadata">
        <p class="comment-permalink"><a href="#comment-30">28 February, 2007 at 7:56 am</a></p>
        <p class="comment-author"><strong class="fn"><a href="https://example.org/">
          Example reader</a></strong></p>
      </div>
      <div class="comment-content">
        <img class="avatar avatar-32" src="https://secure.gravatar.com/blavatar/hash?s=32"
          alt="Unknown's avatar">
        <img src="https://secure.gravatar.com/avatar/other?s=32">
        <a href="https://example.org/profile"><img src="https://example.org/photo"
          alt="Reader’s avatar"></a>
        <p>Real content <img class="latex" alt="\mathbb{R}">
          <img src="https://example.org/diagram.png" alt="A mathematical diagram"></p>
        <div class="cs-rating pd-rating"></div>
      </div>
      <ul class="children"><li class="comment depth-2" id="comment-31">
        <div class="comment-metadata"><p class="comment-author"><strong class="fn">
          Reply reader</strong></p><p class="comment-permalink"><a href="#comment-31">
          28 February, 2007 at 8:00 am</a></p></div>
        <div class="comment-content"><img class="avatar" src="https://example.org/photo">
          <p>A reply with <img class="latex" alt="x^2">.</p></div>
      </li></ul>
    </div></div>"""
    post = parse_post(html, URL)
    body_images = [node for root in post.body for node in root.walk() if node.kind == "image"]
    body_images += [
        node for root in post.comments[0].body for node in root.walk() if node.kind == "image"
    ]
    assert [image.attrs["url"] for image in body_images] == [
        "https://secure.gravatar.com/a",
        "https://example.org/diagram.png",
    ]
    assert len(post.comments) == 1
    parent = post.comments[0]
    assert parent.avatar is not None
    assert parent.avatar.attrs["url"] == "https://secure.gravatar.com/blavatar/hash?s=32"
    assert parent.author == "Example reader"
    assert parent.published_at == "2007-02-28T07:56:00"
    assert parent.replies[0].author == "Reply reader"
    assert parent.replies[0].published_at == "2007-02-28T08:00:00"
    assert [node.text for node in _kinds(post, "math")] == [r"\mathbb{R}", "x^2"]
    assert not any("profile" in node.attrs.get("url", "") for node in parent.walk())


def test_legacy_sqrt_and_mbox_compile_without_changing_ir(tmp_path: Path) -> None:
    # These exact legacy patterns failed in the first real 2007 post batch.
    legacy = r"(\sqrt(n))^{-1} + \sqrt 2 + \mbox{for all } x"
    node = Node("math", legacy, {"display": "true"})
    post = Post("Legacy TeX compatibility", URL, body=[node])
    source_text = render_post(post)
    assert node.text == legacy
    assert r"\\sqrt{(}n)" in source_text
    assert r"\\sqrt {2}" in source_text
    assert r"\\text{for all }" in source_text
    compiler = shutil.which("typst")
    if compiler is None:
        pytest.skip("Typst is not installed; CI installs it for this compilation test")
    source = tmp_path / "legacy.typ"
    pdf = tmp_path / "legacy.pdf"
    source.write_text(source_text, encoding="utf-8")
    result = subprocess.run(
        [compiler, "compile", str(source), str(pdf)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert pdf.read_bytes().startswith(b"%PDF-")
