"""Offline tests for pagination completeness, invalidation, and HTTP limits."""

import gzip
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tao2typ import source
from tao2typ.source import (
    BlogClient,
    SourceError,
    TaxonomyTerm,
    canonical_post_url,
    extract_recent_comments,
)

POST = "https://terrytao.wordpress.com/2024/01/31/example/"
OTHER = "https://terrytao.wordpress.com/2024/02/01/other/"


def metadata(identifier: int = 1, *, url: str = POST) -> dict[str, object]:
    return {
        "ID": identifier,
        "URL": url,
        "title": "Example &amp; <em>maths</em>",
        "date": "2024-01-31T23:30:00-08:00",
        "modified": "2024-02-01T00:15:00-08:00",
    }


@pytest.mark.parametrize(
    "url",
    [POST, POST.rstrip("/"), POST + "comment-page-7/#comment-25", POST + "?cpage=3"],
)
def test_post_url_canonicalization(url: str) -> None:
    assert canonical_post_url(url) == POST


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/2024/01/31/example/",
        "https://terrytao.wordpress.com.evil.example/2024/01/31/example/",
        "https://terrytao.wordpress.com/about/",
        "file:///2024/01/31/example/",
        "https://terrytao.wordpress.com/2024/02/31/example/",
        "https://user@terrytao.wordpress.com/2024/01/31/example/",
    ],
)
def test_rejects_non_post_urls(url: str) -> None:
    with pytest.raises(ValueError):
        canonical_post_url(url)


def test_recent_comment_widget_ignores_authors_avatars_and_body() -> None:
    # This table mirrors the live blog's WordPress widget, which is not a <ul>.
    html = f"""
    <article><a href="{OTHER}#comment-999">Body comment link</a></article>
    <div id="recent-comments-2" class="widget widget_recent_comments">
      <h3>Recent Comments</h3><table class="recentcommentsavatar">
        <tr><td><a href="https://author.example"><img src="avatar.png"></a></td>
          <td><a href="https://author.example">Alice</a> on
          <a href="{POST}comment-page-2/#comment-42">Example</a></td></tr>
        <tr><td><a href="{POST}?cpage=3#comment-42">Same comment moved</a></td></tr>
        <tr><td><a href="{POST}#comment-43">Newer comment</a></td></tr>
        <tr><td><a href="https://example.com/2024/01/31/x/#comment-44">Other blog</a></td></tr>
      </table>
    </div>
    """
    comments = extract_recent_comments(html)
    assert [comment.event_url for comment in comments] == [
        POST + "#comment-42",
        POST + "#comment-43",
    ]
    assert [comment.post_url for comment in comments] == [POST, POST]


def test_missing_recent_comments_widget_is_an_error() -> None:
    with pytest.raises(SourceError, match="Recent Comments"):
        extract_recent_comments("<h1>A changed theme or HTTP challenge</h1>")
    assert extract_recent_comments('<div class="widget_recent_comments"></div>') == []


def test_discovery_uses_cursor_and_timezone_aware_api_dates() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "page_handle" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "found": 2,
                    "posts": [metadata()],
                    "meta": {"next_page": "value=2024-01-31T23:30:00-08:00&id=1"},
                },
            )
        return httpx.Response(200, json={"found": 2, "posts": [metadata(2, url=OTHER)]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        posts = list(client.discover_posts(page_size=1))
    assert len(requests) == 2
    assert [post.id for post in posts] == [1, 2]
    assert posts[0].title == "Example & maths"
    assert posts[0].published_at.utcoffset() == timedelta(hours=-8)
    assert posts[0].published_at.astimezone(UTC).isoformat() == "2024-02-01T07:30:00+00:00"
    assert requests[1].url.params["page_handle"].endswith("&id=1")
    assert requests[0].url.params["fields"] == "ID,URL,title,date,modified,tags,categories"


def test_original_tags_and_hierarchical_categories_are_distinct() -> None:
    data = metadata()
    data["tags"] = {
        "math.GM": {"ID": 10, "name": "math.GM", "slug": "math-gm"},
        "Sophie Huiberts": {
            "ID": 792514498,
            "name": "Sophie Huiberts",
            "slug": "sophie-huiberts",
            "URL": "https://terrytao.wordpress.com/tag/sophie-huiberts/",
        },
    }
    data["categories"] = {
        "math.GM": {
            "ID": 1440160,
            "name": "math.GM",
            "slug": "math-gm",
            "parent": 3582,
        }
    }
    post = source._metadata(data)
    assert post.tags == (
        TaxonomyTerm(10, "math.GM", "math-gm", "https://terrytao.wordpress.com/tag/math-gm/"),
        TaxonomyTerm(
            792514498,
            "Sophie Huiberts",
            "sophie-huiberts",
            "https://terrytao.wordpress.com/tag/sophie-huiberts/",
        ),
    )
    assert post.categories == (
        TaxonomyTerm(
            1440160, "math.GM", "math-gm", "https://terrytao.wordpress.com/?cat=1440160", 3582
        ),
    )


@pytest.mark.parametrize("empty", [None, {}, []])
def test_empty_and_older_metadata_taxonomy_is_backwards_compatible(empty: object) -> None:
    data = metadata()
    data["tags"], data["categories"] = empty, empty
    post = source._metadata(data)
    assert post.tags == post.categories == ()
    assert source._metadata(metadata()).tags == ()


def test_taxonomy_retains_supplied_source_url_and_decodes_name() -> None:
    data = metadata()
    data["categories"] = {
        "Math &amp; Science": {
            "ID": 3,
            "slug": "math-science",
            "URL": "https://terrytao.wordpress.com/category/mathematics/math-science/",
            "parent": 0,
        }
    }
    assert source._metadata(data).categories == (
        TaxonomyTerm(
            3,
            "Math & Science",
            "math-science",
            "https://terrytao.wordpress.com/category/mathematics/math-science/",
            0,
        ),
    )


@pytest.mark.parametrize(
    "terms",
    [
        "bad",
        [{"ID": 1}],
        {"a": "bad"},
        {"a": {"ID": True, "name": "a", "slug": "a"}},
        {"a": {"ID": 0, "name": "a", "slug": "a"}},
        {"a": {"ID": 1, "name": "a", "slug": ""}},
        {"a": {"ID": 1, "name": "a", "slug": "a", "parent": -1}},
        {"a": {"ID": 1, "name": "a", "slug": "a", "URL": False}},
    ],
)
def test_malformed_taxonomy_is_an_error(terms: object) -> None:
    data = metadata()
    data["categories"] = terms
    with pytest.raises(SourceError, match="invalid"):
        source._metadata(data)


def test_discovery_filter_is_modified_time_not_publication() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"found": 0, "posts": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        assert list(client.discover_posts(modified_after=datetime(2024, 1, 1, tzinfo=UTC))) == []
    assert requests[0].url.params["modified_after"] == "2024-01-01T00:00:00+00:00"
    assert "after" not in requests[0].url.params


def test_cursor_remaining_count_does_not_truncate_the_old_post_inventory() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = len(requests)
        return httpx.Response(
            200,
            json={
                # Real WordPress cursor responses reduce found on every page.
                "found": 4 - page,
                "posts": [metadata(page, url=POST if page == 1 else OTHER)],
                "meta": {"next_page": f"cursor-{page}"},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        assert [post.id for post in client.discover_posts(page_size=1)] == [1, 2, 3]
    assert len(requests) == 3


def test_discovery_raises_on_cursor_cycle_instead_of_silently_truncating() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"found": 2, "posts": [metadata()], "meta": {"next_page": "same"}},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        with pytest.raises(SourceError, match="without advancing"):
            list(client.discover_posts())


def test_discovery_falls_back_to_page_numbers_without_cursors() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(
            200, json={"found": 2, "posts": [metadata(page, url=POST if page == 1 else OTHER)]}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        assert len(list(client.discover_posts(page_size=1))) == 2
    assert requests[1].url.params["page"] == "2"


def test_discovery_requires_real_publication_timestamp() -> None:
    data = metadata()
    data["date"] = "2024-01-31T23:30:00"
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"found": 1, "posts": [data]})
        )
    ) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        with pytest.raises(SourceError, match="timezone"):
            list(client.discover_posts())


def test_empty_metadata_page_cannot_hide_missing_posts() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"found": 2, "posts": []})
        )
    ) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        with pytest.raises(SourceError, match="before discovery completed"):
            list(client.discover_posts())


@pytest.mark.parametrize("count", [None, True, -1, "1143"])
def test_missing_or_malformed_count_cannot_report_empty_inventory(count: object) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"found": count, "posts": []})
        )
    ) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        with pytest.raises(SourceError, match="invalid total post count"):
            list(client.discover_posts())


def test_lookup_by_slug_checks_returned_canonical_permalink() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=metadata())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        assert client.lookup_post(POST + "comment-page-3/#comment-1").id == 1
        with pytest.raises(SourceError, match="another post"):
            client.lookup_post(OTHER)
    assert requests[0].url.path.endswith("posts/slug:example/")


def test_comment_pages_traverse_older_newer_with_cycle_guard() -> None:
    requests: list[str] = []
    html_pages = {
        POST: f'<h1>Post</h1><a href="{POST}comment-page-2/#comments">Older</a>',
        POST + "comment-page-2/": (
            f'<a href="{POST}?cpage=1#comments">Older</a>'
            f'<a href="{POST}comment-page-3/#comments">Newer</a>'
            f'<a href="{OTHER}comment-page-1/#comments">Unrelated</a>'
        ),
        POST + "?cpage=1": f'<a href="{POST}comment-page-2/#comments">Newer</a>',
        POST + "comment-page-3/": f'<a href="{POST}comment-page-2/#comments">Older</a>',
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, text=html_pages[str(request.url)])

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        captured = []
        pages = client.fetch_post_pages(POST, on_page=captured.append)
    assert len(pages) == 4
    assert len(requests) == len(set(requests)) == 4
    assert not any(url.startswith(OTHER) for url in requests)
    assert [page.html for page in captured] == pages
    assert [page.url for page in captured] == requests
    assert [page.requested_url for page in captured] == requests
    assert all(page.fetched_at.endswith("+00:00") for page in captured)


def test_comment_page_limit_does_not_report_partial_success() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=f'<a href="{POST}comment-page-2/">Older</a>')
        )
    ) as http_client:
        client = BlogClient(http_client=http_client, interval=0, max_comment_pages=1)
        with pytest.raises(SourceError, match="exceeds 1 pages"):
            client.fetch_post_pages(POST)


def test_retry_after_and_polite_interval() -> None:
    now = 0.0
    waits: list[float] = []
    attempts = 0

    def sleep(seconds: float) -> None:
        nonlocal now
        waits.append(seconds)
        now += seconds

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "4"})
        return httpx.Response(200, content=b"image")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=1, sleep=sleep, monotonic=lambda: now)
        assert client.fetch_asset("https://uploads.example/image.png") == b"image"
        assert client.fetch_asset("https://uploads.example/another-image.png") == b"image"
    assert attempts == 3
    assert waits == [4, 1]


def test_request_statistics_record_rate_limits_waits_hosts_and_cached_successes() -> None:
    now = 0.0
    waits: list[float] = []
    requests: list[httpx.Request] = []

    def sleep(seconds: float) -> None:
        nonlocal now
        waits.append(seconds)
        now += seconds

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": "4"})
        return httpx.Response(200, content=b"image")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=1, sleep=sleep, monotonic=lambda: now)
        url = "https://uploads.example/image.png"
        assert client.fetch_asset(url) == b"image"
        assert client.fetch_asset(url + "#same-image") == b"image"
        assert client.fetch_asset("https://diagrams.example/another-image.png") == b"image"
        snapshot = client.request_statistics
        assert snapshot == {
            "attempts": 3,
            "status_counts": {"429": 1, "200": 2},
            "by_host": {
                "uploads.example": {"attempts": 2, "status_counts": {"429": 1, "200": 1}},
                "diagrams.example": {"attempts": 1, "status_counts": {"200": 1}},
            },
            "rate_limit_responses": 1,
            "retries": 1,
            "retry_wait_seconds": 4.0,
            "transport_failures": 0,
            "asset_cache_hits": 1,
        }
        assert json.loads(json.dumps(snapshot)) == snapshot
        # A caller can serialize or annotate a snapshot without altering counters.
        hosts = snapshot["by_host"]
        assert isinstance(hosts, dict)
        hosts["uploads.example"]["status_counts"]["429"] = 100
        assert client.request_statistics["by_host"] == {
            "uploads.example": {"attempts": 2, "status_counts": {"429": 1, "200": 1}},
            "diagrams.example": {"attempts": 1, "status_counts": {"200": 1}},
        }
        fresh_client = BlogClient(http_client=http_client, interval=0)
        assert fresh_client.request_statistics["attempts"] == 0
        assert fresh_client.request_statistics["by_host"] == {}
    assert waits == [4, 1]
    assert len(requests) == 3


def test_request_statistics_include_exhausted_rate_limits_before_raising() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(429))
    ) as http_client:
        client = BlogClient(http_client=http_client, interval=0, retries=0)
        with pytest.raises(httpx.HTTPStatusError):
            client.fetch_asset("https://uploads.example/rate-limited.png")
        statistics = client.request_statistics
    assert statistics["attempts"] == statistics["rate_limit_responses"] == 1
    assert statistics["status_counts"] == {"429": 1}
    assert statistics["retries"] == statistics["retry_wait_seconds"] == 0


def test_request_statistics_record_transport_failures_and_backoff() -> None:
    requests = 0
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, content=b"image")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0, sleep=waits.append)
        assert client.fetch_asset("https://uploads.example/image.png") == b"image"
        statistics = client.request_statistics
    assert statistics["attempts"] == 2
    assert statistics["status_counts"] == {"200": 1}
    assert statistics["by_host"] == {
        "uploads.example": {"attempts": 2, "status_counts": {"200": 1}}
    }
    assert statistics["transport_failures"] == statistics["retries"] == 1
    assert statistics["retry_wait_seconds"] == 1.0
    assert waits == [1.0]


def test_request_statistics_are_safe_for_offline_mock_subclasses() -> None:
    class OfflineClient(BlogClient):
        def __init__(self) -> None:
            pass

    client = OfflineClient()
    snapshot = client.request_statistics
    assert snapshot["attempts"] == 0
    assert snapshot["by_host"] == {}
    assert snapshot["asset_cache_hits"] == 0
    assert json.loads(json.dumps(snapshot)) == snapshot


def test_permanent_http_errors_are_not_retried() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        with pytest.raises(httpx.HTTPStatusError):
            client.fetch_asset("https://uploads.example/missing.png")
    assert attempts == 1


def test_failed_wordpress_proxy_and_old_wikimedia_thumbnail_recover_original() -> None:
    # These exact URLs failed in the initial local batch. Wikimedia now refuses
    # the old 100px thumbnail, but the original SVG is still available.
    proxy = (
        "https://i0.wp.com/upload.wikimedia.org/wikipedia/commons/thumb/1/11/"
        "Complete_bipartite_graph_K3%2C3.svg/100px-Complete_bipartite_graph_K3%2C3.svg.png"
    )
    thumbnail = proxy.replace("https://i0.wp.com/", "https://")
    original = (
        "https://upload.wikimedia.org/wikipedia/commons/1/11/Complete_bipartite_graph_K3%2C3.svg"
    )
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return (
            httpx.Response(200, content=b'<svg xmlns="http://www.w3.org/2000/svg"/>')
            if str(request.url) == original
            else httpx.Response(400)
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        assert client.fetch_asset(proxy).startswith(b"<svg")
    assert requests == [proxy, thumbnail, original]


def test_failed_wordpress_proxy_strips_only_service_parameters() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(404 if len(requests) == 1 else 200, content=b"image")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        assert client.fetch_asset("https://i2.wp.com/images.example/a.png?resize=20%2C20&v=3")
    assert requests == [
        "https://i2.wp.com/images.example/a.png?resize=20%2C20&v=3",
        "https://images.example/a.png?v=3",
    ]


@pytest.mark.parametrize("status", [403, 429])
def test_image_fallback_does_not_bypass_access_errors_or_rate_limits(status: int) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(status)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0, retries=0)
        with pytest.raises(httpx.HTTPStatusError) as failure:
            client.fetch_asset("https://i0.wp.com/images.example/a.png")
    assert failure.value.response.status_code == status
    assert len(requests) == 1


def test_image_origin_fallback_retains_decoded_download_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source, "MAX_RESPONSE_BYTES", 100)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "i0.wp.com":
            return httpx.Response(404)
        return httpx.Response(200, content=b"x" * 101)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        with pytest.raises(SourceError, match="20 MiB"):
            client.fetch_asset("https://i0.wp.com/images.example/a.png")


def test_asset_rejects_non_http_scheme_without_request() -> None:
    with BlogClient(interval=0) as client:
        with pytest.raises(ValueError, match="HTTP or HTTPS"):
            client.fetch_asset("file:///private/tmp/image.png")


def test_asset_download_limit_checks_decoded_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(source, "MAX_RESPONSE_BYTES", 100)
    compressed = gzip.compress(b"x" * 101)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Content-Encoding": "gzip"}, content=compressed
            )
        )
    ) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        with pytest.raises(SourceError, match="20 MiB"):
            client.fetch_asset("https://uploads.example/huge.png")


def test_gzip_response_is_decoded_once() -> None:
    compressed = gzip.compress(b"actual image bytes")
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Content-Encoding": "gzip"}, content=compressed
            )
        )
    ) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        assert client.fetch_asset("https://uploads.example/image.svg") == b"actual image bytes"


def test_asset_cache_reuses_successes_but_retries_failures_and_revalidates_new_clients() -> None:
    requests: list[str] = []
    url = "https://uploads.example/avatar.png"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if len(requests) == 1:
            return httpx.Response(404)
        return httpx.Response(200, content=f"image version {len(requests)}".encode())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        with pytest.raises(httpx.HTTPStatusError):
            client.fetch_asset(url)
        assert client.fetch_asset(url) == b"image version 2"
        assert client.fetch_asset(url + "#avatar") == b"image version 2"
        assert requests == [url, url]
        fresh_client = BlogClient(http_client=http_client, interval=0)
        assert fresh_client.fetch_asset(url) == b"image version 3"
        assert fresh_client.fetch_asset(url) == b"image version 3"
    assert requests == [url, url, url]


def test_asset_cache_evicts_least_recently_used_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(source, "MAX_CACHED_ASSET_BYTES", 6)
    requests: list[str] = []
    urls = [f"https://uploads.example/{name}.png" for name in ("a", "b", "c")]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, content=b"abc")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BlogClient(http_client=http_client, interval=0)
        for index in (0, 1, 0, 2, 0, 1):
            assert client.fetch_asset(urls[index]) == b"abc"
    assert requests == [urls[0], urls[1], urls[2], urls[1]]
