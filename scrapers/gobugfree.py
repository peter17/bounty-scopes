"""GoBugFree - https://gobugfree.com

How the site works (checked 2026-11-23):
  * https://gobugfree.com/programs lists the programs. Each card links to a public page
    https://app.gobugfree.com/public/engagements/<org>/<slug> (readable without login).
    Programs that need a login only appear as /login?redirectTo=... links: they are skipped.
  * A public program page is server-side rendered with stable `data-*` hooks:
      - hero: title, status badge ("Active"...), tags ("Bug Bounty" / "VDP", "Public",
        "Requires: ID verification");
      - `[data-program-scope]`: one `[data-scope-asset="<title>"]` block per asset, with a type
        tag (Web / Mobile / API) and a free-text description (`[data-scope-asset-description]`);
      - `[data-bounty-table]`: severity / CVSS / reward range (bug bounties only).
  * There is no structured target: each customer writes titles and descriptions by hand
    ("*.example.com", "Website: example.com", "Not in Scope" ...), so targets are extracted
    heuristically, see scopetext.py.

robots.txt: app.gobugfree.com/robots.txt is "Disallow: /" (it also covers /public/). The
repository owner decided on 2026-11-23 to read these public pages anyway (listing on
gobugfree.com is read with robots.txt honoured). The crawl stays gentle: 1 request/second,
identifiable User-Agent, read-only, ~30 pages a day. Remove `respect_robots=False` below to
go back to honouring the file.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

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
from .htmltext import mark_line_breaks, own_lines
from .scopetext import ASSET_BREAK, entries_from_asset_titles, split_scope_lines

log = logging.getLogger("scrapers.gobugfree")

PLATFORM = "gobugfree"
BASE_URL = "https://gobugfree.com"
APP_URL = "https://app.gobugfree.com"
LIST_URL = f"{BASE_URL}/programs"

_PROGRAM_PATH_RE = re.compile(r"^/public/engagements/([^/?#]+)/([^/?#]+)/?$")
_INFO_TITLE_RE = re.compile(
    r"^\s*(source code|documentation|docs|downloads?|credentials|(test )?accounts?|notes?|"
    r"important notes?|information|faq|contact|rules)\s*$",
    re.I,
)
_OUT_TITLE_RE = re.compile(r"^\s*(not\s+in[\s-]*scope|out[\s-]*of[\s-]*scope)\s*:?\s*$", re.I)


def parse_listing(html: str) -> tuple[dict[str, str], list[str]]:
    """Return ({slug: public page URL} in order of appearance, [slugs needing a login])."""
    soup = BeautifulSoup(html, "html.parser")
    public: dict[str, str] = {}
    private: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/login"):
            # "/login?redirectTo=https://app.gobugfree.com/public/engagements/<org>/<slug>"
            target = href.split("redirectTo=", 1)[-1]
            m = _PROGRAM_PATH_RE.match(urlparse(target).path)
            if m and m.group(2) not in public and m.group(2) not in private:
                private.append(m.group(2))
            continue
        parsed = urlparse(href)
        if parsed.netloc != urlparse(APP_URL).netloc:
            continue
        m = _PROGRAM_PATH_RE.match(parsed.path)
        if m:
            public.setdefault(m.group(2), f"{APP_URL}{parsed.path.rstrip('/')}")
    return public, [s for s in private if s not in public]


def _mobile_type(text: str) -> str:
    """Platform named in `text`; generic when none or both ("eID App (Android / iOS)")."""
    low = text.lower()
    ios = bool(re.search(r"\bios\b|apple", low))
    android = "android" in low
    if ios and not android:
        return "mobile-application-ios"
    if android and not ios:
        return "mobile-application-android"
    return "mobile-application"


# An asset titled with a bare host pattern the generic extractor does not catch: "g-*.0.example.ch".
_GLOB_HOST_RE = re.compile(r"^(?=[^\s]*\*)[\w*\-]+(?:\.[\w*\-]+)+$")


def _asset_entries(asset) -> tuple[list[dict], list[dict], str]:
    """(in_scope, out_of_scope, title) of one `[data-scope-asset]` block."""
    title = " ".join((asset.get("data-scope-asset") or "").split())
    tag_el = asset.find(class_="gbf-tag")
    tag = tag_el.get_text(" ", strip=True).lower() if tag_el else ""
    desc = asset.find(attrs={"data-scope-asset-description": True})
    lines = [ASSET_BREAK, title]
    if desc is not None:
        for el in [desc, *desc.find_all(True)]:
            lines.extend(own_lines(el))
    in_scope, out_scope = split_scope_lines(lines)
    if tag == "mobile":
        # Package ids / store names found in the title are apps, not web hosts.
        for e in in_scope:
            if e["type"] == "web-application" and not e["target"].lower().startswith("http"):
                e["type"] = _mobile_type(title)
    if _GLOB_HOST_RE.match(title) and not any(title in e["target"] for e in in_scope + out_scope):
        in_scope.append({"target": title, "type": "wildcard"})
    if not in_scope and tag == "mobile" and not (_INFO_TITLE_RE.match(title) or _OUT_TITLE_RE.match(title)):
        # "Android App", "ch.example.mycard: Android App": the title is the target.
        in_scope = [{"target": title, "type": _mobile_type(title)}]
    return in_scope, out_scope, title


def parse_program(html: str, slug: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    mark_line_breaks(soup)

    title_el = soup.find(class_="gbf-program-hero-title") or soup.find("h1")
    name = title_el.get_text(" ", strip=True) if title_el else slug
    tags = [t.get_text(" ", strip=True).lower() for t in soup.find_all(class_="gbf-program-hero-tag")]
    badge = soup.find(class_="gbf-badge")
    status = badge.get_text(" ", strip=True).lower() if badge else "active"

    in_scope: list[dict] = []
    out_scope: list[dict] = []
    titles: list[str] = []
    scope = soup.find(attrs={"data-program-scope": True})
    assets = scope.find_all(attrs={"data-scope-asset": True}) if scope else []
    if not assets:
        log.warning("%s: no scope asset found (page layout changed?)", slug)
    for asset in assets:
        ins, outs, title = _asset_entries(asset)
        in_scope.extend(ins)
        out_scope.extend(outs)
        titles.append(title)
    if not in_scope:
        # A VDP may have a single asset described by a statement and no domain at all.
        in_scope = entries_from_asset_titles([t for t in titles if not _INFO_TITLE_RE.match(t)])
        if in_scope:
            log.debug("  %s: no target found, using %d asset title(s) as scope", slug, len(in_scope))
    if assets and not in_scope:
        log.warning(
            "%s: scope assets found but no in-scope target identified: scope left empty, needs "
            "manual review (%d out-of-scope entries discarded)", slug, len(out_scope),
        )
        out_scope = []

    is_vdp = "vdp" in tags or any("vulnerability disclosure" in t for t in tags)
    min_b = max_b = None
    if not is_vdp:
        cells = [
            tr.find_all("td")[-1].get_text(" ", strip=True)
            for table in soup.find_all(attrs={"data-bounty-table": True})
            for tr in table.find_all("tr")
            if tr.find_all("td")
        ]
        min_b, max_b = parse_bounty_range(cells)

    program = make_program(
        id=slug,
        name=name,
        public="public" in tags,
        disabled=status != "active",
        managed=None,
        min_bounty=min_b,
        max_bounty=max_b,
        in_scope=in_scope,
        out_of_scope=out_scope,
    )
    log.debug(
        "  %s: %d in scope, %d out of scope, bounty %s-%s%s, status %s",
        slug, len(program["targets"]["in_scope"]), len(program["targets"]["out_of_scope"]),
        min_b, max_b, ", VDP" if is_vdp else "", status,
    )
    return program


def crawl(limit: int | None = None, only: list[str] | None = None) -> list[dict]:
    log.info("Listing page: %s", LIST_URL)
    public, private = parse_listing(Fetcher(BASE_URL, delay=1.0).get(LIST_URL))
    log.info("  %d public program pages, %d need a login", len(public), len(private))
    for slug in private:
        log.info("  skipping %s (login required)", slug)

    # The program pages live on app.gobugfree.com, whose robots.txt disallows everything:
    # deliberate exception decided by the repository owner, see the module docstring.
    fetcher = Fetcher(APP_URL, delay=1.0, respect_robots=False)

    if only:
        for slug in only:
            if slug not in public:
                log.warning("%s is not a public program of the listing", slug)
        slugs = [s for s in only if s in public]
    else:
        slugs = list(public)[:limit] if limit else list(public)

    log.info("Fetching %d program pages", len(slugs))
    previous = load_previous(PLATFORM)
    results, failures = [], []
    for i, slug in enumerate(slugs, 1):
        url = public[slug]
        progress(i, len(slugs), url)
        try:
            results.append(parse_program(fetcher.get(url), slug))
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
