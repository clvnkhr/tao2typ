"""Compatibility fixes preserve source modes, glyphs, and attachment semantics."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_legacy_math_text import contents
from test_math_scripts import attachments, compiled_tree, flattened_text

from tao2typ.ir import Node, Post
from tao2typ.tex_compat import normalize_tex_compat
from tao2typ.typst import render_post


def native_math(tmp_path: Path, source: str) -> Any:
    compiler = shutil.which("typst")
    if compiler is None:
        pytest.skip("Typst is required for native compatibility verification")
    post = Post("Compatibility probe", "https://example.org/", body=[Node("math", source)])
    document = render_post(post)
    marker = "#set math.equation(numbering: none)"
    document = document.replace(
        marker,
        marker + "\n#show math.equation: it => [#metadata(it.body) <compat-probe> #it]",
    )
    path = tmp_path / "compat-probe.typ"
    path.write_text(document)
    result = subprocess.run(
        [compiler, "query", str(path), "<compat-probe>", "--field", "value"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    # Boxed text emits another inline equation after the outer one is observed.
    # The largest recorded tree contains the complete outer math expression.
    return max(json.loads(result.stdout), key=lambda tree: len(contents(tree)))


def test_math_italic_correction_keeps_zero_kern_and_text_contexts() -> None:
    source = r"{P:Q\rightarrow\/{\bf Z}} + \text{\textit{f}\/x} + \hbox{f\/x}"
    assert normalize_tex_compat(source) == (
        r"{P:Q\rightarrow\hspace{0pt}{\bf Z}} + \text{\textit{f}\/x} + \hbox{f\/x}"
    )


def test_noop_boldmath_preserves_groups_and_following_italic_atoms(tmp_path: Path) -> None:
    source = r"{\boldmath v}^2+w+\boldmath z"
    normalized = normalize_tex_compat(source)
    assert normalized == r"{ v}^2+w+ z"
    tree = compiled_tree(tmp_path, normalized)
    assert flattened_text(attachments(tree)[0]["base"]) == "v"
    assert flattened_text(attachments(tree)[0]["top"]) == "2"
    assert not any(
        node.get("func") == "styled" for node in contents(native_math(tmp_path, normalized))
    )
    assert normalize_tex_compat(r"\text{\boldmath $v$}") == r"\text{\boldmath $v$}"


def test_dashed_arrow_retains_relation_class_and_scripts(tmp_path: Path) -> None:
    tree = native_math(tmp_path, normalize_tex_compat(r"A\dashrightarrow^f X"))
    classes = [node for node in contents(tree) if node.get("func") == "class"]
    assert any(node.get("class") == "relation" for node in classes)
    assert any(node.get("text") == "⇢" for node in contents(tree))
    script = next(node for node in contents(tree) if node.get("func") == "attach")
    assert any(node.get("text") == "⇢" for node in contents(script["base"]))
    assert any(node.get("text") == "f" for node in contents(script["t"]))


@pytest.mark.parametrize("command", ["text", "hbox", "mbox", "textit"])
def test_acute_accents_are_text_characters_only(command: str, tmp_path: Path) -> None:
    source = rf"\{command}{{Cram\'er, Cram\'{{e}}r}}"
    normalized = normalize_tex_compat(source)
    assert normalized == rf"\{command}{{Cramér, Cramér}}"
    assert "Cramér" in flattened_text(compiled_tree(tmp_path, normalized))
    assert normalize_tex_compat(r"x\'e + \text{\'{ab}}") == r"x\'e + \text{\'{ab}}"


@pytest.mark.parametrize(("number", "roman"), [(3, "iii"), (14, "xiv"), (0, ""), (-2, "")])
def test_numeric_roman_expansion_keeps_literal_math_letters(number: int, roman: str) -> None:
    assert normalize_tex_compat(rf"\romannumeral{number}\Rightarrow") == roman + r"\Rightarrow"


def test_roman_macro_script_consumes_one_expanded_token(tmp_path: Path) -> None:
    normalized = normalize_tex_compat(r"x^\romannumeral3(y)")
    tree = compiled_tree(tmp_path, normalized)
    assert flattened_text(attachments(tree)[0]["top"]) == "i"
    assert flattened_text(tree).endswith("ii(y)")


@pytest.mark.parametrize("offset", ["2pt", "3pt"])
def test_average_integral_bar_is_zero_width_text_with_exact_offset(
    tmp_path: Path, offset: str
) -> None:
    source = rf"\rlap{{\hspace{{{offset}}}-}}\int_X f\,d\mu"
    normalized = normalize_tex_compat(source)
    assert normalized == rf"\mathclap{{\hspace{{{offset}}}\text{{-}}}}\int_X f\,d\mu"
    tree = native_math(tmp_path, normalized)
    box = next(node for node in contents(tree) if node.get("func") == "box")
    assert box["width"] in {"0pt", "0% + 0pt"}
    assert any(node.get("text") == "-" for node in contents(box))
    assert any(node.get("amount") == offset for node in contents(box))
    integral = next(
        node
        for node in contents(tree)
        if node.get("func") == "attach"
        and any(child.get("text") == "∫" for child in contents(node["base"]))
    )
    assert any(node.get("text") == "X" for node in contents(integral["b"]))


@pytest.mark.parametrize(
    "source",
    [
        r"\centerline k, \langle D\rangle=0",
        r"\rlap{\hspace{\delta}-}\int",
        r"\rlap{\hspace{3ex}-}\int",
        r"\romannumeral\count0",
        r"\romannumeral123456",
        r"\text{\dashrightarrow\romannumeral3}",
        r"\verb|\/\dashrightarrow| % \/\dashrightarrow" + "\n",
        r"\text{unclosed \/",
    ],
)
def test_unverified_forms_comments_and_verbatim_are_not_reinterpreted(source: str) -> None:
    assert normalize_tex_compat(source) == source
