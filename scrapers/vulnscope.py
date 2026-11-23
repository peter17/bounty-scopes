"""VulnScope - https://www.vulnscope.com

Reconnaissance findings (2026-11-23): the site is backed by a public JSON API
(api.vulnscope.com, robots.txt allows everything).
  * GET /api/v1/programs/publics?page=N&page_size=M
      -> JSON array of programs (name, slug, uuid, is_public, reward_model, rewards[], ...).
         Response headers `count`, `page`, `page_size` give the total and the pagination.
  * GET /api/v1/programs/publics/<slug>
      -> the same program with `description` (markdown, prose), `targets[]` and `rewards[]`.

Mapping:
  id        = slug
  name      = name
  public    = is_public
  disabled  = False (no status field), managed = None
  scope     = targets[]: each target is one line, in `description` (`url` is null in practice;
              it is used if set). There is no out-of-scope list: the markdown `description` is
              prose ("everything not listed is out of scope") and is NOT read, which also means
              the test credentials some programs publish there are never stored.
  bounty    = min / max of the strictly positive `rewards[].amount` (programs paying in points
              only have 0 everywhere: null). The API gives no currency: USD is ASSUMED
              (REWARD_CURRENCY, not confirmed on the platform; set it to None to drop amounts).
"""
from __future__ import annotations

import logging
import re

import requests

from .common import (
    COMPARABLE_CURRENCIES,
    Fetcher,
    SuspiciousDrop,
    load_previous,
    make_program,
    progress,
    run_cli,
)
from .scopetext import extract_tokens, guess_type

log = logging.getLogger("scrapers.vulnscope")

PLATFORM = "vulnscope"
BASE_URL = "https://api.vulnscope.com"
LIST_URL = f"{BASE_URL}/api/v1/programs/publics"
PAGE_SIZE = 100
MAX_PAGES = 50  # safety net against endless pagination

# The API does not say which currency `rewards[].amount` is in. ASSUMED, not confirmed.
REWARD_CURRENCY: str | None = "USD"


def reward_range(rewards) -> tuple[int | None, int | None]:
    if REWARD_CURRENCY not in COMPARABLE_CURRENCIES:
        return None, None
    amounts = [
        int(r["amount"]) for r in rewards or []
        if isinstance(r, dict) and isinstance(r.get("amount"), (int, float))
        and not isinstance(r["amount"], bool) and r["amount"] > 0
    ]
    return (min(amounts), max(amounts)) if amounts else (None, None)


def parse_targets(targets) -> list[dict]:
    out: list[dict] = []
    for t in targets or []:
        text = " ".join(str(t.get("url") or t.get("description") or "").split())
        if not text:
            continue
        tokens = extract_tokens(text)
        if tokens:
            for tok in tokens:
                if not tok.lower().startswith("http"):
                    tok = tok.rstrip("/")  # "example.net/" -> "example.net"
                out.append({"target": tok, "type": guess_type(tok)})
        elif len(text.split()) <= 12:
            out.append({"target": text, "type": "other"})
    return out


def build_program(item: dict) -> dict:
    # Only whitelisted fields are read; `description` (markdown prose) never is.
    slug = item["slug"]
    min_b, max_b = reward_range(item.get("rewards"))
    program = make_program(
        id=slug,
        name=(item.get("name") or slug).strip(),
        public=bool(item.get("is_public", True)),
        disabled=False,
        managed=None,
        min_bounty=min_b,
        max_bounty=max_b,
        in_scope=parse_targets(item.get("targets")),
    )
    log.debug(
        "  %s: %d in scope, bounty %s-%s", slug, len(program["targets"]["in_scope"]), min_b, max_b
    )
    if not program["targets"]["in_scope"]:
        log.warning("%s: no target found", slug)
    return program


def list_programs(fetcher: Fetcher) -> list[dict]:
    items: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        url = f"{LIST_URL}?page={page}&page_size={PAGE_SIZE}"
        log.info("Page %d: %s", page, url)
        batch = fetcher.get_json(url)
        if not isinstance(batch, list) or not batch:
            break
        items += batch
        log.info("  %d programs so far", len(items))
        if len(batch) < PAGE_SIZE:
            break
    seen: set[str] = set()
    return [i for i in items if i.get("slug") and not (i["slug"] in seen or seen.add(i["slug"]))]


def crawl(limit: int | None = None, only: list[str] | None = None) -> list[dict]:
    fetcher = Fetcher(BASE_URL, delay=1.0)
    items = list_programs(fetcher)
    if only:
        known = {i["slug"] for i in items}
        for slug in only:
            if slug not in known:
                log.warning("%s is not on the listing", slug)
        items = [i for i in items if i["slug"] in only]
    elif limit:
        items = items[:limit]

    log.info("Fetching %d program details", len(items))
    previous = load_previous(PLATFORM)
    results, failures = [], []
    for i, item in enumerate(items, 1):
        slug = item["slug"]
        url = f"{LIST_URL}/{slug}"
        progress(i, len(items), url)
        try:
            results.append(build_program({**item, **fetcher.get_json(url)}))
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            log.warning("Failed on %s: %s", slug, exc)
            failures.append(slug)
            if slug in previous:
                results.append(previous[slug])
    if failures:
        log.warning("%d program(s) failed: %s", len(failures), ", ".join(failures[:10]))
    if items and len(failures) > max(1, 0.1 * len(items)):
        raise SuspiciousDrop(f"{len(failures)}/{len(items)} programs failed")
    return results


def main() -> None:
    run_cli(PLATFORM, crawl)


if __name__ == "__main__":
    main()
