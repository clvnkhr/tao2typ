"""Small CLI for local conversion, reproducible rerendering and daily batches."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from tao2typ.ir import Post
from tao2typ.parser import parse_post
from tao2typ.pipeline import compile_typst, image_urls, run_batch, save_post
from tao2typ.source import BlogClient, canonical_post_url
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
    convert.add_argument("--stem", default="post")
    render = commands.add_parser("render", help="Render a saved IR JSON without parsing HTML")
    render.add_argument("ir", type=Path)
    render.add_argument("--output", type=Path, required=True, help="Output .typ file")
    render.add_argument("--pdf", type=Path, help="Also compile to this PDF file")
    args = parser.parse_args()
    if args.command == "render":
        post = Post.from_dict(json.loads(args.ir.read_text(encoding="utf-8")))
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
        if Path(args.stem).name != args.stem or args.stem in {".", "..", ""}:
            parser.error("--stem must be a simple filename without directories")
        local = Path(args.source)
        if local.is_file():
            if not args.url:
                parser.error("Saved HTML requires --url for attribution")
            post = parse_post(
                local.read_text(encoding="utf-8"), args.url, published_at=args.published_at
            )
            paths = save_post(post, args.stem, args.output, None)
        else:
            url = canonical_post_url(args.source)
            with BlogClient() as client:
                metadata = client.lookup_post(url)
                pages = client.fetch_post_pages(url)
                post = parse_post(
                    pages[0],
                    url,
                    comment_pages=pages[1:],
                    published_at=metadata.published_at.isoformat(),
                    modified_at=metadata.modified_at.isoformat(),
                )
                paths = save_post(post, args.stem, args.output, client)
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
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["failed"] or report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
