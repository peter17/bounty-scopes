"""GoBugFree - https://gobugfree.com

DISABLED since 2026-10-29: the site moved program pages to
app.gobugfree.com/engagements/<org>/<slug>, which requires a login (Keycloak) and whose
robots.txt is "Disallow: /". The public /programs listing only shows names now, so this
scraper finds 0 programs and keeps the previous data file. The scheduled workflow is off;
the findings below describe the site as it was before.

Reconnaissance findings:
  * /programs lists the programs (bug bounties, VDPs, EDU programs, CTF events).
    The footer also links to /programs/trust, which is NOT a program.
  * /programs/<slug> is server-side rendered, with <h2> sections:
    "Scopes" (free text: "Targets:" / "Not in scope:" lists) and
    "Bounty Levels" (table, e.g. "CHF 2000-3000").
  * Some programs (e.g. raiffeisen-bb) show only their description to anonymous
    visitors: no Scopes section. They are kept, with an empty scope and a warning.

Scope extraction is heuristic, see scopetext.py.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .common import (
    Fetcher,
    SuspiciousDrop,
    load_previous,
    make_program,
    parse_bounty_range,
    progress,
    run_cli,
)
from .htmltext import mark_line_breaks, own_lines as _own_lines
from .scopetext import ASSET_BREAK, entries_from_asset_titles, split_scope_lines

log = logging.getLogger("scrapers.gobugfree")

PLATFORM = "gobugfree"
BASE_URL = "https://gobugfree.com"
LIST_URL = f"{BASE_URL}/programs"

# /programs/<slug> links that are not programs.
NOT_PROGRAMS = {"trust"}

_PROGRAM_RE = re.compile(r"^/programs/([^/?#]+)/?$")
_HEADING_TAGS = ["h1", "h2", "h3"]


def parse_listing(html: str) -> dict[str, str]:
    """Return {slug: card text} in order of appearance.

    The card text (anchor text) carries the tags shown on the listing
    ("Paused", "Vulnerability Disclosure Program", "CTF", "EDU Program", ...).
    """
    soup = BeautifulSoup(html, "html.parser")
    cards: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        m = _PROGRAM_RE.match(urlparse(a["href"]).path)
        if not m or m.group(1) in NOT_PROGRAMS:
            continue
        text = " ".join(a.get_text(" ", strip=True).split())
        slug = m.group(1)
        cards[slug] = f"{cards.get(slug, '')} {text}".strip()
    return cards


# Real section titles of a program page. Any other heading inside "Scopes" (asset titles such
# as "Abacus-ERP") is just a line of text: it must not end the section.
_SECTION_TITLE_RE = re.compile(
    r"^\s*(description|rules|scopes?|procedure|legal|bounty levels|leaderboard|hall of fame|footer)\s*$",
    re.I,
)
def _section(soup: BeautifulSoup, title: str, section_id: str | None = None):
    """All tags of a page section.

    The real pages wrap each section in <section aria-labelledby="scopes"> (also
    "description", "rules", "legal"...): that is the most reliable boundary. For anything else
    we fall back to "everything between the heading `title` and the next real section heading".
    """
    if section_id:
        wrapper = soup.find("section", attrs={"aria-labelledby": section_id})
        if wrapper is not None:
            return wrapper.find_all(True)
    pattern = re.compile(rf"^\s*{title}\s*$", re.I)
    heading = soup.find(
        lambda t: t.name in _HEADING_TAGS and pattern.match(t.get_text(" ", strip=True))
    )
    if heading is None:
        return None
    out = []
    for el in heading.find_all_next(True):
        if el.name == "h1" or (el.name == "h2" and _SECTION_TITLE_RE.match(el.get_text(" ", strip=True))):
            break
        out.append(el)
    return out


def _is_asset_title(el) -> bool:
    """Title of a structured asset: <li><div><div class="font-semibold">title</div>...."""
    return el.name == "div" and "font-semibold" in (el.get("class") or []) and el.find_parent("li") is not None


def parse_program(html: str, slug: str, card_text: str = "") -> dict:
    soup = BeautifulSoup(html, "html.parser")
    mark_line_breaks(soup)

    h1 = soup.find("h1")
    name = h1.get_text(" ", strip=True) if h1 else slug

    in_scope: list[dict] = []
    out_scope: list[dict] = []
    elements = _section(soup, "Scopes?", "scopes")
    if elements is None:
        log.warning("%s: no 'Scopes' section (hidden for anonymous visitors?)", slug)
    else:
        lines: list[str] = []
        for el in elements:
            if _is_asset_title(el):
                lines.append(ASSET_BREAK)
            lines.extend(_own_lines(el))
        in_scope, out_scope = split_scope_lines(lines)
        if not in_scope:
            # Structured asset list (<li><div class="font-semibold">Asset name</div>...): a VDP
            # may have a single asset described by a statement and no domain at all.
            titles = [el.get_text(" ", strip=True) for el in elements if _is_asset_title(el)]
            in_scope = entries_from_asset_titles(titles)
            if in_scope:
                log.debug("  %s: no target found, using %d asset title(s) as scope", slug, len(in_scope))
        if not in_scope:
            # Typical cause: the author listed targets after a "Not in scope" line without
            # an "In scope" header. We cannot tell which is which, and publishing targets
            # as out of scope would be wrong, so we publish nothing for this program.
            log.warning(
                "%s: 'Scopes' section found but no in-scope target identified: scope left "
                "empty, needs manual review (%d out-of-scope entries discarded)",
                slug, len(out_scope),
            )
            out_scope = []

    min_b = max_b = None
    bounty_rows = _section(soup, "Bounty Levels", "bounty-levels")
    if bounty_rows:
        cells = [c for tr in bounty_rows if tr.name == "tr" for c in tr.find_all("td")[1:]]
        min_b, max_b = parse_bounty_range([c.get_text(" ", strip=True) for c in cells])

    card = card_text.lower()
    page_text = soup.get_text(" ").lower()
    is_vdp = "vulnerability disclosure" in card or "no bug bounties are paid" in page_text
    if is_vdp:
        min_b = max_b = None
    restricted = bool(re.search(r"\bedu\b|registered students|students only|edu-id", card))
    # Listed publicly but invite-only ("Get invited to this private bug bounty program").
    private = "invited to this private" in page_text
    restricted = restricted or private
    program = make_program(
        id=slug,
        name=name,
        public=not restricted,
        disabled="paused" in card,
        managed=None,
        min_bounty=min_b,
        max_bounty=max_b,
        in_scope=in_scope,
        out_of_scope=out_scope,
    )
    log.debug(
        "  %s: %d in scope, %d out of scope, bounty %s-%s%s%s",
        slug, len(program["targets"]["in_scope"]), len(program["targets"]["out_of_scope"]),
        min_b, max_b, ", VDP" if is_vdp else "", ", restricted" if restricted else "",
    )
    return program


def crawl(limit: int | None = None, only: list[str] | None = None) -> list[dict]:
    fetcher = Fetcher(BASE_URL, delay=1.0)

    log.info("Listing page: %s", LIST_URL)
    cards = parse_listing(fetcher.get(LIST_URL))
    log.info("  %d programs found on the listing", len(cards))

    # CTF events are not bug bounty programs.
    for slug in [s for s, text in cards.items() if re.search(r"\bctf\b", text, re.I)]:
        log.info("  skipping %s (CTF event)", slug)
        del cards[slug]

    if only:
        for slug in only:
            if slug not in cards:
                log.warning("%s is not on the listing (card tags such as Paused/EDU unknown)", slug)
        slugs = list(only)
    else:
        slugs = list(cards)[:limit] if limit else list(cards)

    log.info("Fetching %d program pages", len(slugs))
    previous = load_previous(PLATFORM)
    results, failures = [], []
    for i, slug in enumerate(slugs, 1):
        url = urljoin(BASE_URL + "/", f"programs/{slug}")
        progress(i, len(slugs), url)
        try:
            results.append(parse_program(fetcher.get(url), slug, cards.get(slug, "")))
        except requests.RequestException as exc:
            log.warning("Failed on %s: %s", slug, exc)
            failures.append(slug)
            if slug in previous:
                results.append(previous[slug])

    if failures:
        log.warning("%d page(s) failed: %s", len(failures), ", ".join(failures[:10]))
    if slugs and len(failures) > 0.1 * len(slugs):
        raise SuspiciousDrop(f"{len(failures)}/{len(slugs)} pages failed")
    return results


def main() -> None:
    run_cli(PLATFORM, crawl)


if __name__ == "__main__":
    main()
