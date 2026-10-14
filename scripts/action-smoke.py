"""Check runner source access and catalogue generation without compiling PDFs."""

import os
from pathlib import Path

from tao2typ.catalogue import generate_site
from tao2typ.parser import parse_post
from tao2typ.pipeline import fetch_raw_pages
from tao2typ.raw import RawCapture, load_raw_capture
from tao2typ.source import BlogClient
from tao2typ.storage import write_json

POST_URL = (
    "https://terrytao.wordpress.com/2007/03/22/"
    "freimans-theorem-in-finite-fields-via-extremal-set-theory/"
)


def main() -> None:
    output = Path(".cache/smoke")
    with BlogClient() as client:
        metadata = client.lookup_post(POST_URL)
        capture = RawCapture(
            output,
            "runner-source-check",
            POST_URL,
            published_at=metadata.published_at.isoformat(),
            modified_at=metadata.modified_at.isoformat(),
        )
        pages = fetch_raw_pages(client, POST_URL, capture)
        post = parse_post(
            pages[0],
            POST_URL,
            comment_pages=pages[1:],
            published_at=metadata.published_at.isoformat(),
            modified_at=metadata.modified_at.isoformat(),
        )
        if not post.body or post.url != POST_URL:
            raise ValueError("Source check did not preserve the expected post")
        _, saved_pages, _ = load_raw_capture(output / capture.manifest_path)
        if saved_pages != pages:
            raise ValueError("Saved source differs from parser input")
        write_json(output / "post.json.gz", post.to_dict())
        recent = client.recent_comments()
        write_json(output / "request-statistics.json.gz", client.request_statistics)
    generate_site(Path("archive"), Path(".cache/smoke-site"))
    summary = (
        f"Source check passed: {post.title}, {len(pages)} source page(s), "
        f"{len(recent)} recent comment link(s). Catalogue build passed. "
        "No PDFs or daily conversion attempts were used."
    )
    print(summary)
    if destination := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(destination).open("a", encoding="utf-8") as file:
            file.write(summary + "\n")


if __name__ == "__main__":
    main()
