"""HTML -> text-line helpers shared by scrapers that read free-form pages."""
from __future__ import annotations

from bs4 import BeautifulSoup, Comment, NavigableString

# Marks <br>; a plain "\n" would also match pretty-printing newlines in the source.
BR = "\u2028"

INLINE = {
    "a", "abbr", "b", "bdi", "bdo", "cite", "code", "em", "font", "i", "kbd", "label", "mark",
    "q", "s", "small", "span", "strong", "sub", "sup", "time", "u", "var", "wbr",
}
SKIP = {"script", "style", "noscript", "svg", "template"}
HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"]


def mark_line_breaks(soup: BeautifulSoup) -> None:
    """Replace every <br> by the BR marker (in place)."""
    for br in soup.find_all("br"):
        br.replace_with(BR)


def own_lines(el) -> list[str]:
    """Text that belongs to `el` itself: its direct strings and inline children, not the text of
    nested blocks (those are visited on their own). So "<li>Title <ul><li>x</li></ul></li>"
    gives "Title" for the outer item and "x" for the inner one.

    Fragments are joined without added spaces, like a browser does: "<a>url</a>*" stays "url*".
    `mark_line_breaks` must have been called first for <br> to split lines.
    """
    if el.name in INLINE or el.name in SKIP:
        return []
    if el.name == "pre":  # line breaks are meaningful in preformatted text
        return [" ".join(l.split()) for l in el.get_text("").split("\n") if l.strip()]
    parts = []
    for child in el.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif child.name in INLINE:
            parts.append(child.get_text(""))
    text = "".join(parts)
    return [" ".join(part.split()) for part in text.split(BR) if part.strip()]
