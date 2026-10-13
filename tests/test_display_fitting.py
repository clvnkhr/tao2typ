"""A real probability product fits the text measure without changing its source."""

from pathlib import Path

from test_display_alignment import URL, display, rendered_words

from tao2typ.ir import Node, Post


def test_wide_probability_product_fits_inside_both_text_margins(tmp_path: Path) -> None:
    # The sunflower-lemma post's unnumbered display extended 21pt into both
    # margins at 11pt. A small uniform reduction preserves its existing row.
    source = (
        r"{\mathbb P}( ({\bf X}, {\bf X}^{\prime}) = (X,X^{\prime}) )"
        r" = 1_{f(X)=f(X^{\prime})} {\mathbb P}( f({\bf X}) = f(X) )"
        r" {\mathbb P}( {\bf X} = X | f({\bf X}) = f(X) )"
        r" {\mathbb P}( {\bf X} = X^{\prime} | f({\bf X}) = f(X) )."
    )
    post = Post(
        "Probability product",
        URL,
        body=[
            Node("paragraph", children=[Node("text", "BEFORE")]),
            display(source),
            Node("paragraph", children=[Node("text", "AFTER")]),
        ],
    )
    words = rendered_words(tmp_path, post)
    before = next(word for word in words if word.text == "BEFORE")
    after = next(word for word in words if word.text == "AFTER")
    formula = [
        word
        for word in words
        if float(before.attrib["yMax"]) < float(word.attrib["yMin"]) < float(after.attrib["yMin"])
    ]
    assert formula
    # A4 with the actual 24mm horizontal margins used by the converter.
    assert min(float(word.attrib["xMin"]) for word in formula) >= 68.03 - 0.05
    assert max(float(word.attrib["xMax"]) for word in formula) <= 527.245 + 0.05
    assert max(float(word.attrib["yMax"]) for word in formula) < float(after.attrib["yMin"]) - 3
    assert [node.text for node in post.walk() if node.kind == "math"] == [source]
