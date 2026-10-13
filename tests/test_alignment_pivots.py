"""Outer alignment pivots survive the constructions in independent lecture notes."""

from __future__ import annotations

import pytest

from tao2typ.alignment import alignment_pivot, with_alignment
from tao2typ.tex import normalize_delimiters


@pytest.mark.parametrize(
    ("source", "outer_relation"),
    [
        # 245B Notes12, post1844: the set-builder conditions inside each sum
        # contain relations that cannot become the display's alignment point.
        (
            r"\displaystyle g(x) \leq \sum_{j: x \in U_{x_j}}"
            r" g(x_j) \psi_j(x) + O(\varepsilon)",
            r"\leq \sum",
        ),
        (
            r"\displaystyle \leq \sum_{j: x \in U_{x_j}} g(x_j) \psi_j(x)"
            r" \sum_{n=1}^{N_{x_j}} f_n(x_j) \psi_j(x) + O(\varepsilon)",
            r"\leq \sum",
        ),
        (
            r"\displaystyle \sum_{j: N_{x_j} \geq n} f_n(x_j) \psi_j(x)"
            r" \leq \sum_{j: N_{x_j} \geq n} f_n(x) \psi_j(x)"
            r" + O(\varepsilon/2^n) = f_n(x) + O(\varepsilon/2^n)",
            r"\leq \sum",
        ),
        (
            r"\displaystyle \sup \{\mu(K): K \subset E, \hbox{closed}\}"
            r" = \inf \{\mu(U): U \supset E, \hbox{op{}en}\} = \mu(E),",
            r"= \inf",
        ),
        # 245B Notes2, post1354: cardinality fences contain intersections,
        # sums and scripts, while the outer equality/inequality remains visible.
        (
            r"2 |D \cap \Gamma_N| = 2 |e^{i\theta}(D) \cap \Gamma_N|",
            "= 2",
        ),
        (
            r"\leq |e^{i\theta}(D \cup (D+v)) \cap \Gamma_{N+5}|",
            r"\leq",
        ),
        (
            r"\leq \sum_{j=1}^J \sum_{k=1}^K"
            r" |e^{i(\theta+\theta_j)} D_{j,k} \cap \Gamma_{N+10}|",
            r"\leq \sum",
        ),
        (
            r"A_\pm := 5^{{\Bbb Z}} \cdot \{"
            r" \begin{pmatrix} x \\ y \\ z \end{pmatrix}:"
            r" x,y,z \in {\Bbb Z}, x=\pm 3y \hbox{mod} 5, z=0 \hbox{mod} 5 \}",
            ":=",
        ),
        # Half-open intervals and relations within normalized fences remain
        # internal even when their closing glyph differs from the opening one.
        (r"|f([0,T)) + g((0,T])| = 1234", "= 1234"),
        (r"\|f\|_{L^p([0,T))} = 5678", "= 5678"),
        (r"\|\mathbf 1_{[0,T)}\|_2 \leq C", r"\leq C"),
        (r"|a=b| + |c=d| \geq 0", r"\geq 0"),
        (r"x_{a=b}^{c \leq d} \geq 0", r"\geq 0"),
        (r"\frac{x=y}{z} = 1234", "= 1234"),
        (r"(a=b) + (c=d) \leq 0", r"\leq 0"),
        (r"\operatorname{a=b} x \geq y", r"\geq y"),
    ],
)
def test_alignment_uses_the_outer_relation_without_changing_math_content(
    source: str, outer_relation: str
) -> None:
    prepared = normalize_delimiters(source)
    pivot = alignment_pivot(prepared)
    assert pivot is not None
    assert pivot.kind == "relation"
    assert pivot.position == prepared.index(outer_relation)
    aligned = with_alignment(prepared)
    assert aligned is not None
    # The alignment marker is the sole change: fences, scripts, conditions,
    # relations and punctuation retain their exact rendering-copy order.
    assert aligned.count(" & ") == 1
    assert aligned.replace(" & ", "", 1) == prepared


def test_source_typo_is_preserved_when_a_leading_relation_is_aligned() -> None:
    # Notes12's third continuation has this unmatched ')' in the original
    # source. Alignment can use the leading relation without repairing it.
    source = (
        r"\displaystyle \leq \sum_{n=1}^\infty \sum_{j: N_{x_j} \geq n}"
        r" f_n(x_j) \psi_j) + O(\varepsilon)."
    )
    aligned = with_alignment(source)
    assert aligned is not None
    assert r"\psi_j)" in aligned
    assert aligned.replace(" & ", "", 1) == source


@pytest.mark.parametrize(
    "source",
    [
        r"{x = 1}",
        r"x \overset{\text{def}}{=} y",
        r"x \stackrel{a_{i=1}}{\leq} y",
    ],
)
def test_unsupported_grouped_or_nested_decorated_relations_are_left_unaligned(source: str) -> None:
    # These are conservative omissions, not guesses at a relation hidden in an
    # outer group or a decorated command whose argument grammar is unsupported.
    assert alignment_pivot(source) is None
    assert with_alignment(source) is None


def test_hidden_group_relations_do_not_override_a_top_level_binary_fallback() -> None:
    source = r"{x = 1} + {y = 2}"
    pivot = alignment_pivot(source)
    assert pivot is not None
    assert pivot.kind == "binary"
    assert source[pivot.position] == "+"


def test_existing_explicit_alignment_points_are_not_rewritten() -> None:
    source = r"x & = \frac{a=b}{c} & + y"
    pivot = alignment_pivot(source)
    assert pivot is not None
    assert pivot.kind == "explicit"
    assert with_alignment(source) == source
