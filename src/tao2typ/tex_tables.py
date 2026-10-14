"""Render simple complete TeX tabular diagrams without discarding their rules.

MiTeX's tabular handler does not support partial horizontal rules (``cline``).
This deliberately small fallback accepts ordinary c/l/r columns, single vertical
rules, hline/cline and possibly short rows. More elaborate TeX remains with the
normal converter and its diagnostics instead of being approximately redrawn.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

_TABLE = re.compile(
    r"(?P<style>(?:\\(?:displaystyle|textstyle)\s*)*)"
    r"\\begin\s*\{tabular\}\s*\{(?P<columns>[^{}]*)\}"
    r"(?P<body>[\s\S]*?)\\end\s*\{tabular\}\s*"
)
_COMMAND = re.compile(r"\\(?:[A-Za-z]+|[^\n])")
_CLINE = re.compile(r"\s*\{\s*(\d+)\s*-\s*(\d+)\s*\}")
_UNSUPPORTED = frozenset(
    {r"\begin", r"\end", r"\multicolumn", r"\multirow", r"\omit", r"\noalign", r"\hhline"}
)


@dataclass
class _Body:
    rows: list[list[str]]
    lines: list[tuple[int, int, int]]


def _body(source: str, columns: int) -> _Body | None:
    rows: list[list[str]] = []
    lines: list[tuple[int, int, int]] = []
    cells: list[str] = []
    cell: list[str] = []
    position, depth = 0, 0
    while position < len(source):
        character = source[position]
        if character == "%":
            newline = source.find("\n", position)
            end = len(source) if newline < 0 else newline + 1
            cell.append(source[position:end])
            position = end
            continue
        if character == "\\":
            command = _COMMAND.match(source, position)
            if command is None:
                return None
            token = command[0]
            position = command.end()
            if token in _UNSUPPORTED:
                return None
            if depth == 0 and token == r"\\":
                # Starred row breaks and explicit row-height overrides need
                # additional layout semantics; leave them to normal diagnostics.
                following = source[position:].lstrip()
                if following.startswith(("*", "[")):
                    return None
                cells.append("".join(cell).strip())
                if len(cells) > columns:
                    return None
                rows.append(cells)
                cells, cell = [], []
                continue
            if depth == 0 and token in {r"\hline", r"\cline"}:
                if cells or "".join(cell).strip():
                    return None  # A rule must be between complete source rows.
                cell = []
                start, end = 0, columns
                if token == r"\cline":
                    bounds = _CLINE.match(source, position)
                    if bounds is None:
                        return None
                    start, end = int(bounds[1]) - 1, int(bounds[2])
                    position = bounds.end()
                    if not 0 <= start < end <= columns:
                        return None
                # Repeated/overlapping rules can express double lines; silently
                # collapsing them into one would change the source appearance.
                if any(y == len(rows) and start < b and end > a for y, a, b in lines):
                    return None
                lines.append((len(rows), start, end))
                continue
            cell.append(token)
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth < 0:
                return None
        elif character == "&" and depth == 0:
            cells.append("".join(cell).strip())
            cell = []
            position += 1
            continue
        cell.append(character)
        position += 1
    if depth:
        return None
    if cells or "".join(cell).strip():
        cells.append("".join(cell).strip())
        if len(cells) > columns:
            return None
        rows.append(cells)
    return _Body(rows, lines) if rows else None


def render_tex_table(source: str, render_cell: Callable[[str], str]) -> str | None:
    """Return safe Typst markup for a supported table, or None without callbacks.

    The callback receives each nonempty present cell's original TeX. Empty cells
    explicitly separated by ``&`` retain their positions. Absent cells in short
    rows stay unruled blanks, retaining the staircase shape of a Young tableau.
    """
    match = _TABLE.fullmatch(source.strip())
    if match is None:
        return None
    specification = re.sub(r"\s+", "", match["columns"])
    if not specification or re.search(r"[^clr|]", specification) or "||" in specification:
        return None
    alignments: list[str] = []
    boundaries: list[int] = []
    for character in specification:
        if character == "|":
            boundaries.append(len(alignments))
        else:
            alignments.append({"c": "center", "l": "left", "r": "right"}[character])
    if not alignments:
        return None
    body = _body(match["body"], len(alignments))
    if body is None:
        return None
    pieces = [
        "#box[#table(",
        "columns: (" + "auto, " * len(alignments) + "),",
        f"rows: {len(body.rows)},",
        "align: (" + ", ".join(alignments) + ",),",
        "inset: (x: 0.2em, y: 0.1em), stroke: none,",
    ]
    for y, row in enumerate(body.rows):
        for x, cell in enumerate(row):
            markup = render_cell(match["style"] + cell) if cell else ""
            pieces.append(f"table.cell(x: {x}, y: {y}, [{markup}]),")
        for x in boundaries:
            if x <= len(row):
                pieces.append(f"table.vline(x: {x}, start: {y}, end: {y + 1}, stroke: 0.4pt),")
    for y, start, end in body.lines:
        pieces.append(f"table.hline(y: {y}, start: {start}, end: {end}, stroke: 0.4pt),")
    pieces.append(")]")
    return "\n".join(pieces)
