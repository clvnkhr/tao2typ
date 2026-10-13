"""Source numbering survives rendering, without inventing document counters."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest

from tao2typ.ir import Comment, Node, Post
from tao2typ.typst import _legacy_tex, render_post

URL = "https://terrytao.wordpress.com/2020/01/01/source-numbers/"


def equation(source: str, number: str | None = None) -> Node:
    attrs = {"display": "true"}
    if number is not None:
        attrs["number"] = number
    return Node("math", source, attrs)


def test_source_equation_numbers_use_native_positioning_without_a_counter() -> None:
    post = Post("Source numbers", URL, body=[equation(r"x=1 \quad (2.3a)")])
    typst = render_post(post)
    assert '#text("(2.3a)")' in typst
    assert '#mitex("x=1")' in typst
    assert "#grid(columns: (1fr, auto)" not in typst
    assert "numbering: n =>" in typst
    assert post.body[0].text == r"x=1 \quad (2.3a)"


def test_legacy_matrix_display_number_is_extracted_after_the_closed_environment() -> None:
    source = (
        r"a=\begin{pmatrix}1&2\\3&4\end{pmatrix}; "
        r"b=\begin{pmatrix}5&6\\7&8\end{pmatrix} (10)"
    )
    post = Post("Matrix source", URL, body=[equation(source)])
    typst = render_post(post)
    assert '#text("(10)")' in typst
    assert r"\\end{pmatrix} (10)" not in typst
    assert post.body[0].text == source


def test_external_equation_punctuation_stays_on_its_source_side_of_the_number() -> None:
    node = equation(r"\nu(E) := \lambda(1_E)", "11")
    node.attrs["punctuation"] = "."
    node.attrs["number_suffix"] = ";"
    typst = render_post(Post("Punctuation", URL, body=[node]))
    assert '#mitex("\\\\nu(E) := \\\\lambda(1_E).")' in typst
    assert '#text("(11);")' in typst
    assert node.text == r"\nu(E) := \lambda(1_E)"


def test_explicit_tags_and_starred_tags_are_retained_exactly() -> None:
    post = Post(
        "Source tags",
        URL,
        body=[equation(r"x=1 \tag{12b}"), equation(r"y=2 \tag*{[A]}")],
    )
    typst = render_post(post)
    assert '#text("(12b)")' in typst
    assert '#text("[A]")' in typst
    assert '#text("([A])")' not in typst
    assert r"\\tag" not in typst


def test_rich_tag_groups_are_not_truncated_or_printed_as_literal_tex() -> None:
    post = Post("Rich tags", URL, body=[equation(r"x=1 \tag{\mathrm{A}_{2}}")])
    typst = render_post(post)
    assert '#mi("\\\\mathrm{A}_{2}")' in typst
    assert '#text("(")' in typst and '#text(")")' in typst
    assert post.body[0].text == r"x=1 \tag{\mathrm{A}_{2}}"


def test_tagged_alignment_preserves_every_row_and_matrix_row_breaks() -> None:
    source = (
        r"\begin{align} x&=\begin{pmatrix}1\\2\end{pmatrix} \tag{3}"
        r"\\ y&=2 \tag{8} \\ z&=3 \notag \end{align}"
    )
    typst = render_post(Post("Aligned tags", URL, body=[equation(source)]))
    assert '#text("(3)")' in typst and '#text("(8)")' in typst
    assert typst.count("numbering: n =>") == 2
    assert r"\\begin{pmatrix}1\\\\2\\end{pmatrix}" in typst
    assert "z&=3" in typst


def test_eqnarray_is_aligned_without_adding_automatic_labels() -> None:
    source = r"\begin{eqnarray*} x&=&1 \\ y&=&2 \end{eqnarray*}"
    typst = render_post(Post("Old alignment", URL, body=[equation(source)]))
    assert r"\\begin{aligned}" in typst
    assert "x&=&1" in typst and "y&=&2" in typst
    assert "numbering: n =>" not in typst


@pytest.mark.parametrize("source", ["x + (10)", "O(1)", "f(x) (10)", "(1,2)"])
def test_mathematical_parentheses_are_never_mistaken_for_plain_space_labels(source: str) -> None:
    typst = render_post(Post("Unnumbered source", URL, body=[equation(source)]))
    assert f'#mitex("{source}")' in typst
    assert "numbering: n =>" not in typst


def test_theorem_captions_retain_source_names_and_numbers_without_renumbering() -> None:
    captions = ["Theorem 7 (Original name)", "Lemma 2.", "Theorem 7.", "Definition 1.3"]
    post = Post(
        "Theorems",
        URL,
        body=[
            Node("quote", children=[Node("strong", children=[Node("text", caption)])])
            for caption in captions
        ],
    )
    typst = render_post(post)
    for caption in captions:
        assert f'#text("{caption}")' in typst
    assert "#set heading(numbering: none)" in typst


def test_encoded_same_post_fragments_resolve_once_and_external_links_stay_external() -> None:
    identifier = "eq:α%3A"
    href = URL + "#eq%3A%CE%B1%253A"
    post = Post(
        "Anchored equations",
        URL,
        body=[
            Node("anchor", attrs={"id": identifier}),
            Node("link", attrs={"url": href}, children=[Node("text", "(9)")]),
            Node(
                "link",
                attrs={"url": "https://example.org/#eq%3A%CE%B1%253A"},
                children=[Node("text", "Elsewhere")],
            ),
        ],
    )
    typst = render_post(post)
    assert "#link(<tao-" in typst
    assert '#link("https://example.org/#eq%3A%CE%B1%253A")' in typst


def test_comment_portrait_is_small_and_missing_optional_asset_keeps_the_author() -> None:
    avatar = Node("image", attrs={"url": "https://example.org/avatar.png"})
    reply = Comment("reply", "Reply author", None, URL + "#reply", avatar=avatar)
    post = Post(
        "Comments",
        URL,
        comments=[Comment("root", "First author", None, URL + "#root", replies=[reply])],
    )
    typst = render_post(post, {avatar.attrs["url"]: "avatar.png"})
    assert "#pad(left: 18pt)" in typst
    assert '#image("avatar.png", width: 22pt, height: 22pt, fit: "cover"' in typst
    assert "width: 90%" not in typst
    without_asset = render_post(post)
    assert "Reply author" in without_asset
    assert '#image("' not in without_asset


def test_deep_comment_replies_continue_to_indent_after_the_first_three_levels() -> None:
    deepest = Comment("level-5", "Deep reply", None, URL + "#level-5")
    for level in range(4, -1, -1):
        deepest = Comment(str(level), f"Level {level}", None, URL, replies=[deepest])
    typst = render_post(Post("Deep conversation", URL, comments=[deepest]))
    assert typst.count("#pad(left: 18pt)") == 3
    assert typst.count("#pad(left: 9pt)") == 2
    assert "#pad(left: 0pt)" not in typst


def test_comment_author_stays_with_first_line_when_a_long_comment_breaks(tmp_path: Path) -> None:
    compiler, extractor = shutil.which("typst"), shutil.which("pdftotext")
    if compiler is None or extractor is None:
        pytest.skip("Typst and Poppler are required for the page-break regression")
    post = Post(
        "Comment pagination",
        URL,
        comments=[
            Comment(
                "one",
                "AUTHOR_SENTINEL",
                None,
                URL + "#one",
                body=[
                    Node("paragraph", children=[Node("text", "BODY_SENTINEL " + "words " * 1000)])
                ],
            )
        ],
    )
    source = tmp_path / "sticky.typ"
    pdf = tmp_path / "sticky.pdf"
    heading = '#heading(level: 1)[#text("Comments (1)")]'
    source.write_text(render_post(post).replace(heading, heading + "\n#v(235mm)"))
    result = subprocess.run(
        [compiler, "compile", str(source), str(pdf)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    pages = subprocess.run(
        [extractor, "-layout", str(pdf), "-"], capture_output=True, text=True, check=True
    ).stdout.split("\f")
    author_page = next(index for index, page in enumerate(pages) if "AUTHOR_SENTINEL" in page)
    body_page = next(index for index, page in enumerate(pages) if "BODY_SENTINEL" in page)
    assert author_page == body_page
    assert len([page for page in pages if page.strip()]) > 2


def test_known_legacy_commands_are_equivalent_and_original_tex_is_untouched() -> None:
    source = r"\null G \textcircled{z} H + \hdots + \textbullet"
    node = equation(source)
    typst = render_post(Post("Legacy source", URL, body=[node]))
    assert node.text == source
    assert "ⓩ" in typst
    assert r"\\ldots" in typst and r"\\bullet" in typst
    assert r"\\null" not in typst
    malformed = r"x_{N}}"
    assert _legacy_tex(malformed) == malformed


def test_numbered_math_and_avatar_compile_and_numbers_reach_the_pdf(tmp_path: Path) -> None:
    compiler = shutil.which("typst")
    if compiler is None:
        pytest.skip("Typst is not installed")
    (tmp_path / "avatar.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
        '<rect width="32" height="32" fill="#275d8c"/></svg>'
    )
    portrait_url = "https://example.org/portrait.svg"
    post = Post(
        "Numbers and portraits",
        URL,
        body=[
            equation("x=1", "2.3a"),
            equation(r"y=2 \tag*{[A]}"),
            equation(r"\begin{align}a&=1\tag{4}\\ b&=2\tag{9}\end{align}"),
            equation(r"\begin{eqnarray*}c&=&3\\ d&=&4\end{eqnarray*}"),
            equation(r"\null G \textcircled{z} H + \hdots + \textbullet"),
            equation(r"\Delta\hbox{\raisebox{0.4ex}{\tiny\ \ \textbullet}}_h"),
            equation(r"\raisebox{-0.4ex}{\textbullet}+\raisebox{0.2em}{x}"),
        ],
        comments=[
            Comment(
                "root",
                "First author",
                None,
                URL + "#root",
                replies=[
                    Comment(
                        "reply",
                        "Reply author",
                        None,
                        URL + "#reply",
                        avatar=Node("image", attrs={"url": portrait_url}),
                    )
                ],
            )
        ],
    )
    source = tmp_path / "numbers.typ"
    pdf = tmp_path / "numbers.pdf"
    source.write_text(render_post(post, {portrait_url: "avatar.svg"}))
    result = subprocess.run(
        [compiler, "compile", str(source), str(pdf)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    extractor = shutil.which("pdftotext")
    if extractor is None:
        return
    extracted = subprocess.run(
        [extractor, "-bbox", str(pdf), "-"], capture_output=True, text=True, check=True
    ).stdout
    document = ElementTree.fromstring(extracted)
    words = [node for node in document.iter() if node.tag.endswith("word")]
    labels = {node.text for node in words}
    assert {"(2.3a)", "[A]", "(4)", "(9)"}.issubset(labels)
    label = next(node for node in words if node.text == "(2.3a)")
    assert float(label.attrib["xMin"]) > 480
