"""Build a catalogue, optionally exporting documents for a public release.

Default builds contain public metadata and repository download links. An explicit
download base URL exports referenced documents and their image assets. Operational
state, job reports and failure files are never exported. The page includes
the ordered queue and brief failure reasons. Local previews can expose diagnostics.
"""

import html
import json
import re
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from unicodedata import normalize
from urllib.parse import quote, urlsplit

from tao2typ.diagnostics import failure_artifacts, failure_stems
from tao2typ.storage import read_bytes, read_json, read_text

_DEFAULT_REPO = "https://github.com/clvnkhr/tao2typ"
_MODULE = Path(__file__).resolve()
_ASSETS = _MODULE.parent / "web"
if not _ASSETS.is_dir():
    _ASSETS = _MODULE.parents[2] / "web"


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _public_url(value: object) -> str:
    url = str(value or "")
    parsed = urlsplit(url)
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else "#"


def _conversion_date(value: object) -> str:
    if not isinstance(value, str):
        return ""
    try:
        converted = datetime.fromisoformat(value)
        if converted.tzinfo is not None:
            converted = converted.astimezone(UTC)
        return converted.date().isoformat()
    except ValueError:
        return ""


def _attempt_date(value: object) -> str:
    """Normalize timestamps for chronological browser sorting and display."""
    if not isinstance(value, str):
        return ""
    try:
        attempted = datetime.fromisoformat(value)
        if attempted.tzinfo is None:
            attempted = attempted.replace(tzinfo=UTC)
        return attempted.astimezone(UTC).isoformat()
    except ValueError:
        return ""


def _failure_summary(error: str) -> str:
    """Summarize known failure categories without publishing local stack traces."""
    if status := re.search(r"(?:Client|Server) error '(\d{3})\b", error):
        code = status[1]
        return "Rate limited (HTTP 429)" if code == "429" else f"Download failed (HTTP {code})"
    if any(
        marker in error.lower()
        for marker in ("nodename nor servname", "name resolution", "getaddrinfo")
    ):
        return "DNS lookup failed"
    if "timeout" in error.lower() or "timed out" in error.lower():
        return "Download timed out"
    if command := re.search(r"unknown command: (\\(?:[a-zA-Z]{1,64}|[^\w\s]))", error):
        return f"Unrecognized TeX command: {command[1]}"
    if "Typst compilation failed" in error:
        for marker, reason in (
            ("unclosed delimiter", "Unclosed math delimiter"),
            ("unknown alignment", "Unsupported TeX alignment"),
            ("failed to decode image", "Image could not be decoded"),
        ):
            if marker in error:
                return reason
        return "PDF compilation failed"
    return "Conversion failed"


def _failure_markup(
    archive: Path, post: dict[str, Any], stem: str, diagnostics_base_url: str | None
) -> str:
    error = post.get("last_error")
    if not isinstance(error, str) or not error:
        return ""
    attempted = _attempt_date(post.get("last_attempted_at"))
    timestamp = (
        f'<p>Last attempt <time datetime="{attempted}">'
        f"{attempted[:16].replace('T', ' ')}</time> UTC</p>"
        if attempted
        else ""
    )
    extra = ""
    if diagnostics_base_url is not None:
        links = []
        for key in failure_artifacts(archive, post, stem):
            label = {"typst": "Failed .typ", "ir": "Failed JSON", "capture": "Capture"}.get(key)
            if key.startswith("html/"):
                label = f"HTML {int(key.split('/')[1]) + 1}"
            url = (
                f"{diagnostics_base_url}/__tao2typ_failure/{quote(str(post['id']), safe='')}/{key}"
            )
            links.append(f'<a href="{_escape(url)}">{_escape(label)}</a>')
        extra = f'<pre>{_escape(error)}</pre><div class="failure-actions">{"".join(links)}</div>'
    return (
        '<details class="failure-details">'
        f"<summary>Failed · {_escape(_failure_summary(error))}</summary>"
        f"{timestamp}{extra}</details>"
    )


def _terms(post: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [
        term
        for term in post.get(kind, [])
        if isinstance(term, dict) and term.get("name") and term.get("slug")
    ]


def _download(
    archive: Path,
    post: dict[str, Any],
    kind: str,
    repo_url: str,
    branch: str,
    download_base_url: str | None,
) -> str | None:
    path = _artifact_path(archive, post, kind)
    if path is None:
        return None
    relative = path.relative_to(archive).as_posix()
    if download_base_url is not None:
        relative = relative.removesuffix(".gz")
        return f"{download_base_url}/{quote(relative, safe='/')}"
    return f"{repo_url}/blob/{quote(branch, safe='')}/{quote('archive/' + relative, safe='/')}"


def _artifact_path(archive: Path, post: dict[str, Any], kind: str) -> Path | None:
    value = post.get(kind)
    if not isinstance(value, str):
        return None
    path = PurePosixPath(value)
    folder, extension = {
        "pdf": ("pdfs", ".pdf"),
        "typst": ("typst", ".typ"),
        "ir": ("ir", ".json"),
    }[kind]
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(path.parts) != 2
        or path.parts[0] != folder
        or not (
            path.name.endswith(extension) or kind != "pdf" and path.name.endswith(extension + ".gz")
        )
    ):
        return None
    source = archive / path
    if (
        not source.is_file()
        or source.is_symlink()
        or not source.resolve().is_relative_to(archive.resolve() / folder)
    ):
        return None
    return source


def _asset_path(archive: Path, value: str) -> Path:
    path = PurePosixPath(value)
    source = archive / path
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(path.parts) != 2
        or path.parts[0] != "assets"
        or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
        or not source.is_file()
        or source.is_symlink()
        or not source.resolve().is_relative_to(archive.resolve() / "assets")
    ):
        raise ValueError(f"Missing or unsafe exported image asset: {value}")
    return source


def _ir_assets(value: Any) -> set[str]:
    if isinstance(value, dict):
        found: set[str] = set()
        if value.get("kind") == "image":
            asset = value.get("attrs", {}).get("asset_path")
            if isinstance(asset, str):
                found.add(asset)
        for item in value.values():
            found.update(_ir_assets(item))
        return found
    if isinstance(value, list):
        return set().union(*(_ir_assets(item) for item in value))
    return set()


def _export_downloads(archive: Path, output: Path, posts: list[dict[str, Any]]) -> int:
    if output.resolve().is_relative_to(archive.resolve()):
        raise ValueError("Public export output must be outside the archive directory")
    sources: set[Path] = set()
    for post in posts:
        for kind in ("pdf", "typst", "ir"):
            source = _artifact_path(archive, post, kind)
            if source is None:
                continue
            sources.add(source)
            if kind == "typst":
                for literal in re.findall(r'#image\(\s*("(?:[^"\\]|\\.)*")', read_text(source)):
                    asset = json.loads(literal)
                    if urlsplit(asset).scheme in {"http", "https"}:
                        continue
                    if not asset.startswith("../assets/"):
                        raise ValueError(f"Unsupported exported Typst image path: {asset}")
                    sources.add(_asset_path(archive, asset[3:]))
            elif kind == "ir":
                payload = read_json(source)
                sources.update(_asset_path(archive, asset) for asset in _ir_assets(payload))
    # Validate the complete set before writing any document, including output symlinks.
    destinations = [
        (source, output / source.relative_to(archive).as_posix().removesuffix(".gz"))
        for source in sources
    ]
    for _, destination in destinations:
        if destination.is_symlink() or not destination.resolve().is_relative_to(output.resolve()):
            raise ValueError(f"Unsafe public export destination: {destination}")
    for source, destination in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == ".gz":
            destination.write_bytes(read_bytes(source))
        else:
            shutil.copyfile(source, destination)
    return len(sources)


def _route(kind: str, slug: object) -> str:
    return f"#{kind}={quote(str(slug), safe='')}"


def _term_index(
    posts: list[dict[str, Any]], kind: str
) -> tuple[dict[str, dict[str, Any]], Counter[str]]:
    terms: dict[str, dict[str, Any]] = {}
    for post in posts:
        for term in _terms(post, kind):
            slug = str(term["slug"])
            terms.setdefault(slug, term)
    counts: Counter[str] = Counter()
    for post in posts:
        assigned = _terms(post, kind)
        counts.update(
            _category_slugs(assigned, terms)
            if kind == "categories"
            else {str(term["slug"]) for term in assigned}
        )
    return terms, counts


def _category_slugs(assigned: list[dict[str, Any]], terms: dict[str, dict[str, Any]]) -> list[str]:
    """A parent category archive includes posts assigned to its descendants."""
    by_id = {str(term.get("id")): term for term in terms.values()}
    slugs: set[str] = set()
    for assigned_term in assigned:
        term = assigned_term
        while str(term["slug"]) not in slugs:
            slugs.add(str(term["slug"]))
            parent = by_id.get(str(term.get("parent_id", 0)))
            if parent is None:
                break
            term = parent
    return sorted(slugs)


def _ordered_terms(
    terms: dict[str, dict[str, Any]], hierarchical: bool
) -> list[tuple[dict[str, Any], int]]:
    by_id = {str(term.get("id")): term for term in terms.values()}
    children: dict[str, list[dict[str, Any]]] = {}
    for term in terms.values():
        parent = str(term.get("parent_id", 0)) if hierarchical else "0"
        if parent == str(term.get("id")) or parent not in by_id:
            parent = "0"
        children.setdefault(parent, []).append(term)
    ordered = []
    seen: set[str] = set()

    def visit(term: dict[str, Any], depth: int) -> None:
        slug = str(term["slug"])
        if slug in seen:
            return
        seen.add(slug)
        ordered.append((term, min(depth, 6)))
        for child in sorted(
            children.get(str(term.get("id")), []), key=lambda item: str(item["name"]).casefold()
        ):
            visit(child, depth + 1)

    for root in sorted(children.get("0", []), key=lambda item: str(item["name"]).casefold()):
        visit(root, 0)
    # Malformed source hierarchies (including cycles) still remain discoverable.
    for term in sorted(terms.values(), key=lambda item: str(item["name"]).casefold()):
        visit(term, 0)
    return ordered


def _taxonomy_links(terms: dict[str, dict[str, Any]], counts: Counter[str], kind: str) -> str:
    return "\n".join(
        f'<a href="{_escape(_route(kind, term["slug"]))}" '
        f'data-route="{_escape(kind + "=" + str(term["slug"]))}" '
        f'data-name="{_escape(term["name"])}" class="depth-{depth}">'
        f"{_escape(term['name'])} <span>{counts[str(term['slug'])]}</span></a>"
        for term, depth in _ordered_terms(terms, hierarchical=kind == "category")
    )


def _post_markup(
    archive: Path,
    post: dict[str, Any],
    repo_url: str,
    branch: str,
    category_index: dict[str, dict[str, Any]],
    download_base_url: str | None,
    queue_position: int | None = None,
    queue_status: str = "",
    queue_reasons: str = "",
    *,
    stem: str = "",
    diagnostics_base_url: str | None = None,
) -> tuple[str, bool]:
    tags = _terms(post, "tags")
    categories = _terms(post, "categories")
    downloads = {
        kind: _download(archive, post, kind, repo_url, branch, download_base_url)
        for kind in ("pdf", "typst", "ir")
    }
    ready = downloads["pdf"] is not None
    title = str(post.get("title") or "Untitled post")
    published = str(post.get("published_at") or "")[:10]
    original = _escape(_public_url(post.get("url")))
    search = " ".join([title, *(str(term["name"]) for term in [*tags, *categories])])
    attributes = {
        "id": post.get("id", ""),
        "title": title,
        "date": published,
        "search": normalize("NFKC", search).lower(),
        "ready": str(ready).lower(),
        "failed": str(bool(post.get("last_error"))).lower(),
        "attempted": _attempt_date(post.get("last_attempted_at")),
        "tags": json.dumps([str(term["slug"]) for term in tags], ensure_ascii=False),
        "categories": json.dumps(_category_slugs(categories, category_index), ensure_ascii=False),
        "queue-position": "" if queue_position is None else str(queue_position),
        "queue-status": queue_status,
        "queue-reasons": queue_reasons,
    }
    attrs = " ".join(f'data-{key}="{_escape(value)}"' for key, value in attributes.items())
    chips = "".join(
        f'<a class="{kind}" href="{_escape(_route(kind, term["slug"]))}" '
        f'title="{kind.capitalize()}: {_escape(term["name"])}">{_escape(term["name"])}</a>'
        for kind, terms in (("category", categories), ("tag", tags))
        for term in terms
    )
    buttons = "".join(
        f'<a class="{kind}" href="{_escape(url)}">{label}{".gz" if url.endswith(".gz") else ""}</a>'
        for kind, label in (("pdf", "PDF"), ("typst", ".typ"), ("ir", "JSON"))
        if (url := downloads[kind])
    )
    converted = _conversion_date(post.get("converted_at")) if ready else ""
    availability = f'Converted <time datetime="{converted}">{converted}</time>' if converted else ""
    ready_class = " ready" if ready else ""
    queue_label = "Retry" if queue_status == "retry" else "Waiting"
    if queue_reasons:
        queue_label += " · " + queue_reasons
    return (
        f'<li class="post" {attrs}><div class="post-meta">'
        f'<time datetime="{_escape(published)}">{_escape(published)}</time>'
        f'<span class="availability{ready_class}">{availability}</span>'
        f'<span class="queue-status">{_escape(queue_label) if queue_status else ""}</span></div>'
        f'<h3><a href="{original}">{_escape(title)}</a></h3>'
        f'<div class="post-taxonomy">{chips}</div><div class="post-actions">{buttons}'
        f'<a class="original" href="{original}">Original</a></div>'
        f"{_failure_markup(archive, post, stem, diagnostics_base_url)}</li>",
        ready,
    )


_QUEUE_REASONS = {
    "initial": "First conversion",
    "one-week": "One-week check",
    "one-month": "One-month check",
    "birthday": "Birthday check",
    "recent-comment": "Recent comments",
    "source-modified": "Post updated",
    "converter-changed": "Converter updated",
    "missing-pdf": "Missing PDF",
    "missing-raw-source": "Missing source",
    "missing-typst": "Missing Typst",
    "missing-ir": "Missing IR",
    "forced": "Refresh",
}


def _queue_entries(
    archive: Path, posts: list[dict[str, Any]]
) -> tuple[dict[str, tuple[int, str, str]], str]:
    by_id = {str(post.get("id", "")): post for post in posts}
    queue_path = archive / "queue.json"
    if queue_path.is_file():
        queue = read_json(queue_path)
        entries = queue.get("posts", [])
        updated = str(queue.get("updated_at") or "")
    else:
        entries = [
            {"id": post.get("id"), "reasons": ["initial"]}
            for post in posts
            if _artifact_path(archive, post, "pdf") is None
        ]
        updated = ""
    result: dict[str, tuple[int, str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        post_id = str(entry.get("id", ""))
        if post_id not in by_id or post_id in result:
            continue
        reasons = entry.get("reasons", [])
        if not isinstance(reasons, list):
            reasons = []
        label = next(
            (
                _QUEUE_REASONS[reason]
                for reason in reasons
                if isinstance(reason, str) and reason in _QUEUE_REASONS
            ),
            "",
        )
        status = "retry" if by_id[post_id].get("last_error") else "waiting"
        result[post_id] = (len(result), status, label)
    return result, updated


def generate_site(
    archive: Path,
    output: Path,
    *,
    repo_url: str = _DEFAULT_REPO,
    branch: str = "main",
    download_base_url: str | None = None,
    copy_downloads: bool = True,
    diagnostics_base_url: str | None = None,
) -> dict[str, int]:
    """Build metadata; a download base URL also exports existing public downloads.

    This never fetches or compiles posts. Document/image paths retain the archive
    layout so downloaded Typst sources resolve their ``../assets/`` references.
    """
    repo_url = _public_url(repo_url.rstrip("/"))
    if repo_url == "#":
        raise ValueError("Repository URL must be an HTTP or HTTPS URL")
    if download_base_url is not None:
        download_base_url = _public_url(download_base_url.rstrip("/"))
        if download_base_url == "#":
            raise ValueError("Download base URL must be an HTTP or HTTPS URL")
    if diagnostics_base_url is not None:
        diagnostics_base_url = _public_url(diagnostics_base_url.rstrip("/"))
        if diagnostics_base_url == "#":
            raise ValueError("Diagnostics base URL must be an HTTP or HTTPS URL")
    raw = json.loads((archive / "state.json").read_text(encoding="utf-8"))
    posts = sorted(
        ({**post, "id": post.get("id", key)} for key, post in raw.get("posts", {}).items()),
        key=lambda post: str(post.get("published_at") or ""),
        reverse=True,
    )
    tags, tag_counts = _term_index(posts, "tags")
    categories, category_counts = _term_index(posts, "categories")
    queue, queue_updated = _queue_entries(archive, posts)
    stems = failure_stems(posts) if diagnostics_base_url is not None else {}
    rendered = [
        _post_markup(
            archive,
            post,
            repo_url,
            branch,
            categories,
            download_base_url,
            *queue.get(str(post["id"]), (None, "", "")),
            stem=stems.get(str(post["id"]), ""),
            diagnostics_base_url=diagnostics_base_url,
        )
        for post in posts
    ]
    converted = sum(ready for _, ready in rendered)
    counts = {
        "total": len(posts),
        "converted": converted,
        "queued": len(queue),
        "failed": sum(bool(post.get("last_error")) for post in posts),
        "tags": len(tags),
        "categories": len(categories),
    }
    if download_base_url is not None and copy_downloads:
        counts["exported_files"] = _export_downloads(archive, output, posts)
    replacements = {
        "REPO_URL": _escape(repo_url),
        "TOTAL": f"{counts['total']:,}",
        "CONVERTED": f"{counts['converted']:,}",
        "QUEUED": f"{counts['queued']:,}",
        "FAILED": f"{counts['failed']:,}",
        "QUEUE_UPDATED": _escape(queue_updated),
        "TAG_COUNT": str(counts["tags"]),
        "CATEGORY_COUNT": str(counts["categories"]),
        "TAG_LINKS": _taxonomy_links(tags, tag_counts, "tag"),
        "CATEGORY_LINKS": _taxonomy_links(categories, category_counts, "category"),
        "POSTS": "\n".join(markup for markup, _ in rendered),
    }
    page = (_ASSETS / "index.html").read_text(encoding="utf-8")
    for name, value in replacements.items():
        page = page.replace(f"@@{name}@@", value)
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(page, encoding="utf-8")
    for filename in ("style.css", "app.js"):
        (output / filename).write_text((_ASSETS / filename).read_text(encoding="utf-8"))
    (output / ".nojekyll").touch()
    return counts
