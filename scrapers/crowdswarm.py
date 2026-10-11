"""CrowdSwarm - https://app.crowdswarm.io

Reconnaissance findings: the site is a SPA backed by a public JSON API that uses POST for its
(read-only) queries.
  * POST https://app.crowdswarm.io/api/v1.0/public-program/get-all   {"take": 10, "skip": 0}
      -> {"totalCount": N, "list": [program, ...]}
  * POST .../public-program/get-details/<id>   (empty body): the same program WITHOUT comments.
    Not needed: every list item already carries the full `details` markdown.

A program has: id, title, companyName, details (markdown), minReward, maxReward, rewardPlans[],
launchedDate, tags, logoFile, comments[].

Mapping:
  id        = id (opaque, stable; there is no slug)
  name      = title
  scope     = markdown sections of `details` whose heading starts with "Scope" ("### Scope",
              "### Scope Platform", "### Scope IP Address"...) are in scope; "Out of scope" /
              "Not in scope" headings are out of scope. Every other section (Program Rules,
              "NOT ALLOWED" activities, "Non-qualifying Vulnerabilities", Legal Terms) is rules,
              not assets, and is ignored.
  bounty    = minReward / maxReward, ONLY if REWARD_CURRENCY is a known comparable currency
              (the API gives no currency: unconfirmed, so null until it is set).
  public    = True   (public-program endpoint)
  disabled  = False  (the API has no status field)
  managed   = None

SECURITY: `comments` are written by anonymous users and contain raw XSS payloads
("<script>alert(3)</script>"). They are never read, parsed or stored.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

import requests

from .common import (
    COMPARABLE_CURRENCIES,
    Fetcher,
    SuspiciousDrop,
    load_previous,
    make_program,
    run_cli,
)
from .scopetext import markdown_sections, split_scope_lines

log = logging.getLogger("scrapers.crowdswarm")

PLATFORM = "crowdswarm"
BASE_URL = "https://app.crowdswarm.io"
LIST_PATH = "/api/v1.0/public-program/get-all"
PAGE_SIZE = 10
MAX_PAGES = 100  # safety net against endless pagination

# Currency of minReward / maxReward. UNCONFIRMED: the API does not say. Keep None until checked
# on the platform, then set it (e.g. "USD").
REWARD_CURRENCY: str | None = None

_IN_HEADING_RE = re.compile(r"^\s*(in[\s-]*scope|scope)\b", re.I)
_OUT_HEADING_RE = re.compile(r"^\s*(out[\s-]*of[\s-]*scope|not\s+in[\s-]*scope)\b", re.I)


def _amount(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def reward_range(item: dict) -> tuple[int | None, int | None]:
    if REWARD_CURRENCY not in COMPARABLE_CURRENCIES:
        return None, None
    return _amount(item.get("minReward")), _amount(item.get("maxReward"))


def parse_scope(details: str | None) -> tuple[list[dict], list[dict]]:
    """Scope from the markdown `details`: only under explicit Scope / Out of scope headings."""
    in_scope: list[dict] = []
    out_scope: list[dict] = []
    for title, lines in markdown_sections(details or ""):
        if title and _OUT_HEADING_RE.match(title):
            ins, outs = split_scope_lines(lines, "out")
        elif title and _IN_HEADING_RE.match(title):
            ins, outs = split_scope_lines(lines, "in", keep_statements=True)
        else:
            # Rules and legal text. Only an explicit "In scope:" / "Not in scope:" line counts.
            ins, outs = split_scope_lines(lines, "ignore")
        in_scope += ins
        out_scope += outs
    return in_scope, out_scope


def build_program(item: dict) -> dict:
    # Only whitelisted fields are read: never `comments` (untrusted, contains XSS payloads).
    pid = item["id"]
    min_b, max_b = reward_range(item)
    in_scope, out_scope = parse_scope(item.get("details"))
    program = make_program(
        id=pid,
        name=(item.get("title") or item.get("companyName") or pid).strip(),
        public=True,
        disabled=False,
        managed=None,
        min_bounty=min_b,
        max_bounty=max_b,
        in_scope=in_scope,
        out_of_scope=out_scope,
    )
    log.debug(
        "  %s (%s): %d in scope, %d out of scope, reward %s-%s",
        program["name"], pid, len(program["targets"]["in_scope"]),
        len(program["targets"]["out_of_scope"]), item.get("minReward"), item.get("maxReward"),
    )
    if not program["targets"]["in_scope"]:
        log.warning("%s (%s): no in-scope target found in the description", program["name"], pid)
    return program


def crawl(limit: int | None = None, only: list[str] | None = None) -> list[dict]:
    fetcher = Fetcher(BASE_URL, delay=1.0)
    url = urljoin(BASE_URL, LIST_PATH)

    items: list[dict] = []
    total: int | None = None
    for page in range(1, MAX_PAGES + 1):
        skip = len(items)
        log.info("Page %d: POST %s (skip=%d, take=%d)", page, url, skip, PAGE_SIZE)
        data = fetcher.post_json(url, {"take": PAGE_SIZE, "skip": skip})
        batch = data.get("list") or []
        total = data.get("totalCount", total)
        items += batch
        log.info("  %d programs so far (API totalCount: %s)", len(items), total)
        if not batch or (total is not None and len(items) >= total):
            break
        if limit and len(items) >= limit:
            break

    seen: set[str] = set()
    items = [i for i in items if i.get("id") and not (i["id"] in seen or seen.add(i["id"]))]
    if only:
        known = {i["id"] for i in items}
        for pid in only:
            if pid not in known:
                log.warning("%s is not on the listing", pid)
        items = [i for i in items if i["id"] in only]
    elif limit:
        items = items[:limit]

    log.info("Parsing %d programs (no extra request: the list already contains the details)", len(items))
    previous = load_previous(PLATFORM)
    results, failures = [], []
    for item in items:
        try:
            results.append(build_program(item))
        except (KeyError, TypeError, ValueError, requests.RequestException) as exc:
            log.warning("Failed on %s: %s", item.get("id"), exc)
            failures.append(item.get("id"))
            if item.get("id") in previous:
                results.append(previous[item["id"]])
    if items and len(failures) > max(1, 0.1 * len(items)):
        raise SuspiciousDrop(f"{len(failures)}/{len(items)} programs could not be parsed")
    return results


def main() -> None:
    run_cli(PLATFORM, crawl)


if __name__ == "__main__":
    main()
