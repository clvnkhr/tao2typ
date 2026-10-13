"""Readable, stable artifact names derived from original WordPress permalinks."""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from urllib.parse import unquote, urlsplit

from tao2typ.state import PostState

_TRANSLITERATIONS = str.maketrans(
    {
        "ß": "ss",
        "æ": "ae",
        "œ": "oe",
        "ø": "o",
        "ł": "l",
        "đ": "d",
        "ð": "d",
        "þ": "th",
        "∞": " infinity ",
        "α": "alpha",
        "β": "beta",
        "γ": "gamma",
        "δ": "delta",
        "ε": "epsilon",
        "ζ": "zeta",
        "η": "eta",
        "θ": "theta",
        "ι": "iota",
        "κ": "kappa",
        "λ": "lambda",
        "μ": "mu",
        "ν": "nu",
        "ξ": "xi",
        "ο": "omicron",
        "π": "pi",
        "ρ": "rho",
        "σ": "sigma",
        "ς": "sigma",
        "τ": "tau",
        "υ": "upsilon",
        "φ": "phi",
        "χ": "chi",
        "ψ": "psi",
        "ω": "omega",
        "+": " plus ",
        "=": " equals ",
        "×": " times ",
        "÷": " divided by ",
        "'": "",
        "’": "",
    }
)
_RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}
_MAX_STEM_LENGTH = 180


def _kebabcase(value: str) -> str:
    value = unicodedata.normalize("NFKD", unquote(value).lower()).translate(_TRANSLITERATIONS)
    ascii_value = value.encode("ascii", "ignore").decode("ascii")
    result = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    if result in _RESERVED_NAMES:
        result = "post-" + result
    return result[:_MAX_STEM_LENGTH].rstrip("-")


def artifact_stem(title: str, url: str, id: int) -> str:
    """Use the original URL slug so subsequent title edits do not rename files."""
    parts = [part for part in urlsplit(url).path.split("/") if part]
    # A paginated comment URL still identifies the original article.
    if parts and re.fullmatch(r"comment-page-\d+", parts[-1]):
        parts.pop()
    slug = _kebabcase(parts[-1]) if parts else ""
    return slug or _kebabcase(title) or f"post-{id}"


def artifact_stems(posts: Iterable[PostState]) -> dict[int, str]:
    """Resolve collisions deterministically, adding the WordPress ID when needed.

    Reserve every original slug before adding suffixes, so a generated suffix
    cannot take another post's actual permalink. WordPress IDs increase over
    time; a later duplicate therefore leaves the earlier post's filename alone.
    """
    groups: dict[str, list[PostState]] = defaultdict(list)
    for post in posts:
        groups[artifact_stem(post.title, post.url, post.id)].append(post)
    occupied = set(groups)
    result: dict[int, str] = {}
    for base, group in sorted(groups.items()):
        ordered = sorted(group, key=lambda post: post.id)
        result[ordered[0].id] = base
        for post in ordered[1:]:
            suffix = f"-{post.id}"
            candidate = base[: _MAX_STEM_LENGTH - len(suffix)].rstrip("-") + suffix
            counter = 2
            while candidate in occupied:
                suffix = f"-{post.id}-{counter}"
                candidate = base[: _MAX_STEM_LENGTH - len(suffix)].rstrip("-") + suffix
                counter += 1
            occupied.add(candidate)
            result[post.id] = candidate
    return result
