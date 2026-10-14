"""Conservative lowering of observed TeX forms to pinned MiTeX equivalents.

The input is a math fragment. Text boxes keep their text semantics; malformed
or unrecognized constructs remain unchanged for normal conversion diagnostics.
"""

import re
import unicodedata

_COMMAND = re.compile(r"\\(?:[A-Za-z]+|.)", re.DOTALL)
_TEXT_COMMANDS = frozenset(
    {
        r"\text",
        r"\mbox",
        r"\hbox",
        r"\textrm",
        r"\textnormal",
        r"\textbf",
        r"\textit",
        r"\textsf",
        r"\texttt",
        r"\textup",
        r"\textsl",
        r"\textsc",
    }
)
_ROMAN_LITERAL = re.compile(r"\s*([+-]?[0-9]{1,4})(?![0-9])")
_ACUTE_LETTER = re.compile(r"\s*(?:\{([A-Za-z])\}|([A-Za-z]))")
_AVERAGE_BAR = re.compile(r"(\\hspace\{[+-]?(?:\d+(?:\.\d*)?|\.\d+)pt\})-")


def _group_end(source: str, start: int) -> int | None:
    depth, position = 1, start + 1
    while position < len(source):
        character = source[position]
        if character == "\\":
            position += 2
            continue
        if character == "%":
            end = source.find("\n", position)
            position = len(source) if end < 0 else end + 1
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return position + 1
        position += 1
    return None


def _roman(value: int) -> str:
    result = []
    for number, letters in (
        (1000, "m"),
        (900, "cm"),
        (500, "d"),
        (400, "cd"),
        (100, "c"),
        (90, "xc"),
        (50, "l"),
        (40, "xl"),
        (10, "x"),
        (9, "ix"),
        (5, "v"),
        (4, "iv"),
        (1, "i"),
    ):
        count, value = divmod(max(0, value), number)
        result.append(letters * count)
    return "".join(result)


def _normalize(source: str, *, text: bool = False) -> str:
    result: list[str] = []
    position = 0
    while position < len(source):
        character = source[position]
        if character == "%":
            end = source.find("\n", position)
            end = len(source) if end < 0 else end + 1
            result.append(source[position:end])
            position = end
            continue
        if character == "{":
            end = _group_end(source, position)
            if end is None:
                result.append(source[position:])
                break
            result.append("{" + _normalize(source[position + 1 : end - 1], text=text) + "}")
            position = end
            continue
        match = _COMMAND.match(source, position)
        if match is None:
            result.append(character)
            position += 1
            continue
        command = match[0]
        after = match.end()
        if command == r"\verb":
            delimiter = after + (source[after : after + 1] == "*")
            end = source.find(source[delimiter : delimiter + 1], delimiter + 1)
            end = len(source) if end < 0 else end + 1
            result.append(source[position:end])
            position = end
            continue
        opening = after
        while opening < len(source) and source[opening].isspace():
            opening += 1
        end = _group_end(source, opening) if source[opening : opening + 1] == "{" else None
        if command in _TEXT_COMMANDS and end is not None:
            result.append(
                source[position : opening + 1]
                + _normalize(source[opening + 1 : end - 1], text=True)
                + "}"
            )
            position = end
            continue
        if text and command == r"\'":
            accent = _ACUTE_LETTER.match(source, after)
            if accent:
                letter = accent[1] or accent[2]
                result.append(unicodedata.normalize("NFC", letter + "\u0301"))
                position = accent.end()
                continue
        if not text:
            if command == r"\/":
                # TeX adds a zero kern here; do not consume or restyle an atom.
                result.append(r"\hspace{0pt}")
                position = after
                continue
            if command == r"\boldmath":
                # LaTeX changes the version for the next formula. Within this
                # math fragment it warns and leaves current glyphs unchanged,
                # as verified against the original WordPress rendering.
                position = after
                continue
            if command == r"\dashrightarrow":
                result.append(r"\mathrel{⇢}")
                position = after
                continue
            if command == r"\romannumeral":
                number = _ROMAN_LITERAL.match(source, after)
                if number:
                    result.append(_roman(int(number[1])))
                    position = number.end()
                    continue
            if command == r"\rlap" and end is not None:
                bar = _AVERAGE_BAR.fullmatch(source[opening + 1 : end - 1])
                if bar:
                    # Pinned MiTeX's mathclap is a zero-width, left-aligned box.
                    # rlap enters text mode: retain the offset and text hyphen.
                    result.append(r"\mathclap{" + bar[1] + r"\text{-}}")
                    position = end
                    continue
        result.append(command)
        position = after
    return "".join(result)


def normalize_tex_compat(source: str) -> str:
    """Lower only verified compatibility forms; preserve original IR separately."""
    return _normalize(source)
