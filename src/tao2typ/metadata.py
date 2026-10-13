"""Metadata-only inventory updates never fetch articles or compile PDFs."""

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from tao2typ.source import BlogClient, PostMetadata
from tao2typ.state import ArchiveState, PostState


def merge_metadata(state: ArchiveState, posts: Iterable[PostMetadata]) -> int:
    count = 0
    for metadata in posts:
        key = str(metadata.id)
        if key not in state.posts:
            state.posts[key] = PostState(
                metadata.id,
                metadata.url,
                metadata.title,
                metadata.published_at.isoformat(),
                metadata.modified_at.isoformat(),
            )
        post = state.posts[key]
        post.url = metadata.url
        post.title = metadata.title
        post.published_at = metadata.published_at.isoformat()
        post.modified_at = metadata.modified_at.isoformat()
        post.tags = list(metadata.tags)
        post.categories = list(metadata.categories)
        count += 1
    return count


def sync_metadata(output: Path, client: BlogClient) -> dict[str, int]:
    state_path = output / "state.json"
    # Materialize first, so a failed inventory leaves prior metadata intact.
    posts = list(client.discover_posts())
    state = ArchiveState.load(state_path)
    count = merge_metadata(state, posts)
    state.last_discovered_at = datetime.now(UTC).isoformat()
    state.save(state_path)
    return {
        "posts": count,
        "tags": len({term.id for post in state.posts.values() for term in post.tags}),
        "categories": len({term.id for post in state.posts.values() for term in post.categories}),
    }
