"""Real TeX fence syntax becomes single abs/norm atoms, without changing scripts."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tao2typ.tex import DELIMITER_HELPERS, normalize_delimiters


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
        (r"|x|_a^b", r"\left|x\right|_a^b"),
        (r"|x_*|/2", r"\left|x_*\right|/2"),
        (r"|x^+|", r"\left|x^+\right|"),
        ("|[0,1)|", r"\left|[0,1)\right|"),
        ("|(0,1]|", r"\left|(0,1]\right|"),
        (r"\|f_{L^p([0,T))}\|", r"\left\|f_{L^p([0,T))}\right\|"),
        (r"\bigl|\left[0,1\right)\bigr|", r"\big{\left|\left[0,1\right)\right|}"),
        (r"x^{|y|}", r"x^{\left|y\right|}"),
        (r"|x^{a_b} + y|", r"\left|x^{a_b} + y\right|"),
        ("2|x|", r"2\left|x\right|"),
        ("|x| |y|", r"\left|x\right| \left|y\right|"),
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
