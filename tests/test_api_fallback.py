"""Official public API fallback preserves rendered content and complete threads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from tao2typ.parser import parse_post
from tao2typ.raw import RawCapture, load_raw_capture
from tao2typ.source import ApiPayload, BlogClient, FetchedPage, SourceError
from tao2typ.storage import read_bytes, read_json, write_bytes

POST = "https://terrytao.wordpress.com/2024/01/31/example/"
API = "https://public-api.wordpress.com/rest/v1.1/sites/terrytao.wordpress.com/"
CONTENT = '<p>Exact &amp; content <img class="latex" alt="|x|" src="latex.php?latex=%7Cx%7C"></p>'


def post(count: int = 3) -> dict[str, object]:
    return {
        "ID": 42,
        "URL": POST,
        "title": "Example &amp; theorem",
        "date": "2024-01-31T23:30:00-08:00",
        "modified": "2024-02-01T00:15:00-08:00",
        "status": "publish",
        "content": CONTENT,
        "discussion": {"comment_count": count},
    }


def comment(identifier: int, parent: int | None = None) -> dict[str, object]:
    return {
        "ID": identifier,
        "URL": POST + f"comment-page-1/#comment-{identifier}",
        "date": "2024-02-01T01:30:00-08:00",
        "content": f"<p>Reply {identifier} &amp; maths.</p>",
        "author": {
            "name": f"Author {identifier}",
            "avatar_URL": "https://gravatar.com/avatar/test",
        },
        "parent": {"ID": parent} if parent is not None else False,
        "post": {"ID": 42},
        "status": "approved",
    }


def encoded(data: object) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\r\n").encode()


def test_html_remains_preferred_without_any_api_requests() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, text='<div class="entry-content">Original HTML</div>')

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        pages = client.fetch_post_pages(POST)
    assert pages == ['<div class="entry-content">Original HTML</div>']
    assert requests == [POST]


def test_api_fallback_retains_content_dates_avatars_threads_and_exact_response_bytes(
    tmp_path: Path,
) -> None:
    responses: list[bytes] = []
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "terrytao.wordpress.com":
            return httpx.Response(403)
        if "/replies/" not in request.url.path:
            body = encoded(post())
        else:
            offset = int(request.url.params["offset"])
            body = encoded(
                {
                    "found": 3,
                    "comments": [comment(20), comment(21, 20)]
                    if offset == 0
                    else [comment(22, 21)],
                }
            )
        responses.append(body)
        return httpx.Response(200, content=body, headers={"Content-Type": "application/json"})

    capture = RawCapture(tmp_path, "example", POST)

    def record(page: FetchedPage) -> None:
        capture.add(
            page.html,
            page.url,
            requested_url=page.requested_url,
            fetched_at=page.fetched_at,
            api_payloads=page.api_payloads,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        pages = client.fetch_post_pages(
            POST, on_page=record, on_api_payload=capture.add_api_payload
        )
    capture.finish()
    document = parse_post(pages[0], POST)
    assert CONTENT in pages[0]
    assert document.title == "Example & theorem"
    assert document.published_at == "2024-01-31T23:30:00-08:00"
    assert document.modified_at == "2024-02-01T00:15:00-08:00"
    assert document.comments[0].id == "comment-20"
    assert document.comments[0].replies[0].id == "comment-21"
    assert document.comments[0].replies[0].replies[0].id == "comment-22"
    assert document.comments[0].avatar is not None
    assert document.comments[0].avatar.attrs["url"] == "https://gravatar.com/avatar/test"
    assert [
        request.url.params["offset"] for request in requests if "/replies/" in request.url.path
    ] == ["0", "2"]
    assert all(
        request.url.params["number"] == "100"
        for request in requests
        if "/replies/" in request.url.path
    )
    metadata, retained, _ = load_raw_capture(tmp_path / capture.manifest_path)
    assert retained == pages
    assert metadata["pages"][0]["source"] == "wordpress-public-api"
    assert len(metadata["api_payloads"]) == 3  # Immediate and page callbacks deduplicate.
    for item, original in zip(metadata["api_payloads"], responses, strict=True):
        assert item["bytes_sha256"] == hashlib.sha256(original).hexdigest()
        assert read_bytes(tmp_path / item["path"]) == original
        assert item["url"].startswith(API)
        assert item["fetched_at"].endswith("+00:00")


@pytest.mark.parametrize("status", [401, 404, 429, 500])
def test_other_http_failures_do_not_trigger_api_fallback(status: int) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(status)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0, retries=0)
        with pytest.raises(httpx.HTTPStatusError):
            client.fetch_post_pages(POST)
    assert requests == [POST]


@pytest.mark.parametrize(
    ("records", "found", "message"),
    [
        ([], 3, "empty page"),
        ([comment(20), comment(20)], 3, "repeated a comment"),
        ([comment(20)], 2, "counts changed or disagree"),
        ([comment(20, 999), comment(21), comment(22)], 3, "omitted a parent"),
        ([comment(20, 21), comment(21, 20), comment(22)], 3, "cyclic"),
        ([{**comment(20), "post": {"ID": 99}}, comment(21), comment(22)], 3, "another post"),
    ],
)
def test_incomplete_or_inconsistent_api_threads_fail_with_json_provenance(
    tmp_path: Path, records: list[dict[str, object]], found: int, message: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "terrytao.wordpress.com":
            return httpx.Response(403)
        data = {"found": found, "comments": records} if "/replies/" in request.url.path else post()
        return httpx.Response(200, content=encoded(data))

    capture = RawCapture(tmp_path, "example", POST)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        with pytest.raises(SourceError, match=message):
            client.fetch_post_pages(POST, on_api_payload=capture.add_api_payload)
    capture.fail("incomplete")
    manifest = read_json(tmp_path / capture.manifest_path)
    assert manifest["complete"] is False
    assert len(manifest["api_payloads"]) == 2
    assert all((tmp_path / item["path"]).exists() for item in manifest["api_payloads"])


def test_raw_api_checksum_is_verified_before_offline_parsing(tmp_path: Path) -> None:
    capture = RawCapture(tmp_path, "example", POST)
    payload = ApiPayload(
        encoded(post(0)), API + "posts/42/", API + "posts/42/", "2024-02-01T00:00:00+00:00"
    )
    capture.add('<div class="entry-content">Body</div>', POST, api_payloads=(payload,))
    capture.finish()
    metadata = read_json(tmp_path / capture.manifest_path)
    path = tmp_path / metadata["api_payloads"][0]["path"]
    write_bytes(path, b'{"changed":true}')
    with pytest.raises(ValueError, match="API payload checksum mismatch"):
        load_raw_capture(tmp_path / capture.manifest_path)


def test_recent_comments_403_fallback_keeps_widget_event_urls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "terrytao.wordpress.com":
            return httpx.Response(403)
        assert request.url.path.endswith("/comments/")
        return httpx.Response(200, json={"found": 2, "comments": [comment(21, 20), comment(20)]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        events = BlogClient(http_client=http_client, interval=0).recent_comments()
    assert [event.event_url for event in events] == [POST + "#comment-21", POST + "#comment-20"]
    assert [event.post_url for event in events] == [POST, POST]


@pytest.mark.parametrize("found", [None, True, -1, "3"])
def test_invalid_api_comment_counts_cannot_report_a_complete_post(found: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "terrytao.wordpress.com":
            return httpx.Response(403)
        data = {"found": found, "comments": []} if "/replies/" in request.url.path else post()
        return httpx.Response(200, json=data)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(SourceError, match="invalid total comment count"):
            BlogClient(http_client=http_client, interval=0).fetch_post_pages(POST)


def test_api_pagination_page_limit_and_empty_post_listing_are_strict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "terrytao.wordpress.com":
            return httpx.Response(403)
        data = (
            {"found": 3, "comments": [comment(20)]} if "/replies/" in request.url.path else post()
        )
        return httpx.Response(200, json=data)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(SourceError, match="exceeds 1 pages"):
            BlogClient(http_client=http_client, interval=0, max_comment_pages=1).fetch_post_pages(
                POST
            )


def test_zero_comment_post_still_verifies_the_api_listing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "terrytao.wordpress.com":
            return httpx.Response(403)
        data = {"found": 0, "comments": []} if "/replies/" in request.url.path else post(0)
        return httpx.Response(200, json=data)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        pages = client.fetch_post_pages(POST)
    assert parse_post(pages[0], POST).comments == []
    assert client.request_statistics["attempts"] == 3


def test_invalid_json_is_retained_even_when_api_decoding_fails(tmp_path: Path) -> None:
    body = b"not JSON\r\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403 if request.url.host == "terrytao.wordpress.com" else 200, content=body
        )

    capture = RawCapture(tmp_path, "example", POST)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(SourceError, match="invalid JSON"):
            BlogClient(http_client=http_client, interval=0).fetch_post_pages(
                POST, on_api_payload=capture.add_api_payload
            )
    capture.fail("invalid JSON")
    manifest = read_json(tmp_path / capture.manifest_path)
    assert read_bytes(tmp_path / manifest["api_payloads"][0]["path"]) == body


@pytest.mark.parametrize(
    "listing",
    [
        {"found": 3, "comments": [comment(20)]},
        {"found": 2, "comments": [comment(20), comment(20)]},
    ],
)
def test_recent_comments_incompleteness_or_duplicates_remain_failures(listing: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "terrytao.wordpress.com":
            return httpx.Response(403)
        return httpx.Response(200, json=listing)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(SourceError):
            BlogClient(http_client=http_client, interval=0).recent_comments()


def test_later_comment_page_403_keeps_original_html_in_parser_and_capture_order() -> None:
    original = (
        '<h1 class="entry-title">Example</h1><div class="entry-content">Original.</div>'
        f'<a href="{POST}comment-page-2/">Older</a>'
    )
    captured: list[FetchedPage] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == POST:
            return httpx.Response(200, text=original)
        if request.url.host == "terrytao.wordpress.com":
            return httpx.Response(403)
        data = {"found": 0, "comments": []} if "/replies/" in request.url.path else post(0)
        return httpx.Response(200, json=data)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        pages = BlogClient(http_client=http_client, interval=0).fetch_post_pages(
            POST, on_page=captured.append
        )
    assert pages[0] == original
    assert [page.html for page in captured] == pages
    assert len(pages) == 2
