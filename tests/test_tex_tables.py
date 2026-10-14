"""A supported tabular diagram retains cells, alignments and partial rules."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tao2typ.tex_tables import render_tex_table

YOUNG = (
    r"\begin{tabular}{|c|c|c|c|c|c|} \hline 1 & 1 & 1 & 2 & 3 & 3 \\ "
    r"\cline{1-6} 2 & 2 & 2 &3\\ \cline{1-4} 3 & 3\\ \cline{1-2} \end{tabular}"
)


def test_sparse_schur_tableau_preserves_partial_rules_and_no_absent_cell_boxes() -> None:
    cells: list[str] = []

    def render_cell(cell: str) -> str:
        cells.append(cell)
        return "$" + cell + "$"

    markup = render_tex_table(YOUNG, render_cell)
    assert markup is not None
    assert cells == ["1", "1", "1", "2", "3", "3", "2", "2", "2", "3", "3", "3"]
    assert "table.hline(y: 0, start: 0, end: 6" in markup
    assert "table.hline(y: 1, start: 0, end: 6" in markup
    assert "table.hline(y: 2, start: 0, end: 4" in markup
    assert "table.hline(y: 3, start: 0, end: 2" in markup
    assert "table.vline(x: 2, start: 2, end: 3" in markup
    assert "table.vline(x: 3, start: 2" not in markup
    assert "table.cell(x: 2, y: 2" not in markup


def test_tabular_delimiters_inside_groups_and_escaped_ampersands_are_cell_content() -> None:
    cells: list[str] = []

    def render_cell(cell: str) -> str:
        cells.append(cell)
        return ""

    source = r"\begin{tabular}{|lcr|} {a&b} & \text{A\&B} & |x| \\ &y\end{tabular}"
    markup = render_tex_table(source, render_cell)
    assert markup is not None
    assert cells == ["{a&b}", r"\text{A\&B}", "|x|", "y"]
    assert "align: (left, center, right,)" in markup
    assert "table.cell(x: 0, y: 1, [])" in markup  # Explicit empty, not an absent cell.


@pytest.mark.parametrize(
    "source",
    [
        r"x + \begin{tabular}{c}1\end{tabular}",
        r"\begin{tabular}{||c|}1\end{tabular}",
        r"\begin{tabular}{p{2cm}}1\end{tabular}",
        r"\begin{tabular}[t]{c}1\end{tabular}",
        r"\begin{tabular}{c}1&2\end{tabular}",
        r"\begin{tabular}{c}1\\[2pt]2\end{tabular}",
        r"\begin{tabular}{c}1\hline\end{tabular}",
        r"\begin{tabular}{c}\hline\hline 1\end{tabular}",
        r"\begin{tabular}{c}\cline{0-1}1\end{tabular}",
        r"\begin{tabular}{c}\cline{1-2}1\end{tabular}",
        r"\begin{tabular}{c}\multicolumn{1}{c}{1}\end{tabular}",
        r"\begin{tabular}{c}{1\end{tabular}",
    ],
)
def test_unsupported_or_malformed_tables_are_not_silently_approximated(source: str) -> None:
    def unexpected_callback(cell: str) -> str:
        pytest.fail("No cells should render when the whole table is unsupported")

    assert render_tex_table(source, unexpected_callback) is None


def test_schur_tableau_compiles_with_native_cell_positions_and_rules(tmp_path: Path) -> None:
    compiler = shutil.which("typst")
    if compiler is None:
        pytest.skip("Typst is required for tableau verification")
    markup = render_tex_table(YOUNG, lambda cell: "$" + cell + "$")
    assert markup is not None
    source = tmp_path / "young-tableau.typ"
    source.write_text(markup + "\n#context metadata(query(table).first())", encoding="utf-8")
    result = subprocess.run(
        [compiler, "query", str(source), "metadata", "--field", "value"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    table = json.loads(result.stdout)[0]
    assert len(table["columns"]) == 6
    assert len([child for child in table["children"] if child["func"] == "cell"]) == 12
