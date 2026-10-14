"""Durable, reviewable state. A successful PDF is the unit of progress."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tao2typ.source import TaxonomyTerm
from tao2typ.storage import write_json as _write_json


def write_json(path: Path, value: Any) -> None:
    _write_json(path, value)


@dataclass
class PostState:
    id: int
    url: str
    title: str
    published_at: str
    modified_at: str
    last_checked_at: str | None = None
    last_attempted_at: str | None = None
    converted_at: str | None = None
    converted_modified_at: str | None = None
    next_check_at: str | None = None
    content_sha256: str | None = None
    converter_fingerprint: str | None = None
    pdf: str | None = None
    ir: str | None = None
    typst: str | None = None
    source_manifest: str | None = None
    last_error: str | None = None
    pending_comments: list[str] = field(default_factory=list)
    seen_comments: list[str] = field(default_factory=list)
    tags: list[TaxonomyTerm] = field(default_factory=list)
    categories: list[TaxonomyTerm] = field(default_factory=list)


@dataclass
class ArchiveState:
    schema_version: int = 1
    last_discovered_at: str | None = None
    posts: dict[str, PostState] = field(default_factory=dict)
    daily_attempts: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "ArchiveState":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema_version") != 1:
            raise ValueError("Unsupported archive state schema; migrate it before running")
        for post in raw["posts"].values():
            post["tags"] = [TaxonomyTerm(**term) for term in post.get("tags", [])]
            post["categories"] = [TaxonomyTerm(**term) for term in post.get("categories", [])]
        return cls(
            last_discovered_at=raw.get("last_discovered_at"),
            posts={key: PostState(**post) for key, post in raw["posts"].items()},
            daily_attempts=raw.get("daily_attempts", {}),
        )

    def save(self, path: Path) -> None:
        write_json(path, asdict(self))
