"""Bugbop - https://bugbop.com

Reconnaissance findings:
  * /programs requires a login, but /public-programs is a public, server-side rendered list of
    cards: name (link to /programs/<uuid>), main URL, "Public" or "Restricted", and
    "Bounty: $0 - $500" / "None" / "Paused" / "Swag or Store Credit".
  * /programs/<uuid> has "Program Description & Scope" (free markdown, written by each
    customer) and a "Rewards" table (Severity | Bounty Range).
  * There is NO structured scope: the only structured asset is the program's main URL.

Decisions:
  * The main URL is the in-scope asset.
  * The description is prose (e.g. "We use preview.gleam.io to prevent XSS"): its URLs are NOT
    targets. It is only read under an explicit heading ("Scope", "In scope", "Targets",
    "Out of scope", ...), or after an explicit "In scope:" / "Not in scope:" line. Sections such
    as "Exclusions" list vulnerability classes (DoS, spam), not assets, and are ignored.
  * "Restricted" programs get public=false; "Paused" ones disabled=true.
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
from .htmltext import HEADINGS, mark_line_breaks, own_lines
from .scopetext import guess_type, split_scope_lines

log = logging.getLogger("scrapers.bugbop")

PLATFORM = "bugbop"
BASE_URL = "https://bugbop.com"
LIST_URL = f"{BASE_URL}/public-programs"
MAX_PAGES = 20  # safety net against endless pagination

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_PROGRAM_PATH_RE = re.compile(rf"^/programs/({_UUID})/?$", re.I)

# Headings of the description whose content is a scope list.
_IN_TITLE_RE = re.compile(
    r"^\s*(in[\s-]*scope(\s+(assets|targets|domains|applications))?|scope|targets?|assets?)\s*:?\s*$", re.I
)
_OUT_TITLE_RE = re.compile(
    r"^\s*(out[\s-]*of[\s-]*scope(\s+(assets|targets|domains))?|not\s+in[\s-]*scope)\s*:?\s*$", re.I
)
# Page sections that end the description (anything else is a sub-heading of the description).
_PAGE_SECTION_RE = re.compile(r"^\s*(rewards|hall of fame|activity|reports|about)\s*$", re.I)


# --------------------------------------------------------------------------- listing


def _program_ids_in(node) -> set[str]:
    ids = set()
    for a in node.find_all("a", href=True):
        if m := _PROGRAM_PATH_RE.match(urlparse(a["href"]).path):
            ids.add(m.group(1).lower())
    return ids


def parse_listing(html: str) -> dict[str, dict]:
    """Return {program id: card info} in order of appearance.

    The card is the largest ancestor of the program link that contains no other program.
    """
    soup = BeautifulSoup(html, "html.parser")
    cards: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        m = _PROGRAM_PATH_RE.match(urlparse(a["href"]).path)
        name = " ".join(a.get_text(" ").split())
        if not m or not name:  # skip logo links (no text) and other links
            continue
        pid = m.group(1).lower()
        if pid in cards:
            continue

        node = a
        while node.parent is not None and node.parent.name not in ("body", "[document]"):
            if len(_program_ids_in(node.parent)) > 1:
                break
            node = node.parent

        text = " ".join(node.get_text(" ").split()).replace(name, "", 1)
        visibility = re.search(r"\b(Public|Restricted)\b", text)
        bounty = re.search(r"Bounty:\s*(.*?)\s*(?:Bounties paid|$)", text)
        main_url = next(
            (
                link["href"]
                for link in node.find_all("a", href=True)
                if link["href"].startswith("http") and not _PROGRAM_PATH_RE.match(urlparse(link["href"]).path)
            ),
            None,
        )
        cards[pid] = {
            "name": name,
            "visibility": visibility.group(1) if visibility else "",
            "bounty_text": bounty.group(1) if bounty else "",
            "url": main_url,
        }
    return cards


# --------------------------------------------------------------------------- detail page


def _description_sections(soup: BeautifulSoup) -> list[tuple[str | None, list[str]]] | None:
    """[(heading title or None, lines)] of the "Program Description & Scope" block."""
    start = soup.find(
        lambda t: t.name in HEADINGS and re.match(r"^\s*Program Description", t.get_text(" ", strip=True), re.I)
    )
    if start is None:
        return None
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for el in start.find_all_next(True):
        if el.name in HEADINGS:
            title = el.get_text(" ", strip=True)
            if _PAGE_SECTION_RE.match(title):
                break
            sections.append((title, []))
            continue
        sections[-1][1].extend(own_lines(el))
    return sections


def _rewards_cells(soup: BeautifulSoup) -> list[str]:
    heading = soup.find(
        lambda t: t.name in HEADINGS and t.get_text(" ", strip=True).lower() == "rewards"
    )
    table = heading.find_next("table") if heading else None
    if table is None:
        return []
    cells = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) >= 2:
            cells.append(tds[1].get_text(" ", strip=True))
    return cells


def parse_program(html: str, pid: str, card: dict | None = None) -> dict:
    card = card or {}
    soup = BeautifulSoup(html, "html.parser")
    mark_line_breaks(soup)

    name = card.get("name") or (soup.title.get_text(strip=True) if soup.title else pid)
    bounty_text = card.get("bounty_text", "")

    # Bounty: the listing range if parsable, else the min/max of the Rewards table
    # (the listing says "Paused" for paused programs but the table keeps the amounts).
    min_b, max_b = parse_bounty_range([bounty_text])
    if min_b is None and max_b is None:
        min_b, max_b = parse_bounty_range(_rewards_cells(soup))

    in_scope: list[dict] = []
    out_scope: list[dict] = []
    if card.get("url"):
        in_scope.append({"target": card["url"], "type": guess_type(card["url"])})

    sections = _description_sections(soup)
    if sections is None:
        log.warning("%s: no 'Program Description' section (login wall for restricted programs?)", pid)
    else:
        for title, lines in sections:
            if title and _IN_TITLE_RE.match(title):
                mode = "in"
            elif title and _OUT_TITLE_RE.match(title):
                mode = "out"
            else:
                mode = "ignore"  # prose: only explicit "In scope:" / "Not in scope:" lines count
            ins, outs = split_scope_lines(lines, mode)
            in_scope += ins
            out_scope += outs

    program = make_program(
        id=pid,
        name=name,
        public=card.get("visibility", "Public") != "Restricted",
        disabled="paused" in bounty_text.lower(),
        managed=None,
        min_bounty=min_b,
        max_bounty=max_b,
        in_scope=in_scope,
        out_of_scope=out_scope,
    )
    log.debug(
        "  %s (%s): %d in scope, %d out of scope, bounty %s-%s%s%s",
        name, pid[:8], len(program["targets"]["in_scope"]), len(program["targets"]["out_of_scope"]),
        min_b, max_b, ", restricted" if not program["public"] else "", ", paused" if program["disabled"] else "",
    )
    return program


# --------------------------------------------------------------------------- crawl


def crawl(limit: int | None = None, only: list[str] | None = None) -> list[dict]:
    fetcher = Fetcher(BASE_URL, delay=1.0)

    cards: dict[str, dict] = {}
    for page in range(1, MAX_PAGES + 1):
        url = LIST_URL if page == 1 else f"{LIST_URL}?page={page}"
        log.info("Listing page %d: %s", page, url)
        found = parse_listing(fetcher.get(url))
        new = {k: v for k, v in found.items() if k not in cards}
        log.info("  %d new programs (total so far: %d)", len(new), len(cards) + len(new))
        if not new:
            break  # empty page or parameter ignored: everything has been seen
        cards.update(new)

    if only:
        for pid in only:
            if pid.lower() not in cards:
                log.warning("%s is not on the public listing", pid)
        ids = [p.lower() for p in only]
    else:
        ids = list(cards)[:limit] if limit else list(cards)

    log.info("Fetching %d program pages", len(ids))
    previous = load_previous(PLATFORM)
    results, failures = [], []
    for i, pid in enumerate(ids, 1):
        url = urljoin(BASE_URL + "/", f"programs/{pid}")
        progress(i, len(ids), url)
        try:
            results.append(parse_program(fetcher.get(url), pid, cards.get(pid)))
        except requests.RequestException as exc:
            log.warning("Failed on %s: %s", pid, exc)
            failures.append(pid)
            if pid in previous:
                results.append(previous[pid])

    if failures:
        log.warning("%d page(s) failed: %s", len(failures), ", ".join(failures[:10]))
    if ids and len(failures) > max(1, 0.1 * len(ids)):  # small platform: tolerate one failure
        raise SuspiciousDrop(f"{len(failures)}/{len(ids)} pages failed")
    return results


def main() -> None:
    run_cli(PLATFORM, crawl)


if __name__ == "__main__":
    main()
