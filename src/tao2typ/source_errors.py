"""Annotate reviewed source defects without guessing mathematical corrections.

The small data registry matches exact source text, post URL and comment ID.
Changed formulas and identical text elsewhere remain normal mathematical nodes.
"""

import hashlib
import json
from pathlib import Path

from tao2typ.ir import Comment, Node, Post

_DATA = Path(__file__).with_name("data") / "source-errors.json"
_FORMULAS = {
    (item["post_url"], item["comment_id"], item["tex_sha256"]): item
    for item in json.loads(_DATA.read_text(encoding="utf-8"))["formulas"]
}


def mark_source_errors(post: Post) -> None:
    def nodes(body: list[Node], comment_id: str) -> None:
        for root in body:
            for node in root.walk():
                if node.kind != "math":
                    continue
                digest = hashlib.sha256(node.text.strip().encode()).hexdigest()
                match = _FORMULAS.get((post.url, comment_id, digest))
                if match is None or node.text.strip() != match["tex"]:
                    continue
                node.attrs["source_error_evidence"] = match["evidence_page"]
                if match.get("render_as") == "source-image":
                    node.kind = "image"
                    node.attrs["url"] = match["source_image_url"]
                    node.attrs["alt"] = "Formula rendered by the original blog"
                    post.warnings.append("Original source image retained for centerline layout.")
                    continue
                node.attrs["source_error"] = match["reason"]
                node.attrs["source_error_url"] = post.url + "#" + comment_id
                post.warnings.append(
                    f"Unrenderable original formula in {comment_id}; exact TeX retained."
                )

    def visit(comments: list[Comment]) -> None:
        for comment in comments:
            nodes(comment.body, comment.id)
            visit(comment.replies)

    nodes(post.body, "")
    visit(post.comments)
