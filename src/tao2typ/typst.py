"""Render the intermediate representation as safe, self-contained Typst source."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import datetime

from tao2typ.ir import Comment, Node, Post

MITEX_VERSION = "0.2.7"


def _string(value: str) -> str:
    """Encode a Typst string literal, never interpreting source text as code."""
    escaped: list[str] = []
    replacements = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
    for character in value:
        if character in replacements:
            escaped.append(replacements[character])
        elif ord(character) < 32:
            escaped.append(f"\\u{{{ord(character):x}}}")
        else:
            escaped.append(character)
    return '"' + "".join(escaped) + '"'


def _label(identifier: str) -> str:
    return "tao-" + hashlib.sha256(identifier.encode()).hexdigest()[:20]


def _positive(value: str, default: int = 1) -> int:
    try:
        return max(1, int(value))
    except ValueError:
        return default


def _friendly_date(value: str | None) -> str:
    if not value:
        return "Date unavailable"
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return f"{date.day} {date:%B %Y}"
    except ValueError:
        return value


class _Renderer:
    def __init__(self, post: Post, assets: Mapping[str, str]) -> None:
        self.post = post
        self.assets = assets
        self.anchors = {
            node.attrs["id"] for node in post.walk() if node.kind == "anchor" and "id" in node.attrs
        }
        self.emitted_anchors: set[str] = set()

    def nodes(self, nodes: list[Node]) -> str:
        return "".join(self.node(node) for node in nodes)

    def blocks(self, nodes: list[Node]) -> str:
        return "\n\n".join(self.node(node) for node in nodes)

    def node(self, node: Node) -> str:
        kind = node.kind
        if kind == "text":
            return f"#text({_string(node.text)})"
        if kind == "anchor":
            identifier = node.attrs.get("id", "")
            if identifier in self.emitted_anchors:
                return ""
            self.emitted_anchors.add(identifier)
            return f"#metadata(none) <{_label(identifier)}>"
        if kind == "break":
            return "#linebreak()"
        if kind == "math":
            return self.math(node)
        if kind == "image":
            return self.image(node)
        if kind == "link":
            url = node.attrs.get("url", self.post.url)
            base, separator, fragment = url.partition("#")
            if (
                separator
                and base.rstrip("/") == self.post.url.split("#")[0].rstrip("/")
                and fragment in self.anchors
            ):
                destination = f"<{_label(fragment)}>"
            else:
                destination = _string(url)
            return f"#link({destination})[{self.nodes(node.children)}]"
        inline = {
            "strong": "strong",
            "emphasis": "emph",
            "strike": "strike",
            "sub": "sub",
            "sup": "super",
        }
        if kind in inline:
            return f"#{inline[kind]}[{self.nodes(node.children)}]"
        if kind == "code":
            return f"#raw({_string(node.text)})"
        if kind == "paragraph":
            content = self.nodes(node.children)
            if node.attrs.get("align") == "center":
                return f"#align(center)[{content}]"
            return content
        if kind == "heading":
            level = min(6, _positive(node.attrs.get("level", "1")))
            return f"#heading(level: {level})[{self.nodes(node.children)}]"
        if kind == "quote":
            return (
                "#block(width: 100%, breakable: true, "
                "inset: (left: 12pt, right: 10pt, top: 5pt, bottom: 5pt), "
                'stroke: (left: 1.5pt + rgb("98a4b6")))[\n'
                f"{self.blocks(node.children)}\n]"
            )
        if kind == "list":
            name = "enum" if node.attrs.get("ordered") == "true" else "list"
            start = f"start: {_positive(node.attrs.get('start', '1'))}, " if name == "enum" else ""
            items = ",\n".join("[" + self.blocks(item.children) + "]" for item in node.children)
            return f"#{name}({start}\n{items}\n)"
        if kind == "list_item":
            return self.blocks(node.children)
        if kind == "codeblock":
            language = node.attrs.get("language", "")
            argument = f", lang: {_string(language)}" if language else ""
            return f"#raw({_string(node.text)}, block: true{argument})"
        if kind == "divider":
            return '#line(length: 100%, stroke: 0.5pt + rgb("d9dfe8"))'
        if kind == "figure":
            return f"#align(center)[\n{self.blocks(node.children)}\n]"
        if kind == "caption":
            return (
                '#align(center)[#text(size: 9pt, fill: rgb("596477"))['
                f"{self.nodes(node.children)}]]"
            )
        if kind == "table":
            return self.table(node)
        if kind in {"table_cell", "table_row"}:
            return self.blocks(node.children)
        raise ValueError(f"Cannot render unknown IR node kind: {kind}")

    def math(self, node: Node) -> str:
        display = node.attrs.get("display") == "true"
        source = node.text.strip()
        number = node.attrs.get("number")
        if display:
            # WordPress bakes equation numbers into image TeX. Preserve those
            # numbers explicitly, rather than renumbering the article.
            match = re.search(r"(?:\\[ ,;!]|\\quad|\\qquad)+\s*\((\d+[a-z]?)\)\s*$", source)
            if match:
                number = number or match.group(1)
                source = source[: match.start()].rstrip()
            source = re.sub(r"^\\displaystyle\s*", "", source)
        command = "mitex" if display else "mi"
        equation = f"#{command}({_string(source)})"
        if number and display:
            return (
                "#grid(columns: (1fr, auto), align: (center, right + horizon), "
                f"column-gutter: 10pt, [{equation}], [#text({_string('(' + number + ')')})])"
            )
        return equation

    def image(self, node: Node) -> str:
        url = node.attrs.get("url", "")
        alt = node.attrs.get("alt", "")
        if url in self.assets:
            # Files are relative to the generated .typ file. Typst owns path
            # resolution, so never interpolate URL paths into executable code.
            path = _string(self.assets[url])
            alternative = f", alt: {_string(alt)}" if alt else ""
            return f"#image({path}, width: 90%{alternative})"
        description = alt or "Image from the original post"
        return (
            '#box(inset: 6pt, stroke: 0.5pt + rgb("d9dfe8"))['
            f"#link({_string(url)})[#text(size: 9pt)[#text({_string(description)})]]]"
        )

    def table(self, node: Node) -> str:
        if not node.children:
            return ""
        columns = max(
            sum(_positive(cell.attrs.get("colspan", "1")) for cell in row.children)
            for row in node.children
        )
        cells: list[str] = []
        for row in node.children:
            row_cells: list[str] = []
            for cell in row.children:
                content = self.blocks(cell.children)
                if cell.attrs.get("header") == "true":
                    content = "#strong[" + content + "]"
                colspan = _positive(cell.attrs.get("colspan", "1"))
                rowspan = _positive(cell.attrs.get("rowspan", "1"))
                row_cells.append(f"table.cell(colspan: {colspan}, rowspan: {rowspan})[{content}]")
            cells.extend(row_cells)
        return (
            f'#table(columns: {columns}, inset: 7pt, stroke: 0.5pt + rgb("d9dfe8"),\n'
            + ",\n".join(cells)
            + "\n)"
        )

    def comment(self, comment: Comment, depth: int = 0) -> str:
        date = f" · {_friendly_date(comment.published_at)}" if comment.published_at else ""
        header = (
            f"#link({_string(comment.url)})[#strong[#text({_string(comment.author)})]]"
            f'#text(size: 9pt, fill: rgb("596477"), {_string(date)})'
        )
        body = self.blocks(comment.body)
        current = (
            f"#block(width: 100%, breakable: true, above: 10pt, below: 6pt)[\n{header}\n\n{body}\n]"
        )
        replies = "\n\n".join(self.comment(reply, depth + 1) for reply in comment.replies)
        if replies:
            # Cap indentation to keep very deep discussions readable.
            inset = "10pt" if depth < 3 else "0pt"
            current += f"\n\n#pad(left: {inset})[\n{replies}\n]"
        return current


def render_post(post: Post, assets: Mapping[str, str] | None = None) -> str:
    """Render a post with an optional source-URL -> local-image-path mapping.

    Typst's pinned MiTeX package typesets the original LaTeX. All source prose,
    TeX, URLs, and filenames enter the document through string literals.
    """
    renderer = _Renderer(post, assets or {})
    preamble = f"""// Generated from tao2typ IR schema {post.schema_version}.
// Original article: {post.url.replace(chr(10), " ").replace(chr(13), " ")}
#import "@preview/mitex:{MITEX_VERSION}": mi, mitex
#set document(title: {_string(post.title)}, author: {_string(post.author)})
#set page(paper: "a4", margin: (x: 24mm, y: 23mm), numbering: "1", number-align: center)
#set text(font: ("Libertinus Serif", "New Computer Modern"), size: 11pt, lang: "en")
#set par(justify: true, leading: 0.65em, spacing: 0.9em)
#set heading(numbering: none)
#show heading.where(level: 1): set text(size: 15pt, weight: "semibold")
#show heading.where(level: 2): set text(size: 12pt, weight: "semibold")
#show link: set text(fill: rgb("275d8c"))
#set list(indent: 1.2em, body-indent: 0.6em, spacing: 0.6em)
#set enum(indent: 1.2em, body-indent: 0.6em, spacing: 0.6em)
#set math.equation(numbering: none)
#show raw.where(block: true): it => block(
  width: 100%, breakable: true, fill: rgb("f5f6f8"), inset: 9pt, radius: 3pt,
)[#text(size: 9pt)[#it]]

#text(size: 9pt, fill: rgb("596477"))[#text({_string(post.blog_title)})]
#v(5pt)
#text(size: 23pt, weight: "semibold")[#text({_string(post.title)})]
#v(7pt)
#text(size: 10pt)[#text({_string(post.author + " · " + _friendly_date(post.published_at))})]
#v(3pt)
#text(size: 8pt)[#link({_string(post.url)})[#text({_string(post.url)})]]
#v(9pt)
#line(length: 100%, stroke: 0.7pt + rgb("d9dfe8"))
#v(10pt)
"""
    result = preamble + "\n" + renderer.blocks(post.body) + "\n"
    if post.comments:
        count = sum(1 for _ in _comments(post.comments))
        result += (
            "\n#pagebreak(weak: true)\n"
            f"#heading(level: 1)[#text({_string(f'Comments ({count})')})]\n\n"
        )
        result += "\n\n".join(renderer.comment(comment) for comment in post.comments)
        result += "\n"
    return result


def _comments(comments: list[Comment]):
    for comment in comments:
        yield comment
        yield from _comments(comment.replies)
