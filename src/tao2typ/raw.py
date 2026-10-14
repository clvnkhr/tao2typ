"""Retain exact parser input and a manifest for every individual fetch attempt."""

import hashlib
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from tao2typ.source import ApiPayload
from tao2typ.storage import read_bytes, read_json, write_bytes, write_json


def load_raw_capture(manifest: Path) -> tuple[dict[str, Any], list[str], Path]:
    """Read a complete capture and verify every input before offline parsing."""
    if manifest.is_symlink():
        raise ValueError("Raw manifest cannot be a symlink")
    manifest = manifest.resolve()
    if (
        len(manifest.parents) < 4
        or manifest.name not in {"manifest.json", "manifest.json.gz"}
        or manifest.parents[2].name != "raw"
    ):
        raise ValueError("Expected archive/raw/<post>/<timestamp>/manifest.json[.gz]")
    output = manifest.parents[3]
    metadata = read_json(manifest)
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
    payloads = metadata.get("api_payloads", [])
    if not isinstance(payloads, list):
        raise ValueError("Invalid raw API payload records")
    for payload in payloads:
        relative = payload.get("path") if isinstance(payload, dict) else None
        if not isinstance(relative, str) or not re.fullmatch(
            r"raw/api/[0-9a-f]{64}\.json\.gz", relative
        ):
            raise ValueError("Invalid raw API payload path")
        path = output / relative
        if path.is_symlink() or not path.resolve().is_relative_to(output / "raw" / "api"):
            raise ValueError("Raw API payload path escapes the capture")
        try:
            content = read_bytes(path)
        except (OSError, EOFError) as error:
            raise ValueError(f"Raw API payload checksum mismatch: {relative}") from error
        digest = hashlib.sha256(content).hexdigest()
        if (
            digest != payload.get("bytes_sha256")
            or path.name.removesuffix(".json.gz") != digest
            or len(content) != payload.get("bytes")
        ):
            raise ValueError(f"Raw API payload checksum mismatch: {relative}")
    for page in metadata["pages"]:
        relative = page.get("path") if isinstance(page, dict) else None
        if not isinstance(relative, str) or not re.fullmatch(
            r"raw/pages/[0-9a-f]{64}\.html(?:\.gz)?", relative
        ):
            raise ValueError("Invalid raw page path")
        path = output / relative
        if path.is_symlink() or not path.resolve().is_relative_to(output / "raw" / "pages"):
            raise ValueError("Raw page path escapes the capture")
        try:
            content = read_bytes(path)
        except (OSError, EOFError) as error:
            raise ValueError(f"Raw page checksum mismatch: {relative}") from error
        digest = hashlib.sha256(content).hexdigest()
        if (
            digest != page.get("bytes_sha256")
            or path.name.removesuffix(".gz").removesuffix(".html") != digest
            or len(content) != page.get("bytes")
        ):
            raise ValueError(f"Raw page checksum mismatch: {relative}")
        pages.append(content.decode("utf-8"))
    return metadata, pages, output


def _now() -> datetime:
    return datetime.now(UTC)


def _save_page(path: Path, content: bytes) -> None:
    """Install a complete blob without ever replacing an existing capture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            dir=path.parent, prefix=".page-", suffix=path.suffix, delete=False
        ) as file:
            temporary = Path(file.name)
        write_bytes(temporary, content)
        try:
            path.hardlink_to(temporary)
        except FileExistsError:
            try:
                existing = read_bytes(path) if not path.is_symlink() else None
            except (OSError, EOFError):
                existing = None
            if existing != content:
                raise ValueError(f"Raw page does not match its content hash: {path}") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class RawCapture:
    """Capture one fetch attempt, including ordered comment pagination.

    Every refresh gets its own manifest. Identical UTF-8 parser inputs share a
    content-addressed compressed HTML file. A failed attempt keeps pages already captured;
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
        self._api_payload_keys: set[tuple[str, str, str]] = set()
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
        self._path = directory / "manifest.json.gz"
        self._manifest: dict[str, Any] = {
            "schema_version": 1,
            "post_url": post_url,
            "published_at": published_at,
            "modified_at": modified_at,
            "captured_at": captured_at.isoformat(),
            "complete": False,
            "pages": [],
        }
        write_json(self._path, self._manifest)

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
        api_payloads: Sequence[ApiPayload] = (),
    ) -> None:
        """Save one parser input before parsing it, preserving its exact text."""
        self._require_open()
        for payload in api_payloads:
            self.add_api_payload(payload)
        content = html.encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        relative = f"raw/pages/{digest}.html.gz"
        _save_page(self.output / relative, content)
        self._manifest["pages"].append(
            {
                "url": url,
                "requested_url": requested_url or url,
                "fetched_at": fetched_at or _now().isoformat(),
                "path": relative,
                "bytes_sha256": digest,
                "bytes": len(content),
                **({"source": "wordpress-public-api"} if api_payloads else {}),
            }
        )
        write_json(self._path, self._manifest)

    def add_api_payload(self, payload: ApiPayload) -> None:
        """Retain an exact JSON response immediately, including partial attempts."""
        self._require_open()
        digest = hashlib.sha256(payload.content).hexdigest()
        key = (payload.url, payload.fetched_at, digest)
        if key in self._api_payload_keys:
            return
        relative = f"raw/api/{digest}.json.gz"
        _save_page(self.output / relative, payload.content)
        self._manifest.setdefault("api_payloads", []).append(
            {
                "url": payload.url,
                "requested_url": payload.requested_url,
                "fetched_at": payload.fetched_at,
                "path": relative,
                "bytes_sha256": digest,
                "bytes": len(payload.content),
            }
        )
        self._api_payload_keys.add(key)
        write_json(self._path, self._manifest)

    def finish(self) -> None:
        """Mark a complete fetch sequence and freeze its manifest."""
        self._require_open()
        self._manifest["complete"] = True
        self._manifest["finished_at"] = _now().isoformat()
        write_json(self._path, self._manifest)
        self._closed = True

    def fail(self, error: str) -> None:
        """Keep partial pages and record why this fetch sequence stopped."""
        self._require_open()
        self._manifest["error"] = error
        self._manifest["finished_at"] = _now().isoformat()
        write_json(self._path, self._manifest)
        self._closed = True
