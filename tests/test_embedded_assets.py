"""Recover exact embedded files and source-encoded image viewers."""

import base64

import httpx
import pytest

from tao2typ import source
from tao2typ.parser import parse_post
from tao2typ.source import BlogClient, SourceError


def test_bare_embedded_image_is_retained_and_decoded_without_http() -> None:
    content = b"\x89PNG\r\n\x1a\nexact embedded payload"
    original = "image/png;base64," + base64.b64encode(content).decode()
    post = parse_post(
        '<h2 class="entry-title">A post</h2>'
        f'<div class="entry-content"><img src="{original}"></div>',
        "https://terrytao.wordpress.com/2023/12/05/a-post/",
    )
    image = next(node for node in post.walk() if node.kind == "image")
    assert image.attrs["url"] == "data:" + original
    assert image.attrs["original_src"] == original

    def fail(request: httpx.Request) -> httpx.Response:
        pytest.fail("Embedded image must not make an HTTP request")

    with httpx.Client(transport=httpx.MockTransport(fail)) as http:
        client = BlogClient(http_client=http, interval=0)
        assert client.fetch_asset(image.attrs["url"]) == content


@pytest.mark.parametrize(
    "uri",
    ["data:text/html;base64,PGh0bWw+", "data:image/png;base64,!bad!", "data:image/png,no-base64"],
)
def test_invalid_embedded_image_is_an_explicit_failure(uri: str) -> None:
    with BlogClient(interval=0) as client, pytest.raises(ValueError):
        client.fetch_asset(uri)


def test_embedded_image_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(source, "MAX_RESPONSE_BYTES", 4)
    uri = "data:image/png;base64," + base64.b64encode(b"12345").decode()
    with BlogClient(interval=0) as client, pytest.raises(SourceError, match="size limit"):
        client.fetch_asset(uri)


@pytest.mark.parametrize(
    ("original", "target"),
    [
        (
            "https://github.com/user/repo/blob/main/plot%20one.png",
            "https://raw.githubusercontent.com/user/repo/main/plot%20one.png",
        ),
        (
            "https://en.wikipedia.org/wiki/Article#/media/File:Photo_one.jpg",
            "https://en.wikipedia.org/wiki/Special:Redirect/file/Photo_one.jpg",
        ),
    ],
)
def test_image_viewer_resolves_only_the_file_encoded_in_source(original: str, target: str) -> None:
    requested: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=b"image bytes")

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = BlogClient(http_client=http, interval=0)
        assert client.fetch_asset(original) == b"image bytes"
    assert requested == [target]


def test_proxy_html_landing_page_retries_exact_original_file() -> None:
    requested: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.host == "i0.wp.com":
            return httpx.Response(
                200, text="<html>Image service error</html>", headers={"content-type": "text/html"}
            )
        return httpx.Response(200, content=b"original image")

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = BlogClient(http_client=http, interval=0)
        assert (
            client.fetch_asset("https://i0.wp.com/images.example/plot.png?w=200")
            == b"original image"
        )
    assert requested == [
        "https://i0.wp.com/images.example/plot.png?w=200",
        "https://images.example/plot.png",
    ]
