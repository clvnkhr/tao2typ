"""Reviewed source errors are contextual annotations, never inferred repairs."""

import hashlib
import json
import re
from pathlib import Path

import pytest
from test_display_alignment import rendered_words

from tao2typ import source_errors
from tao2typ.ir import Comment, Node, Post
from tao2typ.source_errors import mark_source_errors
from tao2typ.typst import render_post


def reviewed() -> dict[str, str]:
    return next(iter(source_errors._FORMULAS.values()))


def test_registry_entries_have_exact_hashes_context_and_capture_evidence() -> None:
    data = json.loads(source_errors._DATA.read_text())
    assert data["schema_version"] == 1
    keys = set()
    for item in data["formulas"]:
        assert hashlib.sha256(item["tex"].encode()).hexdigest() == item["tex_sha256"]
        assert re.fullmatch(r"comment-\d+", item["comment_id"])
        assert re.fullmatch(r"raw/pages/[a-f0-9]{64}\.html(?:\.gz)?", item["evidence_page"])
        key = (item["post_url"], item["comment_id"], item["tex_sha256"])
        assert key not in keys
        keys.add(key)


def test_exact_error_in_nested_reply_retains_all_original_tex_and_source_number() -> None:
    item = reviewed()
    original = " \n" + item["tex"] + "\n "
    math = Node("math", original, {"display": "true", "number": "17"})
    reply = Comment(item["comment_id"], "Reader", None, "", body=[math])
    parent = Comment("comment-1", "Parent", None, "", replies=[reply])
    post = Post("Source errors", item["post_url"], comments=[parent])
    mark_source_errors(post)
    assert math.text == original
    assert math.attrs == {
        "display": "true",
        "number": "17",
        "source_error": item["reason"],
        "source_error_url": item["post_url"] + "#" + item["comment_id"],
        "source_error_evidence": item["evidence_page"],
    }
    assert len(post.warnings) == 1
    document = render_post(post)
    assert "Source formula could not be rendered." in document
    assert original.replace("\\", "\\\\").replace("\n", "\\n") in document


@pytest.mark.parametrize("different", ["post", "comment", "formula", "body", "plain-text"])
def test_identical_or_changed_text_elsewhere_is_not_marked(different: str) -> None:
    item = reviewed()
    math = Node("math", item["tex"] + ("+1" if different == "formula" else ""))
    if different == "plain-text":
        math.kind = "text"
    comment = Comment(
        item["comment_id"] + ("-other" if different == "comment" else ""),
        "Reader",
        None,
        "",
        body=[math],
    )
    post = Post(
        "Context",
        item["post_url"] + ("other/" if different == "post" else ""),
        body=[math] if different == "body" else [],
        comments=[] if different == "body" else [comment],
    )
    mark_source_errors(post)
    assert "source_error" not in math.attrs
    assert not post.warnings


def test_a_matching_hash_key_still_requires_exact_tex(monkeypatch: pytest.MonkeyPatch) -> None:
    item = reviewed()
    key = (item["post_url"], item["comment_id"], item["tex_sha256"])
    monkeypatch.setattr(source_errors, "_FORMULAS", {key: {**item, "tex": "different"}})
    math = Node("math", item["tex"])
    post = Post(
        "Exact comparison",
        item["post_url"],
        comments=[Comment(item["comment_id"], "Reader", None, "", body=[math])],
    )
    mark_source_errors(post)
    assert math.attrs == {}


def test_reviewed_source_error_note_and_long_tex_compile_without_math_guessing(
    tmp_path: Path,
) -> None:
    item = reviewed()
    source = " ".join([item["tex"]] * 3)
    math = Node(
        "math",
        source,
        {
            "source_error": item["reason"],
            "source_error_url": item["post_url"] + "#" + item["comment_id"],
        },
    )
    post = Post("Original formula", item["post_url"], body=[math])
    words = rendered_words(tmp_path, post)
    text = " ".join(word.text or "" for word in words)
    assert "Source formula could not be rendered." in text
    assert "X_{>N}" in text
    assert math.text == source
    body_words = [word for word in words if "X_{>N}" in (word.text or "")]
    assert len({word.attrib["yMin"] for word in body_words}) >= 3
    assert max(float(word.attrib["xMax"]) for word in words) <= 531
