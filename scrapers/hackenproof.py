"""HackenProof - https://hackenproof.com

Reconnaissance findings:
  * /programs lists the bug bounty programs (server-side rendered HTML).
  * Some cards link to /companies/<slug> instead of /programs/<slug>;
    the company page links to its programs.
  * /programs/<slug> contains the "In scope" / "Out of scope" tables
    (columns Target | Type | Severity) and "Range of bounty $a - $b".

Audit programs (/audit-programs) are deliberately excluded: they are not bug bounties.
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
    parse_money,
    progress,
    run_cli,
    slugify,
)

log = logging.getLogger("scrapers.hackenproof")

PLATFORM = "hackenproof"
BASE_URL = "https://hackenproof.com"
LIST_URL = f"{BASE_URL}/programs"
MAX_PAGES = 60  # safety net against endless pagination

# Type displayed by HackenProof -> type used by the reference format.
TYPE_MAP = {
    "web": "web-application",
    "website": "web-application",
    "web app": "web-application",
    "web application": "web-application",
    "api": "api",
    "ios": "mobile-application-ios",
    "android": "mobile-application-android",
    "mobile": "mobile-application",
    "mobile app": "mobile-application",
    "smart contract": "smart-contract",
    "protocol": "protocol",
    "blockchain": "blockchain",
    "desktop": "application",
    "other": "other",
}

_PROGRAM_RE = re.compile(r"^/programs/([^/?#]+)/?$")
_COMPANY_RE = re.compile(r"^/companies/([^/?#]+)/?$")


def map_type(raw: str) -> str:
    key = " ".join(raw.lower().split())
    return TYPE_MAP.get(key) or slugify(raw) or "other"


def parse_listing(html: str) -> tuple[list[str], list[str]]:
    """Return (program slugs, company slugs) in order of appearance."""
    soup = BeautifulSoup(html, "html.parser")
    programs: list[str] = []
    companies: list[str] = []
    for a in soup.find_all("a", href=True):
        path = urlparse(a["href"]).path
        if (m := _PROGRAM_RE.match(path)) and m.group(1) not in programs:
            programs.append(m.group(1))
        elif (m := _COMPANY_RE.match(path)) and m.group(1) not in companies:
            companies.append(m.group(1))
    return programs, companies


def _scope_kind(table) -> str:
    """'in' or 'out', based on the preceding "In scope" / "Out of scope" heading."""
    heading = table.find_previous(
        string=re.compile(r"\b(in|out of)[\s-]*scope\b", re.I)
    )
    if heading and re.search(r"\bout\b", heading, re.I):
        return "out"
    return "in"


def _first_text(cell) -> str:
    # The Target cell also contains a "Copy / Copied" button: keep only the first
    # text fragment.
    return next(cell.stripped_strings, "")


def parse_program(html: str, slug: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).split())

    # Name: <title> = "NEAR Protocol: Program Info | HackenProof"
    title = soup.title.get_text(strip=True) if soup.title else slug
    name = re.sub(r"\s*\|\s*HackenProof\s*$", "", title)
    name = re.sub(r":\s*Program info\s*$", "", name, flags=re.I).strip() or slug

    # Reward range: "Range of bounty $100 - $1,000,000"
    min_b = max_b = None
    if m := re.search(
        r"Range of bounty\s*([^\s-]*[\d.,]+)\s*-\s*([^\s-]*[\d.,]+)", text, re.I
    ):
        min_b, max_b = parse_money(m.group(1)), parse_money(m.group(2))

    # Status: active by default (we only scrape programs that are listed).
    disabled = bool(re.search(r"Program is (paused|closed|suspended|finished)", text, re.I))

    scopes: dict[str, list[dict]] = {"in": [], "out": []}
    for table in soup.find_all("table"):
        headers = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
        if not headers or headers[0] != "target":
            continue
        type_idx = headers.index("type") if "type" in headers else None
        kind = _scope_kind(table)
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            target = _first_text(cells[0])
            raw_type = _first_text(cells[type_idx]) if type_idx is not None and type_idx < len(cells) else ""
            scopes[kind].append({"target": target, "type": map_type(raw_type) if raw_type else "other"})

    program = make_program(
        id=slug,
        name=name,
        public=True,
        disabled=disabled,
        managed=None,
        min_bounty=min_b,
        max_bounty=max_b,
        in_scope=scopes["in"],
        out_of_scope=scopes["out"],
    )
    if not program["targets"]["in_scope"]:
        log.warning("%s: no in-scope target found (page layout changed, or empty scope?)", slug)
    log.debug(
        "  %s: %d in scope, %d out of scope, bounty %s-%s%s",
        slug, len(program["targets"]["in_scope"]), len(program["targets"]["out_of_scope"]),
        min_b, max_b, ", DISABLED" if disabled else "",
    )
    return program


def crawl(limit: int | None = None) -> list[dict]:
    fetcher = Fetcher(BASE_URL, delay=1.0)

    # Phase 1: program listing (paginated).
    program_slugs: list[str] = []
    company_slugs: list[str] = []
    for page in range(1, MAX_PAGES + 1):
        url = LIST_URL if page == 1 else f"{LIST_URL}?page={page}"
        log.info("Listing page %d: %s", page, url)
        progs, comps = parse_listing(fetcher.get(url))
        new_p = [s for s in progs if s not in program_slugs]
        new_c = [s for s in comps if s not in company_slugs]
        log.info("  %d new programs, %d new company links (total so far: %d programs, %d companies)",
                 len(new_p), len(new_c), len(program_slugs) + len(new_p), len(company_slugs) + len(new_c))
        if not new_p and not new_c:
            break  # empty page or parameter ignored: everything has been seen
        program_slugs += new_p
        company_slugs += new_c
        if limit and len(program_slugs) >= limit:
            break

    # Phase 2: company pages (they point to additional programs).
    if company_slugs and not (limit and len(program_slugs) >= limit):
        log.info("Fetching %d company pages", len(company_slugs))
        for i, company in enumerate(company_slugs, 1):
            url = f"{BASE_URL}/companies/{company}"
            progress(i, len(company_slugs), url)
            progs, _ = parse_listing(fetcher.get(url))
            added = [s for s in progs if s not in program_slugs]
            if added:
                log.debug("  %s: +%d programs", company, len(added))
            program_slugs += added

    if limit:
        program_slugs = program_slugs[:limit]

    # Phase 3: program pages.
    log.info("Fetching %d program pages", len(program_slugs))
    previous = load_previous(PLATFORM)
    results, failures = [], []
    for i, slug in enumerate(program_slugs, 1):
        url = urljoin(BASE_URL + "/", f"programs/{slug}")
        progress(i, len(program_slugs), url)
        try:
            results.append(parse_program(fetcher.get(url), slug))
        except requests.RequestException as exc:
            log.warning("Failed on %s: %s", slug, exc)
            failures.append(slug)
            if slug in previous:  # keep the old entry to avoid diff noise
                results.append(previous[slug])

    if failures:
        log.warning("%d page(s) failed: %s", len(failures), ", ".join(failures[:10]))
    # A few isolated failures are tolerated; beyond that, better write nothing.
    if program_slugs and len(failures) > 0.1 * len(program_slugs):
        raise SuspiciousDrop(f"{len(failures)}/{len(program_slugs)} pages failed")
    return results


def main() -> None:
    run_cli(PLATFORM, crawl)


if __name__ == "__main__":
    main()
