"""Discover local failure evidence and serve checked captures as inert text."""

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any
from zlib import error as ZlibError

from tao2typ.naming import artifact_stems
from tao2typ.state import PostState
from tao2typ.storage import read_bytes, read_json

_RAW_PAGE = re.compile(r"raw/pages/([0-9a-f]{64})\.html(?:\.gz)?")
_READ_ERRORS = (OSError, EOFError, ValueError, ZlibError)
_TEXT_TYPE = "text/plain; charset=utf-8"


def _post_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        try:
            return int(value)
        except ValueError:
            pass
    return None


def failure_stems(posts: list[dict[str, Any]]) -> dict[str, str]:
    """Use the converter's collision-safe names for valid injected post IDs."""
    records: dict[int, PostState] = {}
    for post in posts:
        identifier = _post_id(post.get("id"))
        if identifier is None or identifier in records:
            continue
        values = {
            name: post[name] if isinstance(post.get(name), str) else ""
            for name in ("title", "url", "published_at", "modified_at")
        }
        records[identifier] = PostState(
            id=identifier,
            url=values["url"],
            title=values["title"],
            published_at=values["published_at"],
            modified_at=values["modified_at"],
        )
    return {str(identifier): stem for identifier, stem in artifact_stems(records.values()).items()}


def _safe_file(archive: Path, relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative or "\\" in relative or "\0" in relative:
        return None
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    try:
        if archive.is_symlink() or not archive.is_dir():
            return None
        root = archive.resolve(strict=True)
        path = root
        for part in parts:
            path /= part
            if path.is_symlink():
                return None
        if not path.is_file() or not path.resolve().is_relative_to(root):
            return None
        return path
    except (OSError, RuntimeError, ValueError):
        return None


def _legacy_stem(post: dict[str, Any]) -> str | None:
    identifier = _post_id(post.get("id"))
    published = post.get("published_at")
    if identifier is None or not isinstance(published, str):
        return None
    prefix = published[:10]
    try:
        date.fromisoformat(prefix)
    except ValueError:
        return None
    return f"{prefix}-{identifier}"


def _discover(
    archive: Path, post: dict[str, Any], stem: str
) -> tuple[dict[str, Path], dict[str, dict[str, Any]]]:
    artifacts: dict[str, Path] = {}
    pages: dict[str, dict[str, Any]] = {}
    names = [name for name in (stem, _legacy_stem(post)) if name]
    for key, extension in (("typst", ".typ"), ("ir", ".json")):
        for name in names:
            if "/" in name or "\\" in name or name in {".", ".."}:
                continue
            for suffix in (extension + ".gz", extension):
                if path := _safe_file(archive, f"failures/{name}{suffix}"):
                    artifacts[key] = path
                    break
            if key in artifacts:
                break
    relative = post.get("source_manifest")
    if not isinstance(relative, str):
        return artifacts, pages
    parts = relative.split("/")
    if (
        len(parts) != 4
        or parts[0] != "raw"
        or parts[-1]
        not in {
            "manifest.json",
            "manifest.json.gz",
        }
    ):
        return artifacts, pages
    capture = _safe_file(archive, relative)
    if capture is None:
        return artifacts, pages
    artifacts["capture"] = capture
    try:
        metadata = read_json(capture)
    except _READ_ERRORS:
        return artifacts, pages
    if (
        not isinstance(metadata, dict)
        or metadata.get("schema_version") != 1
        or not isinstance(metadata.get("complete"), bool)
        or not isinstance(metadata.get("post_url"), str)
        or not isinstance(metadata.get("pages"), list)
    ):
        return artifacts, pages
    for index, page in enumerate(metadata["pages"]):
        if not isinstance(page, dict) or not isinstance(page.get("path"), str):
            continue
        if not _RAW_PAGE.fullmatch(page["path"]):
            continue
        if path := _safe_file(archive, page["path"]):
            key = f"html/{index}"
            artifacts[key] = path
            pages[key] = page
    return artifacts, pages


def failure_artifacts(archive: Path, post: dict[str, Any], stem: str) -> dict[str, Path]:
    """List safe existing evidence, including pages from partial captures.

    Listing reads only the manifest. Use read_failure_artifact to verify a raw
    page's contents immediately before serving it.
    """
    return _discover(archive, post, stem)[0]


def read_failure_artifact(
    archive: Path, post: dict[str, Any], stem: str, key: str
) -> tuple[bytes, str] | None:
    """Decompress one diagnostic; verify selected raw HTML and keep it inert."""
    artifacts, pages = _discover(archive, post, stem)
    path = artifacts.get(key)
    if path is None:
        return None
    try:
        # Check again before reading: a stale discovery must not follow a new link.
        safe = _safe_file(archive, path.relative_to(archive.resolve()).as_posix())
        if safe is None:
            return None
        content = read_bytes(safe)
        if key in pages:
            page = pages[key]
            expected = _RAW_PAGE.fullmatch(page["path"])
            digest = hashlib.sha256(content).hexdigest()
            if (
                expected is None
                or digest != expected[1]
                or digest != page.get("bytes_sha256")
                or type(page.get("bytes")) is not int
                or len(content) != page["bytes"]
            ):
                return None
        return content, _TEXT_TYPE
    except _READ_ERRORS:
        return None
