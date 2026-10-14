"""Legacy TeX compatibility retains text styling and complete math atoms."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_display_alignment import URL, display, rendered_words
from test_math_scripts import attachments, compiled_tree, flattened_text

from tao2typ.ir import Node, Post
from tao2typ.typst import _legacy_tex, render_post


def test_hbox_phrases_render_as_spaced_words_without_altering_source(tmp_path: Path) -> None:
    source = r"x = y \hbox{ for some } y \hbox{ mod } q \hbox{ for all } i"
    post = Post("Horizontal text", URL, body=[display(source)])
    words = rendered_words(tmp_path, post)
    text = " ".join(word.text or "" for word in words)
    assert "for some" in text
    assert "mod" in text
    assert "for all" in text
    assert "forsome" not in text and "foralli" not in text
    assert post.body[0].children[0].text == source


def test_hbox_operator_names_use_text_and_primitive_box_forms_are_retained() -> None:
    assert _legacy_tex(r"\hbox{Ran}(T) + \hbox {Log}(z)") == r"\text{Ran}(T) + \text {Log}(z)"
    source = r"\hbox to 0pt{abc} + \hboxx{def}"
    assert _legacy_tex(source) == source


@pytest.mark.parametrize("command", ["bf", "rm", "it", "sf", "frak", "tt", "cal"])
def test_empty_font_declarations_keep_group_boundaries(command: str) -> None:
    source = rf"(0,1)\times{{ \{command} }}T"
    assert _legacy_tex(source) == r"(0,1)\times{}T"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (r"\frac{\bf}{2}", r"\frac{}{2}"),
        (r"x_{\rm}", r"x_{}"),
        (r"y^{\it}", r"y^{}"),
        (r"\hat{\cal}T", r"\hat{}T"),
        (r"\sqrt{\tt}n", r"\sqrt{}n"),
    ],
)
def test_empty_font_argument_does_not_consume_the_following_atom(
    source: str, expected: str
) -> None:
    assert _legacy_tex(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        r"{\bf T} + {\rm R} + {\it x} + {\sf y} + {\frak g} + {\tt z} + {\cal A}",
        r"{\Bbb}T + {\mathbf}T + {\mathbb}T + {\frac}x + {\bff}T",
        r"\{\bf\} + \{ \rm } + {\bf\}",
    ],
)
def test_only_isolated_empty_font_declarations_are_normalized(source: str) -> None:
    assert _legacy_tex(source) == source


def test_empty_font_declarations_and_arguments_compile_without_changing_ir(
    tmp_path: Path,
) -> None:
    declarations = [
        rf"(0,1)\times{{\{command}}}T" for command in ("bf", "rm", "it", "sf", "frak", "tt", "cal")
    ]
    arguments = r"\frac{\bf}{2} + x_{\rm} + y^{\it} + \hat{\cal}T + \sqrt{\tt}n"
    sources = declarations + [arguments]
    post = Post("Empty font declarations", URL, body=[display(source) for source in sources])
    words = rendered_words(tmp_path, post)
    # Each declaration's scope ends at the empty group; every following T is
    # ordinary mathematical italic, including the one after the empty accent.
    assert sum((word.text or "").count("𝑇") for word in words) == len(declarations) + 1
    assert [node.text for node in post.walk() if node.kind == "math"] == sources


def native_math(tmp_path: Path, source: str) -> Any:
    compiler = shutil.which("typst")
    if compiler is None:
        pytest.skip("Typst is required for native math verification")
    post = Post("Legacy math probe", URL, body=[Node("math", source)])
    document = render_post(post)
    marker = "#set math.equation(numbering: none)"
    document = document.replace(
        marker,
        marker + "\n#show math.equation: it => [#metadata(it.body) <legacy-probe> #it]",
    )
    path = tmp_path / "legacy-probe.typ"
    path.write_text(document)
    result = subprocess.run(
        [compiler, "query", str(path), "<legacy-probe>", "--field", "value"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert post.body[0].text == source
    return json.loads(result.stdout)[-1]


def contents(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value] + [node for child in value.values() for node in contents(child)]
    if isinstance(value, list):
        return [node for child in value for node in contents(child)]
    return []


def test_text_superscript_is_an_independent_upright_text_script(tmp_path: Path) -> None:
    tree = compiled_tree(tmp_path, r"i\textsuperscript{th}(x)")
    scripts = attachments(tree)
    assert len(scripts) == 1
    assert flattened_text(scripts[0]["base"]) == "\u200b"
    assert flattened_text(scripts[0]["top"]) == "th"
    assert flattened_text(tree).endswith("(x)")
    native = native_math(tmp_path, r"i\textsuperscript{t{h}}")
    assert any(node.get("func") == "text" and node.get("text") == "th" for node in contents(native))


def test_text_superscript_as_a_script_argument_retains_both_script_levels(tmp_path: Path) -> None:
    tree = compiled_tree(tmp_path, r"i^\textsuperscript{th}(x)")
    scripts = attachments(tree)
    assert len(scripts) == 2
    assert flattened_text(scripts[0]["base"]) == "i"
    assert flattened_text(scripts[1]["base"]) == "\u200b"
    assert scripts[1] in attachments(scripts[0]["top"])
    assert flattened_text(scripts[1]["top"]) == "th"
    assert flattened_text(tree).endswith("(x)")


@pytest.mark.parametrize(
    "source",
    [r"\textsuperscript th", r"\textsuperscript{th", r"\textsuperscripted{th}"],
)
def test_text_superscript_only_expands_complete_braced_commands(source: str) -> None:
    assert _legacy_tex(source) == source


@pytest.mark.parametrize(("command", "arrow"), [("varinjlim", "→"), ("varprojlim", "←")])
def test_variant_limit_is_an_operator_with_arrow_and_separate_outer_limits(
    tmp_path: Path, command: str, arrow: str
) -> None:
    tree = native_math(tmp_path, rf"\{command}_{{n\to\infty}}^m x_n")
    script = next(
        node
        for node in contents(tree)
        if node.get("func") == "attach" and node.get("base", {}).get("func") == "op"
    )
    operator = script["base"]
    assert operator["limits"] is True
    assert any(node.get("text") == arrow for node in contents(operator))
    assert {node["text"] for node in contents(operator) if isinstance(node.get("text"), str)} >= {
        "l",
        "i",
        "m",
    }
    assert [node["text"] for node in contents(script["t"]) if node.get("func") == "symbol"] == ["m"]
    assert any(node.get("text") == "∞" for node in contents(script["b"]))
    assert not any(node.get("text") == "x" for node in contents(operator))


@pytest.mark.parametrize(
    ("source", "radicand"),
    [(r"\sqrt[]{x+1}", "x+1"), (r"\sqrt[ ]x", "x"), (r"\sqrt[]n(x)", "n")],
)
def test_empty_optional_root_index_preserves_radical_and_argument(
    tmp_path: Path, source: str, radicand: str
) -> None:
    tree = native_math(tmp_path, source)
    roots = [node for node in contents(tree) if node.get("func") == "root"]
    assert len(roots) == 1
    assert "index" not in roots[0] or roots[0]["index"] is None
    assert (
        "".join(
            node["text"]
            for node in contents(roots[0]["radicand"])
            if isinstance(node.get("text"), str)
        )
        == radicand
    )


def test_nonempty_root_indices_are_not_normalized() -> None:
    source = r"\sqrt[3]{x} + \sqrt[n]{y}"
    assert _legacy_tex(source) == source
