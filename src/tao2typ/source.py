"""Polite WordPress discovery and fetching, independent of document conversion.

The public API supplies publication dates; dates in URL paths are never treated
as creation timestamps. Prefer the actual site's HTML; its official public API
provides rendered content and complete discussion when the site returns 403.
"""

from __future__ import annotations

import base64
import binascii
import html as html_module
import re
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from urllib.parse import (
    parse_qs,
    parse_qsl,
    quote,
    unquote,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

import httpx
from bs4 import BeautifulSoup

SITE_HOST = "terrytao.wordpress.com"
SITE_URL = f"https://{SITE_HOST}/"
POSTS_API = f"https://public-api.wordpress.com/rest/v1.1/sites/{SITE_HOST}/posts/"
COMMENTS_API = f"https://public-api.wordpress.com/rest/v1.1/sites/{SITE_HOST}/comments/"
METADATA_FIELDS = "ID,URL,title,date,modified,tags,categories"
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_CACHED_ASSET_BYTES = 64 * 1024 * 1024
_POST_PATH = re.compile(
    r"^/(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/"
    r"(?P<slug>[^/]+)/?(?:comment-page-(?P<page>[1-9]\d*)/?)?$"
)
_COMMENT_FRAGMENT = re.compile(r"^comment-\d+$")
_RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_PROXY_IMAGE_HOST = re.compile(r"i[012]\.wp\.com")
_WIKIMEDIA_THUMB = re.compile(r"^(/wikipedia/[^/]+)/thumb/([0-9a-f]/[0-9a-f]{2}/[^/]+)/[^/]+$")
_PROXY_PARAMETERS = frozenset(
    {"crop", "fit", "h", "lb", "quality", "resize", "ssl", "strip", "w", "zoom"}
)
# Verified zero-change Git rename (blob ca3b37b0d8f687accb4ffe5f1ef3a5ca88fa9e7c).
# Pin the moved file to its original repository commit, rather than replacing it.
_VERIFIED_ASSET_MOVES = {
    "https://raw.githubusercontent.com/km-git-acc/dbn_upper_bound/master/"
    "python/H_t%20zero%20trajectory%20plot.png": "https://raw.githubusercontent.com/km-git-acc/dbn_upper_bound/"
    "a76b3aaee1b3601c81d95fdf66808cec5d586d71/"
    "dbn_upper_bound/python/H_t%20zero%20trajectory%20plot.png"
}


def _embedded_asset(url: str) -> bytes | None:
    if not url.startswith("data:"):
        return None
    match = re.fullmatch(r"data:image/[A-Za-z0-9.+-]+;base64,(.*)", url, flags=re.DOTALL)
    if match is None:
        raise ValueError("Embedded image must use a base64 image data URI")
    payload = unquote(match[1])
    if len(payload) > 4 * ((MAX_RESPONSE_BYTES + 2) // 3):
        raise SourceError("Embedded image exceeds the response size limit")
    try:
        content = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("Invalid base64 embedded image") from error
    if len(content) > MAX_RESPONSE_BYTES:
        raise SourceError("Embedded image exceeds the response size limit")
    return content


def _asset_wrapper(url: str) -> str | None:
    """Resolve a source-encoded file view, without choosing a replacement image."""
    parts = urlsplit(url)
    if parts.hostname == "github.com":
        path = parts.path.split("/")
        if len(path) >= 6 and path[3] == "blob" and all(path[1:]):
            return "https://raw.githubusercontent.com/" + "/".join(path[1:3] + path[4:])
    if re.fullmatch(r"[a-z-]+\.wikipedia\.org", parts.hostname or "") and parts.fragment.startswith(
        "/media/File:"
    ):
        filename = unquote(parts.fragment[len("/media/File:") :])
        if filename and "/" not in filename and "\\" not in filename:
            return f"https://{parts.hostname}/wiki/Special:Redirect/file/{quote(filename, safe='')}"
    return None


class SourceError(RuntimeError):
    """An incomplete or inconsistent source must not become a successful batch."""


def _original_asset(url: str) -> str | None:
    """Recover only the source explicitly encoded in a failed image-service URL.

    Photon proxies can retain broken thumbnails for years. Wikimedia also
    rejects historical thumbnail widths while keeping the original file. Each
    transformation removes one service layer; downloads retain normal limits.
    """
    if url in _VERIFIED_ASSET_MOVES:
        return _VERIFIED_ASSET_MOVES[url]
    parts = urlsplit(url)
    if _PROXY_IMAGE_HOST.fullmatch(parts.hostname or ""):
        original = urlsplit("https://" + parts.path.lstrip("/"))
        if original.hostname and "." in original.hostname and not original.username:
            query = urlencode(
                [
                    (key, value)
                    for key, value in parse_qsl(parts.query, keep_blank_values=True)
                    if key not in _PROXY_PARAMETERS
                ]
            )
            return urlunsplit(("https", original.netloc, original.path, query, ""))
    if parts.hostname == "upload.wikimedia.org" and (
        match := _WIKIMEDIA_THUMB.fullmatch(parts.path)
    ):
        return urlunsplit((parts.scheme, parts.netloc, f"{match[1]}/{match[2]}", "", ""))
    return None


@dataclass(frozen=True)
class TaxonomyTerm:
    """An original WordPress tag or category, with a stable source link."""

    id: int
    name: str
    slug: str
    url: str
    parent_id: int | None = None


@dataclass(frozen=True)
class PostMetadata:
    id: int
    url: str
    title: str
    published_at: datetime
    modified_at: datetime
    tags: tuple[TaxonomyTerm, ...] = ()
    categories: tuple[TaxonomyTerm, ...] = ()


@dataclass(frozen=True)
class RecentComment:
    """The stable comment permalink and the post it invalidates."""

    event_url: str
    post_url: str


@dataclass(frozen=True)
class ApiPayload:
    """Exact public API response bytes, retained independently of render HTML."""

    content: bytes
    url: str
    requested_url: str
    fetched_at: str


@dataclass(frozen=True)
class FetchedPage:
    """Exact parser input, with HTTP or official API response provenance."""

    html: str
    url: str
    requested_url: str
    fetched_at: str
    api_payloads: tuple[ApiPayload, ...] = ()


def _api_comments(data: object) -> tuple[int, list[dict[str, object]]]:
    if not isinstance(data, dict) or not isinstance(data.get("comments"), list):
        raise SourceError("WordPress API returned an invalid comments listing")
    found = data.get("found")
    if not isinstance(found, int) or isinstance(found, bool) or found < 0:
        raise SourceError("WordPress API returned an invalid total comment count")
    comments = data["comments"]
    if len(comments) > 100 or any(not isinstance(item, dict) for item in comments):
        raise SourceError("WordPress API returned invalid comment records")
    return found, comments


def _api_json(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError as error:
        raise SourceError("WordPress API returned invalid JSON") from error


def _comment_identity(comment: Mapping[str, object]) -> tuple[int, str, str]:
    identifier, url = comment.get("ID"), comment.get("URL")
    if (
        not isinstance(identifier, int)
        or isinstance(identifier, bool)
        or identifier <= 0
        or not isinstance(url, str)
        or urlsplit(url).fragment != f"comment-{identifier}"
    ):
        raise SourceError("WordPress API returned an invalid comment ID or permalink")
    try:
        canonical = canonical_post_url(url)
    except ValueError as error:
        raise SourceError("WordPress API comment permalink is not a Tao blog post") from error
    return identifier, canonical, url


def _api_html(post: Mapping[str, object], comments: list[dict[str, object]]) -> str:
    """Wrap the API's rendered HTML verbatim in parser-compatible structure."""
    metadata = _metadata(post)
    content = post.get("content")
    if not isinstance(content, str):
        raise SourceError("WordPress API post is missing rendered HTML content")
    children: dict[int | None, list[int]] = {}
    records: dict[int, str] = {}
    parents: dict[int, int | None] = {}
    for comment in comments:
        identifier, canonical, url = _comment_identity(comment)
        linked_post = comment.get("post")
        if (
            canonical != metadata.url
            or not isinstance(linked_post, dict)
            or linked_post.get("ID") != metadata.id
        ):
            raise SourceError("WordPress API returned a comment for another post")
        if identifier in records:
            raise SourceError("WordPress API repeated a comment")
        author, body = comment.get("author"), comment.get("content")
        if (
            not isinstance(author, dict)
            or not isinstance(author.get("name"), str)
            or not isinstance(body, str)
            or comment.get("status") != "approved"
        ):
            raise SourceError("WordPress API returned an invalid public comment")
        timestamp = _aware_datetime(comment.get("date"), "comment date").isoformat()
        parent = comment.get("parent")
        parent_id = parent.get("ID") if isinstance(parent, dict) else None
        if parent not in (None, False) and (
            not isinstance(parent, dict)
            or not isinstance(parent_id, int)
            or isinstance(parent_id, bool)
            or parent_id <= 0
        ):
            raise SourceError("WordPress API returned an invalid comment parent")
        parents[identifier] = parent_id
        children.setdefault(parent_id, []).append(identifier)
        name = BeautifulSoup(author["name"], "html.parser").get_text(" ", strip=True)
        avatar = author.get("avatar_URL")
        avatar_html = (
            '<img class="avatar" width="48" height="48" src="'
            + html_module.escape(avatar, quote=True)
            + '" alt="">'
            if isinstance(avatar, str) and avatar
            else ""
        )
        records[identifier] = (
            f'<li id="comment-{identifier}" class="comment">'
            f'<div class="comment-author">{avatar_html}<cite class="fn">'
            + html_module.escape(name)
            + '</cite></div><div class="comment-meta"><a class="comment-permalink" href="'
            + html_module.escape(url, quote=True)
            + '"><time datetime="'
            + html_module.escape(timestamp, quote=True)
            + '">'
            + html_module.escape(timestamp)
            + f'</time></a></div><div class="comment-content">{body}</div>'
        )
    for identifier, parent in parents.items():
        ancestors = {identifier}
        while parent is not None:
            if parent not in parents:
                raise SourceError("WordPress API omitted a parent comment")
            if parent in ancestors:
                raise SourceError("WordPress API returned cyclic comment ancestry")
            ancestors.add(parent)
            parent = parents[parent]
    pieces = [
        '<!doctype html><html><head><meta property="article:published_time" content="',
        html_module.escape(metadata.published_at.isoformat(), quote=True),
        '"><meta property="article:modified_time" content="',
        html_module.escape(metadata.modified_at.isoformat(), quote=True),
        '"></head><body><article><h1 class="entry-title">',
        str(post["title"]),
        '</h1><div class="entry-content">',
        content,
        '</div></article><section id="comments"><ol class="commentlist">',
    ]
    # Iteration avoids Python's recursion limit for deeply nested public threads.
    stack: list[int | str] = list(reversed(children.get(None, [])))
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            pieces.append(item)
            continue
        pieces.append(records[item])
        replies = children.get(item, [])
        if replies:
            pieces.append('<ol class="children">')
            stack.append("</ol></li>")
            stack.extend(reversed(replies))
        else:
            pieces.append("</li>")
    pieces.append("</ol></section></body></html>")
    return "".join(pieces)


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


def _taxonomy(data: object, kind: str) -> tuple[TaxonomyTerm, ...]:
    """Read WordPress v1.1's name-keyed taxonomy maps, including empty lists.

    This API does not normally supply public taxonomy URLs. Category IDs are
    stable even when the API slug differs from the site's hierarchical URL,
    so use WordPress's category-ID link rather than guessing a permalink.
    """
    if data is None or data == []:
        return ()
    if not isinstance(data, dict):
        raise SourceError(f"WordPress returned invalid {kind} metadata")
    terms: dict[int, TaxonomyTerm] = {}
    for key, item in data.items():
        if not isinstance(item, dict):
            raise SourceError(f"WordPress returned an invalid {kind} term")
        identifier = item.get("ID")
        name, slug = item.get("name", key), item.get("slug")
        if (
            not isinstance(identifier, int)
            or isinstance(identifier, bool)
            or identifier <= 0
            or not isinstance(name, str)
            or not name
            or not isinstance(slug, str)
            or not slug
        ):
            raise SourceError(f"WordPress returned an invalid {kind} term")
        parent = item.get("parent") if kind == "categories" else None
        if parent is not None and (
            not isinstance(parent, int) or isinstance(parent, bool) or parent < 0
        ):
            raise SourceError("WordPress returned an invalid category parent")
        url = item.get("URL")
        if url is None:
            url = (
                f"{SITE_URL}tag/{quote(slug, safe='%')}/"
                if kind == "tags"
                else f"{SITE_URL}?cat={identifier}"
            )
        if not isinstance(url, str) or not url:
            raise SourceError(f"WordPress returned an invalid {kind} URL")
        terms[identifier] = TaxonomyTerm(
            id=identifier,
            name=BeautifulSoup(name, "html.parser").get_text(" ", strip=True),
            slug=slug,
            url=url,
            parent_id=parent,
        )
    return tuple(terms.values())


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
        tags=_taxonomy(data.get("tags"), "tags"),
        categories=_taxonomy(data.get("categories"), "categories"),
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


@dataclass
class _HostRequestStatistics:
    attempts: int = 0
    status_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class _RequestStatistics:
    attempts: int = 0
    status_counts: dict[str, int] = field(default_factory=dict)
    by_host: dict[str, _HostRequestStatistics] = field(default_factory=dict)
    rate_limit_responses: int = 0
    retries: int = 0
    retry_wait_seconds: float = 0.0
    transport_failures: int = 0
    asset_cache_hits: int = 0

    def begin_attempt(self, host: str) -> None:
        self.attempts += 1
        self.by_host.setdefault(host, _HostRequestStatistics()).attempts += 1

    def record_status(self, host: str, status: int) -> None:
        key = str(status)
        self.status_counts[key] = self.status_counts.get(key, 0) + 1
        counts = self.by_host[host].status_counts
        counts[key] = counts.get(key, 0) + 1
        if status == 429:
            self.rate_limit_responses += 1


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
        self._statistics = _RequestStatistics()
        # Reuse avatars/diagrams across posts in one job, while every new
        # client revalidates remote bytes during the next scheduled refresh.
        self._asset_cache: OrderedDict[str, bytes] = OrderedDict()
        self._asset_cache_bytes = 0
        self._post_ids: dict[str, int] = {}
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=httpx.Timeout(timeout),
            headers={
                "User-Agent": (
                    "tao2typ/0.1 (+https://clvnkhr.github.io/tao2typ/; blog-to-PDF converter)"
                )
            },
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

    @property
    def request_statistics(self) -> dict[str, object]:
        """Return an independent JSON-ready snapshot for this client lifetime.

        Host counts describe requested hosts, including final response statuses
        after redirects. Retry waits exclude the ordinary polite interval.
        Offline mock subclasses need not call this class's initializer.
        """
        statistics = getattr(self, "_statistics", None)
        return asdict(statistics if statistics is not None else _RequestStatistics())

    def _request(
        self, url: str, *, params: Mapping[str, str | int] | None = None
    ) -> httpx.Response:
        host = urlsplit(url).hostname or ""
        for attempt in range(self.retries + 1):
            if self._last_request is not None:
                delay = self.interval - (self._monotonic() - self._last_request)
                if delay > 0:
                    self._sleep(delay)
            self._last_request = self._monotonic()
            self._statistics.begin_attempt(host)
            try:
                with self._client.stream(
                    "GET", url, params=params, follow_redirects=True
                ) as response:
                    self._statistics.record_status(host, response.status_code)
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
                delay = _retry_delay(error.response, attempt)
                self._statistics.retries += 1
                self._statistics.retry_wait_seconds += delay
                self._sleep(delay)
            except httpx.TransportError:
                self._statistics.transport_failures += 1
                if attempt == self.retries:
                    raise
                delay = _retry_delay(None, attempt)
                self._statistics.retries += 1
                self._statistics.retry_wait_seconds += delay
                self._sleep(delay)
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
                    self._post_ids[post.url] = post.id
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
        self._post_ids[post.url] = post.id
        return post

    def recent_comments(self) -> list[RecentComment]:
        try:
            return extract_recent_comments(self._request(SITE_URL).text)
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 403:
                raise
        response = self._request(
            COMMENTS_API, params={"number": 100, "order": "DESC", "status": "approved"}
        )
        found, comments = _api_comments(_api_json(response))
        if len(comments) != min(found, 100):
            raise SourceError("WordPress API recent comments listing is incomplete")
        events: dict[str, RecentComment] = {}
        seen: set[int] = set()
        for comment in comments:
            # Public page comments are not dated blog posts, just as the HTML
            # Recent Comments reader ignores unrelated/page links.
            url = comment.get("URL")
            if not isinstance(url, str):
                raise SourceError("WordPress API recent comment has no permalink")
            try:
                canonical_post_url(url)
            except ValueError:
                continue
            identifier, post_url, _ = _comment_identity(comment)
            if identifier in seen:
                raise SourceError("WordPress API repeated a recent comment")
            seen.add(identifier)
            event_url = f"{post_url}#comment-{identifier}"
            events[event_url] = RecentComment(event_url, post_url)
        return list(events.values())

    def _api_post_pages(
        self,
        canonical: str,
        *,
        on_page: Callable[[FetchedPage], None] | None,
        on_api_payload: Callable[[ApiPayload], None] | None,
    ) -> list[str]:
        payloads: list[ApiPayload] = []

        def request(url: str, params: Mapping[str, str | int]) -> object:
            response = self._request(url, params=params)
            payload = ApiPayload(
                response.content,
                str(response.url),
                str(httpx.Request("GET", url, params=params).url),
                datetime.now(UTC).isoformat(),
            )
            payloads.append(payload)
            if on_api_payload is not None:
                on_api_payload(payload)
            return _api_json(response)

        identifier = self._post_ids.get(canonical)
        slug = urlsplit(canonical).path.rstrip("/").rsplit("/", 1)[-1]
        endpoint = POSTS_API + (
            f"{identifier}/" if identifier is not None else f"slug:{quote(slug, safe='%')}/"
        )
        data = request(endpoint, {"context": "display"})
        metadata = _metadata(data)
        if metadata.url != canonical:
            raise SourceError("WordPress API fallback returned another post")
        assert isinstance(data, dict)
        if (
            data.get("status") != "publish"
            or data.get("has_password") is True
            or not isinstance(data.get("content"), str)
        ):
            raise SourceError("WordPress API fallback did not return a public rendered post")
        discussion = data.get("discussion")
        expected = discussion.get("comment_count") if isinstance(discussion, dict) else None
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
            raise SourceError("WordPress API post has an invalid total comment count")
        comments: list[dict[str, object]] = []
        seen: set[int] = set()
        page = 0
        while True:
            if page >= self.max_comment_pages:
                raise SourceError(f"API comment pagination exceeds {self.max_comment_pages} pages")
            listing = request(
                f"{POSTS_API}{metadata.id}/replies/",
                {
                    "context": "display",
                    "number": 100,
                    "offset": len(comments),
                    "hierarchical": "false",
                    "order": "ASC",
                    "status": "approved",
                    "type": "any",
                },
            )
            page += 1
            found, records = _api_comments(listing)
            if found != expected:
                raise SourceError("WordPress API comment counts changed or disagree during fetch")
            if not records and len(comments) < expected:
                raise SourceError("WordPress API returned an empty page before comments completed")
            for record in records:
                comment_id, post_url, _ = _comment_identity(record)
                if post_url != canonical:
                    raise SourceError("WordPress API returned a comment for another post")
                if comment_id in seen:
                    raise SourceError("WordPress API pagination repeated a comment")
                seen.add(comment_id)
                comments.append(record)
            if len(comments) > expected:
                raise SourceError("WordPress API returned more comments than its total count")
            if len(comments) == expected:
                break
        html = _api_html(data, comments)
        fetched = FetchedPage(
            html, canonical, canonical, datetime.now(UTC).isoformat(), tuple(payloads)
        )
        if on_page is not None:
            on_page(fetched)
        return [html]

    def fetch_post_pages(
        self,
        url: str,
        *,
        on_page: Callable[[FetchedPage], None] | None = None,
        on_api_payload: Callable[[ApiPayload], None] | None = None,
    ) -> list[str]:
        """Fetch the post and every reachable comment page, with cycle protection.

        Follow only explicit pagination links for this same dated post, including
        both pretty permalinks and older ``?cpage=N`` links. Repeated post bodies
        and overlapping comments are deduplicated by the document parser. Only
        HTTP 403 switches to the site's official public rendered-content API.
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
            try:
                response = self._request(current)
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 403:
                    raise
                return pages + self._api_post_pages(
                    canonical, on_page=on_page, on_api_payload=on_api_payload
                )
            html = response.text
            if on_page is not None:
                on_page(
                    FetchedPage(html, str(response.url), current, datetime.now(UTC).isoformat())
                )
            if canonical_post_url(str(response.request.url)) != canonical:
                raise SourceError(f"Post fetch redirected to another post: {response.request.url}")
            pages.append(html)
            soup = BeautifulSoup(html, "html.parser")
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
        """Fetch an image, recovering failed Photon/Wikimedia service URLs.

        At most two source transformations follow permanent 400/404 responses.
        A rate limit, authorization error or oversized response remains a failure.
        All requests retain the normal delay, retries and 20 MiB download cap.
        Successful bytes are reused only for this client's lifetime.
        """
        if (embedded := _embedded_asset(url)) is not None:
            return embedded
        url = _asset_wrapper(url) or url
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError(f"Asset URL must use HTTP or HTTPS: {url}")
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
        cache_key = url
        if cache_key in self._asset_cache:
            self._statistics.asset_cache_hits += 1
            self._asset_cache.move_to_end(cache_key)
            return self._asset_cache[cache_key]
        for attempt in range(3):
            try:
                response = self._request(url)
                if (
                    response.headers.get("content-type", "").lower().startswith("text/html")
                    and (original := _original_asset(url))
                    and attempt < 2
                ):
                    url = original
                    continue
                data = response.content
                if len(data) <= MAX_CACHED_ASSET_BYTES:
                    while self._asset_cache_bytes + len(data) > MAX_CACHED_ASSET_BYTES:
                        _, evicted = self._asset_cache.popitem(last=False)
                        self._asset_cache_bytes -= len(evicted)
                    self._asset_cache[cache_key] = data
                    self._asset_cache_bytes += len(data)
                return data
            except httpx.HTTPStatusError as error:
                original = _original_asset(url)
                if error.response.status_code not in {400, 404} or not original or attempt == 2:
                    raise
                url = original
        raise AssertionError("Unreachable image fallback state")
