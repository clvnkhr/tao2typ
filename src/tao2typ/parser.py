"""Parse WordPress HTML into semantic IR without doing I/O or formatting."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.element import Comment as HtmlComment
from bs4.element import PageElement

from tao2typ.ir import Comment, Node, Post

_BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "blockquote",
    "ul",
    "ol",
    "li",
    "table",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "pre",
    "figure",
    "figcaption",
    "hr",
}
_SKIP_TAGS = {"style", "noscript", "iframe", "form", "button", "nav", "svg"}
_SKIP_CLASSES = {"sharedaddy", "jp-relatedposts", "cs-rating", "wpcnt", "adsbygoogle"}
_MATH_TEXT = re.compile(r"(\\\[(.+?)\\\]|\\\((.+?)\\\)|\$\$(.+?)\$\$)", re.DOTALL)
_NUMBER = re.compile(
    r"(?P<punctuation>[.,;:!?]?)\s*\((?P<number>\d+(?:\.\d+)*[a-z]?)\)"
    r"\s*(?P<number_suffix>[.,;:!?]?)\s*$",
    re.IGNORECASE,
)


def _attr(tag: Tag, name: str, default: str = "") -> str:
    value = tag.get(name)
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value) if value is not None else default


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _trim(nodes: list[Node]) -> list[Node]:
    while nodes and nodes[0].kind == "text" and not nodes[0].text.strip():
        nodes.pop(0)
    while nodes and nodes[-1].kind == "text" and not nodes[-1].text.strip():
        nodes.pop()
    if nodes and nodes[0].kind == "text":
        nodes[0].text = nodes[0].text.lstrip()
    if nodes and nodes[-1].kind == "text":
        nodes[-1].text = nodes[-1].text.rstrip()
    return nodes


def _date(text: str) -> str | None:
    text = text.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        pass
    for fmt in ("%B %d, %Y at %I:%M %p", "%d %B, %Y at %I:%M %p", "%B %d, %Y", "%d %B, %Y"):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    return None


def _metadata_date(soup: BeautifulSoup, name: str) -> str | None:
    meta = soup.find("meta", attrs={"property": f"article:{name}_time"})
    if isinstance(meta, Tag) and (value := _attr(meta, "content")):
        return _date(value) or value
    field = "datePublished" if name == "published" else "dateModified"
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.get_text())
        except (ValueError, TypeError):
            continue
        objects = data if isinstance(data, list) else [data]
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            candidates = obj.get("@graph", [obj])
            if not isinstance(candidates, list):
                continue
            for candidate in candidates:
                if isinstance(candidate, dict) and isinstance(candidate.get(field), str):
                    return _date(candidate[field]) or candidate[field]
    if name == "published":
        time = soup.select_one("time.entry-date, .post-metadata time, .postmetadata time")
        if isinstance(time, Tag):
            return _date(_attr(time, "datetime") or time.get_text(" ", strip=True))
    return None


def _comment_owner(element: Tag) -> Tag | None:
    """Find the closest actual comment, rather than a metadata/body wrapper."""
    return next(
        (
            parent
            for parent in element.parents
            if isinstance(parent, Tag)
            and (
                "comment" in _attr(parent, "class").split()
                or re.fullmatch(r"(?:div-)?comment-\d+", _attr(parent, "id"))
            )
        ),
        None,
    )


def _owned_one(owner: Tag, selector: str) -> Tag | None:
    # select_one can borrow a reply's author/time/avatar when the parent has
    # missing metadata. Never let a descendant comment fill its parent's data.
    return next((tag for tag in owner.select(selector) if _comment_owner(tag) is owner), None)


class _Parser:
    def __init__(self, url: str) -> None:
        self.url = url
        self.warnings: list[str] = []
        self._in_comment_body = False

    def _avatar(self, tag: Tag) -> bool:
        if tag.name != "img":
            return False
        classes = _attr(tag, "class").split()
        source = _attr(tag, "src") or _attr(tag, "data-src")
        host = (urlparse(urljoin(self.url, source)).hostname or "").lower()
        alternative = _attr(tag, "alt").lower().replace("’", "'").strip()
        return (
            any(item == "avatar" or item.startswith("avatar-") for item in classes)
            or host == "gravatar.com"
            or host.endswith(".gravatar.com")
            or alternative.endswith("'s avatar")
        )

    def _skip(self, tag: Tag) -> bool:
        if self._in_comment_body and self._avatar(tag):
            return True
        if tag.name in _SKIP_TAGS:
            return True
        if tag.name == "script" and not _attr(tag, "type").startswith("math/tex"):
            return True
        return bool(set(_attr(tag, "class").split()) & _SKIP_CLASSES) or _attr(tag, "id") in {
            "jp-post-flair",
            "jp-relatedposts",
        }

    def text(self, value: str) -> list[Node]:
        # Only unambiguous delimiters: a dollar sign in prose may mean currency.
        nodes: list[Node] = []
        position = 0
        for match in _MATH_TEXT.finditer(value):
            if match.start() > position:
                nodes.append(Node("text", _normalize(value[position : match.start()])))
            latex = match.group(2) or match.group(3) or match.group(4) or ""
            display = match.group(3) is None
            nodes.append(Node("math", latex, {"display": str(display).lower()}))
            position = match.end()
        if position < len(value):
            nodes.append(Node("text", _normalize(value[position:])))
        return nodes

    def math_image(self, tag: Tag) -> Node | None:
        src = _attr(tag, "src") or _attr(tag, "data-src")
        classes = set(_attr(tag, "class").split())
        query = parse_qs(urlparse(src).query)
        if "latex" not in classes and "latex" not in query and "latex.php" not in src:
            return None
        # alt is not URL-encoded. parse_qs decodes a query exactly once, including +.
        latex = _attr(tag, "alt") or (query.get("latex") or [""])[0]
        latex = re.sub(r"^\s*\$latex\s+", "", latex).strip()
        if not latex:
            self.warnings.append(f"Math image has no LaTeX alternative: {src}")
            return Node("image", attrs={"url": urljoin(self.url, src), "alt": "Equation"})
        return Node("math", latex, {"display": "false"})

    def inline(self, element: PageElement) -> list[Node]:
        if isinstance(element, HtmlComment):
            return []
        if isinstance(element, NavigableString):
            return self.text(str(element))
        if not isinstance(element, Tag) or self._skip(element):
            return []
        name = element.name
        if name == "img":
            math = self.math_image(element)
            if math is not None:
                return [math]
            src = _attr(element, "src") or _attr(element, "data-src")
            if not src:
                self.warnings.append("Image has no source URL")
                return [Node("text", _attr(element, "alt"))]
            return [
                Node(
                    "image",
                    attrs={
                        "url": urljoin(self.url, src),
                        "alt": _attr(element, "alt"),
                        "width": _attr(element, "width"),
                        "height": _attr(element, "height"),
                    },
                )
            ]
        if name == "br":
            return [Node("break")]
        if name == "script" and _attr(element, "type").startswith("math/tex"):
            return [
                Node(
                    "math",
                    element.get_text(),
                    {"display": str("mode=display" in _attr(element, "type")).lower()},
                )
            ]
        if name == "math":
            annotation = element.find("annotation", attrs={"encoding": "application/x-tex"})
            if isinstance(annotation, Tag):
                return [
                    Node(
                        "math",
                        annotation.get_text(),
                        {"display": str(_attr(element, "display") == "block").lower()},
                    )
                ]
            self.warnings.append("MathML without a TeX annotation was preserved as text")
        classes = set(_attr(element, "class").split())
        if name in {"span", "div"} and classes & {"math", "math-inline", "math-display"}:
            latex = element.get_text().strip()
            latex = re.sub(r"^\\[\[(]|\\[\])]$", "", latex).strip()
            return [Node("math", latex, {"display": str("math-display" in classes).lower()})]
        children = [node for child in element.children for node in self.inline(child)]
        anchor = _attr(element, "id") or (_attr(element, "name") if name == "a" else "")
        prefix = [Node("anchor", attrs={"id": anchor})] if anchor else []
        if name == "a" and _attr(element, "href"):
            href = _attr(element, "href")
            url = urljoin(self.url, href)
            if urlparse(url).scheme not in {"http", "https", "mailto"}:
                return prefix + children
            if not children:
                if self._in_comment_body and any(
                    self._avatar(image) for image in element.find_all("img")
                ):
                    return prefix
                children = [Node("text", href)]
            return prefix + [Node("link", attrs={"url": url}, children=children)]
        styles = {
            "b": "strong",
            "strong": "strong",
            "em": "emphasis",
            "i": "emphasis",
            "strike": "strike",
            "s": "strike",
            "del": "strike",
            "sub": "sub",
            "sup": "sup",
        }
        if name in styles:
            return prefix + [Node(styles[name], children=children)]
        if name == "code":
            return prefix + [Node("code", element.get_text())]
        return prefix + children

    def blocks(self, elements: Iterable[PageElement]) -> list[Node]:
        result: list[Node] = []
        pending: list[Node] = []

        def flush() -> None:
            nonlocal pending
            if _trim(pending):
                result.append(Node("paragraph", children=pending))
            pending = []

        for element in elements:
            if isinstance(element, Tag) and self._skip(element):
                continue
            is_block = isinstance(element, Tag) and (
                element.name in _BLOCK_TAGS
                or (
                    element.name == "a"
                    and any(
                        isinstance(child, Tag) and child.name in _BLOCK_TAGS
                        for child in element.children
                    )
                )
            )
            if is_block and isinstance(element, Tag):
                flush()
                result.extend(self.block(element))
            else:
                pending.extend(self.inline(element))
        flush()
        return result

    def block(self, tag: Tag) -> list[Node]:
        name = tag.name
        anchor = _attr(tag, "id") or (_attr(tag, "name") if name == "a" else "")
        prefix = [Node("anchor", attrs={"id": anchor})] if anchor else []
        if name in {"div", "section", "article", "a"}:
            if set(_attr(tag, "class").split()) & {"math", "math-inline", "math-display"}:
                return self.inline(tag)
            return prefix + self.blocks(tag.children)
        if name == "blockquote":
            return prefix + [Node("quote", children=self.blocks(tag.children))]
        if name in {"ul", "ol"}:
            items = [
                Node("list_item", children=self.blocks(item.children))
                for item in tag.find_all("li", recursive=False)
            ]
            return prefix + [
                Node(
                    "list",
                    attrs={"ordered": str(name == "ol").lower(), "start": _attr(tag, "start", "1")},
                    children=items,
                )
            ]
        if name == "li":
            return prefix + self.blocks(tag.children)
        if name == "pre":
            code = tag.find("code")
            lang = ""
            if isinstance(code, Tag):
                lang = next(
                    (
                        item.removeprefix("language-")
                        for item in _attr(code, "class").split()
                        if item.startswith("language-")
                    ),
                    "",
                )
            return prefix + [Node("codeblock", tag.get_text(), {"language": lang})]
        if name == "hr":
            return prefix + [Node("divider")]
        if name == "figure":
            return prefix + [Node("figure", children=self.blocks(tag.children))]
        if name == "figcaption":
            return prefix + [
                Node(
                    "caption",
                    children=_trim([node for child in tag.children for node in self.inline(child)]),
                )
            ]
        if name == "table":
            rows: list[Node] = []
            # Some legacy pages use <th><td>…</td></th> as a table row.
            for row in tag.find_all(["tr", "th"]):
                if row.find_parent("table") is not tag:
                    continue
                if row.name == "th" and not row.find("td", recursive=False):
                    continue
                cells = [
                    Node(
                        "table_cell",
                        attrs={
                            "header": str(cell.name == "th").lower(),
                            "colspan": _attr(cell, "colspan", "1"),
                            "rowspan": _attr(cell, "rowspan", "1"),
                        },
                        children=self.blocks(cell.children),
                    )
                    for cell in row.find_all(["th", "td"], recursive=False)
                ]
                if cells:
                    rows.append(Node("table_row", children=cells))
            caption = tag.find("caption", recursive=False)
            if isinstance(caption, Tag):
                prefix.append(
                    Node(
                        "caption",
                        children=_trim(
                            [node for child in caption.children for node in self.inline(child)]
                        ),
                    )
                )
            return prefix + [Node("table", children=rows)]
        children = _trim([node for child in tag.children for node in self.inline(child)])
        if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            return prefix + [
                Node("heading", attrs={"level": str(max(1, int(name[1]) - 1))}, children=children)
            ]
        centered = _attr(tag, "align").lower() == "center" or bool(
            re.search(r"text-align\s*:\s*center", _attr(tag, "style"))
        )
        if centered and name == "p":
            significant = [
                node
                for node in children
                if node.kind != "anchor" and not (node.kind == "text" and not node.text.strip())
            ]
            # LaTeX2WP encodes section titles as centered bold paragraphs.
            if significant and all(node.kind == "strong" for node in significant):
                return prefix + [Node("heading", attrs={"level": "1"}, children=children)]
            # Older posts also use dash-framed section titles with inline
            # maths, e.g. "— Linear functionals on L^p —". Centering alone
            # must not turn their inline formula into a separate display.
            title_text = tag.get_text(" ", strip=True)
            if len(title_text) > 2 and re.fullmatch(r"[—–]\s*.+?\s*[—–]", title_text):
                return prefix + [Node("heading", attrs={"level": "1"}, children=children)]
            number = (
                _NUMBER.fullmatch(significant[1].text.strip())
                if len(significant) == 2
                and significant[0].kind == "math"
                and significant[1].kind == "text"
                else None
            )
            if significant and (
                all(node.kind == "math" for node in significant) or number is not None
            ):
                for node in children:
                    if node.kind == "math":
                        node.attrs["display"] = "true"
                if number is not None:
                    significant[0].attrs["number"] = number.group("number")
                    for attribute in ("punctuation", "number_suffix"):
                        if value := number.group(attribute):
                            significant[0].attrs[attribute] = value
                    children.remove(significant[1])
            return prefix + (
                [Node("paragraph", attrs={"align": "center"}, children=children)]
                if children
                else []
            )
        return prefix + ([Node("paragraph", children=children)] if children else [])

    def comments(self, soup: BeautifulSoup) -> list[tuple[Comment, str | None]]:
        records: list[tuple[Tag, Comment]] = []
        owners: dict[int, Comment] = {}
        for content in soup.select("#comments .comment-content, .commentlist .comment-content"):
            owner = _comment_owner(content)
            if owner is None or id(owner) in owners:
                continue
            author = _owned_one(owner, ".comment-author .fn, .fn") or _owned_one(
                owner, ".comment-author"
            )
            # Prefer a comment permalink over unrelated links in metadata.
            permalink = _owned_one(
                owner,
                ".comment-permalink a, a.comment-permalink",
            ) or _owned_one(
                owner, '.comment-meta a[href*="#comment-"], .comment-metadata a[href*="#comment-"]'
            )
            time = _owned_one(owner, "time")
            timestamp = (
                _date(_attr(time, "datetime") or time.get_text(" ", strip=True))
                if isinstance(time, Tag)
                else None
            )
            if timestamp is None and isinstance(permalink, Tag):
                timestamp = _date(permalink.get_text(" ", strip=True))
            self._in_comment_body = True
            try:
                body = self.blocks(content.children)
            finally:
                self._in_comment_body = False
            identifier = (
                _attr(owner, "id").removeprefix("div-")
                or hashlib.sha256(
                    (str(author) + str(timestamp) + str(content)).encode()
                ).hexdigest()[:16]
            )
            comment = Comment(
                identifier,
                author.get_text(" ", strip=True) if isinstance(author, Tag) else "Anonymous",
                timestamp,
                urljoin(self.url, _attr(permalink, "href"))
                if isinstance(permalink, Tag)
                else self.url + "#" + identifier,
                body,
            )
            avatar = next(
                (
                    image
                    for image in owner.find_all("img")
                    if _comment_owner(image) is owner and self._avatar(image)
                ),
                None,
            )
            if isinstance(avatar, Tag):
                source = _attr(avatar, "src") or _attr(avatar, "data-src")
                if source:
                    comment.avatar = Node(
                        "image",
                        attrs={
                            "url": urljoin(self.url, source),
                            "alt": _attr(avatar, "alt") or f"{comment.author}'s avatar",
                            "width": _attr(avatar, "width"),
                            "height": _attr(avatar, "height"),
                        },
                    )
            owners[id(owner)] = comment
            records.append((owner, comment))

        result: list[tuple[Comment, str | None]] = []
        depths: dict[int, Comment] = {}
        for owner, comment in records:
            parent_comment = None
            for ancestor in owner.parents:
                if not isinstance(ancestor, Tag):
                    continue
                if id(ancestor) in owners:
                    parent_comment = owners[id(ancestor)]
                    break
                if ancestor.name in {"ul", "ol"} and "children" in _attr(ancestor, "class").split():
                    # Tao's live theme closes div.comment before its reply
                    # list. The list is a sibling, unlike WordPress's usual
                    # nested li.comment fixture. Its preceding comment owns it.
                    previous = ancestor.find_previous_sibling()
                    if isinstance(previous, Tag):
                        candidates = [previous, *previous.find_all(True)]
                        parent_comment = next(
                            (owners[id(tag)] for tag in candidates if id(tag) in owners), None
                        )
                    if parent_comment is not None:
                        break
            depth_match = re.search(r"\bdepth-(\d+)\b", _attr(owner, "class"))
            if depth_match:
                depth = int(depth_match.group(1))
                if parent_comment is None and depth > 1:
                    parent_comment = depths.get(depth - 1)
                depths = {level: value for level, value in depths.items() if level < depth}
                depths[depth] = comment
            result.append((comment, parent_comment.id if parent_comment is not None else None))
        return result


def parse_post(
    html: str,
    url: str,
    comment_pages: Sequence[str] = (),
    published_at: str | None = None,
    modified_at: str | None = None,
) -> Post:
    """Parse one post and optional additional WordPress comment-page HTML.

    Canonical API timestamps may override HTML metadata. No network, filesystem,
    or output-language logic is involved in this stage.
    """
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one(".post-content, .entry-content")
    if not isinstance(content, Tag):
        raise ValueError(f"No WordPress post content found at {url}")
    title = soup.select_one(".post-title, .entry-title, article h1, h1")
    if not isinstance(title, Tag):
        title = soup.find("title")
    title_text = title.get_text(" ", strip=True) if isinstance(title, Tag) else "Untitled post"
    parser = _Parser(url)
    body = parser.blocks(content.children)
    publication = published_at or _metadata_date(soup, "published")
    if publication is None and (match := re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url)):
        publication = "-".join(match.groups())
    blog_title = soup.select_one("#blog-title, .site-title")
    post = Post(
        title_text,
        url,
        publication,
        modified_at or _metadata_date(soup, "modified"),
        body,
        author="Terence Tao",
        blog_title=blog_title.get_text(" ", strip=True)
        if isinstance(blog_title, Tag)
        else "What’s new",
    )
    seen: dict[str, Comment] = {}
    parents: dict[str, str] = {}
    for page in [soup, *(BeautifulSoup(page, "html.parser") for page in comment_pages)]:
        for comment, parent in parser.comments(page):
            if comment.id not in seen:
                seen[comment.id] = comment
            elif seen[comment.id].avatar is None:
                seen[comment.id].avatar = comment.avatar
            if parent is not None:
                parents.setdefault(comment.id, parent)
    # Rebuild once after all pages: an orphan seen on one comment page may
    # acquire its parent from a later page's repeated wrapper.
    for identifier, comment in seen.items():
        parent = parents.get(identifier)
        ancestors = {identifier}
        while parent is not None and parent not in ancestors:
            ancestors.add(parent)
            parent = parents.get(parent)
        if parent in ancestors:
            parser.warnings.append(f"Cyclic comment ancestry for {identifier}; kept at top level")
            parents.pop(identifier, None)
        target = seen.get(parents.get(identifier, ""))
        (target.replies if target is not None else post.comments).append(comment)
    post.warnings = parser.warnings
    return post
