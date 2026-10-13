"""Alignment is checked at rendered operator positions, with source rows intact."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest

from tao2typ.ir import Node, Post
from tao2typ.parser import parse_post
from tao2typ.typst import render_post

URL = "https://terrytao.wordpress.com/2020/01/01/alignment-regression/"


def display(source: str) -> Node:
    return Node(
        "paragraph",
        attrs={"align": "center"},
        children=[Node("math", source, {"display": "true"})],
    )


def rendered_words(tmp_path: Path, post: Post) -> list[ElementTree.Element]:
    compiler = shutil.which("typst")
    extractor = shutil.which("pdftotext")
    if compiler is None or extractor is None:
        pytest.skip("Typst and Poppler are required to verify alignment geometry")
    original = post.to_dict()
    source = tmp_path / "alignment.typ"
    pdf = tmp_path / "alignment.pdf"
    source.write_text(render_post(post), encoding="utf-8")
    assert post.to_dict() == original
    result = subprocess.run(
        [compiler, "compile", str(source), str(pdf)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    extraction = subprocess.run(
        [extractor, "-bbox", str(pdf), "-"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    document = ElementTree.fromstring(extraction.stdout)
    words = []
    for page_number, page in enumerate(
        (node for node in document.iter() if node.tag.endswith("page")), 1
    ):
        for word in page.iter():
            if word.tag.endswith("word"):
                word.attrib["page"] = str(page_number)
                words.append(word)
    return words


def assert_common_column(words: list[ElementTree.Element]) -> None:
    assert len(words) > 1
    positions = [float(word.attrib["xMin"]) for word in words]
    assert max(positions) - min(positions) < 0.05, positions


def test_adjacent_starred_weak_convergence_rows_share_the_relation_column(tmp_path: Path) -> None:
    # These are independent centered image paragraphs in the saved WordPress
    # page, rather than a pre-existing TeX align environment.
    rows = [
        r"\displaystyle \partial_t u^{(n)} \rightharpoonup^* \partial_t u",
        r"\displaystyle \nu \Delta u^{(n)} \rightharpoonup^* \nu \Delta u",
        r"\displaystyle P_{\leq N^{(n)}} u_0(x) \delta_0(t)"
        r" \rightharpoonup^* u_0(x) \delta_0(t).",
    ]
    post = Post("Weak convergence", URL, body=[display(row) for row in rows])
    words = rendered_words(tmp_path, post)
    arrows = [word for word in words if word.text == "⇀"]
    assert len(arrows) == 3
    assert_common_column(arrows)
    assert [node.text for node in post.walk() if node.kind == "math"] == rows


def test_norm_chain_continuations_share_equality_and_inequality_column(tmp_path: Path) -> None:
    rows = [
        r"\displaystyle \|v-u\|^2 = \langle u,u\rangle"
        r" - 2\langle v,u\rangle + \langle v,v\rangle",
        r"\displaystyle = \langle v,v\rangle - \langle u,u\rangle",
        r"\displaystyle = \|v\|^2 - \|u\|^2",
        r"\displaystyle \leq 0",
    ]
    words = rendered_words(tmp_path, Post("Norm chain", URL, body=[display(row) for row in rows]))
    # A relation at the start of a row can share Poppler's word with its RHS.
    relations = [word for word in words if (word.text or "").startswith(("=", "≤"))]
    assert len(relations) == 4
    assert_common_column(relations)


def test_wrapped_numbered_equations_align_and_preserve_original_row_labels(tmp_path: Path) -> None:
    # Legacy LaTeX2WP uses an anchor surrounding each display paragraph. The
    # parser flattens those wrappers into anchor + paragraph sibling nodes.
    rows = [
        r"\displaystyle \frac{1}{2}\lambda\mathrm{div}_{vol}w + v\neg d\theta"
        r" + \frac{1}{2}d(\theta(v)) = 0 \ \ \ \ \ (3)",
        r"\displaystyle -\theta\mathrm{div}_{vol}w + v\neg d\lambda + w\neg d\theta"
        r" + \frac{1}{2}d(\theta(w)+\lambda(v)) = 0 \ \ \ \ \ (4)",
        r"\displaystyle -\frac{3}{2}\lambda\mathrm{div}_{vol}w + w\neg d\lambda"
        r" + \frac{1}{2}d(\lambda(w)) = 0 \ \ \ \ \ (5)",
        r"\displaystyle \mathrm{div}_{vol}v = 0. \ \ \ \ \ (6)",
    ]
    names = ["eq1", "eq2", "eq3", "div-vol"]
    html = '<div class="entry-content">' + "".join(
        f'<a name="{name}"><p align="center"><img class="latex" alt="{row}"></p></a>'
        for name, row in zip(names, rows, strict=True)
    )
    html += '<p>See <a href="#eq2">second row</a>.</p></div>'
    post = parse_post(html, URL)
    assert [node.attrs["id"] for node in post.body if node.kind == "anchor"] == names
    words = rendered_words(tmp_path, post)
    relations = [word for word in words if word.text == "="]
    assert len(relations) == 4
    assert_common_column(relations)
    labels = [word for word in words if word.text in {"(3)", "(4)", "(5)", "(6)"}]
    assert [word.text for word in labels] == ["(3)", "(4)", "(5)", "(6)"]
    right_edges = [float(word.attrib["xMax"]) for word in labels]
    assert min(right_edges) > 515
    assert max(right_edges) - min(right_edges) < 0.05
    for relation, label in zip(relations, labels, strict=True):
        assert abs(float(relation.attrib["yMin"]) - float(label.attrib["yMin"])) < 2


def test_prose_between_displays_breaks_inferred_alignment_groups(tmp_path: Path) -> None:
    post = Post(
        "Separate statements",
        URL,
        body=[
            display("x^2 = 1"),
            Node("paragraph", children=[Node("text", "A new statement follows.")]),
            display("a+b+c+d+e+f^2 = 2"),
        ],
    )
    words = rendered_words(tmp_path, post)
    equalities = [word for word in words if (word.text or "").startswith("=")]
    assert len(equalities) == 2
    assert abs(float(equalities[0].attrib["xMin"]) - float(equalities[1].attrib["xMin"])) > 20


def test_explicit_tagged_alignment_retains_alignment_and_no_label_on_untagged_row(
    tmp_path: Path,
) -> None:
    source = (
        r"\begin{align} x^2 & = 1\tag{3} \\ x+y+z^2 & = 2\tag{8}"
        r" \\ w^2 & = 3\notag \end{align}"
    )
    words = rendered_words(tmp_path, Post("Tagged rows", URL, body=[display(source)]))
    equalities = [word for word in words if (word.text or "").startswith("=")]
    assert len(equalities) == 3
    assert_common_column(equalities)
    labels = [word.text for word in words if word.text in {"(3)", "(8)"}]
    assert labels == ["(3)", "(8)"]
    assert not any(word.text in {"(1)", "(2)", "(9)"} for word in words)


def test_relations_inside_fractions_and_subscripts_are_not_alignment_pivots(tmp_path: Path) -> None:
    rows = [r"\frac{x = y}{z} = 1234", r"a_{i = 1} + b = 5678"]
    words = rendered_words(
        tmp_path, Post("Nested relations", URL, body=[display(row) for row in rows])
    )
    outer_relations: list[ElementTree.Element] = []
    for rhs in ("1234", "5678"):
        rhs_word = next(word for word in words if (word.text or "").endswith(rhs))
        if (rhs_word.text or "").startswith("="):
            outer_relations.append(rhs_word)
            continue
        baseline = float(rhs_word.attrib["yMin"])
        candidates = [
            word
            for word in words
            if word.text == "=" and abs(float(word.attrib["yMin"]) - baseline) < 0.05
        ]
        assert len(candidates) == 1
        outer_relations.append(candidates[0])
    assert_common_column(outer_relations)


def test_existing_binary_continuation_rows_align_without_creating_new_line_breaks(
    tmp_path: Path,
) -> None:
    rows = [r"x^2 + y + z", r"+ w^2 + v", r"- q^2"]
    words = rendered_words(
        tmp_path, Post("Binary continuation", URL, body=[display(row) for row in rows])
    )
    operators = [word for word in words if (word.text or "").startswith(("+", "−"))]
    # The first operator on each existing row is its alignment point. Other
    # additions remain part of that row and do not acquire new line breaks.
    row_operators: dict[float, list[ElementTree.Element]] = {}
    for operator in operators:
        row_operators.setdefault(round(float(operator.attrib["yMin"]), 1), []).append(operator)
    assert len(row_operators) == 3
    pivots = [
        min(row, key=lambda word: float(word.attrib["xMin"])) for row in row_operators.values()
    ]
    assert_common_column(pivots)


def test_unrelated_unary_negatives_are_not_inferred_as_binary_continuations(tmp_path: Path) -> None:
    rows = [r"- x^2", r"- (x+y+z+a+b+c+d+e+f+g+h)^2"]
    words = rendered_words(
        tmp_path, Post("Unary negatives", URL, body=[display(row) for row in rows])
    )
    negatives = [word for word in words if (word.text or "").startswith("−")]
    assert len(negatives) == 2
    positions = [float(word.attrib["xMin"]) for word in negatives]
    assert abs(positions[0] - positions[1]) > 20


def test_source_subequation_labels_are_preserved_per_aligned_row(tmp_path: Path) -> None:
    rows = [r"x^2 = 1 \tag{3a}", r"x+y+z^2 = 2 \tag{3b}"]
    words = rendered_words(
        tmp_path, Post("Source sublabels", URL, body=[display(row) for row in rows])
    )
    equalities = [word for word in words if (word.text or "").startswith("=")]
    assert len(equalities) == 2
    assert_common_column(equalities)
    labels = [word for word in words if (word.text or "").startswith("(3")]
    assert [word.text for word in labels] == ["(3a)", "(3b)"]
    assert abs(float(labels[0].attrib["xMax"]) - float(labels[1].attrib["xMax"])) < 0.05


def test_short_calculation_stays_together_when_it_reaches_a_page_boundary(tmp_path: Path) -> None:
    # A live Notes 12 calculation originally left its first row on page 8 and
    # its two continuation rows on page 9. Enough preceding prose reproduces
    # that boundary without relying on the full blog post as a test fixture.
    rows = [r"x^2 = 1234", r"= 5678", r"\leq 9012"]
    post = Post(
        "Page boundary",
        URL,
        body=[Node("paragraph", children=[Node("text", "Preceding prose.")]) for _ in range(34)]
        + [display(row) for row in rows],
    )
    words = rendered_words(tmp_path, post)
    rhs = [word for word in words if (word.text or "").endswith(("1234", "5678", "9012"))]
    assert len(rhs) == 3
    assert len({word.attrib["page"] for word in rhs}) == 1


def test_long_aligned_source_run_can_paginate_without_losing_rows(tmp_path: Path) -> None:
    rows = [rf"x_{{{n}}} = {1000 + n}" for n in range(40)]
    words = rendered_words(tmp_path, Post("Long system", URL, body=[display(row) for row in rows]))
    right_sides = {str(1000 + n) for n in range(40)}
    rhs = [word for word in words if (word.text or "").lstrip("=") in right_sides]
    assert len(rhs) == 40
    assert len({word.attrib["page"] for word in rhs}) > 1
