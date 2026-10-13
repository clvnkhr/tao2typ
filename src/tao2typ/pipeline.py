"""Coordinate discovery, persistent scheduling, assets, IR and transactional output."""

import hashlib
import json
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tao2typ import __version__
from tao2typ.ir import Post
from tao2typ.parser import parse_post
from tao2typ.schedule import next_scheduled_check, plan_refresh
from tao2typ.source import BlogClient
from tao2typ.state import ArchiveState, PostState, write_json
from tao2typ.typst import render_post


def timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def converter_fingerprint() -> str:
    directory = Path(__file__).parent
    digest = hashlib.sha256(__version__.encode())
    for name in ("ir.py", "parser.py", "typst.py"):
        digest.update((directory / name).read_bytes())
    return digest.hexdigest()


def image_urls(post: Post) -> set[str]:
    # Traverse the serializable IR, including nested comment replies.
    def visit(value: Any) -> set[str]:
        if isinstance(value, dict):
            found = set()
            if value.get("kind") == "image" and value.get("attrs", {}).get("url"):
                found.add(value["attrs"]["url"])
            for item in value.values():
                found.update(visit(item))
            return found
        if isinstance(value, list):
            return set().union(*(visit(item) for item in value))
        return set()

    return visit(post.to_dict())


def asset_extension(url: str, data: bytes) -> str:
    """Image service URLs frequently have no suffix, or lie about their type."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if b"<svg" in data[:512].lower():
        return ".svg"
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}:
        return suffix
    raise ValueError(f"Unrecognized image data from {url}")


def resolve_assets(post: Post, client: BlogClient, output: Path) -> dict[str, str]:
    assets: dict[str, str] = {}
    for url in sorted(image_urls(post)):
        # Revalidate images during a scheduled refresh: publishers can replace
        # the bytes at an existing URL without changing the article markup.
        data = client.fetch_asset(url)
        suffix = asset_extension(url, data)
        name = hashlib.sha256(url.encode()).hexdigest() + "-" + hashlib.sha256(data).hexdigest()
        relative = Path("assets") / (name + suffix)
        path = output / relative
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
        # .typ documents are one directory below the archive root.
        assets[url] = "../" + relative.as_posix()
        for node in post.walk():
            if node.kind == "image" and node.attrs.get("url") == url:
                node.attrs["asset_path"] = relative.as_posix()
    return assets


def compile_typst(source: Path, pdf: Path, root: Path) -> None:
    compiler = shutil.which("typst")
    if compiler is None:
        raise RuntimeError("Typst CLI is missing; install Typst before converting PDFs")
    result = subprocess.run(
        [compiler, "compile", "--root", str(root.resolve()), str(source), str(pdf)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Typst compilation failed: " + result.stderr[-8000:])


def save_post(
    post: Post,
    stem: str,
    output: Path,
    client: BlogClient | None,
    *,
    previous_digest: str | None = None,
) -> dict[str, str]:
    """Stage all outputs; compilation failure preserves the prior IR/Typst/PDF."""
    paths = {"ir": f"ir/{stem}.json", "typst": f"typst/{stem}.typ", "pdf": f"pdfs/{stem}.pdf"}
    source = output / paths["typst"]
    pdf = output / paths["pdf"]
    source.parent.mkdir(parents=True, exist_ok=True)
    pdf.parent.mkdir(parents=True, exist_ok=True)
    staged_source = source.with_name(source.stem + ".tmp.typ")
    staged_pdf = pdf.with_name(pdf.stem + ".tmp.pdf")
    assets = resolve_assets(post, client, output) if client else {}
    payload = post.to_dict()
    rendered = render_post(post, assets=assets)
    image_digests = {
        url: hashlib.sha256((source.parent / relative).read_bytes()).hexdigest()
        for url, relative in assets.items()
    }
    digest = hashlib.sha256(
        (
            json.dumps(payload, ensure_ascii=False, sort_keys=True)
            + rendered
            + json.dumps(image_digests, sort_keys=True)
        ).encode()
    ).hexdigest()
    if previous_digest == digest and all((output / path).exists() for path in paths.values()):
        return {**paths, "content_sha256": digest}
    try:
        staged_source.write_text(rendered, encoding="utf-8")
        compile_typst(staged_source, staged_pdf, output)
        write_json(output / paths["ir"], payload)
        staged_source.replace(source)
        staged_pdf.replace(pdf)
    finally:
        staged_source.unlink(missing_ok=True)
        staged_pdf.unlink(missing_ok=True)
    return {**paths, "content_sha256": digest}


def write_index(state: ArchiveState, output: Path) -> None:
    rows = ["# Converted posts", "", "Generated PDFs and source publication dates.", ""]
    for post in sorted(state.posts.values(), key=lambda p: (p.published_at, p.id), reverse=True):
        if post.pdf:
            title = post.title.replace("[", "\\[").replace("]", "\\]").replace("\n", " ")
            rows.append(
                f"- {post.published_at[:10]} · [{title}]({post.pdf}) · [source]({post.url})"
            )
    (output / "README.md").write_text("\n".join(rows) + "\n", encoding="utf-8")


def run_batch(
    output: Path,
    client: BlogClient,
    *,
    now: datetime | None = None,
    max_posts: int = 0,
    force: bool = False,
    max_seconds: float = 18_000,
) -> dict[str, Any]:
    if max_posts < 0:
        raise ValueError("max_posts must be non-negative (0 means unlimited)")
    if max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    started_monotonic = time.monotonic()
    clock_supplied = now is not None
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Batch timestamps require a timezone")
    now = now.astimezone(UTC)
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "state.json"
    state = ArchiveState.load(state_path)
    fingerprint = converter_fingerprint()
    report: dict[str, Any] = {
        "schema_version": 1,
        "started_at": timestamp(now),
        "converter_version": __version__,
        "force": force,
        "max_posts": max_posts,
        "max_seconds": max_seconds,
        "results": [],
        "errors": [],
    }
    report_path = output / "jobs" / (now.strftime("%Y%m%dT%H%M%S.%fZ") + ".json")
    try:
        # Small metadata-only pages scan the complete inventory every run. This also
        # detects modifications to older posts without relying on an RSS window.
        discovered = list(client.discover_posts())
        for metadata in discovered:
            key = str(metadata.id)
            if key not in state.posts:
                state.posts[key] = PostState(
                    metadata.id,
                    metadata.url,
                    metadata.title,
                    metadata.published_at.isoformat(),
                    metadata.modified_at.isoformat(),
                )
            else:
                post_state = state.posts[key]
                post_state.url = metadata.url
                post_state.title = metadata.title
                post_state.published_at = metadata.published_at.isoformat()
                post_state.modified_at = metadata.modified_at.isoformat()
        state.last_discovered_at = timestamp(now)
        report["discovered"] = len(discovered)
        try:
            recent = client.recent_comments()
        except Exception as exc:
            recent = []
            report["errors"].append({"stage": "recent-comments", "error": str(exc)})
        by_url = {p.url: p for p in state.posts.values()}
        for event in recent:
            post_state = by_url.get(event.post_url)
            if post_state and event.event_url not in post_state.seen_comments:
                if event.event_url not in post_state.pending_comments:
                    post_state.pending_comments.append(event.event_url)
        candidates: list[tuple[PostState, list[str]]] = []
        for post_state in state.posts.values():
            published = parse_timestamp(post_state.published_at)
            checked = (
                parse_timestamp(post_state.last_checked_at) if post_state.last_checked_at else None
            )
            plan = plan_refresh(
                published, checked, now=now, recent_comment=bool(post_state.pending_comments)
            )
            post_state.next_check_at = timestamp(plan.next_check_at)
            reasons = list(plan.reasons)
            if post_state.converted_modified_at != post_state.modified_at:
                reasons.append("source-modified")
            if post_state.pdf and not (output / post_state.pdf).exists():
                reasons.append("missing-pdf")
            if post_state.pdf and post_state.converter_fingerprint != fingerprint:
                reasons.append("converter-changed")
            if force:
                reasons.append("forced")
            if reasons and published <= now:
                candidates.append((post_state, reasons))
        # Healthy updates precede retries; rotate failed posts by last attempt so
        # a broken old post cannot consume the whole batch indefinitely.
        candidates.sort(
            key=lambda item: (
                bool(item[0].last_error),
                0 if item[0].pending_comments else 1,
                0 if item[0].last_checked_at else 1,
                item[0].last_attempted_at or "",
                item[0].published_at,
                item[0].id,
            )
        )
        report["due"] = len(candidates)
        selected = candidates[:max_posts] if max_posts else candidates
        for post_state, reasons in selected:
            if time.monotonic() - started_monotonic >= max_seconds:
                report["time_budget_reached"] = True
                break
            result: dict[str, Any] = {
                "id": post_state.id,
                "url": post_state.url,
                "reasons": reasons,
            }
            post_state.last_attempted_at = timestamp(now)
            try:
                pages = client.fetch_post_pages(post_state.url)
                post = parse_post(
                    pages[0],
                    post_state.url,
                    comment_pages=pages[1:],
                    published_at=post_state.published_at,
                    modified_at=post_state.modified_at,
                )
                stem = f"{post_state.published_at[:10]}-{post_state.id}"
                artifacts = save_post(
                    post, stem, output, client, previous_digest=post_state.content_sha256
                )
                completed = now if clock_supplied else datetime.now(UTC)
                post_state.last_checked_at = timestamp(completed)
                post_state.converted_at = timestamp(completed)
                post_state.converted_modified_at = post_state.modified_at
                post_state.next_check_at = timestamp(
                    next_scheduled_check(parse_timestamp(post_state.published_at), completed)
                )
                post_state.content_sha256 = artifacts["content_sha256"]
                post_state.converter_fingerprint = fingerprint
                post_state.pdf = artifacts["pdf"]
                post_state.ir = artifacts["ir"]
                post_state.typst = artifacts["typst"]
                post_state.seen_comments.extend(post_state.pending_comments)
                post_state.pending_comments.clear()
                post_state.last_error = None
                result.update(
                    status="converted",
                    checked_at=timestamp(completed),
                    warnings=post.warnings,
                    artifacts=artifacts,
                )
            except Exception as exc:
                post_state.last_error = str(exc)
                result.update(status="failed", error=str(exc))
            report["results"].append(result)
            state.save(state_path)
            write_json(report_path, report)
        report["remaining"] = len(candidates) - len(report["results"])
    except Exception as exc:
        report["errors"].append({"stage": "discovery", "error": str(exc)})
    finally:
        report["finished_at"] = timestamp(now if clock_supplied else datetime.now(UTC))
        report["converted"] = sum(r["status"] == "converted" for r in report["results"])
        report["failed"] = sum(r["status"] == "failed" for r in report["results"])
        state.save(state_path)
        write_json(report_path, report)
        write_index(state, output)
    return report
