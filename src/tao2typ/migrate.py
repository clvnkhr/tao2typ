"""Rename existing artifacts without fetching or recompiling the archive."""

import os
from pathlib import Path

from tao2typ.naming import artifact_stems
from tao2typ.state import ArchiveState


def _archive_path(output: Path, relative: str) -> Path:
    path = output / relative
    if Path(relative).is_absolute() or not path.resolve().is_relative_to(output.resolve()):
        raise ValueError(f"Artifact path escapes the archive: {relative}")
    return path


def _move_without_overwrite(source: Path, destination: Path) -> None:
    # A hard link creates the destination atomically and fails if it already
    # exists. Both names live in the same directory/filesystem, preserving bytes.
    os.link(source, destination)
    try:
        source.unlink()
    except Exception:
        destination.unlink()
        raise


def migrate_existing_archive(output: Path) -> dict[str, int]:
    """Rename state-referenced PDF, Typst and IR files; preserve history/assets.

    All conflicts are checked before moving anything. A failure rolls completed
    moves back, and state is committed only after every file move succeeds.
    Missing files keep their existing state paths so a later conversion can
    repair them; an interrupted rename with an existing target is recovered.
    """
    state_path = output / "state.json"
    state = ArchiveState.load(state_path)
    stems = artifact_stems(state.posts.values())
    moves: list[tuple[Path, Path]] = []
    sources: set[Path] = set()
    targets: set[Path] = set()
    changed_posts: set[int] = set()
    counts = {"renamed": 0, "missing": 0, "posts": 0}

    def plan_move(relative: str, stem: str, *, required: bool) -> str | None:
        source = _archive_path(output, relative)
        destination = source.with_name(stem + source.suffix)
        if source.is_symlink() or destination.is_symlink():
            raise ValueError(f"Artifact is not a regular file: {relative}")
        if source == destination:
            if required and not source.is_file():
                counts["missing"] += 1
            return None
        if not source.exists():
            if required and destination.is_file():
                return destination.relative_to(output).as_posix()
            if required:
                counts["missing"] += 1
            return None
        if not source.is_file():
            raise ValueError(f"Artifact is not a regular file: {relative}")
        if destination.exists() or destination in targets:
            raise FileExistsError(f"Refusing to overwrite existing artifact: {destination}")
        if source in sources:
            raise ValueError(f"Multiple state entries refer to artifact: {relative}")
        sources.add(source)
        targets.add(destination)
        moves.append((source, destination))
        return destination.relative_to(output).as_posix()

    for post in state.posts.values():
        for attribute in ("pdf", "typst", "ir"):
            relative = getattr(post, attribute)
            if relative is not None:
                new_relative = plan_move(relative, stems[post.id], required=True)
                if new_relative is not None:
                    setattr(post, attribute, new_relative)
                    changed_posts.add(post.id)
        # Failed conversions have diagnostic source/IR outside the canonical
        # state paths. Keep those inspectable under the same readable names.
        legacy_stem = f"{post.published_at[:10]}-{post.id}"
        for suffix in (".typ", ".json"):
            relative = f"failures/{legacy_stem}{suffix}"
            if plan_move(relative, stems[post.id], required=False) is not None:
                changed_posts.add(post.id)
    completed: list[tuple[Path, Path]] = []
    try:
        for source, destination in moves:
            _move_without_overwrite(source, destination)
            completed.append((source, destination))
        if changed_posts:
            state.save(state_path)
    except Exception:
        for source, destination in reversed(completed):
            _move_without_overwrite(destination, source)
        raise
    counts["renamed"] = len(completed)
    counts["posts"] = len(changed_posts)
    return counts
