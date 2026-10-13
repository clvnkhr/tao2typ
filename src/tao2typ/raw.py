"""Retain exact parser input and a manifest for every individual fetch attempt."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


def load_raw_capture(manifest: Path) -> tuple[dict[str, Any], list[str], Path]:
    """Read a complete capture and verify every input before offline parsing."""
    manifest = manifest.resolve()
    if (
        len(manifest.parents) < 4
        or manifest.name != "manifest.json"
        or manifest.parents[2].name != "raw"
    ):
        raise ValueError("Expected archive/raw/<post>/<timestamp>/manifest.json")
    output = manifest.parents[3]
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    if (
        not isinstance(metadata, dict)
        or metadata.get("schema_version") != 1
        or metadata.get("complete") is not True
        or not isinstance(metadata.get("post_url"), str)
        or not isinstance(metadata.get("pages"), list)
        or not metadata["pages"]
    ):
        raise ValueError("Raw capture must be complete and contain ordered pages")
    pages = []
    for page in metadata["pages"]:
        relative = page.get("path") if isinstance(page, dict) else None
        if not isinstance(relative, str) or not re.fullmatch(
            r"raw/pages/[0-9a-f]{64}\.html", relative
        ):
            raise ValueError("Invalid raw page path")
        path = output / relative
        if path.is_symlink() or not path.resolve().is_relative_to(output / "raw" / "pages"):
            raise ValueError("Raw page path escapes the capture")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if (
            digest != page.get("bytes_sha256")
            or path.stem != digest
            or len(content) != page.get("bytes")
        ):
            raise ValueError(f"Raw page checksum mismatch: {relative}")
        pages.append(content.decode("utf-8"))
    return metadata, pages, output


def _now() -> datetime:
    return datetime.now(UTC)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _save_page(path: Path, content: bytes) -> None:
    """Install a complete blob without ever replacing an existing capture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            dir=path.parent, prefix=".page-", suffix=".tmp", delete=False
        ) as file:
            temporary = Path(file.name)
            file.write(content)
        try:
            path.hardlink_to(temporary)
        except FileExistsError:
            if path.read_bytes() != content:
                raise ValueError(f"Raw page does not match its content hash: {path}") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class RawCapture:
    """Capture one fetch attempt, including ordered comment pagination.

    Every refresh gets its own manifest. Identical UTF-8 parser inputs share a
    content-addressed HTML file. A failed attempt keeps pages already captured;
    a finalized manifest cannot be changed through this object.
    """

    def __init__(
        self,
        output: Path,
        stem: str,
        post_url: str,
        *,
        published_at: str | None = None,
        modified_at: str | None = None,
    ) -> None:
        if not stem or stem in {".", ".."} or "/" in stem or "\\" in stem or "\0" in stem:
            raise ValueError("Raw capture stem must be a simple filename")
        self.output = output
        self._closed = False
        captured_at = _now()
        captures = output / "raw" / stem
        captures.mkdir(parents=True, exist_ok=True)
        # Avoid replacing another attempt even if clocks coincide or move back.
        while True:
            directory = captures / captured_at.strftime("%Y%m%dT%H%M%S.%fZ")
            try:
                directory.mkdir()
                break
            except FileExistsError:
                captured_at += timedelta(microseconds=1)
        self._path = directory / "manifest.json"
        self._manifest: dict[str, Any] = {
            "schema_version": 1,
            "post_url": post_url,
            "published_at": published_at,
            "modified_at": modified_at,
            "captured_at": captured_at.isoformat(),
            "complete": False,
            "pages": [],
        }
        _write_json(self._path, self._manifest)

    @property
    def manifest_path(self) -> str:
        """Return the manifest path relative to the archive directory."""
        return self._path.relative_to(self.output).as_posix()

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("Raw capture has already been finalized")

    def add(
        self,
        html: str,
        url: str,
        *,
        requested_url: str | None = None,
        fetched_at: str | None = None,
    ) -> None:
        """Save one parser input before parsing it, preserving its exact text."""
        self._require_open()
        content = html.encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        relative = f"raw/pages/{digest}.html"
        _save_page(self.output / relative, content)
        self._manifest["pages"].append(
            {
                "url": url,
                "requested_url": requested_url or url,
                "fetched_at": fetched_at or _now().isoformat(),
                "path": relative,
                "bytes_sha256": digest,
                "bytes": len(content),
            }
        )
        _write_json(self._path, self._manifest)

    def finish(self) -> None:
        """Mark a complete fetch sequence and freeze its manifest."""
        self._require_open()
        self._manifest["complete"] = True
        self._manifest["finished_at"] = _now().isoformat()
        _write_json(self._path, self._manifest)
        self._closed = True

    def fail(self, error: str) -> None:
        """Keep partial pages and record why this fetch sequence stopped."""
        self._require_open()
        self._manifest["error"] = error
        self._manifest["finished_at"] = _now().isoformat()
        _write_json(self._path, self._manifest)
        self._closed = True
