"""Code shared by all platform scrapers.

Output format (identical to `yeswehack_data.json` from arkadiyt/bounty-targets-data):

[
  {
    "id": str, "name": str, "public": bool, "disabled": bool, "managed": null,
    "min_bounty": int|null, "max_bounty": int|null,
    "targets": {
      "in_scope":     [{"target": str, "type": str}, ...],
      "out_of_scope": [{"target": str, "type": str}, ...]
    }
  }
]

Every scraper exposes `crawl(limit=None, only=None) -> list[dict]` and ends with
`run_cli("<platform>", crawl)`, which gives it the common command line:

    python -m scrapers.<platform> [--verbose] [--limit N] [--only SLUG] [--dry-run] [--dump-html DIR]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.robotparser
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("scrapers")

# When set (--dump-html DIR), every fetched page is saved there, raw, for debugging.
DUMP_DIR: Path | None = None

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Identify yourself properly: set SCRAPER_USER_AGENT or edit the default below.
USER_AGENT = os.environ.get(
    "SCRAPER_USER_AGENT",
    "bounty-scopes-bot/0.1 (+https://github.com/OWNER/REPO)",
)

# If the number of programs drops below this ratio compared to the current file,
# the scraper is considered broken and the existing data is left untouched.
MIN_RATIO_VS_PREVIOUS = 0.5


class RobotsDisallowed(RuntimeError):
    pass


class SuspiciousDrop(RuntimeError):
    pass


# --------------------------------------------------------------------------- logging


_handler: logging.Handler | None = None


def setup_logging(verbose: bool = False) -> None:
    """Log to stderr (unbuffered, so progress shows up live, also in GitHub Actions).

    Only the "scrapers" logger is configured; the root logger is left alone.
    """
    global _handler
    if _handler is not None:
        log.removeHandler(_handler)
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    )
    log.addHandler(_handler)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)


def progress(index: int, total: int, url: str) -> None:
    """Standard progress line, e.g. `[12/324] https://example.com/programs/foo`."""
    log.info("[%d/%d] %s", index, total, url)


class LoggingRetry(Retry):
    """Retry policy that tells you when it is retrying (otherwise a 429/5xx backoff
    looks exactly like a hung process)."""

    def increment(self, method=None, url=None, response=None, error=None, _pool=None, _stacktrace=None):
        new = super().increment(method, url, response, error, _pool, _stacktrace)
        status = getattr(response, "status", None)
        reason = f"HTTP {status}" if status else repr(error)
        log.warning(
            "Retrying %s %s (%s), %s attempt(s) left, waiting %.0fs",
            method, url, reason, new.total, new.get_backoff_time(),
        )
        return new


# --------------------------------------------------------------------------- HTTP


class Fetcher:
    """Polite HTTP client: identifiable User-Agent, robots.txt honoured,
    delay between requests, retries with backoff on 429/5xx."""

    def __init__(self, base_url: str, delay: float = 1.0, timeout: float = 30.0, backoff: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.delay = delay
        self.timeout = timeout
        self._last = 0.0

        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        retry = LoggingRetry(
            total=4,
            backoff_factor=backoff,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))

        self.robots = urllib.robotparser.RobotFileParser()
        self._load_robots()

    def _load_robots(self) -> None:
        url = f"{self.base_url}/robots.txt"
        log.debug("Fetching %s", url)
        resp = self.session.get(url, timeout=self.timeout)
        if resp.status_code == 200:
            self.robots.parse(resp.text.splitlines())
            log.debug("robots.txt loaded (%d bytes)", len(resp.content))
        elif resp.status_code in (401, 403):
            raise RobotsDisallowed(f"robots.txt is not accessible ({resp.status_code})")
        else:  # 404 and the like: no restrictions declared
            self.robots.parse([])
            log.debug("robots.txt returned %s: no restrictions assumed", resp.status_code)

    def get(self, url: str) -> str:
        if not self.robots.can_fetch(USER_AGENT, url):
            raise RobotsDisallowed(f"robots.txt disallows: {url}")
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        started = time.monotonic()
        resp = self.session.get(url, timeout=self.timeout)
        self._last = time.monotonic()
        log.debug(
            "GET %s -> %s, %.1f KB, %.2fs",
            url, resp.status_code, len(resp.content) / 1024, self._last - started,
        )
        if DUMP_DIR is not None:
            DUMP_DIR.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", re.sub(r"^https?://", "", url))[:150] or "index"
            (DUMP_DIR / f"{name}.html").write_text(resp.text, encoding="utf-8")
            log.debug("Saved raw HTML to %s", DUMP_DIR / f"{name}.html")
        resp.raise_for_status()
        return resp.text


# --------------------------------------------------------------------------- helpers


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def parse_money(text: str | None) -> int | None:
    """'$1,000,000' -> 1000000; None if it cannot be parsed."""
    if not text:
        return None
    digits = re.sub(r"[^\d.]", "", text)
    if not digits:
        return None
    try:
        return int(float(digits))
    except ValueError:
        return None


# The reference format has no currency field, so amounts are only kept when they are in a
# currency of comparable magnitude; anything else (VND, JPY, mixed...) becomes null.
COMPARABLE_CURRENCIES = {"USD", "EUR", "CHF", "GBP"}
_SYMBOLS = {"$": "USD", "\u20ac": "EUR", "\u00a3": "GBP"}
_AMOUNT_RE = re.compile(
    r"(?P<cur>[A-Z]{3}|[$\u20ac\u00a3])?\s*(?P<a>\d[\d',]*(?:\.\d+)?)"
    r"(?:\s*[-\u2013\u2014]\s*(?P<cur2>[A-Z]{3}|[$\u20ac\u00a3])?\s*(?P<b>\d[\d',]*(?:\.\d+)?))?"
)


def parse_bounty_range(cells: list[str]) -> tuple[int | None, int | None]:
    """(min, max) over cells such as "CHF 2000-3000" or "$100 - $1,000".

    Returns (None, None) if a cell has no currency, currencies are mixed, or the
    currency is not in COMPARABLE_CURRENCIES.
    """
    lows: list[int] = []
    highs: list[int] = []
    currencies: set[str] = set()
    for text in cells:
        m = _AMOUNT_RE.search(text or "")
        if not m:
            continue
        cur = m.group("cur") or m.group("cur2")
        cur = _SYMBOLS.get(cur, cur)
        if not cur:
            return None, None
        currencies.add(cur)
        low = parse_money(m.group("a"))
        high = parse_money(m.group("b")) if m.group("b") else low
        if low is None or high is None:
            continue
        lows.append(low)
        highs.append(high)
    if not lows or len(currencies) != 1 or not currencies <= COMPARABLE_CURRENCIES:
        return None, None
    return min(lows), max(highs)


def make_program(
    *,
    id: str,
    name: str,
    public: bool = True,
    disabled: bool = False,
    managed: bool | None = None,
    min_bounty: int | None = None,
    max_bounty: int | None = None,
    in_scope: list[dict] | None = None,
    out_of_scope: list[dict] | None = None,
) -> dict:
    def clean(items: list[dict] | None) -> list[dict]:
        seen, out = set(), []
        for it in items or []:
            target = (it.get("target") or "").strip()
            typ = (it.get("type") or "other").strip() or "other"
            if not target or (target, typ) in seen:
                continue
            seen.add((target, typ))
            out.append({"target": target, "type": typ})
        return out

    return {
        "id": id,
        "name": name,
        "public": public,
        "disabled": disabled,
        "managed": managed,
        "min_bounty": min_bounty,
        "max_bounty": max_bounty,
        "targets": {
            "in_scope": clean(in_scope),
            "out_of_scope": clean(out_of_scope),
        },
    }


def load_previous(platform: str) -> dict[str, dict]:
    """Programs from the previous run, indexed by id (empty if there is no file)."""
    path = DATA_DIR / f"{platform}_data.json"
    if not path.exists():
        return {}
    try:
        return {p["id"]: p for p in json.loads(path.read_text(encoding="utf-8"))}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def write_platform_data(platform: str, programs: list[dict]) -> bool:
    """Write data/<platform>_data.json. Returns True if the content changed.

    Safety nets: refuses to overwrite the existing file if the result is empty or
    drops sharply (the scraper is probably broken by a change on the site).
    """
    path = DATA_DIR / f"{platform}_data.json"
    programs = sorted(programs, key=lambda p: p["id"])

    if not programs:
        raise SuspiciousDrop("0 programs scraped: keeping the previous file.")

    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = []
        if len(previous) >= 10 and len(programs) < MIN_RATIO_VS_PREVIOUS * len(previous):
            raise SuspiciousDrop(
                f"{len(programs)} programs vs {len(previous)} previously "
                f"(< {MIN_RATIO_VS_PREVIOUS:.0%}): keeping the previous file."
            )

    payload = json.dumps(programs, indent=2, ensure_ascii=False) + "\n"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == payload:
        return False
    path.write_text(payload, encoding="utf-8")
    return True


# --------------------------------------------------------------------------- CLI


def parse_args(platform: str, argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=f"python -m scrapers.{platform}",
        description=f"Scrape public {platform} bug bounty programs into data/{platform}_data.json.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="debug output: every HTTP request (status, size, time), per-program scope counts, retries",
    )
    parser.add_argument(
        "--limit", type=int, metavar="N",
        help="only process the first N programs (quick test). Implies --dry-run.",
    )
    parser.add_argument(
        "--only", action="append", metavar="SLUG",
        help="only process this program (repeatable). Implies --dry-run.",
    )
    parser.add_argument(
        "--dump-html", metavar="DIR",
        help="save the raw HTML of every fetched page in DIR (to debug or send as a fixture)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="run everything but do not write anything to data/",
    )
    return parser.parse_args(argv)


def run_cli(platform: str, crawl, argv: list[str] | None = None) -> None:
    args = parse_args(platform, argv)
    setup_logging(args.verbose)
    # Never overwrite data with a partial run.
    dry_run = args.dry_run or args.limit is not None or bool(args.only)
    global DUMP_DIR
    DUMP_DIR = Path(args.dump_html) if args.dump_html else None
    started = time.monotonic()

    log.info("[%s] starting%s%s%s", platform,
             f" (limit={args.limit})" if args.limit else "",
             f" (only={','.join(args.only)})" if args.only else "",
             " (dry run: nothing will be written)" if dry_run else "")
    try:
        programs = crawl(limit=args.limit, only=args.only)
        if dry_run:
            outcome = "dry run, nothing written"
        else:
            outcome = "file updated" if write_platform_data(platform, programs) else "file unchanged"
    except (RobotsDisallowed, SuspiciousDrop) as exc:
        log.error("[%s] %s", platform, exc)
        sys.exit(1)
    except KeyboardInterrupt:
        log.error("[%s] interrupted", platform)
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001 - last-resort handler for a CLI
        if args.verbose:
            log.exception("[%s] unexpected error", platform)
        else:
            log.error("[%s] unexpected error: %s: %s (re-run with --verbose for the traceback)",
                      platform, type(exc).__name__, exc)
        sys.exit(1)

    in_scope = sum(len(p["targets"]["in_scope"]) for p in programs)
    out_scope = sum(len(p["targets"]["out_of_scope"]) for p in programs)
    log.info("[%s] done in %.0fs: %d programs, %d in-scope targets, %d out-of-scope targets (%s)",
             platform, time.monotonic() - started, len(programs), in_scope, out_scope, outcome)
    if dry_run and args.verbose and programs:
        log.debug("First program:\n%s", json.dumps(programs[0], indent=2, ensure_ascii=False))
