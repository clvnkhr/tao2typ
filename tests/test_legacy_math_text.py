"""Legacy horizontal boxes retain text spacing and operator styling."""

from pathlib import Path

from test_display_alignment import URL, display, rendered_words

from tao2typ.ir import Post
from tao2typ.typst import _legacy_tex


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
