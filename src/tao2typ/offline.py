"""Checkpointed local regeneration from saved source and cached image files."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tao2typ.catalogue import generate_site
from tao2typ.ir import Post
from tao2typ.naming import artifact_stems
from tao2typ.parser import parse_post
from tao2typ.pipeline import (
    asset_extension,
    converter_fingerprint,
    parse_timestamp,
    save_post,
    timestamp,
    write_index,
)
from tao2typ.raw import load_raw_capture
from tao2typ.schedule import plan_refresh
from tao2typ.source import SourceError
from tao2typ.state import ArchiveState, PostState
from tao2typ.storage import read_json, write_json

_PRIORITY = (5128, 18, 416, 10913)


def _saved_post(output: Path, row: PostState) -> Post:
    if row.ir and not row.last_error and (output / row.ir).is_file():
        post = Post.from_dict(read_json(output / row.ir))
    elif row.source_manifest:
        metadata, pages, _ = load_raw_capture(output / row.source_manifest)
        if metadata["post_url"] != row.url:
            raise SourceError("Saved raw capture belongs to another post")
        post = parse_post(
            pages[0],
            row.url,
            comment_pages=pages[1:],
            published_at=metadata.get("published_at") or row.published_at,
            modified_at=metadata.get("modified_at") or row.modified_at,
        )
        post.source_manifest = row.source_manifest
    else:
        raise SourceError("No saved IR or complete raw capture is available offline")
    if post.url != row.url:
        raise SourceError("Saved IR belongs to another post")
    return post


def _valid_asset(path: Path, output: Path) -> bool:
    if (
        not path.is_file()
        or path.is_symlink()
        or not path.resolve().is_relative_to(output.resolve() / "assets")
    ):
        return False
    try:
        data = path.read_bytes()
        if asset_extension(str(path), data) != path.suffix.lower():
            return False
        hashed = re.fullmatch(r"[0-9a-f]{64}-([0-9a-f]{64})", path.stem)
        return hashed is None or hashlib.sha256(data).hexdigest() == hashed[1]
    except (OSError, ValueError):
        return False


def _asset_index(output: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    candidates = [
        path for path in (output / "assets").glob("*") if path.is_file() and not path.is_symlink()
    ]
    for path in sorted(candidates, key=lambda item: item.stat().st_mtime_ns):
        if re.fullmatch(r"[0-9a-f]{64}", path.name[:64]) and _valid_asset(path, output):
            result[path.name[:64]] = path
    return result


def _cached_assets(post: Post, output: Path, index: dict[str, Path]) -> dict[str, str]:
    assets = {}
    for node in post.walk():
        if node.kind != "image" or not (url := node.attrs.get("url")):
            continue
        saved = node.attrs.get("asset_path")
        path = output / saved if saved else None
        if path is None or not _valid_asset(path, output):
            path = index.get(hashlib.sha256(url.encode()).hexdigest())
        if path is not None:
            path = path.resolve()
            relative = path.relative_to(output).as_posix()
            node.attrs["asset_path"] = relative
            node.attrs.pop("unavailable_asset", None)
            node.attrs.pop("unavailable", None)
            assets[url] = "../" + relative
        else:
            node.attrs.pop("asset_path", None)
            node.attrs.pop("unavailable_asset", None)
            node.attrs["unavailable"] = "true"
            warning = f"Image unavailable in local cache: {url}"
            if warning not in post.warnings:
                post.warnings.append(warning)
    return assets


def _apply_result(state: ArchiveState, result: dict[str, Any], fingerprint: str) -> None:
    row = state.posts[str(result["id"])]
    row.last_attempted_at = result["attempted_at"]
    if result["status"] == "converted":
        artifacts = result["artifacts"]
        for key in ("pdf", "ir", "typst", "content_sha256", "converted_at"):
            setattr(row, key, artifacts[key])
        row.converted_modified_at = result["source_modified_at"]
        row.converter_fingerprint = fingerprint
        row.source_manifest = result["source_manifest"]
        row.last_error = None
    elif result["status"] == "failed":
        row.last_error = result["error"]


def run_offline_regeneration(
    output: Path,
    *,
    workers: int = 2,
    max_posts: int = 0,
    post_ids: Sequence[int] | None = None,
    priority: Sequence[int] = _PRIORITY,
    resume: Path | None = None,
    new: bool = False,
) -> dict[str, Any]:
    """Resume saved work; retain source checks, schedules, comment events and quotas."""
    if not 1 <= workers <= 4 or max_posts < 0:
        raise ValueError("Use 1–4 workers and a non-negative max_posts")
    if new and resume:
        raise ValueError("--new and --resume cannot be combined")
    output = output.resolve()
    state_path, queue_path = output / "state.json", output / "queue.json"
    state = ArchiveState.load(state_path)
    if not state.posts:
        raise SourceError("No saved posts are available for offline regeneration")
    fingerprint = converter_fingerprint()
    report: dict[str, Any]
    report_path = resume
    if report_path is not None and not report_path.exists():
        report_path = output / "jobs" / report_path
    if report_path is None and not new:
        for candidate in sorted((output / "jobs").glob("*.json*"), reverse=True):
            old = read_json(candidate)
            if old.get("kind") == "offline-regeneration" and not old.get("finished_at"):
                report_path = candidate
                break
    if report_path is not None:
        report = read_json(report_path)
        if report.get("kind") != "offline-regeneration":
            raise ValueError("--resume requires an offline-regeneration job")
        if report.get("converter_fingerprint") != fingerprint:
            raise SourceError(
                "Converter changed since this job; use --new for a fresh regeneration"
            )
        if post_ids is not None and set(post_ids) != set(report["post_ids"]):
            raise ValueError("Resuming retains the original post selection")
        report.pop("finished_at", None)
        report.pop("interrupted_at", None)
    else:
        selected = (
            set(post_ids) if post_ids is not None else {row.id for row in state.posts.values()}
        )
        if selected - {row.id for row in state.posts.values()}:
            raise ValueError("Requested post ID is absent from the saved archive")
        ranks = {identifier: rank for rank, identifier in enumerate(priority)}
        ordered = sorted(
            (state.posts[str(identifier)] for identifier in selected),
            key=lambda row: (ranks.get(row.id, len(ranks)), row.published_at, row.id),
        )
        started = datetime.now(UTC)
        old_queue = read_json(queue_path).get("posts", []) if queue_path.is_file() else []
        report = {
            "schema_version": 1,
            "kind": "offline-regeneration",
            "started_at": timestamp(started),
            "converter_fingerprint": fingerprint,
            "post_ids": [row.id for row in ordered],
            "due": len(ordered),
            "prior_queue": [entry for entry in old_queue if entry["id"] not in selected],
            "results": [],
            "errors": [],
            "network_requests": 0,
            "daily_attempts_consumed": 0,
        }
        report_path = (
            output / "jobs" / (started.strftime("%Y%m%dT%H%M%S.%fZ") + "-offline-regeneration.json")
        )
    report_path = report_path.resolve()
    if not report_path.is_relative_to(output / "jobs") or report_path.is_symlink():
        raise ValueError("Regeneration reports must be inside this archive's jobs directory")
    results: dict[int, dict[str, Any]] = {result["id"]: result for result in report["results"]}
    for result in results.values():
        if result["status"] in {"converted", "failed"}:
            _apply_result(state, result, fingerprint)
    stems, index = artifact_stems(state.posts.values()), _asset_index(output)

    def checkpoint() -> None:
        report["results"] = [
            results[identifier] for identifier in report["post_ids"] if identifier in results
        ]
        completed = {
            identifier for identifier, result in results.items() if result["status"] == "converted"
        }
        report["converted"] = len(completed)
        report["failed"] = sum(result["status"] == "failed" for result in results.values())
        report["remaining"] = len(report["post_ids"]) - len(completed)
        report["updated_at"] = timestamp(datetime.now(UTC))
        # Journal first: resume can replay a success if state's save was interrupted.
        write_json(report_path, report)
        state.save(state_path)
        entries = []
        now = datetime.now(UTC)
        for identifier in report["post_ids"]:
            row = state.posts[str(identifier)]
            plan = plan_refresh(
                parse_timestamp(row.published_at),
                parse_timestamp(row.last_checked_at) if row.last_checked_at else None,
                now=now,
                recent_comment=bool(row.pending_comments),
            )
            reasons = list(plan.reasons)
            if row.converted_modified_at is None or (
                parse_timestamp(row.modified_at) > parse_timestamp(row.converted_modified_at)
            ):
                reasons.append("source-modified")
            if identifier not in completed:
                reasons.insert(0, "local-regeneration")
            if reasons:
                entries.append(
                    {"id": row.id, "url": row.url, "title": row.title, "reasons": reasons}
                )
        write_json(
            queue_path,
            {"updated_at": report["updated_at"], "posts": entries + report["prior_queue"]},
        )

    def convert(row: PostState) -> dict[str, Any]:
        attempted = timestamp(datetime.now(UTC))
        try:
            post = _saved_post(output, row)
            assets = _cached_assets(post, output, index)
            artifacts = save_post(post, stems[row.id], output, None, assets=assets)
            return {
                "id": row.id,
                "url": row.url,
                "status": "converted",
                "attempted_at": attempted,
                "artifacts": artifacts,
                "source_manifest": post.source_manifest,
                "source_modified_at": post.modified_at,
                "warnings": post.warnings,
                "reasons": ["local-regeneration"],
            }
        except Exception as error:
            return {
                "id": row.id,
                "url": row.url,
                "status": "failed",
                "attempted_at": attempted,
                "error": str(error),
                "reasons": ["local-regeneration"],
            }

    pending: list[int] = [
        identifier
        for identifier in report["post_ids"]
        if results.get(identifier, {}).get("status") != "converted"
    ]
    unfinished_count = len(pending)
    if max_posts:
        pending = pending[:max_posts]
    checkpoint()
    tasks: dict[Future[dict[str, Any]], int] = {}
    iterator = iter(pending)
    interrupted = False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        try:
            while True:
                while len(tasks) < workers:
                    identifier = next(iterator, None)
                    if identifier is None:
                        break
                    tasks[pool.submit(convert, replace(state.posts[str(identifier)]))] = identifier
                if not tasks:
                    break
                done, _ = wait(tasks, return_when=FIRST_COMPLETED)
                for future in done:
                    result = future.result()
                    tasks.pop(future)
                    results[result["id"]] = result
                    _apply_result(state, result, fingerprint)
                    checkpoint()
        except KeyboardInterrupt:
            interrupted = True
            report["interrupted_at"] = timestamp(datetime.now(UTC))
            # Stop dispatching, finish/checkpoint only the at-most-workers tasks.
            for future in tasks:
                result = future.result()
                results[result["id"]] = result
                _apply_result(state, result, fingerprint)
                checkpoint()
        finally:
            if not interrupted and len(pending) == unfinished_count:
                report["finished_at"] = timestamp(datetime.now(UTC))
            checkpoint()
            write_index(state, output)
            generate_site(output, output.parent / "docs")
    report["job"] = report_path.relative_to(output).as_posix()
    return report
