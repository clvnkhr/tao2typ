"""Compress a settled archive without changing PDF bytes or conversion dates."""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tao2typ.state import ArchiveState
from tao2typ.storage import read_bytes, read_json, write_bytes, write_json


def compress_completed_reports(output: Path) -> int:
    """Finish small job reports without rescanning an already compressed archive."""
    compressed = 0
    for source in (output / "jobs").glob("*.json"):
        if source.is_symlink():
            raise ValueError(f"Unsafe job report: {source}")
        if not read_json(source).get("finished_at"):
            continue
        target = source.with_suffix(".json.gz")
        if target.is_symlink():
            raise ValueError(f"Unsafe job report destination: {target}")
        content = source.read_bytes()
        if target.exists() and read_bytes(target) != content:
            raise ValueError(f"Conflicting job reports: {source}")
        write_bytes(target, content)
        if read_bytes(target) != content:
            raise ValueError(f"Job report compression failed: {source}")
        source.unlink()
        compressed += 1
    return compressed


def _non_pdf_bytes(output: Path) -> int:
    return sum(
        path.stat().st_size
        for path in output.rglob("*")
        if path.is_file() and not path.is_symlink() and path.suffix != ".pdf"
    )


def _references(value: Any, paths: dict[str, str]) -> Any:
    """Relink operational records; never apply this to article prose or TeX."""
    if isinstance(value, str):
        return paths.get(value, value)
    if isinstance(value, list):
        return [_references(item, paths) for item in value]
    if isinstance(value, dict):
        return {key: _references(item, paths) for key, item in value.items()}
    return value


def compress_archive(output: Path) -> dict[str, Any]:
    """Migrate settled text artifacts to deterministic gzip.

    Run after a batch stops. Live job reports stay plain. All compressed targets
    are verified before state references change; legacy files are removed last.
    A crash therefore leaves either readable old paths or complete new paths.
    Small working indexes and native image formats stay directly usable.
    """
    output = output.resolve()
    started = datetime.now(UTC)
    state_path = output / "state.json"
    state = ArchiveState.load(state_path)
    before = _non_pdf_bytes(output)
    groups: dict[str, list[Path]] = {}
    candidates = [
        *(output / "raw" / "pages").glob("*.html*"),
        *(output / "raw").glob("*/*/manifest.json*"),
        *(output / "ir").glob("*.json*"),
        *(output / "typst").glob("*.typ*"),
        *(output / "failures").glob("*.json*"),
        *(output / "failures").glob("*.typ*"),
        *(output / "jobs").glob("*.json*"),
    ]
    unfinished = 0
    for path in candidates:
        logical = path.relative_to(output).as_posix().removesuffix(".gz")
        if (
            not logical.endswith((".html", ".json", ".typ"))
            or path.name.startswith(".")
            or logical.endswith((".tmp.typ", ".previous.typ", ".tmp.json", ".previous.json"))
        ):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(output):
            raise ValueError(f"Unsafe compression input: {logical}")
        if not path.is_file():
            continue
        if logical.startswith("jobs/") and not read_json(path).get("finished_at"):
            unfinished += 1
            continue
        groups.setdefault(logical, []).append(path)
    paths = {logical: logical + ".gz" for logical in groups}
    canonical = {
        value
        for post in state.posts.values()
        for value in (post.ir, post.typst, post.source_manifest)
        if value
    }

    def transform(logical: str, content: bytes) -> bytes:
        if logical.startswith("raw/pages/"):
            match = re.fullmatch(r"raw/pages/([0-9a-f]{64})\.html", logical)
            if not match or hashlib.sha256(content).hexdigest() != match[1]:
                raise ValueError(f"Raw page checksum mismatch: {logical}")
        if not logical.endswith(".json"):
            return content
        value = json.loads(content)
        if logical.startswith("raw/"):
            for page in value.get("pages", []):
                relative = page.get("path")
                if not isinstance(relative, str) or not re.fullmatch(
                    r"raw/pages/[0-9a-f]{64}\.html(?:\.gz)?", relative
                ):
                    raise ValueError("Invalid raw page path")
                source = output / relative
                if not source.exists() and relative in paths:
                    source = output / paths[relative]
                if source.is_symlink() or not source.resolve().is_relative_to(output / "raw/pages"):
                    raise ValueError("Raw page path escapes the capture")
                original = read_bytes(source)
                if hashlib.sha256(original).hexdigest() != page.get("bytes_sha256") or len(
                    original
                ) != page.get("bytes"):
                    raise ValueError(f"Raw page checksum mismatch: {relative}")
                page["path"] = paths.get(relative, relative)
        elif logical.startswith(("ir/", "failures/")):
            manifest = value.get("source_manifest")
            if isinstance(manifest, str):
                value["source_manifest"] = paths.get(manifest, manifest)
        elif logical.startswith("jobs/"):
            value = _references(value, paths)
        return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    # Preflight every input and duplicate before writing or removing anything.
    prepared: dict[str, bytes] = {}
    for logical, files in groups.items():
        referenced = [path for path in files if path.relative_to(output).as_posix() in canonical]
        chosen = (
            referenced[0]
            if referenced
            else next((path for path in files if path.suffix == ".gz"), files[0])
        )
        content = transform(logical, read_bytes(chosen))
        # A state-referenced gzip artifact supersedes an old plain refresh copy.
        if chosen.suffix != ".gz" or chosen.relative_to(output).as_posix() not in canonical:
            if any(transform(logical, read_bytes(path)) != content for path in files):
                raise ValueError(f"Conflicting plain and gzip artifacts: {logical}")
        prepared[logical] = content
    created = 0
    rewritten = 0
    for logical, content in prepared.items():
        target = output / paths[logical]
        if target.is_symlink():
            raise ValueError(f"Unsafe compression destination: {target}")
        if target.exists() and read_bytes(target) == content:
            continue
        existed = target.exists()
        write_bytes(target, content)
        if read_bytes(target) != content:
            raise ValueError(f"Compression verification failed: {target}")
        created += not existed
        rewritten += existed
    for post in state.posts.values():
        for attribute in ("ir", "typst", "source_manifest"):
            value = getattr(post, attribute)
            if value in paths:
                setattr(post, attribute, paths[value])
    state.save(state_path)
    removed = 0
    for files in groups.values():
        for path in files:
            if path.suffix != ".gz":
                path.unlink()
                removed += 1
    after = _non_pdf_bytes(output)
    report = {
        "files_created": created,
        "files_rewritten": rewritten,
        "plaintext_files_removed": removed,
        "unfinished_reports_retained": unfinished,
        "non_pdf_bytes_before": before,
        "non_pdf_bytes_after": after,
        "bytes_saved": before - after,
    }
    if created or rewritten or removed:
        finished = datetime.now(UTC)
        report["finished_at"] = finished.isoformat()
        report["started_at"] = started.isoformat()
        report["kind"] = "artifact-compression"
        report_path = (
            output / "jobs" / (finished.strftime("%Y%m%dT%H%M%S.%fZ") + "-compression.json.gz")
        )
        report["report_path"] = report_path.relative_to(output).as_posix()
        write_json(report_path, report)
    return report
