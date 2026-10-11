"""Bugzero - https://bugzero.io

Reconnaissance findings: the site is a SPA backed by public JSON endpoints.
  * List:   GET https://us-central1-bug-zero.cloudfunctions.net/api/programs
            -> [{"name", "handle", "minimum_bounties": 5 | "", "rewards": {"bug_bounty": bool, ...}}]
  * Detail: GET https://us-central1-bug-zero.cloudfunctions.net/api/organization/<handle>
            -> {"generalData": {"org_name", "org_website", ...},
                "programData": {"policy": markdown, "scope": [
                    {"type": "Domain", "identifier": "drawify.com", "submission": "In Scope",
                     "bounty": "Yes", "threat_level": "Critical", "instruction": "...", "reference": "..."}]}}

Mapping:
  id        = handle
  name      = generalData.org_name (fallback: list name), stripped
  scope     = programData.scope[] (structured). The "policy" markdown is prose / a generic
              template and is NOT read.
  public    = True   (everything on the public list is public)
  disabled  = False  (the API has no status field)
  managed   = None
  bounty    = minimum_bounties, ONLY if MINIMUM_BOUNTY_CURRENCY is a known comparable currency.
              The API gives no currency: it is left unconfirmed (None) on purpose, so min/max are
              null. Set the constant to "USD" (or the right currency) once confirmed on the site.
              There is no maximum in the API.

Only type seen so far: "Domain". Unknown types are kept (slugified) and logged at INFO.
"""
from __future__ import annotations

import logging
from urllib.parse import quote, urljoin

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

log = logging.getLogger("scrapers.bugzero")

PLATFORM = "bugzero"
BASE_URL = "https://us-central1-bug-zero.cloudfunctions.net"
LIST_PATH = "/api/programs"
DETAIL_PATH = "/api/organization/{handle}"

# Currency of `minimum_bounties`. UNCONFIRMED: the API does not say. Keep None until checked.
MINIMUM_BOUNTY_CURRENCY: str | None = None

# Bugzero scope type (lower case) -> reference type. Only "domain" is confirmed on real data.
TYPE_MAP = {
    "domain": "web-application",
    "web": "web-application",
    "website": "web-application",
    "web application": "web-application",
    "api": "api",
    "mobile app": "mobile-application",
    "mobile application": "mobile-application",
    "android": "mobile-application-android",
    "ios": "mobile-application-ios",
    "ip": "ip-address",
    "ip address": "ip-address",
    "source code": "source-code",
    "other": "other",
}
_seen_unknown_types: set[str] = set()


def map_type(raw: str | None, identifier: str = "") -> str:
    key = " ".join((raw or "").lower().split())
    if not key:
        return "other"
    if key == "domain" and identifier.startswith("*."):
        return "wildcard"
    if key in TYPE_MAP:
        return TYPE_MAP[key]
    if key not in _seen_unknown_types:
        _seen_unknown_types.add(key)
        log.info("Unknown Bugzero scope type %r kept as %r (add it to TYPE_MAP)", raw, slugify(key))
    return slugify(key) or "other"


def parse_scope(detail: dict) -> tuple[list[dict], list[dict]]:
    in_scope, out_scope = [], []
    for t in (detail.get("programData") or {}).get("scope") or []:
        identifier = (t.get("identifier") or "").strip()
        if not identifier:
            continue
        entry = {"target": identifier, "type": map_type(t.get("type"), identifier)}
        out = "out" in str(t.get("submission", "In Scope")).lower()  # "Out of Scope"
        (out_scope if out else in_scope).append(entry)
    return in_scope, out_scope


def minimum_bounty(item: dict) -> int | None:
    if MINIMUM_BOUNTY_CURRENCY not in COMPARABLE_CURRENCIES:
        return None
    value = item.get("minimum_bounties")
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def build_program(item: dict, detail: dict | None) -> dict:
    handle = item["handle"]
    general = (detail or {}).get("generalData") or {}
    name = (general.get("org_name") or item.get("name") or handle).strip()
    in_scope, out_scope = parse_scope(detail) if detail else ([], [])
    program = make_program(
        id=handle,
        name=name,
        public=True,
        disabled=False,
        managed=None,
        min_bounty=minimum_bounty(item),
        max_bounty=None,
        in_scope=in_scope,
        out_of_scope=out_scope,
    )
    log.debug(
        "  %s: %d in scope, %d out of scope, bug_bounty=%s",
        handle, len(program["targets"]["in_scope"]), len(program["targets"]["out_of_scope"]),
        (item.get("rewards") or {}).get("bug_bounty"),
    )
    if not program["targets"]["in_scope"]:
        log.warning("%s: no in-scope target in the API response", handle)
    return program


def crawl(limit: int | None = None, only: list[str] | None = None) -> list[dict]:
    fetcher = Fetcher(BASE_URL, delay=1.0)

    url = urljoin(BASE_URL, LIST_PATH)
    log.info("Listing: %s", url)
    items = [i for i in fetcher.get_json(url) if i.get("handle")]
    log.info("  %d programs found", len(items))

    if only:
        known = {i["handle"] for i in items}
        for handle in only:
            if handle not in known:
                log.warning("%s is not on the listing", handle)
        items = [i for i in items if i["handle"] in only]
    elif limit:
        items = items[:limit]

    log.info("Fetching %d program details", len(items))
    previous = load_previous(PLATFORM)
    results, failures = [], []
    for n, item in enumerate(items, 1):
        handle = item["handle"]
        url = urljoin(BASE_URL, DETAIL_PATH.format(handle=quote(handle, safe="")))
        progress(n, len(items), url)
        try:
            results.append(build_program(item, fetcher.get_json(url)))
        except (requests.RequestException, ValueError) as exc:
            log.warning("Failed on %s: %s", handle, exc)
            failures.append(handle)
            if handle in previous:
                results.append(previous[handle])

    if failures:
        log.warning("%d program(s) failed: %s", len(failures), ", ".join(failures[:10]))
    if items and len(failures) > max(1, 0.1 * len(items)):
        raise SuspiciousDrop(f"{len(failures)}/{len(items)} program details failed")
    return results


def main() -> None:
    run_cli(PLATFORM, crawl)


if __name__ == "__main__":
    main()
