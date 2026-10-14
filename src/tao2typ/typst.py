"""Render the intermediate representation as safe, self-contained Typst source."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import unquote

from tao2typ.alignment import alignment_pivot, with_alignment
from tao2typ.ir import Comment, Node, Post
from tao2typ.tex import DELIMITER_HELPERS, normalize_delimiters, normalize_scripts
from tao2typ.tex_compat import normalize_tex_compat
from tao2typ.tex_tables import render_tex_table

MITEX_VERSION = "0.2.7"
EQUATE_VERSION = "0.3.3"
_ALIGNMENT_HELPERS = f"""
#import "@preview/equate:{EQUATE_VERSION}": equate, share-align
#let tao-aligned(body, keep: true) = {{
  show: equate.with(sub-numbering: false)
  block(breakable: not keep, above: 1em, below: 1em, share-align(body))
}}
"""
_DISPLAY_FIT_HELPERS = """
// Fit an unnumbered single display to its actual local text measure.
#let tao-fit-display(body) = context layout(size => {
  let natural = [#show math.equation: set block(width: auto)
  #body]
  let width = measure(natural).width
  if width > size.width {
    align(center, scale((size.width / width) * 100%, reflow: true, natural))
  } else { body }
})
"""
_EX_RAISEBOX = re.compile(r"(?<!\\)\\raisebox\s*\{\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*ex\s*\}")
_MATH_HELPERS = (
    DELIMITER_HELPERS
    + r"""
// TeX ex dimensions use the current font's x-height, not a guessed em ratio.
#import "@preview/mitex:0.2.7": mitex-convert, mitex-scope
#let tao-raisebox(space, body) = context {
  let dimension = if space.has("children") {
    space.children.filter(it => it.has("text")).map(it => it.text).join("").replace(" ", "")
  } else { space.text }
  if dimension.ends-with("ex") {
    let magnitude = float(dimension.slice(0, -2))
    let x-height = measure(text(top-edge: "x-height", bottom-edge: "baseline", "x")).height
    text(baseline: -magnitude * x-height, body)
  } else { mitex-scope.at("raisebox")(space, body) }
}
#let tao-math(source, block: false, ..args) = {
  let converted = mitex-convert(source)
  let body = eval("$" + converted + "$", scope: mitex-scope + (
    raisebox: tao-raisebox, lr: tao-lr,
    big: body => tao-big(body, 1.2em), Big: body => tao-big(body, 1.8em),
    bigg: body => tao-big(body, 2.4em), Bigg: body => tao-big(body, 3em),
  ))
  math.equation(block: block, body, ..args)
}
"""
)


def _string(value: str) -> str:
    """Encode a Typst string literal, never interpreting source text as code."""
    escaped: list[str] = []
    replacements = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
    for character in value:
        if character in replacements:
            escaped.append(replacements[character])
        elif ord(character) < 32:
            escaped.append(f"\\u{{{ord(character):x}}}")
        else:
            escaped.append(character)
    return '"' + "".join(escaped) + '"'


def _label(identifier: str) -> str:
    return "tao-" + hashlib.sha256(identifier.encode()).hexdigest()[:20]


def _positive(value: str, default: int = 1) -> int:
    try:
        return max(1, int(value))
    except ValueError:
        return default


def _friendly_date(value: str | None) -> str:
    if not value:
        return "Date unavailable"
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return f"{date.day} {date:%B %Y}"
    except ValueError:
        return value


def _conversion_date(value: str | None) -> str:
    if value:
        try:
            return (
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                .astimezone(UTC)
                .date()
                .isoformat()
            )
        except ValueError:
            pass
    return "date unavailable"


def _text_superscripts(source: str) -> str:
    """Expand braced text superscripts as one empty-base text-script atom."""
    result: list[str] = []
    position = 0
    command = re.compile(r"(?<!\\)\\textsuperscript(?![A-Za-z])\s*(?=\{)")
    while match := command.search(source, position):
        opening = match.end()
        cursor, depth = opening + 1, 1
        while cursor < len(source) and depth:
            character = source[cursor]
            if character == "\\":
                cursor += 2
                continue
            if character == "%":
                newline = source.find("\n", cursor)
                cursor = len(source) if newline < 0 else newline + 1
                continue
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
            cursor += 1
        if depth:
            break  # Leave a malformed source argument for the normal diagnostics.
        result.append(source[position : match.start()])
        # LaTeX encloses the empty-base superscript in a group. Retain that group
        # so i^\textsuperscript{th} has a nested script, not two scripts on i.
        result.append(r"{{}^{\text" + source[opening:cursor] + "}}")
        position = cursor
    result.append(source[position:])
    return "".join(result)


def _legacy_tex(source: str) -> str:
    """Normalize equivalent legacy TeX forms unsupported by pinned MiTeX.

    Bracing sqrt's next ordinary token preserves TeX's exact argument semantics:
    ``\\sqrt(n)`` becomes ``\\sqrt{(}n)``, not a newly invented radicand ``n``.
    Original source remains untouched in the IR.
    """
    source = normalize_tex_compat(source)
    # A font declaration affects only the remainder of its group. In an empty
    # group it has no glyphs to style, while MiTeX's greedy font handler errors.
    # Keep the empty group: it may be a fraction, script or accent argument.
    source = re.sub(r"(?<!\\)\{\s*\\(?:bf|rm|it|sf|frak|tt|cal)\s*\}", "{}", source)
    source = _text_superscripts(source)
    # AMS's variant limits are complete operators with an arrow underneath.
    # Use pinned MiTeX's operator-with-limits and underset, keeping later scripts
    # attached to the operator rather than to the arrow or the following term.
    source = re.sub(
        r"(?<!\\)\\var(inj|proj)lim(?![A-Za-z])",
        lambda match: (
            r"\operatorname*{\underset{\textstyle"
            + (r"\rightarrow" if match[1] == "inj" else r"\leftarrow")
            + "}{lim}}"
        ),
        source,
    )
    # An explicitly empty optional root index prints no index, like sqrt's
    # default. Retain a separator so an unbraced following token stays an arg.
    source = re.sub(r"(?<!\\)\\sqrt\s*\[\s*\]", r"\\sqrt ", source)
    source = re.sub(r"(?<!\\)\\mbox(?![A-Za-z])", r"\\text", source)
    # MiTeX drops hbox's text styling and spaces. Its simple braced form is
    # horizontal text like mbox; primitive forms such as hbox to ... stay intact.
    source = re.sub(r"(?<!\\)\\hbox(?=\s*\{)", r"\\text", source)
    source = re.sub(r"(?<!\\)\\hdots(?![A-Za-z])", r"\\ldots", source)
    source = re.sub(r"(?<!\\)\\textbullet(?![A-Za-z])", r"\\bullet", source)
    # TeX's \null is an empty box, not a mathematical symbol.
    source = re.sub(r"(?<!\\)\\null(?![A-Za-z])", "{}", source)

    # Unicode encodes exactly the single characters used by legacy posts.
    def circled(match: re.Match[str]) -> str:
        character = match.group(1)
        if "a" <= character <= "z":
            symbol = chr(0x24D0 + ord(character) - ord("a"))
        elif "A" <= character <= "Z":
            symbol = chr(0x24B6 + ord(character) - ord("A"))
        elif character == "0":
            symbol = "⓪"
        else:
            symbol = chr(0x2460 + int(character) - 1)
        return r"\text{" + symbol + "}"

    source = re.sub(r"(?<!\\)\\textcircled\s*\{([A-Za-z0-9])\}", circled, source)
    # eqnarray has the same source alignment points; no counter is introduced.
    source = re.sub(r"\\(begin|end)\{eqnarray\*?\}", r"\\\1{aligned}", source)
    source = normalize_scripts(source)
    return re.sub(
        r"(?<!\\)(\\sqrt\s*(?:\[[^\]\n]*\]\s*)?)([^\s\\{}\[])",
        r"\1{\2}",
        source,
    )


def _tags(source: str) -> list[tuple[int, int, str, bool]]:
    """Read explicit TeX tags without truncating nested groups in a label."""
    result: list[tuple[int, int, str, bool]] = []
    for match in re.finditer(r"(?<!\\)\\tag(\*)?\s*\{", source):
        depth = 1
        position = match.end()
        while position < len(source) and depth:
            character = source[position]
            if character == "\\":
                position += 2
                continue
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
            position += 1
        if depth:
            raise ValueError("Unclosed explicit equation tag in source TeX")
        result.append((match.start(), position, source[match.end() : position - 1], bool(match[1])))
    return result


def _without_tags(source: str, tags: list[tuple[int, int, str, bool]]) -> str:
    for start, end, _, _ in reversed(tags):
        source = source[:start] + source[end:]
    return source.strip()


def _equation_rows(source: str) -> list[str] | None:
    environment = re.fullmatch(
        r"\\begin\{(align\*?|aligned|eqnarray\*?|gather\*?)\}(.*)\\end\{\1\}",
        source.strip(),
        re.DOTALL,
    )
    if environment is None:
        return None
    body = environment[2]
    rows: list[str] = []
    braces = nested = start = position = 0
    while position < len(body):
        character = body[position]
        if character == "\\":
            nested_environment = re.match(r"\\(begin|end)\{[^}]+\}", body[position:])
            if nested_environment:
                nested += 1 if nested_environment[1] == "begin" else -1
                position += nested_environment.end()
                continue
            if body.startswith(r"\\", position) and braces == nested == 0:
                rows.append(body[start:position].strip())
                position += 2
                row_spacing = re.match(r"\s*\[[+-]?[\d.]+(?:pt|em|ex|mm|cm)\]", body[position:])
                if row_spacing:
                    position += row_spacing.end()
                start = position
                continue
            position += 2
            continue
        if character == "{":
            braces += 1
        elif character == "}":
            braces -= 1
        position += 1
    rows.append(body[start:].strip())
    return [row for row in rows if row]


class _Renderer:
    def __init__(self, post: Post, assets: Mapping[str, str]) -> None:
        self.post = post
        self.assets = assets
        self.anchors = {
            node.attrs["id"] for node in post.walk() if node.kind == "anchor" and "id" in node.attrs
        }
        self.emitted_anchors: set[str] = set()
        self.uses_math_helpers = False
        self.uses_aligned_math = False
        self.uses_display_fitting = False

    def nodes(self, nodes: list[Node]) -> str:
        return "".join(self.node(node) for node in nodes)

    def blocks(self, nodes: list[Node]) -> str:
        rendered: list[str] = []
        position = 0
        while position < len(nodes):
            if self.display_row(nodes[position]) is None:
                rendered.append(self.node(nodes[position]))
                position += 1
                continue
            rows: list[tuple[Node, list[Node]]] = [(nodes[position], [])]
            end = position + 1
            pending: list[Node] = []
            while end < len(nodes):
                node = nodes[end]
                if node.kind == "anchor" or node.kind == "text" and not node.text.strip():
                    pending.append(node)
                elif self.display_row(node) is not None:
                    rows.append((node, pending))
                    pending = []
                else:
                    break
                end += 1
            # Anchors belonging to following prose must not move into a math group.
            end -= len(pending)
            rendered.extend(self.display_run(rows))
            position = end
        return "\n\n".join(rendered)

    @staticmethod
    def display_row(node: Node) -> Node | None:
        if node.kind == "math" and node.attrs.get("display") == "true":
            return None if node.attrs.get("source_error") else node
        if node.kind != "paragraph":
            return None
        significant = [
            child
            for child in node.children
            if child.kind != "anchor" and not (child.kind == "text" and not child.text.strip())
        ]
        if (
            len(significant) == 1
            and significant[0].kind == "math"
            and significant[0].attrs.get("display") == "true"
            and not significant[0].attrs.get("source_error")
        ):
            return significant[0]
        return None

    def display_run(self, rows: list[tuple[Node, list[Node]]]) -> list[str]:
        pivots = []
        for node, _ in rows:
            math = self.display_row(node)
            assert math is not None
            pivots.append(alignment_pivot(normalize_delimiters(_legacy_tex(math.text))))
        result: list[str] = []
        start = 0
        while start < len(rows):
            pivot = pivots[start]
            if pivot is None or pivot.kind == "continuation":
                node, prefix = rows[start]
                result.append(self.nodes(prefix) + self.node(node))
                start += 1
                continue
            mode = "binary" if pivot.kind == "binary" else "relation"
            allowed = {mode, "explicit", "continuation"}
            end = start + 1
            while end < len(rows):
                following = pivots[end]
                if following is None or following.kind not in allowed:
                    break
                end += 1
            group = rows[start:end]
            continuations = any(
                p is not None and p.kind == "continuation" for p in pivots[start:end]
            )
            if len(group) < 2 or mode == "binary" and not continuations:
                for node, prefix in group:
                    result.append(self.nodes(prefix) + self.node(node))
            else:
                contents: list[str] = []
                for node, prefix in group:
                    contents.append(self.nodes(prefix))
                    if node.kind == "paragraph":
                        contents.append(
                            "".join(
                                self.math(child, aligned=True)
                                if child.kind == "math"
                                else self.node(child)
                                for child in node.children
                            )
                        )
                    else:
                        contents.append(self.math(node, aligned=True))
                result.append(self.aligned_block(contents))
            start = end
        return result

    def aligned_block(self, contents: list[str]) -> str:
        self.uses_aligned_math = True
        # Keep short calculations together, while long source runs can paginate.
        keep = sum("#tao-math(" in item for item in contents) <= 6
        return f"#tao-aligned(keep: {str(keep).lower()})[\n" + "\n\n".join(contents) + "\n]"

    def node(self, node: Node) -> str:
        kind = node.kind
        if kind == "text":
            return f"#text({_string(node.text)})"
        if kind == "anchor":
            identifier = node.attrs.get("id", "")
            if identifier in self.emitted_anchors:
                return ""
            self.emitted_anchors.add(identifier)
            return f"#metadata(none) <{_label(identifier)}>"
        if kind == "break":
            return "#linebreak()"
        if kind == "math":
            return self.math(node)
        if kind == "image":
            return self.image(node)
        if kind == "link":
            url = node.attrs.get("url", self.post.url)
            base, separator, fragment = url.partition("#")
            fragment = unquote(fragment)
            if (
                separator
                and base.rstrip("/") == self.post.url.split("#")[0].rstrip("/")
                and fragment in self.anchors
            ):
                destination = f"<{_label(fragment)}>"
            else:
                destination = _string(url)
            return f"#link({destination})[{self.nodes(node.children)}]"
        inline = {
            "strong": "strong",
            "emphasis": "emph",
            "strike": "strike",
            "sub": "sub",
            "sup": "super",
        }
        if kind in inline:
            return f"#{inline[kind]}[{self.nodes(node.children)}]"
        if kind == "code":
            return f"#raw({_string(node.text)})"
        if kind == "paragraph":
            content = self.nodes(node.children)
            if node.attrs.get("align") == "center":
                return f"#align(center)[{content}]"
            return content
        if kind == "heading":
            level = min(6, _positive(node.attrs.get("level", "1")))
            return f"#heading(level: {level})[{self.nodes(node.children)}]"
        if kind == "quote":
            return (
                "#block(width: 100%, breakable: true, "
                "inset: (left: 12pt, right: 10pt, top: 5pt, bottom: 5pt), "
                'stroke: (left: 1.5pt + rgb("98a4b6")))[\n'
                f"{self.blocks(node.children)}\n]"
            )
        if kind == "list":
            name = "enum" if node.attrs.get("ordered") == "true" else "list"
            start = f"start: {_positive(node.attrs.get('start', '1'))}, " if name == "enum" else ""
            items = ",\n".join("[" + self.blocks(item.children) + "]" for item in node.children)
            return f"#{name}({start}\n{items}\n)"
        if kind == "list_item":
            return self.blocks(node.children)
        if kind == "codeblock":
            language = node.attrs.get("language", "")
            argument = f", lang: {_string(language)}" if language else ""
            return f"#raw({_string(node.text)}, block: true{argument})"
        if kind == "divider":
            return '#line(length: 100%, stroke: 0.5pt + rgb("d9dfe8"))'
        if kind == "figure":
            return f"#align(center)[\n{self.blocks(node.children)}\n]"
        if kind == "caption":
            return (
                '#align(center)[#text(size: 9pt, fill: rgb("596477"))['
                f"{self.nodes(node.children)}]]"
            )
        if kind == "table":
            return self.table(node)
        if kind in {"table_cell", "table_row"}:
            return self.blocks(node.children)
        raise ValueError(f"Cannot render unknown IR node kind: {kind}")

    def math(self, node: Node, *, aligned: bool = False) -> str:
        if node.attrs.get("source_error"):
            return (
                "#block(width: 100%, above: 12pt, below: 14pt)["
                "#set par(justify: false)\n"
                '#text(size: 7pt, fill: rgb("596477"))['
                f"#link({_string(node.attrs['source_error_url'])})"
                "[Source formula could not be rendered.] ] "
                f'#text(font: "DejaVu Sans Mono", size: 7.5pt, {_string(node.text)})]'
            )
        display = node.attrs.get("display") == "true"
        source = _legacy_tex(node.text.strip())
        table = render_tex_table(source, lambda cell: self.math(Node("math", cell)))
        if table is not None:
            if node.attrs.get("number") or _tags(source):
                raise ValueError("Numbered TeX tabular diagrams require explicit layout support")
            return f"#align(center)[{table}]" if display else table
        number = node.attrs.get("number")
        tags = _tags(source)
        # MiTeX ignores \tag. Keep each explicit source label, including tagged
        # rows inside align, while never assigning numbers to untagged rows.
        if len(tags) > 1:
            rows = _equation_rows(source)
            if rows is None or len(rows) < 2:
                raise ValueError("Multiple equation tags without separable source rows")
            return self.aligned_block(
                [self.math(Node("math", row, {"display": "true"}), aligned=True) for row in rows]
            )
        starred = False
        if tags:
            _, _, tag_number, starred = tags[0]
            number = number or tag_number
            source = _without_tags(source, tags)
            display = True
        if display:
            # WordPress bakes equation numbers into image TeX. Preserve those
            # numbers explicitly, rather than renumbering the article.
            match = re.search(
                r"(?:\\[ ,;!]|\\quad|\\qquad)+\s*\((\d+(?:\.\d+)*[a-z]?)\)\s*$",
                source,
                re.IGNORECASE,
            )
            if match:
                number = number or match.group(1)
                source = source[: match.start()].rstrip()
            elif matrix_number := re.search(
                r"\\end\{(?:[pbBvV]?matrix|smallmatrix)\}(?P<separator>\s+)"
                r"\((?P<number>\d+(?:\.\d+)*[a-z]?)\)\s*$",
                source,
                re.IGNORECASE,
            ):
                # The legacy LaTeX2WP matrix display has no TeX spacer before
                # its baked-in label. Restrict this fallback to a closed matrix
                # environment: ordinary x + (10), O(1), and tuples stay intact.
                number = number or matrix_number["number"]
                source = source[: matrix_number.start("separator")].rstrip()
            source = re.sub(r"^\\displaystyle\s*", "", source)
            source += node.attrs.get("punctuation", "")
        source = normalize_delimiters(source)
        if aligned:
            source = with_alignment(source) or source
        if aligned or _EX_RAISEBOX.search(source) or re.search(r"\\left\s*(?:\||\\\|)", source):
            self.uses_math_helpers = True
            equation = f"#tao-math({_string(source)}, block: {str(display).lower()})"
        else:
            command = "mitex" if display else "mi"
            equation = f"#{command}({_string(source)})"
        if number and display:
            suffix = node.attrs.get("number_suffix", "")
            label = (number if starred else "(" + number + ")") + suffix
            if "\\" in number or "{" in number:
                content = f"#mi({_string(number)})"
                if not starred:
                    content = '#text("(")' + content + '#text(")")'
                if suffix:
                    content += f"#text({_string(suffix)})"
            else:
                content = f"#text({_string(label)})"
            if aligned:
                return f"#tao-math({_string(source)}, block: true, numbering: n => [{content}])"
            # Native equation numbering centers the equation across the whole
            # text measure and places the *source* label at the right margin.
            return (
                f"#block(width: 100%)[#set math.equation(numbering: n => [{content}])\n{equation}]"
            )
        if display and not aligned:
            self.uses_display_fitting = True
            return f"#tao-fit-display[{equation}]"
        return equation

    def image(self, node: Node) -> str:
        url = node.attrs.get("url", "")
        alt = node.attrs.get("alt", "")
        if url in self.assets:
            # Files are relative to the generated .typ file. Typst owns path
            # resolution, so never interpolate URL paths into executable code.
            path = _string(self.assets[url])
            alternative = f", alt: {_string(alt)}" if alt else ""
            return f"#image({path}, width: 90%{alternative})"
        description = "Image unavailable"
        caption = f"#text({_string(alt)}) · " if alt else ""
        return (
            '#box(width: 90%, inset: 6pt, stroke: 0.5pt + rgb("d9dfe8"))['
            f"#text(size: 9pt)[#text({_string(description)}) · {caption}"
            f"#link({_string(url)})[Original image]]]"
        )

    def table(self, node: Node) -> str:
        if not node.children:
            return ""
        columns = max(
            sum(_positive(cell.attrs.get("colspan", "1")) for cell in row.children)
            for row in node.children
        )
        cells: list[str] = []
        for row in node.children:
            row_cells: list[str] = []
            for cell in row.children:
                content = self.blocks(cell.children)
                if cell.attrs.get("header") == "true":
                    content = "#strong[" + content + "]"
                colspan = _positive(cell.attrs.get("colspan", "1"))
                rowspan = _positive(cell.attrs.get("rowspan", "1"))
                row_cells.append(f"table.cell(colspan: {colspan}, rowspan: {rowspan})[{content}]")
            cells.extend(row_cells)
        return (
            f'#table(columns: {columns}, inset: 7pt, stroke: 0.5pt + rgb("d9dfe8"),\n'
            + ",\n".join(cells)
            + "\n)"
        )

    def comment(self, comment: Comment, depth: int = 0) -> str:
        date = f" · {_friendly_date(comment.published_at)}" if comment.published_at else ""
        header = (
            f"#link({_string(comment.url)})[#strong[#text({_string(comment.author)})]]"
            f'#text(size: 9pt, fill: rgb("596477"), {_string(date)})'
        )
        if comment.avatar is not None:
            avatar = comment.avatar
            url = avatar.attrs.get("url", "")
            if url in self.assets:
                portrait = (
                    "#box(width: 22pt, height: 22pt, radius: 50%, clip: true)["
                    f"#image({_string(self.assets[url])}, width: 22pt, height: 22pt, "
                    f'fit: "cover", alt: {_string("Avatar of " + comment.author)})]'
                )
                header = (
                    "#grid(columns: (22pt, 1fr), column-gutter: 7pt, "
                    f"align: (left + horizon), [{portrait}], [{header}])"
                )
        body = self.blocks(comment.body)
        current = (
            "#block(width: 100%, breakable: true, above: 10pt, below: 6pt)[\n"
            '#set par(justify: false, first-line-indent: 0pt, linebreaks: "optimized")\n'
            f"#block(sticky: true, above: 0pt, below: 4pt)[{header}]\n\n{body}\n]"
        )
        replies = "\n\n".join(self.comment(reply, depth + 1) for reply in comment.replies)
        if replies:
            # Keep every reply level visible, with a smaller step in deep threads.
            inset = "18pt" if depth < 3 else "9pt"
            current += f"\n\n#pad(left: {inset})[\n{replies}\n]"
        return current


def render_post(post: Post, assets: Mapping[str, str] | None = None) -> str:
    """Render a post with an optional source-URL -> local-image-path mapping.

    Typst's pinned MiTeX package typesets the original LaTeX. All source prose,
    TeX, URLs, and filenames enter the document through string literals.
    """
    renderer = _Renderer(post, assets or {})
    conversion_note = " · converted to Typst " + _conversion_date(post.converted_at) + " (UTC)"
    provenance = (
        '#text(size: 6.5pt, fill: rgb("596477"))['
        '#link("https://clvnkhr.github.io/tao2typ/")[#text("tao2typ")]'
        f"#text({_string(conversion_note)})]"
    )
    preamble = f"""// Generated from tao2typ IR schema {post.schema_version}.
// Original article: {post.url.replace(chr(10), " ").replace(chr(13), " ")}
// Converted to Typst by tao2typ: {_string(post.converted_at or "unavailable")}
#import "@preview/mitex:{MITEX_VERSION}": mi, mitex
#set document(title: {_string(post.title)}, author: {_string(post.author)})
#set page(paper: "a4", margin: (x: 24mm, y: 23mm), numbering: "1", number-align: center)
#set text(font: ("Libertinus Serif", "New Computer Modern"), size: 12pt, lang: "en")
#set par(
  justify: true,
  first-line-indent: (amount: 1.5em, all: false),
  justification-limits: (tracking: (min: -0.01em, max: 0.01em)),
)
#set heading(numbering: none)
#show heading: set par(justify: false, first-line-indent: 0pt, linebreaks: "optimized")
#show heading.where(level: 1): set text(size: 15pt, weight: "semibold")
#show heading.where(level: 2): set text(size: 12pt, weight: "semibold")
#show link: set text(fill: rgb("275d8c"))
#set list(indent: 1.2em, body-indent: 0.6em, spacing: 0.6em)
#set enum(indent: 1.2em, body-indent: 0.6em, spacing: 0.6em)
#set math.equation(numbering: none)
#show raw.where(block: true): it => block(
  width: 100%, breakable: true, fill: rgb("f5f6f8"), inset: 9pt, radius: 3pt,
)[#text(size: 9pt)[#it]]

#text(size: 9pt, fill: rgb("596477"))[#text({_string(post.blog_title)})]
#v(5pt)
#block(above: 0pt, below: 0pt)[
#set par(justify: false, first-line-indent: 0pt, linebreaks: "optimized")
#text(size: 23pt, weight: "semibold", hyphenate: false)[#text({_string(post.title)})]]
#v(7pt)
#text(size: 10pt)[#text({_string(post.author + " · " + _friendly_date(post.published_at))})]
#v(3pt)
#text(size: 8pt)[#link({_string(post.url)})[#text({_string(post.url)})]]
#v(3pt)
{provenance}
#v(9pt)
#line(length: 100%, stroke: 0.7pt + rgb("d9dfe8"))
#v(10pt)
"""
    result = "\n" + renderer.blocks(post.body) + "\n"
    if post.comments:
        count = sum(1 for _ in _comments(post.comments))
        result += f"\n#heading(level: 1)[#text({_string(f'Comments ({count})')})]\n\n"
        result += "\n\n".join(renderer.comment(comment) for comment in post.comments)
        result += "\n"
    if renderer.uses_math_helpers:
        preamble += _MATH_HELPERS
    if renderer.uses_aligned_math:
        preamble += _ALIGNMENT_HELPERS
    if renderer.uses_display_fitting:
        preamble += _DISPLAY_FIT_HELPERS
    return preamble + result


def _comments(comments: list[Comment]):
    for comment in comments:
        yield comment
        yield from _comments(comment.replies)
