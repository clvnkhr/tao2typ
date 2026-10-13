"""Polite WordPress discovery and fetching, independent of document conversion.

The public API supplies publication dates; dates in URL paths are never treated
as creation timestamps. HTML is fetched from the actual site so the converter
can retain the post's maths markup and the complete paginated discussion.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from urllib.parse import parse_qs, quote, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

SITE_HOST = "terrytao.wordpress.com"
SITE_URL = f"https://{SITE_HOST}/"
POSTS_API = f"https://public-api.wordpress.com/rest/v1.1/sites/{SITE_HOST}/posts/"
METADATA_FIELDS = "ID,URL,title,date,modified"
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
_POST_PATH = re.compile(
    r"^/(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/"
    r"(?P<slug>[^/]+)/?(?:comment-page-(?P<page>[1-9]\d*)/?)?$"
)
_COMMENT_FRAGMENT = re.compile(r"^comment-\d+$")
_RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class SourceError(RuntimeError):
    """An incomplete or inconsistent source must not become a successful batch."""


@dataclass(frozen=True)
class PostMetadata:
    id: int
    url: str
    title: str
    published_at: datetime
    modified_at: datetime


@dataclass(frozen=True)
class RecentComment:
    """The stable comment permalink and the post it invalidates."""

    event_url: str
    post_url: str


def canonical_post_url(url: str) -> str:
    """Validate a dated Tao permalink and strip comment pages/query/fragment."""
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or parts.netloc.lower() != SITE_HOST
        or not (match := _POST_PATH.fullmatch(parts.path))
    ):
        raise ValueError(f"Not a Tao blog post URL: {url}")
    try:
        date(int(match["year"]), int(match["month"]), int(match["day"]))
    except ValueError as error:
        raise ValueError(f"Invalid publication date in post URL: {url}") from error
    return f"{SITE_URL}{match['year']}/{match['month']}/{match['day']}/{match['slug']}/"


def _page_url(url: str) -> str:
    canonical = canonical_post_url(url)
    parts = urlsplit(url)
    match = _POST_PATH.fullmatch(parts.path)
    assert match is not None
    if page := match["page"]:
        return f"{canonical}comment-page-{page}/"
    cpage = parse_qs(parts.query).get("cpage", [])
    if cpage and re.fullmatch(r"[1-9]\d*", cpage[0]):
        return f"{canonical}?cpage={cpage[0]}"
    return canonical


def extract_recent_comments(html: str) -> list[RecentComment]:
    """Read only Recent Comments widgets, excluding authors and avatar links.

    Keep the comment ID but canonicalize away its page number. A comment can
    move between pages as the discussion grows without becoming a new event.
    A missing widget is an error: silently accepting a changed theme would
    prevent comment-triggered updates.
    """
    soup = BeautifulSoup(html, "html.parser")
    widgets = soup.select(".widget_recent_comments, [id^='recent-comments-']")
    if not widgets:
        raise SourceError("The homepage has no recognizable Recent Comments widget")
    events: dict[str, RecentComment] = {}
    for widget in widgets:
        for anchor in widget.select("a[href]"):
            href = anchor.get("href")
            if not isinstance(href, str):
                continue
            absolute = urljoin(SITE_URL, href)
            fragment = urlsplit(absolute).fragment
            if not _COMMENT_FRAGMENT.fullmatch(fragment):
                continue
            try:
                post_url = canonical_post_url(absolute)
            except ValueError:
                continue
            event_url = f"{post_url}#{fragment}"
            events[event_url] = RecentComment(event_url, post_url)
    return list(events.values())


def _aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise SourceError(f"WordPress metadata field {field!r} is not a timestamp")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as error:
        raise SourceError(f"Invalid WordPress timestamp for {field!r}: {value}") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise SourceError(f"WordPress timestamp for {field!r} has no timezone: {value}")
    # Preserve the original publication offset for faithful site metadata.
    return timestamp


def _metadata(data: object) -> PostMetadata:
    if not isinstance(data, dict):
        raise SourceError("WordPress returned a non-object post")
    identifier, url, title = data.get("ID"), data.get("URL"), data.get("title")
    if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier <= 0:
        raise SourceError("WordPress returned an invalid post ID")
    if not isinstance(url, str) or not isinstance(title, str):
        raise SourceError(f"WordPress post {identifier} has missing URL or title")
    try:
        url = canonical_post_url(url)
    except ValueError as error:
        raise SourceError(f"WordPress post {identifier} has an invalid permalink") from error
    return PostMetadata(
        id=identifier,
        url=url,
        title=BeautifulSoup(title, "html.parser").get_text(" ", strip=True),
        published_at=_aware_datetime(data.get("date"), "date"),
        modified_at=_aware_datetime(data.get("modified"), "modified"),
    )


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    backoff = min(2.0**attempt, 60.0)
    if response is None or not (retry_after := response.headers.get("Retry-After")):
        return backoff
    try:
        delay = float(retry_after)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(retry_after)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            delay = (parsed - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return backoff
    return max(backoff, min(delay, 300.0))


class BlogClient:
    """A synchronous, rate-limited client with bounded downloads and retries.

    ``retries`` counts retries after the first attempt. A caller may inject an
    httpx client and sleep/clock functions for deterministic offline tests.
    Injected clients remain owned by the caller; otherwise use a context manager
    or call ``close`` to release connections.
    """

    def __init__(
        self,
        *,
        interval: float = 1.0,
        timeout: float = 45.0,
        retries: int = 3,
        max_comment_pages: int = 1000,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval < 0 or timeout <= 0 or retries < 0 or max_comment_pages < 1:
            raise ValueError("Invalid HTTP client limits")
        self.interval = interval
        self.retries = retries
        self.max_comment_pages = max_comment_pages
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request: float | None = None
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": "tao2typ/0.1 (public blog archive; polite sequential fetcher)"},
        )

    def __enter__(self) -> BlogClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _request(
        self, url: str, *, params: Mapping[str, str | int] | None = None
    ) -> httpx.Response:
        for attempt in range(self.retries + 1):
            if self._last_request is not None:
                delay = self.interval - (self._monotonic() - self._last_request)
                if delay > 0:
                    self._sleep(delay)
            self._last_request = self._monotonic()
            try:
                with self._client.stream(
                    "GET", url, params=params, follow_redirects=True
                ) as response:
                    response.raise_for_status()
                    if content_length := response.headers.get("Content-Length"):
                        try:
                            declared_length = int(content_length)
                        except ValueError:
                            declared_length = 0
                        if declared_length > MAX_RESPONSE_BYTES:
                            raise SourceError(f"Response exceeds 20 MiB: {url}")
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise SourceError(f"Response exceeds 20 MiB: {url}")
                        chunks.append(chunk)
                    # iter_bytes has already decompressed the response. Do not
                    # let httpx decode it a second time when rebuilding it.
                    headers = dict(response.headers)
                    headers.pop("content-encoding", None)
                    headers.pop("content-length", None)
                    return httpx.Response(
                        status_code=response.status_code,
                        headers=headers,
                        content=b"".join(chunks),
                        request=response.request,
                    )
            except httpx.HTTPStatusError as error:
                if error.response.status_code not in _RETRY_STATUS or attempt == self.retries:
                    raise
                self._sleep(_retry_delay(error.response, attempt))
            except httpx.TransportError:
                if attempt == self.retries:
                    raise
                self._sleep(_retry_delay(None, attempt))
        raise AssertionError("Unreachable HTTP retry state")

    def discover_posts(
        self, *, page_size: int = 100, modified_after: datetime | None = None
    ) -> Iterator[PostMetadata]:
        """Yield all public posts, using stable WordPress cursor pagination.

        A full metadata scan costs about twelve small requests for this blog.
        Optional ``modified_after`` is the API's edit-time filter, not ``after``
        (which only filters publication time). Callers should overlap timestamp
        windows when using incremental discovery.
        """
        if not 1 <= page_size <= 100:
            raise ValueError("WordPress page_size must be between 1 and 100")
        params: dict[str, str | int] = {
            "number": page_size,
            "fields": METADATA_FIELDS,
            "type": "post",
            "status": "publish",
            "order_by": "date",
            "order": "ASC",
        }
        if modified_after is not None:
            if modified_after.tzinfo is None or modified_after.utcoffset() is None:
                raise ValueError("modified_after must have a timezone")
            params["modified_after"] = modified_after.astimezone(UTC).isoformat()
        seen_ids: set[int] = set()
        seen_handles: set[str] = set()
        total_found: int | None = None
        page = 1
        while True:
            data = self._request(POSTS_API, params=params).json()
            if not isinstance(data, dict) or not isinstance(data.get("posts"), list):
                raise SourceError("WordPress returned an invalid posts listing")
            found = data.get("found")
            if not isinstance(found, int) or isinstance(found, bool) or found < 0:
                raise SourceError("WordPress returned an invalid total post count")
            # In cursor mode WordPress reports the number of posts remaining
            # from this cursor, rather than the original inventory size. Only
            # the first page's count can be compared with all IDs seen so far.
            if total_found is None:
                total_found = found
            posts = data["posts"]
            if not posts:
                if len(seen_ids) < total_found:
                    raise SourceError("WordPress returned an empty page before discovery completed")
                return
            previous_count = len(seen_ids)
            for item in posts:
                post = _metadata(item)
                if post.id not in seen_ids:
                    seen_ids.add(post.id)
                    yield post
            if len(seen_ids) == previous_count:
                raise SourceError("WordPress pagination repeated posts without advancing")
            if len(seen_ids) >= total_found:
                return
            meta = data.get("meta")
            next_handle = meta.get("next_page") if isinstance(meta, dict) else None
            page += 1
            if isinstance(next_handle, str) and next_handle:
                if next_handle in seen_handles:
                    raise SourceError("WordPress pagination repeated a page cursor")
                seen_handles.add(next_handle)
                params["page_handle"] = next_handle
                params.pop("page", None)
            else:
                if len(posts) < page_size:
                    if len(seen_ids) < total_found:
                        raise SourceError(
                            "WordPress listing ended before all posts were discovered"
                        )
                    return
                params.pop("page_handle", None)
                params["page"] = page

    def lookup_post(self, url: str) -> PostMetadata:
        """Look up metadata for a comment-linked post absent from the local index."""
        canonical = canonical_post_url(url)
        slug = urlsplit(canonical).path.rstrip("/").rsplit("/", 1)[-1]
        endpoint = f"{POSTS_API}slug:{quote(slug, safe='%')}/"
        post = _metadata(self._request(endpoint, params={"fields": METADATA_FIELDS}).json())
        if post.url != canonical:
            raise SourceError(f"WordPress slug lookup returned another post: {post.url}")
        return post

    def recent_comments(self) -> list[RecentComment]:
        return extract_recent_comments(self._request(SITE_URL).text)

    def fetch_post_pages(self, url: str) -> list[str]:
        """Fetch the post and every reachable comment page, with cycle protection.

        Follow only explicit pagination links for this same dated post, including
        both pretty permalinks and older ``?cpage=N`` links. Repeated post bodies
        and overlapping comments are deduplicated by the document parser.
        """
        canonical = canonical_post_url(url)
        pending = deque([canonical])
        seen: set[str] = set()
        pages: list[str] = []
        while pending:
            current = pending.popleft()
            if current in seen:
                continue
            if len(seen) >= self.max_comment_pages:
                raise SourceError(f"Comment pagination exceeds {self.max_comment_pages} pages")
            seen.add(current)
            response = self._request(current)
            if canonical_post_url(str(response.request.url)) != canonical:
                raise SourceError(f"Post fetch redirected to another post: {response.request.url}")
            pages.append(response.text)
            soup = BeautifulSoup(response.text, "html.parser")
            for anchor in soup.select("a[href]"):
                href = anchor.get("href")
                if not isinstance(href, str):
                    continue
                absolute = urljoin(current, href)
                parts = urlsplit(absolute)
                if "comment-page-" not in parts.path and "cpage" not in parse_qs(parts.query):
                    continue
                try:
                    if canonical_post_url(absolute) != canonical:
                        continue
                    candidate = _page_url(absolute)
                except ValueError:
                    continue
                if candidate not in seen and candidate not in pending:
                    pending.append(candidate)
        return pages

    def fetch_asset(self, url: str) -> bytes:
        """Fetch a linked image over HTTP(S), capped at 20 MiB after decompression."""
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError(f"Asset URL must use HTTP or HTTPS: {url}")
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
        return self._request(url).content
