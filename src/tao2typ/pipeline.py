"""Coordinate discovery, persistent scheduling, assets, IR and transactional output."""

import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from tao2typ import __version__
from tao2typ.ir import Comment, Node, Post
from tao2typ.metadata import merge_metadata
from tao2typ.naming import artifact_stems
from tao2typ.parser import parse_post
from tao2typ.raw import RawCapture
from tao2typ.schedule import next_scheduled_check, plan_refresh
from tao2typ.source import BlogClient, FetchedPage, SourceError
from tao2typ.state import ArchiveState, PostState, write_json
from tao2typ.storage import read_json, read_text, write_text
from tao2typ.typst import render_post


def timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def converter_fingerprint() -> str:
    directory = Path(__file__).parent
    digest = hashlib.sha256(__version__.encode())
    for name in (
        "ir.py",
        "parser.py",
        "typst.py",
        "tex.py",
        "tex_compat.py",
        "tex_tables.py",
        "source_errors.py",
        "data/source-errors.json",
        "alignment.py",
        "pipeline.py",
        "source.py",
        "raw.py",
        "storage.py",
    ):
        digest.update((directory / name).read_bytes())
    return digest.hexdigest()


def fetch_raw_pages(client: BlogClient, url: str, capture: RawCapture) -> list[str]:
    """Persist each parser input before parsing, even if a later fetch fails."""

    def record(page: FetchedPage) -> None:
        capture.add(
            page.html,
            page.url,
            requested_url=page.requested_url,
            fetched_at=page.fetched_at,
            api_payloads=page.api_payloads,
        )

    try:
        pages = client.fetch_post_pages(url, on_page=record, on_api_payload=capture.add_api_payload)
        capture.finish()
        return pages
    except BaseException as error:
        capture.fail(str(error))
        raise


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
    raise ValueError(f"Unrecognized image data from {url}")


def resolve_assets(post: Post, client: BlogClient, output: Path) -> dict[str, str]:
    assets: dict[str, str] = {}
    # Avatars are optional decoration. An unavailable avatar must not prevent
    # preserving a discussion, while missing article diagrams still fail loudly.
    required: set[str] = set()

    def require(nodes: list[Node]) -> None:
        required.update(
            node.attrs["url"]
            for root in nodes
            for node in root.walk()
            if node.kind == "image" and node.attrs.get("url")
        )

    def require_comments(comments: list[Comment]) -> None:
        for comment in comments:
            require(comment.body)
            require_comments(comment.replies)

    require(post.body)
    require_comments(post.comments)
    for url in sorted(image_urls(post)):
        # Revalidate images during a scheduled refresh: publishers can replace
        # the bytes at an existing URL without changing the article markup.
        try:
            data = client.fetch_asset(url)
            suffix = asset_extension(url, data)
        except (httpx.HTTPError, SourceError, ValueError) as error:
            if url in required:
                raise
            post.warnings.append(f"Comment avatar unavailable: {url}: {error}")
            continue
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
    converted_at: datetime | None = None,
) -> dict[str, str]:
    """Stage outputs and roll back a failed or interrupted artifact promotion.

    Each replacement is atomic; handled failures preserve the previous complete
    set. A process kill during promotion can still leave mixed generations.
    """
    paths = {
        "ir": f"ir/{stem}.json.gz",
        "typst": f"typst/{stem}.typ.gz",
        "pdf": f"pdfs/{stem}.pdf",
    }
    source = output / paths["typst"]
    pdf = output / paths["pdf"]
    source.parent.mkdir(parents=True, exist_ok=True)
    pdf.parent.mkdir(parents=True, exist_ok=True)
    # Typst requires ordinary source files; retain only the losslessly compressed
    # source after a successful compilation.
    native_source = source.with_name(stem + ".tmp.typ")
    staged_source = source.with_name(stem + ".tmp.typ.gz")
    staged_pdf = pdf.with_name(pdf.stem + ".tmp.pdf")
    ir = output / paths["ir"]
    staged_ir = ir.with_name(stem + ".tmp.json.gz")
    promotions = [(staged_ir, ir), (staged_source, source), (staged_pdf, pdf)]
    backups = {
        target: target.with_name(target.stem + ".previous" + target.suffix)
        for _, target in promotions
    }
    assets = resolve_assets(post, client, output) if client else {}
    payload = post.to_dict()
    # Conversion time is provenance, not a content change. Hash a render copy
    # without its date so a fresh check alone cannot regenerate an identical PDF.
    rendered_for_digest = render_post(replace(post, converted_at=None), assets=assets)
    image_digests = {
        url: hashlib.sha256((source.parent / relative).read_bytes()).hexdigest()
        for url, relative in assets.items()
    }
    digest = hashlib.sha256(
        (
            json.dumps(
                {
                    key: value
                    for key, value in payload.items()
                    if key not in {"source_manifest", "converted_at"}
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + rendered_for_digest
            + json.dumps(image_digests, sort_keys=True)
        ).encode()
    ).hexdigest()
    previous_ir = ir if ir.is_file() else ir.with_suffix("")
    previous_source = source if source.is_file() else source.with_suffix("")
    if previous_digest == digest and all(
        path.is_file() for path in (previous_ir, previous_source, pdf)
    ):
        # Refresh capture provenance without recompiling identical document content.
        previous_post = Post.from_dict(read_json(previous_ir))
        if previous_post.converted_at:
            post.converted_at = previous_post.converted_at
            if not source.is_file():
                write_text(source, read_text(previous_source))
            write_json(ir, post.to_dict())
            return {**paths, "content_sha256": digest, "converted_at": post.converted_at}
    post.converted_at = timestamp(converted_at or datetime.now(UTC))
    payload = post.to_dict()
    rendered = render_post(post, assets=assets)
    attempted: list[Path] = []
    originals: set[Path] = set()
    recovery_backups: set[Path] = set()
    try:
        write_text(native_source, rendered)
        compile_typst(native_source, staged_pdf, output)
        write_text(staged_source, rendered)
        write_json(staged_ir, payload)
        # Copy before changing any canonical path. Backups also retain the old
        # bytes when an interrupt lands immediately after a replacement.
        for _, target in promotions:
            if target.exists():
                shutil.copy2(target, backups[target])
                originals.add(target)
        for staged, target in promotions:
            attempted.append(target)
            staged.replace(target)
        for suffix in (".json", ".typ", ".json.gz", ".typ.gz"):
            (output / "failures" / (stem + suffix)).unlink(missing_ok=True)
    except BaseException as error:
        for target in reversed(attempted):
            try:
                if target in originals:
                    backups[target].replace(target)
                else:
                    target.unlink(missing_ok=True)
            except OSError as rollback_error:
                # Keep any backup that could not be restored for recovery.
                recovery_backups.add(backups[target])
                error.add_note(f"Could not restore {target}: {rollback_error}")
        # Preserve an inspectable IR/source for unsupported equations, while the
        # canonical paths keep the last successfully compiled document.
        if isinstance(error, Exception):
            write_json(output / "failures" / (stem + ".json.gz"), payload)
            write_text(output / "failures" / (stem + ".typ.gz"), rendered)
        raise
    finally:
        native_source.unlink(missing_ok=True)
        for staged, _ in promotions:
            staged.unlink(missing_ok=True)
        staged_ir.with_suffix(staged_ir.suffix + ".tmp").unlink(missing_ok=True)
        for backup in backups.values():
            if backup not in recovery_backups:
                backup.unlink(missing_ok=True)
    return {**paths, "content_sha256": digest, "converted_at": post.converted_at}


def write_index(state: ArchiveState, output: Path) -> None:
    rows = ["# Converted posts", "", "Generated PDFs and source publication dates.", ""]
    for post in sorted(state.posts.values(), key=lambda p: (p.published_at, p.id), reverse=True):
        if post.pdf:
            title = post.title.replace("[", "\\[").replace("]", "\\]").replace("\n", " ")
            rows.append(
                f"- {post.published_at[:10]} · [{title}]({post.pdf}) · [source]({post.url})"
                + (f" · [Typst]({post.typst})" if post.typst else "")
                + (f" · [IR]({post.ir})" if post.ir else "")
                + (f" · [HTML capture]({post.source_manifest})" if post.source_manifest else "")
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
    daily_limit: int | None = None,
) -> dict[str, Any]:
    if max_posts < 0:
        raise ValueError("max_posts must be non-negative (0 means unlimited)")
    if max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    if daily_limit is not None and daily_limit < 0:
        raise ValueError("daily_limit must be non-negative")
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
    quota_day = now.date().isoformat()
    report: dict[str, Any] = {
        "schema_version": 1,
        "started_at": timestamp(now),
        "converter_version": __version__,
        "force": force,
        "max_posts": max_posts,
        "max_seconds": max_seconds,
        "daily_limit": daily_limit,
        "results": [],
        "errors": [],
    }
    report_path = output / "jobs" / (now.strftime("%Y%m%dT%H%M%S.%fZ") + ".json")

    def checkpoint_report() -> None:
        report["request_statistics"] = client.request_statistics
        write_json(report_path, report)

    checkpoint_report()
    try:
        # Small metadata-only pages scan the complete inventory every run. This also
        # detects modifications to older posts without relying on an RSS window.
        discovered = list(client.discover_posts())
        merge_metadata(state, discovered)
        stems = artifact_stems(state.posts.values())
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
            if post_state.last_error:
                reasons.append("previous-failure")
            if post_state.converted_modified_at != post_state.modified_at:
                reasons.append("source-modified")
            if post_state.pdf and not (output / post_state.pdf).exists():
                reasons.append("missing-pdf")
            if post_state.pdf:
                if (
                    not post_state.source_manifest
                    or not (output / post_state.source_manifest).is_file()
                ):
                    reasons.append("missing-raw-source")
                for kind in ("typst", "ir"):
                    path = getattr(post_state, kind)
                    if not path or not (output / path).is_file():
                        reasons.append(f"missing-{kind}")
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
        state.save(state_path)
        checkpoint_report()
        selected = candidates[:max_posts] if max_posts else candidates
        if daily_limit is not None:
            allowance = max(0, daily_limit - state.daily_attempts.get(quota_day, 0))
            selected = selected[:allowance]
            report["daily_allowance"] = allowance
        queue = [
            {"id": post.id, "url": post.url, "title": post.title, "reasons": reasons}
            for post, reasons in candidates
        ]
        queue_path = output / "queue.json"
        write_json(queue_path, {"updated_at": timestamp(now), "posts": queue})
        for post_state, reasons in selected:
            if time.monotonic() - started_monotonic >= max_seconds:
                report["time_budget_reached"] = True
                break
            attempt_day = (now if clock_supplied else datetime.now(UTC)).date().isoformat()
            if daily_limit is not None:
                if state.daily_attempts.get(attempt_day, 0) >= daily_limit:
                    report["daily_limit_reached"] = True
                    break
            result: dict[str, Any] = {
                "id": post_state.id,
                "url": post_state.url,
                "reasons": reasons,
            }
            post_state.last_attempted_at = timestamp(now if clock_supplied else datetime.now(UTC))
            if daily_limit is not None:
                # Reserve the budget before fetching/compiling. An interrupted
                # or failed attempt still consumes runner time.
                state.daily_attempts[attempt_day] = state.daily_attempts.get(attempt_day, 0) + 1
                state.save(state_path)
            try:
                stem = stems[post_state.id]
                capture = RawCapture(
                    output,
                    stem,
                    post_state.url,
                    published_at=post_state.published_at,
                    modified_at=post_state.modified_at,
                )
                post_state.source_manifest = capture.manifest_path
                result["source_manifest"] = capture.manifest_path
                state.save(state_path)
                pages = fetch_raw_pages(client, post_state.url, capture)
                post = parse_post(
                    pages[0],
                    post_state.url,
                    comment_pages=pages[1:],
                    published_at=post_state.published_at,
                    modified_at=post_state.modified_at,
                )
                post.source_manifest = capture.manifest_path
                artifacts = save_post(
                    post,
                    stem,
                    output,
                    client,
                    previous_digest=post_state.content_sha256,
                    converted_at=now if clock_supplied else None,
                )
                completed = now if clock_supplied else datetime.now(UTC)
                post_state.last_checked_at = timestamp(completed)
                post_state.converted_at = artifacts["converted_at"]
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
                queue = [entry for entry in queue if entry["id"] != post_state.id]
                result.update(
                    status="converted",
                    checked_at=timestamp(completed),
                    warnings=post.warnings,
                    artifacts=artifacts,
                )
            except Exception as exc:
                post_state.last_error = str(exc)
                result.update(status="failed", error=str(exc))
            finished_day = (now if clock_supplied else datetime.now(UTC)).date().isoformat()
            if daily_limit is not None and finished_day != attempt_day:
                # An attempt spanning midnight also uses the new day's budget,
                # so a manual rerun cannot generate an eleventh PDF that day.
                state.daily_attempts[finished_day] = state.daily_attempts.get(finished_day, 0) + 1
            report["results"].append(result)
            state.save(state_path)
            write_json(
                queue_path,
                {
                    "updated_at": timestamp(now if clock_supplied else datetime.now(UTC)),
                    "posts": queue,
                },
            )
            checkpoint_report()
        report["remaining"] = len(queue)
    except Exception as exc:
        report["errors"].append({"stage": "discovery", "error": str(exc)})
    finally:
        report["finished_at"] = timestamp(now if clock_supplied else datetime.now(UTC))
        report["converted"] = sum(r["status"] == "converted" for r in report["results"])
        report["failed"] = sum(r["status"] == "failed" for r in report["results"])
        state.save(state_path)
        checkpoint_report()
        write_index(state, output)
    return report
