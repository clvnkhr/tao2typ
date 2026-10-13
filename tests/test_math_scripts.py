"""Script boundaries are checked in compiled math, rather than source spelling."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tao2typ.ir import Node, Post
from tao2typ.typst import render_post

# Record semantic content after the real renderer and MiTeX have run. Whitespace
# content is discarded; its effect on the parse tree remains fully observable.
_SCRIPT_PROBE = r"""
#let script-tree(it) = {
  if type(it) != content { return repr(it) }
  if repr(it.func()) == "space" { return none }
  if it.func() == math.attach {
    let fields = it.fields()
    return (
      kind: "attach", base: script-tree(it.base),
      top: if fields.at("t", default: none) != none { script-tree(it.t) },
      bottom: if fields.at("b", default: none) != none { script-tree(it.b) },
    )
  }
  if it.func() == math.accent { return script-tree(it.base) }
  if it.func() == math.frac {
    return (kind: "frac", numerator: script-tree(it.num), denominator: script-tree(it.denom))
  }
  if it.has("children") {
    return it.children.map(script-tree).filter(it => it != none)
  }
  if it.has("body") { return script-tree(it.body) }
  if it.has("child") { return script-tree(it.child) }
  if it.has("text") { return it.text }
  (kind: repr(it.func()))
}
#show math.equation: it => [#metadata(script-tree(it.body)) <script-probe> #it]
"""


def compiled_tree(tmp_path: Path, source: str) -> Any:
    compiler = shutil.which("typst")
    if compiler is None:
        pytest.skip("Typst is required to verify the compiled script tree")
    node = Node("math", source)
    post = Post("Math script regression", "https://example.org/script-regression/", body=[node])
    typst = render_post(post)
    marker = "#set math.equation(numbering: none)"
    assert marker in typst
    document = tmp_path / "script-regression.typ"
    document.write_text(typst.replace(marker, marker + "\n" + _SCRIPT_PROBE), encoding="utf-8")
    result = subprocess.run(
        [compiler, "query", str(document), "<script-probe>", "--field", "value"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    trees = json.loads(result.stdout)
    assert trees
    assert node.text == source  # Rendering must not mutate the original IR.
    # A fraction inside a script can introduce a nested inline equation. The
    # outer equation is the last one recorded by its enclosing show rule.
    return trees[-1]


def flattened_text(tree: Any) -> str:
    if isinstance(tree, str):
        return tree
    if isinstance(tree, list):
        return "".join(flattened_text(child) for child in tree)
    if isinstance(tree, dict):
        return "".join(flattened_text(value) for key, value in tree.items() if key != "kind")
    return ""


@pytest.mark.parametrize(
    ("source", "base", "top", "bottom", "argument"),
    [
        (r"L^p([0,1])", "L", "p", "", "([0,1])"),
        (r"L^p ([0,1])", "L", "p", "", "([0,1])"),
        (r"L^{p}([0,1])", "L", "p", "", "([0,1])"),
        (r"x_i(y)", "x", "", "i", "(y)"),
        (r"x_{i+1}(y)", "x", "", "i+1", "(y)"),
        (r"x^{2n}(y)", "x", "2n", "", "(y)"),
        (r"x^\alpha(y)", "x", "α", "", "(y)"),
        (r"L^\infty_t([0,T])", "L", "∞", "t", "([0,T])"),
        (r"L^3_x(\mathbb{R}^3)", "L", "3", "x", "(R3)"),
        (r"L^{3,q}_x({\bf R}^3)", "L", "3,q", "x", "(R3)"),
        (r"\dot B_{p,q}^{-1+3/p}({\bf R}^3)", "B", "−1+3/p", "p,q", "(R3)"),
        (r"x^2_3(y)", "x", "2", "3", "(y)"),
        (r"x_3^2(y)", "x", "2", "3", "(y)"),
        (r"x^{\frac{p}{q}}(y)", "x", "pq", "", "(y)"),
        (r"x_{\substack{i=1\\j=2}}(y)", "x", "", "i=1j=2", "(y)"),
    ],
)
def test_tex_scripts_end_before_the_following_parenthesized_argument(
    tmp_path: Path, source: str, base: str, top: str, bottom: str, argument: str
) -> None:
    tree = compiled_tree(tmp_path, source)
    assert isinstance(tree, list)
    attachment, *siblings = tree
    assert attachment["kind"] == "attach"
    assert flattened_text(attachment["base"]) == base
    assert flattened_text(attachment["top"]) == top
    assert flattened_text(attachment["bottom"]) == bottom
    assert flattened_text(siblings) == argument


def test_unbraced_tex_script_consumes_exactly_one_token(tmp_path: Path) -> None:
    tree = compiled_tree(tmp_path, r"x^12+y_ab")
    assert isinstance(tree, list)
    assert flattened_text(tree[0]["top"]) == "1"
    assert tree[1:3] == ["2", "+"]
    assert flattened_text(tree[3]["bottom"]) == "a"
    assert tree[4] == "b"


def test_nested_tex_script_group_retains_its_own_attachment(tmp_path: Path) -> None:
    tree = compiled_tree(tmp_path, r"x^{a_i}(y)")
    assert isinstance(tree, list)
    script = tree[0]["top"]
    assert isinstance(script, dict)
    assert script["kind"] == "attach"
    assert script["base"] == "a"
    assert flattened_text(script["bottom"]) == "i"
    assert flattened_text(tree[1:]) == "(y)"


def attachments(tree: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(tree, list):
        for child in tree:
            result.extend(attachments(child))
    elif isinstance(tree, dict):
        if tree.get("kind") == "attach":
            result.append(tree)
        for value in tree.values():
            result.extend(attachments(value))
    return result


def test_navier_stokes_mixed_norm_has_independent_time_and_space_scripts(tmp_path: Path) -> None:
    source = r"\|u\|_{L^q_t L^p_x([0,T_*)\times {\bf R}^3)}"
    tree = compiled_tree(tmp_path, source)
    scripts = attachments(tree)
    norm = next(script for script in scripts if flattened_text(script["base"]) == "‖u‖")
    lebesgue_spaces = [
        script for script in attachments(norm["bottom"]) if flattened_text(script["base"]) == "L"
    ]
    assert len(lebesgue_spaces) == 2
    time, space = lebesgue_spaces
    assert flattened_text(time["top"]) == "q"
    assert flattened_text(time["bottom"]) == "t"
    assert flattened_text(space["top"]) == "p"
    assert flattened_text(space["bottom"]) == "x"
    # The domain of the mixed norm remains at the baseline of its subscript,
    # rather than becoming part of p or x.
    assert "([0,T" in flattened_text(norm["bottom"])
    assert "×R3)" in flattened_text(norm["bottom"])


def test_limit_subscript_and_grouped_fractional_power_stay_separate(tmp_path: Path) -> None:
    tree = compiled_tree(tmp_path, r"\liminf_{t\to T_*}(T_*-t)^{\frac12-\frac{3}{2p}}")
    assert isinstance(tree, list)
    limit = tree[0]
    assert limit["kind"] == "attach"
    assert limit["top"] is None
    assert flattened_text(limit["bottom"]) == "t→T*"
    fractional_power = tree[-1]
    assert fractional_power["kind"] == "attach"
    assert fractional_power["base"] == ")"
    assert fractional_power["bottom"] is None
    assert flattened_text(fractional_power["top"]) == "12−32p"
    fractions = fractional_power["top"]
    assert fractions[0]["kind"] == "frac"
    assert fractions[2]["kind"] == "frac"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("R'", r"R^{\prime}"),
        ("x'_i(y)", r"x^{\prime}_i(y)"),
        ("x_i'(y)", r"x_i^{\prime}(y)"),
        ("x''_i(y)", r"x^{\prime\prime}_i(y)"),
        ("x_i''(y)", r"x_i^{\prime\prime}(y)"),
        ("x_{i'}'(y)", r"x_{i^{\prime}}^{\prime}(y)"),
        ("x'^2", r"x^{\prime 2}"),
        ("x''^{2n}", r"x^{\prime\prime 2n}"),
        ("x'^p", r"x^{\prime p}"),
        (r"x'^\alpha", r"x^{\prime \alpha}"),
        (r"x'^\alpha*", r"x^{\prime \alpha}*"),
        ("x'^{a_i'}", r"x^{\prime a_i^{\prime}}"),
        (r"x^{\prime}_i", r"x^{\prime}_i"),
        (r"x_i^{\prime\prime}", r"x_i^{\prime\prime}"),
        (r"\text{It's p' or x_i'.}", r"\text{It's p' or x_i'.}"),
        (r"\operatorname{can't}(x)", r"\operatorname{can't}(x)"),
        (r"\'{e}", r"\'{e}"),
        ("x % Don't parse this p'\n+y", "x % Don't parse this p'\n+y"),
    ],
)
def test_prime_normalization_preserves_tex_argument_semantics(source: str, expected: str) -> None:
    from tao2typ.tex import normalize_scripts

    assert normalize_scripts(source) == expected


@pytest.mark.parametrize(
    ("source", "top", "bottom"),
    [
        ("x'_i(y)", "′", "i"),
        ("x_i'(y)", "′", "i"),
        ("x''_i(y)", "′′", "i"),
        ("x_i''(y)", "′′", "i"),
        ("x'^2_i(y)", "′2", "i"),
        ("x''^{2n}_i(y)", "′′2n", "i"),
        ("x_{i'}'(y)", "′", "i′"),
    ],
)
def test_prime_and_index_attach_to_the_same_base(
    tmp_path: Path, source: str, top: str, bottom: str
):
    tree = compiled_tree(tmp_path, source)
    assert isinstance(tree, list)
    attachment, *siblings = tree
    assert attachment["kind"] == "attach"
    assert flattened_text(attachment["base"]) == "x"
    assert flattened_text(attachment["top"]) == top
    assert flattened_text(attachment["bottom"]) == bottom
    assert flattened_text(siblings) == "(y)"


def test_navier_stokes_annulus_endpoint_prime_is_a_superscript(tmp_path: Path) -> None:
    tree = compiled_tree(tmp_path, "R'")
    assert tree["kind"] == "attach"
    assert tree["base"] == "R"
    assert flattened_text(tree["top"]) == "′"
