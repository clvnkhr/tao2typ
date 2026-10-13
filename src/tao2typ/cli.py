"""Small CLI for local conversion, reproducible rerendering and daily batches."""

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from tao2typ.catalogue import generate_site
from tao2typ.ir import Post
from tao2typ.metadata import sync_metadata
from tao2typ.migrate import migrate_existing_archive
from tao2typ.naming import artifact_stem
from tao2typ.parser import parse_post
from tao2typ.pipeline import (
    compile_typst,
    fetch_raw_pages,
    image_urls,
    run_batch,
    save_post,
    write_index,
)
from tao2typ.raw import RawCapture, load_raw_capture
from tao2typ.source import BlogClient, canonical_post_url
from tao2typ.state import ArchiveState, write_json
from tao2typ.typst import render_post


def main() -> int:
    parser = argparse.ArgumentParser(description="Tao's WordPress HTML → IR → Typst → PDF")
    commands = parser.add_subparsers(dest="command", required=True)
    batch = commands.add_parser("batch", help="Discover all posts and refresh those due")
    batch.add_argument("--output", type=Path, default=Path("archive"))
    batch.add_argument("--max-posts", type=int, default=0, help="0 converts every due post")
    batch.add_argument("--max-seconds", type=float, default=18_000, help="Soft batch time limit")
    batch.add_argument("--daily-limit", type=int, help="Durable attempt budget per UTC day")
    batch.add_argument(
        "--force", action="store_true", help="Refresh every post, ignoring its schedule"
    )
    batch.add_argument("--interval", type=float, default=1.0, help="Seconds between HTTP requests")
    convert = commands.add_parser("convert", help="Convert one URL or a saved HTML file")
    convert.add_argument("source")
    convert.add_argument("--url", help="Canonical source URL for a saved HTML file")
    convert.add_argument("--published-at", help="Source publication time (ISO8601), for local HTML")
    convert.add_argument("--output", type=Path, default=Path("archive"))
    convert.add_argument(
        "--stem", help="Optional filename stem; defaults to the original post slug"
    )
    render = commands.add_parser("render", help="Render a saved IR JSON without parsing HTML")
    render.add_argument("ir", type=Path)
    render.add_argument("--output", type=Path, required=True, help="Output .typ file")
    render.add_argument("--pdf", type=Path, help="Also compile to this PDF file")
    replay = commands.add_parser(
        "parse-raw", help="Reparse saved HTML without fetching or compiling"
    )
    replay.add_argument("manifest", type=Path)
    replay.add_argument("--output", type=Path, required=True, help="Output IR JSON")
    migrate = commands.add_parser(
        "migrate-names", help="Rename existing outputs without conversion"
    )
    migrate.add_argument("--output", type=Path, default=Path("archive"))
    metadata = commands.add_parser("sync-metadata", help="Update post tags/categories without PDFs")
    metadata.add_argument("--output", type=Path, default=Path("archive"))
    metadata.add_argument("--interval", type=float, default=1.0)
    site = commands.add_parser("site", help="Build the catalogue or export public downloads")
    site.add_argument("--archive", type=Path, default=Path("archive"))
    site.add_argument("--output", type=Path, default=Path("docs"))
    site.add_argument("--repo-url", default="https://github.com/clvnkhr/tao2typ")
    site.add_argument("--branch", default="main")
    site.add_argument(
        "--download-base-url", help="Export public documents/assets and use this hosting URL"
    )
    args = parser.parse_args()
    if args.command == "parse-raw":
        metadata, pages, archive = load_raw_capture(args.manifest)
        post = parse_post(
            pages[0],
            metadata["post_url"],
            comment_pages=pages[1:],
            published_at=metadata.get("published_at"),
            modified_at=metadata.get("modified_at"),
        )
        post.source_manifest = args.manifest.resolve().relative_to(archive).as_posix()
        write_json(args.output, post.to_dict())
        return 0
    if args.command == "migrate-names":
        result = migrate_existing_archive(args.output)
        write_index(ArchiveState.load(args.output / "state.json"), args.output)
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "sync-metadata":
        with BlogClient(interval=args.interval) as client:
            result = sync_metadata(args.output, client)
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "site":
        result = generate_site(
            args.archive,
            args.output,
            repo_url=args.repo_url,
            branch=args.branch,
            download_base_url=args.download_base_url,
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "render":
        post = Post.from_dict(json.loads(args.ir.read_text(encoding="utf-8")))
        post.converted_at = datetime.now(UTC).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Existing archive image assets can be located independently of fetching HTML.
        assets = {}
        archive = args.ir.parent.parent
        for url in image_urls(post):
            saved = next(
                (
                    node.attrs["asset_path"]
                    for node in post.walk()
                    if node.kind == "image"
                    and node.attrs.get("url") == url
                    and "asset_path" in node.attrs
                ),
                None,
            )
            if saved:
                asset = archive / saved
                if asset.exists():
                    assets[url] = os.path.relpath(asset, args.output.parent)
                continue
            suffix = Path(urlsplit(url).path).suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}:
                suffix = ".png"
            asset = archive / "assets" / (hashlib.sha256(url.encode()).hexdigest() + suffix)
            if asset.exists():
                assets[url] = os.path.relpath(asset, args.output.parent)
        args.output.write_text(render_post(post, assets=assets), encoding="utf-8")
        if args.pdf:
            args.pdf.parent.mkdir(parents=True, exist_ok=True)
            root = Path(os.path.commonpath([archive.resolve(), args.output.resolve()]))
            compile_typst(args.output, args.pdf, root)
        return 0
    if args.command == "convert":
        if args.stem and (Path(args.stem).name != args.stem or args.stem in {".", "..", ""}):
            parser.error("--stem must be a simple filename without directories")
        local = Path(args.source)
        if local.is_file():
            if not args.url:
                parser.error("Saved HTML requires --url for attribution")
            html = local.read_bytes().decode("utf-8")
            stem = args.stem or artifact_stem(local.stem, args.url, 0)
            capture = RawCapture(args.output, stem, args.url, published_at=args.published_at)
            capture.add(html, args.url)
            capture.finish()
            post = parse_post(html, args.url, published_at=args.published_at)
            post.source_manifest = capture.manifest_path
            paths = save_post(
                post, args.stem or artifact_stem(post.title, post.url, 0), args.output, None
            )
        else:
            url = canonical_post_url(args.source)
            with BlogClient() as client:
                metadata = client.lookup_post(url)
                stem = args.stem or artifact_stem(metadata.title, url, metadata.id)
                capture = RawCapture(
                    args.output,
                    stem,
                    url,
                    published_at=metadata.published_at.isoformat(),
                    modified_at=metadata.modified_at.isoformat(),
                )
                pages = fetch_raw_pages(client, url, capture)
                post = parse_post(
                    pages[0],
                    url,
                    comment_pages=pages[1:],
                    published_at=metadata.published_at.isoformat(),
                    modified_at=metadata.modified_at.isoformat(),
                )
                post.source_manifest = capture.manifest_path
                paths = save_post(
                    post,
                    stem,
                    args.output,
                    client,
                )
        paths["source_manifest"] = capture.manifest_path
        print(json.dumps(paths, indent=2))
        return 0
    with BlogClient(interval=args.interval) as client:
        report = run_batch(
            args.output,
            client,
            max_posts=args.max_posts,
            force=args.force,
            max_seconds=args.max_seconds,
            daily_limit=args.daily_limit,
        )
    if (args.output / "state.json").exists():
        generate_site(args.output, args.output.parent / "docs")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["failed"] or report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
