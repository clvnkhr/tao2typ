"""Normalize math fences while retaining the source's TeX argument boundaries.

This is a small lexer, not a replacement TeX parser. It identifies paired vertical
fences; MiTeX remains responsible for scripts, commands, groups and environments.
Bars used as relations, conditional separators or evaluation marks stay as bars.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

DELIMITER_HELPERS = r"""
// Evaluate paired vertical fences as a single native math atom before attaching scripts.
#let tao-lr(..args) = {
  let positional = args.pos()
  if positional.len() == 1 {
    let body = positional.first()
    let children = if body.has("children") { body.children } else { (body,) }
    if children.len() >= 2 {
      let first = children.first()
      let last = children.last()
      if first.has("text") and last.has("text") and first.text == last.text {
        let inner = children.slice(1, -1).sum(default: [])
        if first.text == "|" { return math.abs(inner, ..args.named()) }
        if first.text == "‖" { return math.norm(inner, ..args.named()) }
      }
    }
  }
  math.lr(..args)
}
#let tao-big(body, size) = {
  // Preserve MiTeX's fixed \big/\Big/\bigg/\Bigg heights for a paired atom.
  if type(body) == content and body.func() == math.lr and body.has("body") {
    let children = if body.body.has("children") { body.body.children } else { (body.body,) }
    if children.len() >= 2 {
      let first = children.first()
      let last = children.last()
      if first.has("text") and last.has("text") and first.text == last.text {
        if first.text == "|" or first.text == "‖" { return tao-lr(body.body, size: size) }
      }
    }
  }
  math.lr(body, size: size)
}
"""


@dataclass(frozen=True)
class _Token:
    text: str
    start: int
    end: int
    # \left and \right delimiters are one token, retaining the complete range.
    direction: str = ""
    size: str = ""


@dataclass
class _Opening:
    token: _Token
    family: str
    direction: str
    size: str


@dataclass
class _Scope:
    delimiter: str = ""
    bars: list[_Opening] = field(default_factory=list)
    expects_operand: bool = True
    explicit: _Token | None = None
    script_argument: bool = False
    function_operand: bool = False


_TOKEN = re.compile(r"\\[A-Za-z]+|\\[^\n]|%[^\n]*(?:\n|$)|\s+|.", re.DOTALL)
_TEXT_COMMANDS = frozenset(
    {
        r"\text",
        r"\mbox",
        r"\textrm",
        r"\textsf",
        r"\texttt",
        r"\textnormal",
        r"\textbf",
        r"\textit",
        r"\operatorname",
        r"\operatorname*",
        r"\begin",
        r"\end",
    }
)
_SPACE_COMMANDS = frozenset(
    {r"\,", r"\;", r"\:", r"\!", r"\ ", r"\quad", r"\qquad", r"\enspace", r"\thinspace"}
)
_OPERATORS = frozenset(
    {
        r"\le",
        r"\leq",
        r"\ge",
        r"\geq",
        r"\lt",
        r"\gt",
        r"\ll",
        r"\gg",
        r"\sim",
        r"\lesssim",
        r"\gtrsim",
        r"\simeq",
        r"\approx",
        r"\equiv",
        r"\neq",
        r"\ne",
        r"\in",
        r"\notin",
        r"\subset",
        r"\subseteq",
        r"\supset",
        r"\supseteq",
        r"\times",
        r"\cdot",
        r"\pm",
        r"\mp",
        r"\cup",
        r"\cap",
        r"\to",
        r"\mapsto",
        r"\mid",
        r"\parallel",
        r"\colon",
        r"\implies",
        r"\iff",
        r"\land",
        r"\lor",
        r"\displaystyle",
        r"\textstyle",
        r"\scriptstyle",
        r"\scriptscriptstyle",
        r"\oplus",
        r"\ominus",
        r"\otimes",
        r"\oslash",
        r"\odot",
        r"\circ",
        r"\bullet",
        r"\ast",
        r"\star",
        r"\diamond",
        r"\div",
        r"\setminus",
        r"\backslash",
        r"\vee",
        r"\wedge",
        r"\uplus",
        r"\sqcup",
        r"\sqcap",
        r"\wr",
        r"\triangleleft",
        r"\triangleright",
        r"\bigtriangleup",
        r"\bigtriangledown",
        r"\dagger",
        r"\ddagger",
        r"\amalg",
        r"\bmod",
        r"\mod",
        r"\prec",
        r"\preceq",
        r"\succ",
        r"\succeq",
        r"\perp",
        r"\propto",
        r"\doteq",
        r"\leftrightarrow",
        r"\rightarrow",
        r"\leftarrow",
    }
)
_FUNCTIONS = frozenset(
    "\\" + name
    for name in (
        "sin",
        "cos",
        "tan",
        "cot",
        "sec",
        "csc",
        "sinh",
        "cosh",
        "tanh",
        "coth",
        "arcsin",
        "arccos",
        "arctan",
        "log",
        "ln",
        "lg",
        "exp",
        "min",
        "max",
        "sup",
        "inf",
        "lim",
        "liminf",
        "limsup",
        "det",
        "gcd",
        "ker",
        "dim",
        "arg",
        "Pr",
        "hom",
        "deg",
    )
)
_OPEN = {
    "{": "}",
    "(": ")",
    "[": "]",
    r"\{": r"\}",
    r"\lbrace": r"\rbrace",
    r"\langle": r"\rangle",
    r"\lfloor": r"\rfloor",
    r"\lceil": r"\rceil",
    r"\lbrack": "]",
    r"\lparen": ")",
}
_CLOSE_ALIASES = {r"\rbrack": "]", r"\rparen": ")"}
_CLOSE = frozenset(_OPEN.values()) | _CLOSE_ALIASES.keys()
_FAMILY = {
    "|": "abs",
    r"\vert": "abs",
    r"\lvert": "abs",
    r"\rvert": "abs",
    r"\|": "norm",
    r"\Vert": "norm",
    r"\lVert": "norm",
    r"\rVert": "norm",
}
_SIZES = frozenset(
    "\\" + size + side for size in ("big", "Big", "bigg", "Bigg") for side in ("", "l", "r", "m")
)


def _tokens(source: str) -> list[_Token]:
    raw = [_Token(match[0], match.start(), match.end()) for match in _TOKEN.finditer(source)]
    result: list[_Token] = []
    index = 0
    while index < len(raw):
        token = raw[index]
        command_end = index + 1
        # A star is not part of a TeX control-sequence token. Only this known
        # starred text operator combines it so its name argument stays opaque.
        if token.text == r"\operatorname" and command_end < len(raw):
            if raw[command_end].text == "*":
                token = _Token(token.text + "*", token.start, raw[command_end].end)
                command_end += 1
        if token.text.startswith("%") or token.text.isspace():
            index += 1
            continue
        if token.text in _TEXT_COMMANDS:
            end = command_end
            while end < len(raw) and raw[end].text.isspace():
                end += 1
            if end < len(raw) and raw[end].text == "{":
                depth = 1
                end += 1
                while end < len(raw) and depth:
                    depth += (raw[end].text == "{") - (raw[end].text == "}")
                    end += 1
                # An unclosed source group stays untouched and is left to MiTeX.
                if depth == 0:
                    result.append(
                        _Token(
                            source[token.start : raw[end - 1].end], token.start, raw[end - 1].end
                        )
                    )
                    index = end
                    continue
        if token.text in {r"\left", r"\right", r"\middle"} | _SIZES:
            end = index + 1
            while end < len(raw) and raw[end].text.isspace():
                end += 1
            if end < len(raw):
                delimiter = raw[end]
                result.append(
                    _Token(
                        delimiter.text,
                        token.start,
                        delimiter.end,
                        (
                            token.text.removeprefix("\\")
                            if token.text not in _SIZES
                            else "sized-left"
                            if token.text.endswith("l")
                            else "sized-right"
                            if token.text.endswith("r")
                            else "middle"
                            if token.text.endswith("m")
                            else ""
                        ),
                        token.text.rstrip("lrm") if token.text in _SIZES else "",
                    )
                )
                index = end + 1
                continue
        result.append(token)
        index = command_end
    return result


def _starts_paired_fence(tokens: list[_Token], index: int) -> bool:
    """A complete following fence makes adjacency a product of math atoms.

    Earlier normalization passes turn plain pairs into explicit left/right
    atoms. Recognizing those atoms allows a whole chain of adjacent factors to
    become native fences, rather than leaving every intermediate pair as bars.
    Only complete explicit pairs are accepted; a lone opening is insufficient.
    """
    opening = tokens[index]
    # A fixed-size pair canonicalizes as \big{\left|...\right|}.
    if opening.size and opening.text == "{" and index + 1 < len(tokens):
        index += 1
        opening = tokens[index]
    family = _FAMILY.get(opening.text)
    if opening.direction != "left" or family is None:
        return False
    group_depth = 0
    openings: list[int] = []
    for token in tokens[index:]:
        if token.direction == "left":
            openings.append(group_depth)
        elif token.direction == "right":
            # TeX does not permit a left/right pair to cross an argument group.
            if not openings or openings[-1] != group_depth:
                return False
            openings.pop()
            if not openings:
                return _FAMILY.get(token.text) == family
        elif token.text == "{" and not token.direction:
            group_depth += 1
        elif token.text == "}" and not token.direction:
            if group_depth == 0 or openings[-1] == group_depth:
                return False
            group_depth -= 1
    return False


def _safe_implicit_open(
    tokens: list[_Token], index: int, family: str, enclosing_family: str = ""
) -> bool:
    """Recognize multiplication by a fenced atom without swallowing a|b|c.

    A second bar at this syntactic level must be followed by a script, operator,
    closing delimiter, complete fence or end. A following ordinary operand makes
    a relation chain ambiguous.
    """
    depth = 0
    for position in range(index + 1, len(tokens)):
        token = tokens[position]
        if token.direction == "left" or token.text in _OPEN:
            depth += 1
        elif token.direction == "right" or token.text in _CLOSE:
            if depth == 0:
                return False
            depth -= 1
        elif depth == 0 and _FAMILY.get(token.text) == family:
            if position + 1 == len(tokens):
                return True
            following = tokens[position + 1]
            return (
                following.text in _CLOSE | _OPERATORS
                or following.text in "^_+-=*/<>,:;&"
                or following.direction == "right"
                or _starts_paired_fence(tokens, position + 1)
                # Multiplication inside a different enclosing fence is clear:
                # |a\|u\|| and \|a|x|\| do not form relation chains.
                or enclosing_family != family
                and _FAMILY.get(following.text) == enclosing_family
            )
    return False


def normalize_delimiters(source: str) -> str:
    """Turn paired absolute/norm bars into one MiTeX left/right atom.

    The Typst ``tao-lr`` helper then constructs native ``abs``/``norm`` content.
    Unpaired bars and text-command arguments are retained. Fixed-size fence pairs
    retain their source size. Never replace the source/IR with this render copy.

    Inner pairs can become explicit before an implicit outer opening is clear.
    Re-scan until stable: each changing pass canonicalizes at least one original
    pair, and already canonical left/right pairs stay canonical.
    """
    maximum_passes = 1 + sum(token.text in _FAMILY for token in _tokens(source)) // 2
    for _ in range(maximum_passes):
        normalized = _normalize_delimiters_once(source)
        if normalized == source:
            return source
        source = normalized
    return source


def _normalize_delimiters_once(source: str) -> str:
    """Canonicalize the pairs visible at the current lexical scope boundaries."""
    tokens = _tokens(source)
    scopes = [_Scope()]
    replacements: list[tuple[int, int, str]] = []
    for index, token in enumerate(tokens):
        scope = scopes[-1]
        family = _FAMILY.get(token.text)
        previous = tokens[index - 1].text if index else ""
        # TeX pairs \left/\right by nesting, independently of the glyphs.
        # Keeping their bodies in separate scopes also protects evaluation bars
        # behind \left. and bars inside floor/ceil or unusual delimiter pairs.
        if token.direction == "left":
            scope.function_operand = False
            scopes.append(_Scope(explicit=token))
            continue
        if token.direction == "right":
            explicit_index = None
            for position in range(len(scopes) - 1, 0, -1):
                candidate = scopes[position]
                if candidate.explicit is not None:
                    explicit_index = position
                    break
                if candidate.delimiter == "}":
                    break  # Never cross a TeX group; escaped brace glyphs are literal.
            if explicit_index is not None:
                opening = scopes[explicit_index].explicit
                assert opening is not None
                if family is not None and _FAMILY.get(opening.text) == family:
                    delimiter = "|" if family == "abs" else r"\|"
                    replacements.extend(
                        [
                            (opening.start, opening.end, r"\left" + delimiter),
                            (token.start, token.end, r"\right" + delimiter),
                        ]
                    )
                del scopes[explicit_index:]
                scopes[-1].expects_operand = False
                scopes[-1].function_operand = False
            else:
                scope.expects_operand = False
            continue
        if family is not None and token.direction != "middle":
            directed_open = token.direction in {"left", "sized-left"} or token.text in {
                r"\lvert",
                r"\lVert",
            }
            directed_close = token.direction in {"right", "sized-right"} or token.text in {
                r"\rvert",
                r"\rVert",
            }
            # Unbraced TeX scripts consume exactly one token, including a bar.
            if previous in {"^", "_"} and not token.direction:
                scope.expects_operand = False
                continue
            following = tokens[index + 1] if index + 1 < len(tokens) else None
            # A function may itself be the fenced object, as in \|\sin\|_p.
            # A script/operator/end after the bar marks completion; an operand
            # instead can start its nested argument, as in |\sin |x||.
            function_finished = scope.function_operand and (
                following is None
                or following.text in _CLOSE | _OPERATORS
                or following.text in "^_+-=*/<>,:;&"
                or following.direction == "right"
            )
            if directed_close or (
                not directed_open
                and (not scope.expects_operand or function_finished)
                and scope.bars
                and scope.bars[-1].family == family
            ):
                if scope.bars and scope.bars[-1].family == family:
                    opening = scope.bars[-1]
                    # Never pair a plain bar with an explicitly directed fence.
                    closing_direction = (
                        "left"
                        if token.direction == "right"
                        else "sized"
                        if token.size
                        else "named"
                        if directed_close
                        else ""
                    )
                    if opening.direction == closing_direction and opening.size == token.size:
                        scope.bars.pop()
                        delimiter = "|" if family == "abs" else r"\|"
                        prefix = opening.size + "{" if opening.size else ""
                        suffix = "}" if opening.size else ""
                        replacements.extend(
                            [
                                (
                                    opening.token.start,
                                    opening.token.end,
                                    prefix + r"\left" + delimiter,
                                ),
                                (token.start, token.end, r"\right" + delimiter + suffix),
                            ]
                        )
                scope.expects_operand = False
                scope.function_operand = False
            elif (
                directed_open
                or scope.expects_operand
                or _safe_implicit_open(
                    tokens, index, family, scope.bars[-1].family if scope.bars else ""
                )
            ):
                direction = (
                    "left"
                    if token.direction == "left"
                    else "sized"
                    if token.size
                    else "named"
                    if directed_open
                    else ""
                )
                scope.bars.append(_Opening(token, family, direction, token.size))
                scope.expects_operand = True
                scope.function_operand = False
            else:
                # A conditional/divisibility separator expects its right operand.
                scope.expects_operand = True
            continue
        if token.text in _OPEN:
            scopes.append(_Scope(_OPEN[token.text], script_argument=previous in {"^", "_"}))
        elif (
            token.text in _CLOSE
            and len(scopes) > 1
            and (
                scope.delimiter == _CLOSE_ALIASES.get(token.text, token.text)
                # Half-open intervals are valid math; braces remain strict TeX groups.
                or scope.delimiter in {")", "]"}
                and _CLOSE_ALIASES.get(token.text, token.text) in {")", "]"}
            )
        ):
            scopes.pop()
            parent = scopes[-1]
            parent.expects_operand = parent.function_operand if scope.script_argument else False
            if not scope.script_argument:
                parent.function_operand = False
        elif token.text in _SPACE_COMMANDS:
            continue
        elif previous in {"^", "_"}:
            # A bare +, -, * etc. in a script is its one-token argument,
            # not a binary operator in the enclosing absolute value.
            scope.expects_operand = scope.function_operand
        elif token.text in _FUNCTIONS or re.match(r"\\operatorname\*?\s*\{", token.text):
            scope.expects_operand = True
            scope.function_operand = True
        elif token.text in {"^", "_"}:
            # A function's own script does not supply its following argument.
            scope.expects_operand = False
        elif token.direction == "middle" or token.text in _OPERATORS or token.text in "+-=*/<>,:;&":
            scope.expects_operand = True
            scope.function_operand = False
        else:
            scope.expects_operand = False
            scope.function_operand = False
    for start, end, replacement in sorted(replacements, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source


def normalize_scripts(source: str) -> str:
    """Expand TeX's math-prime shorthand before MiTeX loses its attachment.

    MiTeX 0.2.7 emits ``x '_(i )`` for ``x'_i``, attaching the subscript
    to a detached prime. Explicit ``x^{\\prime}_i`` keeps both scripts on x.
    The source IR is retained; this operates only on the rendering copy.

    TeX's active-prime macro absorbs an immediately following superscript:
    ``x''^{2}`` is equivalent to ``x^{\\prime\\prime 2}``. We consume exactly
    its single token or brace group, never guessing extra command arguments.
    Text-command arguments and escaped apostrophes are opaque lexer tokens.
    """
    tokens = _tokens(source)
    replacements: list[tuple[int, int, str]] = []
    index = 0
    while index < len(tokens):
        opening = tokens[index]
        if opening.text != "'":
            index += 1
            continue
        end = index + 1
        while (
            end < len(tokens)
            and tokens[end].text == "'"
            and tokens[end].start == tokens[end - 1].end
        ):
            end += 1
        primes = r"\prime" * (end - index)
        replacement_end = tokens[end - 1].end
        suffix = ""
        if (
            end + 1 < len(tokens)
            and tokens[end].text == "^"
            and tokens[end].start == replacement_end
        ):
            argument = tokens[end + 1]
            argument_end = end + 2
            if argument.text == "{":
                depth = 1
                while argument_end < len(tokens) and depth:
                    depth += (tokens[argument_end].text == "{") - (tokens[argument_end].text == "}")
                    argument_end += 1
                if depth == 0:
                    replacement_end = tokens[argument_end - 1].end
                    suffix = normalize_scripts(
                        source[argument.end : tokens[argument_end - 1].start]
                    )
                    end = argument_end
            elif argument.text not in {"}", "^", "_", "'"}:
                # The fence lexer can combine a command with a delimiter or
                # text argument, and accepts starred command spellings. TeX's
                # prime macro consumes only the control sequence token itself.
                command = re.match(r"\\(?:[A-Za-z]+|.)", source[argument.start :])
                replacement_end = (
                    argument.start + command.end() if command is not None else argument.start + 1
                )
                suffix = source[argument.start : replacement_end]
                end = argument_end
        # A separator keeps an absorbed alphabetic token from extending the
        # control sequence name, e.g. \prime p must never become \primep.
        replacement = "^{" + primes + (" " + suffix if suffix else "") + "}"
        replacements.append((opening.start, replacement_end, replacement))
        index = end
    for start, end, replacement in reversed(replacements):
        source = source[:start] + replacement + source[end:]
    return source
