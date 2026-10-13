"""Versioned, output-independent representation of a blog post.

The HTML parser never emits Typst. Renderers never need BeautifulSoup. The JSON
form keeps original TeX and source URLs, making it useful for future renderers.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1


@dataclass
class Node:
    """A semantic content node; ``attrs`` contains only string-valued metadata.

    Block kinds: paragraph, heading, quote, list, list_item, table, table_row,
    table_cell, codeblock, figure, caption, divider. Inline kinds: text, math,
    image, link, anchor, strong, emphasis, strike, code, sub, sup, break.
    Math stores *original LaTeX*, with ``display=true`` for displayed equations.
    An external source label uses ``number``; ``punctuation`` and
    ``number_suffix`` retain punctuation before and after that label.
    """

    kind: str
    text: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)

    def walk(self) -> Iterator[Node]:
        yield self
        for child in self.children:
            yield from child.walk()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "attrs": self.attrs,
            "children": [child.to_dict() for child in self.children],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Node:
        return cls(
            kind=str(value["kind"]),
            text=str(value.get("text", "")),
            attrs={str(k): str(v) for k, v in value.get("attrs", {}).items()},
            children=[cls.from_dict(child) for child in value.get("children", [])],
        )


@dataclass
class Comment:
    id: str
    author: str
    published_at: str | None
    url: str
    body: list[Node] = field(default_factory=list)
    replies: list[Comment] = field(default_factory=list)
    avatar: Node | None = None

    def walk(self) -> Iterator[Node]:
        if self.avatar is not None:
            yield from self.avatar.walk()
        for node in self.body:
            yield from node.walk()
        for reply in self.replies:
            yield from reply.walk()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "author": self.author,
            "published_at": self.published_at,
            "url": self.url,
            "body": [node.to_dict() for node in self.body],
            "replies": [reply.to_dict() for reply in self.replies],
            "avatar": self.avatar.to_dict() if self.avatar is not None else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Comment:
        return cls(
            id=str(value["id"]),
            author=str(value["author"]),
            published_at=value.get("published_at"),
            url=str(value["url"]),
            body=[Node.from_dict(node) for node in value.get("body", [])],
            replies=[cls.from_dict(reply) for reply in value.get("replies", [])],
            avatar=Node.from_dict(value["avatar"])
            if isinstance(value.get("avatar"), dict)
            else None,
        )


@dataclass
class Post:
    title: str
    url: str
    published_at: str | None = None
    modified_at: str | None = None
    body: list[Node] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    author: str = "Terence Tao"
    blog_title: str = "What’s new"
    warnings: list[str] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    source_manifest: str | None = None

    def walk(self) -> Iterator[Node]:
        for node in self.body:
            yield from node.walk()
        for comment in self.comments:
            yield from comment.walk()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "title": self.title,
            "source_manifest": self.source_manifest,
            "url": self.url,
            "published_at": self.published_at,
            "modified_at": self.modified_at,
            "author": self.author,
            "blog_title": self.blog_title,
            "body": [node.to_dict() for node in self.body],
            "comments": [comment.to_dict() for comment in self.comments],
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Post:
        version = value.get("schema_version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported IR schema version: {version}")
        return cls(
            title=str(value["title"]),
            source_manifest=str(value["source_manifest"])
            if value.get("source_manifest") is not None
            else None,
            url=str(value["url"]),
            published_at=value.get("published_at"),
            modified_at=value.get("modified_at"),
            author=str(value.get("author", "Terence Tao")),
            blog_title=str(value.get("blog_title", "What’s new")),
            body=[Node.from_dict(node) for node in value.get("body", [])],
            comments=[Comment.from_dict(comment) for comment in value.get("comments", [])],
            warnings=[str(warning) for warning in value.get("warnings", [])],
        )
