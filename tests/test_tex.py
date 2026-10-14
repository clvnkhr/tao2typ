"""Real TeX fence syntax becomes single abs/norm atoms, without changing scripts."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tao2typ.ir import Node, Post
from tao2typ.tex import DELIMITER_HELPERS, normalize_delimiters
from tao2typ.typst import render_post


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            r"\bigg \lceil \left|\frac{\partial f}{\partial z}\right| \bigg\rceil",
            r"\bigg{\left\lceil \left|\frac{\partial f}{\partial z}\right| \right\rceil}",
        ),
        (r"\Bigl\lfloor |x|\Bigr\rfloor", r"\Big{\left\lfloor \left|x\right|\right\rfloor}"),
        (r"\bigl[0,1\bigr)", r"\big{\left[0,1\right)}"),
        (r"\bigl(|x|\big)", r"\bigl(\left|x\right|\big)"),
        (r"\bigl\lceil x\Bigl\rceil", r"\bigl\lceil x\Bigl\rceil"),
        (
            r"\begin{tabular}[t]{|c|c|} |x| & y \\ |z| & q\end{tabular}",
            r"\begin{tabular}[t]{|c|c|} \left|x\right| & y \\ \left|z\right| & q\end{tabular}",
        ),
        (
            r"\begin{array}{|c|c|} \multicolumn{2}{|c|}{|x|} \end{array}",
            r"\begin{array}{|c|c|} \multicolumn{2}{|c|}{\left|x\right|} \end{array}",
        ),
        (
            r"\begin{tabular*}{5cm}[t]{|c|c|} |x| & y\end{tabular*}",
            r"\begin{tabular*}{5cm}[t]{|c|c|} \left|x\right| & y\end{tabular*}",
        ),
        (
            "\\begin% structural comment\n{tabular}{|c|} |x|\\end{tabular}",
            "\\begin% structural comment\n{tabular}{|c|} \\left|x\\right|\\end{tabular}",
        ),
    ],
)
def test_fixed_delimiters_and_structural_table_arguments(source: str, expected: str) -> None:
    assert normalize_delimiters(source) == expected
    assert normalize_delimiters(expected) == expected


def test_sized_ceiling_with_nested_absolute_value_compiles_as_one_fixed_atom(
    tmp_path: Path,
) -> None:
    compiler = shutil.which("typst")
    if compiler is None:
        pytest.skip("Typst is required for native fixed delimiter verification")
    latex = r"\bigg \lceil \left|\frac{\partial f}{\partial z}\right| \bigg\rceil^2"
    source = tmp_path / "ceiling.typ"
    source.write_text(
        '#import "@preview/mitex:0.2.7": mitex-convert, mitex-scope\n'
        + DELIMITER_HELPERS
        + "\n#let input="
        + json.dumps(normalize_delimiters(latex))
        + '\n#metadata(eval("$" + mitex-convert(input) + "$", scope:mitex-scope + '
        + "(lr:tao-lr, bigg:body=>tao-big(body,2.4em))))",
        encoding="utf-8",
    )
    result = subprocess.run(
        [compiler, "query", str(source), "metadata", "--field", "value"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    attachments = [
        item for item in _contents(json.loads(result.stdout)) if item.get("func") == "attach"
    ]
    base = attachments[0]["base"]
    assert isinstance(base, dict)
    assert base["func"] == "lr"
    assert base["size"] == "0% + 2.4em"
    assert base["body"]["children"][0]["text"] == "⌈"
    assert base["body"]["children"][-1]["text"] == "⌉"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("|x+y|", r"\left|x+y\right|"),
        (r"\|x+y\|", r"\left\|x+y\right\|"),
        (r"\Vert x\Vert", r"\left\| x\right\|"),
        (r"\lVert x\rVert", r"\left\| x\right\|"),
        (r"\lvert x\rvert", r"\left| x\right|"),
        (r"\left\lvert x\right\rvert", r"\left| x\right|"),
        (r"\left\lVert x\right\rVert", r"\left\| x\right\|"),
        (r"\bigl|x\bigr|", r"\big{\left|x\right|}"),
        (r"\Bigl\|x\Bigr\|", r"\Big{\left\|x\right\|}"),
        (r"\big|x\big|", r"\big{\left|x\right|}"),
        ("|x + |y||", r"\left|x + \left|y\right|\right|"),
        (r"\| |x|+y\|", r"\left\| \left|x\right|+y\right\|"),
        (r"|\lfloor |x| \rfloor|", r"\left|\lfloor \left|x\right| \rfloor\right|"),
        (r"|\lceil|x|\rceil|", r"\left|\lceil\left|x\right|\rceil\right|"),
        (
            r"|\left\lfloor |x| \right\rfloor|",
            r"\left|\left\lfloor \left|x\right| \right\rfloor\right|",
        ),
        (r"|\sin |x||", r"\left|\sin \left|x\right|\right|"),
        (r"|\sin|", r"\left|\sin\right|"),
        (r"\|\sin\|_p", r"\left\|\sin\right\|_p"),
        (r"|\sin^2 |x||", r"\left|\sin^2 \left|x\right|\right|"),
        (
            r"|\lim_{n\to\infty} |x_n||",
            r"\left|\lim_{n\to\infty} \left|x_n\right|\right|",
        ),
        (r"|\operatorname{sgn}|x||", r"\left|\operatorname{sgn}\left|x\right|\right|"),
        (
            r"|\operatorname* {sgn}|x||",
            r"\left|\operatorname* {sgn}\left|x\right|\right|",
        ),
        (r"|a\|u\||", r"\left|a\left\|u\right\|\right|"),
        (r"\|a|x|\|", r"\left\|a\left|x\right|\right\|"),
        (r"|x\oplus |y||", r"\left|x\oplus \left|y\right|\right|"),
        (r"|x\cdot*|y||", r"\left|x\cdot*\left|y\right|\right|"),
        (r"a|\left|x\right||", r"a\left|\left|x\right|\right|"),
        (r"c ||u||_{H^2_0}", r"c \left|\left|u\right|\right|_{H^2_0}"),
        (r"a|x+|y||", r"a\left|x+\left|y\right|\right|"),
        (r"\|\left|x\right)\|", r"\left\|\left|x\right)\right\|"),
        (
            r"|\left.x\right|_0^1|",
            r"\left|\left.x\right|_0^1\right|",
        ),
        (
            r"|\left.\{ |x| \right)|",
            r"\left|\left.\{ \left|x\right| \right)\right|",
        ),
        (r"|x|_a^b", r"\left|x\right|_a^b"),
        (r"|x_*|/2", r"\left|x_*\right|/2"),
        (r"|x^+|", r"\left|x^+\right|"),
        ("|[0,1)|", r"\left|[0,1)\right|"),
        ("|(0,1]|", r"\left|(0,1]\right|"),
        (r"|\lbrack0,1\rparen|", r"\left|\lbrack0,1\rparen\right|"),
        (r"|\lparen0,1\rbrack|", r"\left|\lparen0,1\rbrack\right|"),
        (
            r"\|\left[|x|,|y|\right)\|",
            r"\left\|\left[\left|x\right|,\left|y\right|\right)\right\|",
        ),
        (r"\|f_{L^p([0,T))}\|", r"\left\|f_{L^p([0,T))}\right\|"),
        (r"\bigl|\left[0,1\right)\bigr|", r"\big{\left|\left[0,1\right)\right|}"),
        (r"x^{|y|}", r"x^{\left|y\right|}"),
        (r"|x^{a_b} + y|", r"\left|x^{a_b} + y\right|"),
        ("2|x|", r"2\left|x\right|"),
        ("|x| |y|", r"\left|x\right| \left|y\right|"),
        ("|u| |w| |v|", r"\left|u\right| \left|w\right| \left|v\right|"),
        ("|u||w||v|", r"\left|u\right|\left|w\right|\left|v\right|"),
        (
            "a|u| |w| |v| |z|",
            r"a\left|u\right| \left|w\right| \left|v\right| \left|z\right|",
        ),
        (
            r"\|u\| \|w\| \|v\|",
            r"\left\|u\right\| \left\|w\right\| \left\|v\right\|",
        ),
        (
            r"|u| \|w\| |v|",
            r"\left|u\right| \left\|w\right\| \left|v\right|",
        ),
        (
            r"|u| |w| \bigl|v\bigr|",
            r"\left|u\right| \left|w\right| \big{\left|v\right|}",
        ),
        (
            r"|u| |w| \left|x\right)",
            r"\left|u\right| |w| \left|x\right)",
        ),
        (r"|u| |w| \left|x", r"\left|u\right| |w| \left|x"),
        (
            r"|u| |w| \left|{x}\right|",
            r"\left|u\right| \left|w\right| \left|{x}\right|",
        ),
        (r"|u| |w| \left|{x\right|}", r"\left|u\right| |w| \left|{x\right|}"),
        ("P(|X|<1|Y=y)", r"P(\left|X\right|<1|Y=y)"),
        (r"\{x : |x|<1\}", r"\{x : \left|x\right|<1\}"),
        (r"\left(|x|+1\right)", r"\left(\left|x\right|+1\right)"),
        (
            r"\|u\|_{L^q_t L^p_x([0,T_*)\times {\bf R}^3)}",
            r"\left\|u\right\|_{L^q_t L^p_x([0,T_*)\times {\bf R}^3)}",
        ),
    ],
)
def test_paired_fences_form_one_math_atom(source: str, expected: str) -> None:
    assert normalize_delimiters(source) == expected
    assert normalize_delimiters(expected) == expected


@pytest.mark.parametrize(
    "source",
    [
        "a|b",
        "a|b|c",
        "a|b + c|d",
        "P(A|B)",
        "P(A|B|C)",
        r"P(A|\sin B)",
        r"P(A|\sin B|C)",
        r"f|_0^1",
        r"\left.f\right|_0^1",
        "|x",
        "x|",
        r"\|x",
        r"x\|",
        r"x^|y|",
        r"x_\|y\|",
        r"\text{a | b | c}",
        r"\mbox{cost | other}",
        r"\operatorname{a|b|c}",
        r"\text{a {nested |word|} b}",
        r"a\mid b + c\mid d",
        r"\left(P(A\middle|B)\right)",
        r"\bigl|x\Bigr|",
        r"\bigm|x\bigm|",
        r"|x\rvert",
        r"\lvert x|",
        r"\left|x\rvert",
        r"\lvert x\right|",
        r"\lVert x\rvert",
        r"{|x} |",
        r"|[0,1}|",
        r"|{x)|",
        r"\left|{x\right|}",
    ],
)
def test_relations_text_unpaired_and_single_token_scripts_stay_untouched(source: str) -> None:
    assert normalize_delimiters(source) == source


def test_tex_comments_cannot_supply_a_matching_fence() -> None:
    assert normalize_delimiters("x|y % |not a closing fence\n") == "x|y % |not a closing fence\n"
    assert normalize_delimiters("|x % |not a closing fence\n+y|") == (
        "\\left|x % |not a closing fence\n+y\\right|"
    )


def _contents(value: object) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    if isinstance(value, dict):
        if "func" in value:
            result.append(value)
        for child in value.values():
            result.extend(_contents(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(_contents(child))
    return result


def test_mitex_fence_helper_wraps_the_entire_native_script_base(tmp_path: Path) -> None:
    compiler = shutil.which("typst")
    if compiler is None:
        pytest.skip("Typst is required for native math atom verification")
    source = tmp_path / "fences.typ"
    inputs = [r"|x+y|^2", r"\|u\|_{L^p([0,1])}", r"\left(x+y\right)"]
    source.write_text(
        '#import "@preview/mitex:0.2.7": mitex-convert, mitex-scope\n'
        + DELIMITER_HELPERS
        + "\n#for input in ("
        + ",".join(json.dumps(normalize_delimiters(value)) for value in inputs)
        + ',) { metadata(eval("$" + mitex-convert(input) + "$", '
        + "scope: mitex-scope + (lr: tao-lr))) }\n",
        encoding="utf-8",
    )
    query = subprocess.run(
        [compiler, "query", str(source), "metadata", "--field", "value"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert query.returncode == 0, query.stderr
    results = json.loads(query.stdout)
    for result in results[:2]:
        attachments = [item for item in _contents(result) if item.get("func") == "attach"]
        assert attachments
        # A complete fence atom is the base, instead of just its closing bar.
        base = attachments[0]["base"]
        assert isinstance(base, dict)
        assert base["func"] == "lr"
    assert any(item.get("func") == "lr" for item in _contents(results[2]))


@pytest.mark.parametrize(
    ("source", "fences"),
    [
        (r"|\lfloor |x| \rfloor|", ["|", "|"]),
        (r"|\left\lfloor |x| \right\rfloor|", ["|", "|"]),
        (r"|\sin |x||", ["|", "|"]),
        (r"|\sin^2 |x||", ["|", "|"]),
        (r"|\lim_{n\to\infty} |x_n||", ["|", "|"]),
        (r"|\operatorname{sgn}|x||", ["|", "|"]),
        (r"|a\|u\||", ["|", "‖"]),
        (r"a|x+|y||", ["|", "|"]),
        (r"\|a|x|\|", ["‖", "|"]),
        (r"|x\oplus |y||", ["|", "|"]),
        (r"\|\left[|x|,|y|\right)\|", ["‖", "|", "|"]),
        (r"\|\left|x\right)\|", ["‖"]),
        (r"|\left.x\right|_0^1|", ["|"]),
    ],
)
def test_renderer_keeps_nested_fences_as_complete_atoms(
    tmp_path: Path, source: str, fences: list[str]
) -> None:
    compiler = shutil.which("typst")
    if compiler is None:
        pytest.skip("Typst is required for native nested fence verification")
    node = Node("math", source + "_p")
    post = Post("Fence regression", "https://example.org/fences/", body=[node])
    document = render_post(post)
    marker = "#set math.equation(numbering: none)"
    document = document.replace(
        marker,
        marker + "\n#show math.equation: it => [#metadata(it.body) <fence-probe> #it]",
    )
    path = tmp_path / "nested-fences.typ"
    path.write_text(document, encoding="utf-8")
    query = subprocess.run(
        [compiler, "query", str(path), "<fence-probe>", "--field", "value"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert query.returncode == 0, query.stderr
    compiled = json.loads(query.stdout)[-1]
    attachments = [item for item in _contents(compiled) if item.get("func") == "attach"]
    base = attachments[0]["base"]
    assert isinstance(base, dict)
    assert base["func"] == "lr"  # The complete outer expression receives _p.
    actual = []
    for item in _contents(base):
        if item.get("func") != "lr":
            continue
        body = item.get("body")
        if not isinstance(body, dict):
            continue
        children = body.get("children")
        if not isinstance(children, list) or len(children) < 2:
            continue
        first, last = children[0], children[-1]
        if isinstance(first, dict) and isinstance(last, dict):
            if first.get("text") in {"|", "‖"} and first.get("text") == last.get("text"):
                actual.append(first["text"])
    assert actual == fences
    assert node.text == source + "_p"  # Original LaTeX is never rewritten in the IR.


@pytest.mark.parametrize(
    ("source", "fences"),
    [
        (r"|u| |w| |\nabla w|", ["|", "|", "|"]),
        (r"|u||w||\nabla w|", ["|", "|", "|"]),
        (r"a |u| |w| |v| |z|", ["|", "|", "|", "|"]),
        (r"\|u\| \|w\| \|v\|", ["‖", "‖", "‖"]),
        (r"|u| \|w\| |v|", ["|", "‖", "|"]),
        (
            r"\displaystyle  |u \cdot \partial_j(w_j w)| \leq |u| |w| |\nabla w|"
            r" \leq \nu |\nabla w|^2 + \frac{1}{4 \nu} |u|^2 |w|^2",
            ["|"] * 7,
        ),
    ],
)
def test_adjacent_products_have_no_detached_vertical_bars(
    tmp_path: Path, source: str, fences: list[str]
) -> None:
    """The live weak-Navier–Stokes bound's middle |w| must be one native atom."""
    compiler = shutil.which("typst")
    if compiler is None:
        pytest.skip("Typst is required for native adjacent fence verification")
    node = Node("math", source, attrs={"display": "true"})
    document = render_post(Post("Adjacent factors", "https://example.org/factors/", body=[node]))
    marker = "#set math.equation(numbering: none)"
    document = document.replace(
        marker,
        marker + "\n#show math.equation: it => [#metadata(it.body) <fence-probe> #it]",
    )
    path = tmp_path / "adjacent-fences.typ"
    path.write_text(document, encoding="utf-8")
    query = subprocess.run(
        [compiler, "query", str(path), "<fence-probe>", "--field", "value"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert query.returncode == 0, query.stderr
    compiled = _contents(json.loads(query.stdout)[-1])
    actual = []
    for item in compiled:
        body = item.get("body")
        if item.get("func") != "lr" or not isinstance(body, dict):
            continue
        children = body.get("children")
        if not isinstance(children, list) or len(children) < 2:
            continue
        first, last = children[0], children[-1]
        if isinstance(first, dict) and isinstance(last, dict):
            if first.get("text") in {"|", "‖"} and first.get("text") == last.get("text"):
                actual.append(first["text"])
    assert actual == fences
    # Every vertical glyph belongs to a complete native pair. None is a loose
    # punctuation atom, which is what gave the middle |w| its incorrect spacing.
    bars = [item for item in compiled if item.get("text") in {"|", "‖"}]
    assert len(bars) == 2 * len(fences)
    assert node.text == source


@pytest.mark.parametrize(
    ("command", "size"), [("big", "1.2em"), ("Big", "1.8em"), ("bigg", "2.4em"), ("Bigg", "3em")]
)
def test_fixed_size_norms_retain_the_pinned_mitex_delimiter_height(
    tmp_path: Path, command: str, size: str
) -> None:
    compiler = shutil.which("typst")
    if compiler is None:
        pytest.skip("Typst is required for delimiter sizing verification")
    source = tmp_path / "sized.typ"
    normalized = normalize_delimiters(f"\\{command}l\\|x\\{command}r\\|^2")
    source.write_text(
        '#import "@preview/mitex:0.2.7": mitex-convert, mitex-scope\n'
        + DELIMITER_HELPERS
        + "\n#let input="
        + json.dumps(normalized)
        + '\n#metadata(eval("$" + mitex-convert(input) + "$", scope:mitex-scope + '
        + "(lr:tao-lr,"
        + command
        + ":body=>tao-big(body,"
        + size
        + "))))",
        encoding="utf-8",
    )
    query = subprocess.run(
        [compiler, "query", str(source), "metadata", "--field", "value"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert query.returncode == 0, query.stderr
    attachments = [
        item for item in _contents(json.loads(query.stdout)) if item.get("func") == "attach"
    ]
    assert len(attachments) == 1
    base = attachments[0]["base"]
    assert isinstance(base, dict)
    assert base["func"] == "lr"
    assert base["size"] == "0% + " + size
