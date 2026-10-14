"""Atomic artifact storage with explicit, deterministic gzip compression."""

import gzip
import io
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


def read_bytes(path: Path) -> bytes:
    """Read original bytes from a plain file or an explicit ``.gz`` artifact."""
    content = path.read_bytes()
    return gzip.decompress(content) if path.suffix == ".gz" else content


def read_text(path: Path) -> str:
    """Read UTF-8 without normalizing the source's line endings."""
    return read_bytes(path).decode("utf-8")


def read_json(path: Path) -> Any:
    return json.loads(read_text(path))


def write_bytes(path: Path, content: bytes) -> None:
    """Install a complete artifact atomically, compressing explicit ``.gz`` paths.

    The gzip header carries no source filename or wall-clock timestamp, so the
    same input produces the same compressed artifact on every conversion.
    """
    if path.suffix == ".gz":
        buffer = io.BytesIO()
        with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as file:
            file.write(content)
        content = buffer.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}-", suffix=".tmp", delete=False
        ) as file:
            temporary = Path(file.name)
            file.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_text(path: Path, text: str) -> None:
    write_bytes(path, text.encode("utf-8"))


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
