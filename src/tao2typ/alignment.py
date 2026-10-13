"""Choose conservative alignment points in a TeX rendering copy.

Only existing display rows are grouped. Operators inside fences, environments,
brace groups or script arguments cannot become alignment points for that row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tao2typ.tex import _CLOSE, _CLOSE_ALIASES, _OPEN, _SPACE_COMMANDS, _tokens

_RELATIONS = frozenset(
    {
        "=",
        "<",
        ">",
        r"\le",
        r"\leq",
        r"\ge",
        r"\geq",
        r"\lt",
        r"\gt",
        r"\ll",
        r"\gg",
        r"\sim",
        r"\simeq",
        r"\approx",
        r"\equiv",
        r"\neq",
        r"\ne",
        r"\lesssim",
        r"\gtrsim",
        r"\in",
        r"\notin",
        r"\subset",
        r"\subseteq",
        r"\supset",
        r"\supseteq",
        r"\to",
        r"\rightarrow",
        r"\leftarrow",
        r"\leftrightarrow",
        r"\mapsto",
        r"\longrightarrow",
        r"\Longrightarrow",
        r"\Longleftrightarrow",
        r"\implies",
        r"\iff",
        r"\rightharpoonup",
        r"\rightharpoondown",
        r"\leftharpoonup",
        r"\leftharpoondown",
        r"\rightrightarrows",
        r"\rightharpoons",
        r"\rightleftharpoons",
        r"\propto",
        r"\perp",
        r"\prec",
        r"\preceq",
        r"\succ",
        r"\succeq",
        r"\doteq",
        r"\xrightarrow",
        r"\xleftarrow",
    }
)
_BINARY = frozenset(
    {"+", "-", r"\pm", r"\mp", r"\times", r"\cdot", r"\oplus", r"\otimes", r"\cup", r"\cap"}
)
_STYLE = frozenset({r"\displaystyle", r"\textstyle", r"\scriptstyle", r"\scriptscriptstyle"})


@dataclass(frozen=True)
class Pivot:
    position: int
    kind: str  # relation, explicit, binary, or leading +/- continuation


def alignment_pivot(source: str) -> Pivot | None:
    """Prefer a top-level relation, retaining an existing explicit alignment.

    Binary operators are a fallback. A leading sign is only a continuation candidate;
    the renderer must require a preceding row that establishes the alignment group.
    Input fences should already have been normalized in the rendering copy.
    """
    tokens = _tokens(source)
    scopes: list[str] = []
    relation = binary = continuation = None
    operand = False
    script = False
    for index, token in enumerate(tokens):
        value = token.text
        if value.startswith(r"\begin"):
            scopes.append("environment")
            continue
        if value.startswith(r"\end"):
            if scopes and scopes[-1] == "environment":
                scopes.pop()
                operand = True
            continue
        if token.direction == "left":
            scopes.append("explicit")
            continue
        if token.direction == "right":
            # Explicit pairs may have different glyphs; actual TeX groups are strict.
            while scopes and scopes[-1] not in {"explicit", "}"}:
                scopes.pop()
            if scopes and scopes[-1] == "explicit":
                scopes.pop()
                operand = True
            continue
        if value in _OPEN:
            scopes.append(_OPEN[value])
            script = False
            continue
        if value in _CLOSE:
            closing = _CLOSE_ALIASES.get(value, value)
            if scopes and (
                scopes[-1] == closing or scopes[-1] in {")", "]"} and closing in {")", "]"}
            ):
                scopes.pop()
                operand = True
            continue
        if scopes:
            continue
        if value in _SPACE_COMMANDS or value in _STYLE:
            continue
        if script:
            script = False
            operand = True
            continue
        if value in {"^", "_"}:
            script = True
            continue
        if value == "&":
            return Pivot(token.start, "explicit")
        if value in _RELATIONS:
            position = token.start
            # Treat := as one relation rather than putting its colon in the LHS.
            if value == "=" and index and tokens[index - 1].text == ":":
                position = tokens[index - 1].start
            relation = relation or Pivot(position, "relation")
            operand = False
        elif value in _BINARY:
            if operand:
                binary = binary or Pivot(token.start, "binary")
            elif value in {"+", "-"} and binary is None and relation is None:
                continuation = continuation or Pivot(token.start, "continuation")
            operand = False
        elif value in {r"\overset", r"\underset", r"\stackrel", r"\mathrel"}:
            # Known decorated relations are one operator, including their annotations.
            rest = source[token.end :]
            groups = re.match(r"\s*\{[^{}]*\}\s*\{\s*([^{}]+?)\s*\}", rest)
            single = re.match(r"\s*\{\s*([^{}]+?)\s*\}", rest)
            inner = single[1] if value == r"\mathrel" and single else groups[1] if groups else ""
            if inner in _RELATIONS:
                relation = relation or Pivot(token.start, "relation")
            operand = True
        else:
            operand = True
    return relation or continuation or binary


def with_alignment(source: str) -> str | None:
    """Insert a native alignment marker without changing the original TeX/IR."""
    pivot = alignment_pivot(source)
    if pivot is None:
        return None
    if pivot.kind == "explicit":
        return source
    return source[: pivot.position] + " & " + source[pivot.position :]
