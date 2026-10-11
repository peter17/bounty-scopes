"""WhiteHub (CyStack) - https://whitehub.net

Reconnaissance findings: the site is a SPA backed by a public JSON API.
  * List:   GET https://api.cystack.net/v2/bug-bounty/programs?page=N&is_owner=0&status=open,locked
            -> {"count", "next": "/v2/...?page=2...", "previous", "results": [...]}, 10 per page.
  * Detail: GET https://api.cystack.net/v2/bug-bounty/programs/<alias>
            -> "target": [{"name", "type", "scope": "in_scope"|..., ...}] (structured scope),
               plus "metadata": {"en": markdown} (prose, NOT used: the structured list is the scope).

Mapping:
  id        = org_alias
  name      = org.name (program_projection.name is just the alias)
  public    = visibility == "public"           (semi_private programs are listed but unreadable)
  disabled  = status != "open"                  ("locked")
  managed   = is_wh_managed
  bounty    = reward_range, only if currency is USD/EUR/CHF/GBP (WhiteHub mostly uses VND)

Only type seen so far: "source_code". Unknown types are kept (normalised) and logged at INFO.
"""
from __future__ import annotations

import logging
from urllib.parse import urljoin

import requests

from .common import (
    COMPARABLE_CURRENCIES,
    Fetcher,
    SuspiciousDrop,
    load_previous,
    make_program,
    progress,
    run_cli,
    slugify,
)

log = logging.getLogger("scrapers.whitehub")

PLATFORM = "whitehub"
BASE_URL = "https://api.cystack.net"
LIST_PATH = "/v2/bug-bounty/programs?page=1&is_owner=0&status=open,locked&sort=last_updated_desc"
DETAIL_PATH = "/v2/bug-bounty/programs/{alias}"
MAX_PAGES = 50  # safety net against endless pagination

# WhiteHub target type -> reference type. Only "source_code" is confirmed on real data; the
# others are obvious equivalents that cost nothing if they never occur.
TYPE_MAP = {
    "source_code": "source-code",
    "web": "web-application",
    "website": "web-application",
    "web_app": "web-application",
    "api": "api",
    "android": "mobile-application-android",
    "ios": "mobile-application-ios",
    "mobile": "mobile-application",
    "mobile_app": "mobile-application",
    "other": "other",
}
_seen_unknown_types: set[str] = set()


def map_type(raw: str | None) -> str:
    key = (raw or "").strip().lower()
    if not key:
        return "other"
    if key in TYPE_MAP:
        return TYPE_MAP[key]
    if key not in _seen_unknown_types:
        _seen_unknown_types.add(key)
        log.info("Unknown WhiteHub target type %r kept as %r (add it to TYPE_MAP)", raw, slugify(key))
    return slugify(key) or "other"


def _amount(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def bounty_range(item: dict) -> tuple[int | None, int | None]:
    """reward_range as ints, only in a currency of comparable magnitude (never VND...)."""
    if item.get("currency") not in COMPARABLE_CURRENCIES:
        return None, None
    rr = item.get("reward_range") or {}
    return _amount(rr.get("min")), _amount(rr.get("max"))


def parse_targets(detail: dict) -> tuple[list[dict], list[dict]]:
    in_scope, out_scope = [], []
    for t in detail.get("target") or []:
        name = (t.get("name") or "").strip()
        if not name:
            continue
        entry = {"target": name, "type": map_type(t.get("type"))}
        (out_scope if str(t.get("scope", "in_scope")).lower().startswith("out") else in_scope).append(entry)
    return in_scope, out_scope


def build_program(item: dict, detail: dict | None) -> dict:
    alias = item.get("org_alias") or item["program_projection"]["alias"]
    org = item.get("org") or {}
    min_b, max_b = bounty_range(item)
    in_scope, out_scope = parse_targets(detail) if detail else ([], [])
    program = make_program(
        id=alias,
        name=org.get("name") or alias,
        public=item.get("visibility") == "public",
        disabled=item.get("status") != "open",
        managed=bool(item.get("is_wh_managed")),
        min_bounty=min_b,
        max_bounty=max_b,
        in_scope=in_scope,
        out_of_scope=out_scope,
    )
    log.debug(
        "  %s: %s, %s, %d in scope, %d out of scope, bounty %s-%s (%s)",
        alias, item.get("visibility"), item.get("status"), len(program["targets"]["in_scope"]),
        len(program["targets"]["out_of_scope"]), min_b, max_b, item.get("currency"),
    )
    return program


def crawl(limit: int | None = None, only: list[str] | None = None) -> list[dict]:
    fetcher = Fetcher(BASE_URL, delay=1.0)

    items: list[dict] = []
    path: str | None = LIST_PATH
    for page in range(1, MAX_PAGES + 1):
        if not path:
            break
        url = urljoin(BASE_URL, path)
        log.info("Listing page %d: %s", page, url)
        data = fetcher.get_json(url)
        items += data.get("results") or []
        log.info("  %d programs so far (API count: %s)", len(items), data.get("count"))
        path = data.get("next")

    if only:
        known = {i.get("org_alias") for i in items}
        for alias in only:
            if alias not in known:
                log.warning("%s is not on the listing", alias)
        items = [i for i in items if i.get("org_alias") in only]
    elif limit:
        items = items[:limit]

    log.info("Fetching %d program details", len(items))
    previous = load_previous(PLATFORM)
    results, failures = [], []
    for i, item in enumerate(items, 1):
        alias = item.get("org_alias")
        url = urljoin(BASE_URL, DETAIL_PATH.format(alias=alias))
        progress(i, len(items), url)
        if not item.get("is_readable", True):
            log.info("  %s: not readable (%s), no scope", alias, item.get("visibility"))
            results.append(build_program(item, None))
            continue
        try:
            results.append(build_program(item, fetcher.get_json(url)))
        except (requests.RequestException, ValueError) as exc:
            log.warning("Failed on %s: %s", alias, exc)
            failures.append(alias)
            if alias in previous:
                results.append(previous[alias])

    if failures:
        log.warning("%d program(s) failed: %s", len(failures), ", ".join(failures[:10]))
    if items and len(failures) > max(1, 0.1 * len(items)):
        raise SuspiciousDrop(f"{len(failures)}/{len(items)} program details failed")
    return results


def main() -> None:
    run_cli(PLATFORM, crawl)


if __name__ == "__main__":
    main()
