# bounty-scopes

Scopes of **public** bug bounty programs from secondary platforms, in the format of
[arkadiyt/bounty-targets-data](https://github.com/arkadiyt/bounty-targets-data)
(schema of `yeswehack_data.json`). One JSON file per platform in `data/`, updated by a
dedicated GitHub Actions workflow, **once a day**.

## Format

```json
[
  {
    "id": "program-slug",
    "name": "Name",
    "public": true,
    "disabled": false,
    "managed": null,
    "min_bounty": 100,
    "max_bounty": 1000000,
    "targets": {
      "in_scope":     [{ "target": "https://example.com", "type": "web-application" }],
      "out_of_scope": [{ "target": "blog.example.com",    "type": "web-application" }]
    }
  }
]
```

Types used: `web-application`, `wildcard`, `api`, `mobile-application`, `mobile-application-ios`,
`mobile-application-android`, `ip-address`, `source-code`, `smart-contract`, `protocol`,
`blockchain`, `application`, `other`
(a type unknown to us is kept as a slug of the platform's own label).

## Platforms

| Platform | File | Workflow | Status |
|---|---|---|---|
| HackenProof | `data/hackenproof_data.json` | `hackenproof.yml` | validated on a real run |
| GoBugFree | `data/gobugfree_data.json` | `gobugfree.yml` | validated on a real run |
| Bugbop | `data/bugbop_data.json` | `bugbop.yml` | validated on a real run |
| WhiteHub | `data/whitehub_data.json` | `whitehub.yml` | validated on a real run |
| Bugzero | `data/bugzero_data.json` | `bugzero.yml` | validated on a real run |
| CrowdSwarm | `data/crowdswarm_data.json` | `crowdswarm.yml` | implemented from real API responses, to be validated on the first real run |

## Platforms not covered

Checked in October 2026. A platform is skipped when it cannot be read automatically without
authentication, or when it asks not to be read automatically. Nothing here circumvents either.

| Platform | Why it is not covered |
|---|---|
| Yogosha, hackr.fi, SecureBug, hckrt, Zerocopter | `robots.txt` disallows automated access |
| RedStorm | blocks automated requests (bot detection) |
| bug-bounty.com, Comolho, Safehats, Hackaflag | the program pages are behind a login |
| Inspectiv, BugBounter, SignalFisher, bughunt.com.br | no program data without authentication |
| Hacktify | static page last edited in 2024: 5 programs, no structured scope (abandoned) |
| BugV | single-page app with no public program data found (abandoned) |

If a platform changes its policy (or publishes an official API), it can be added with a new
scraper: see "Adding a platform".

## Free-text scopes

Some platforms (GoBugFree, ...) have no structured scope: each customer writes its own list.
`scrapers/scopetext.py` extracts targets heuristically:

* each URL / domain / wildcard / IP found becomes one entry, with a type guessed from the target;
* `In scope` / `Targets` / `Not in scope` lines (colon optional) switch the mode;
* a line without any target is kept (as `other`) only **out of scope**, where it is an exclusion
  rule; in scope it is a title or a description and is dropped;
* lines longer than 40 words are prose and ignored (their URLs are rarely targets);
* in scope, a target with a remark (`id.example.com (only misconfigurations are in scope)`)
  yields the clean target **plus** the full line as `other`;
* out of scope, a target with a remark is a *partial* exclusion (`id.example.com: known issues
  from X`), so only the full line is kept, never a clean out-of-scope domain;
* GoBugFree pages have two zones inside `<section aria-labelledby="scopes">`: a free-text zone
  and a list of **structured assets** (`<li><div class="font-semibold">title</div>description`).
  - each asset restarts in "in scope" mode, so an exclusion paragraph above the list cannot leak
    onto the assets below it; an asset whose title is `Not in Scope` makes only *its own* lines
    out of scope; purely informational assets (`Source Code`, `Credentials`, `Documentation`...)
    are skipped;
  - "... out of scope: https://x/*" inside a line puts that URL out of scope without changing the
    mode; a lead-in ending with "out of scope:" switches to out of scope;
  - `host: API` gives type `api`; `<pre>` blocks keep their line breaks;
  - when no target is found, the short asset titles become `other` entries (VDP whose scope is a
    statement);
* exception: if no target at all was found in scope, short statements under an explicit
  `In scope` header are kept as `other` (a VDP whose scope is "public facing systems of X");
* if no in-scope target can be identified (e.g. targets listed after a `Not in scope` line with
  no `In scope` header), the program is published with an **empty scope** and a
  `WARNING ... needs manual review`: no data is better than wrong exclusions.

### Bugbop

Bugbop has no structured scope. The program's main URL (shown under its name) is the in-scope
asset. The description is prose, so its URLs are **not** read as targets unless they sit under an
explicit heading (`Scope`, `In scope`, `Targets`, `Out of scope`...) or after an explicit
`In scope:` / `Not in scope:` line. Sections such as `Exclusions` list vulnerability classes
(DoS, spam), not assets, and are ignored. `Restricted` programs get `public: false`, programs with
`Bounty: Paused` get `disabled: true` (their amounts then come from the Rewards table).

### WhiteHub

WhiteHub is a SPA over a public JSON API (`api.cystack.net`), so there is no scraping of HTML:
the list gives the programs, the per-program endpoint gives a **structured** `target[]` list
(name, type, scope). The markdown description is prose and is not used. `visibility != public`
gives `public: false` (such programs are unreadable and have no scope), `status: locked` gives
`disabled: true`, `is_wh_managed` gives `managed`. Most programs pay in VND, so their bounty is
`null` (see below). Target types other than `source_code` have not been seen yet: unknown ones are
kept (slugified) and logged at INFO as `Unknown WhiteHub target type ...`.

### Bugzero

Bugzero is a SPA over public JSON endpoints (Cloud Functions): `/api/programs` lists the programs
and `/api/organization/<handle>` gives a **structured** `programData.scope[]` (`type`,
`identifier`, `submission`: "In Scope" / "Out of Scope"). The `policy` markdown is a generic
template and is not read. The API gives a `minimum_bounties` but no currency and no maximum:
the currency is USD (confirmed on the platform, `MINIMUM_BOUNTY_CURRENCY` in
`scrapers/bugzero.py`) and `max_bounty` is always `null`. The API has no status field either, so
`disabled` is always `false`. Only the `Domain` type has been seen; unknown types are kept and logged at INFO.

### CrowdSwarm

CrowdSwarm is a SPA over a public JSON API that uses **POST** for its read-only queries
(`/api/v1.0/public-program/get-all` with `{"take": 10, "skip": N}`). Every program in the list
already carries its full markdown `details`, so no per-program request is made. The scope is read
from the markdown headings: `### Scope`, `### Scope Platform`, `### Scope IP Address`... are in
scope (short bullets without a URL are kept as `other`), `Out of scope` headings are out of scope,
and everything else (rules, `NOT ALLOWED` activities, `Non-qualifying Vulnerabilities`, legal
terms) is ignored. The API gives `minReward` / `maxReward` but no currency: it is USD (confirmed on
the platform, `REWARD_CURRENCY` in `scrapers/crowdswarm.py`). The `comments` field is **never read**:
it is written by anonymous users and contains raw XSS payloads.

Bounty amounts are kept only when the currency is USD, EUR, CHF or GBP (the format has no
currency field); anything else, or mixed currencies, gives `null`.

## Running a scraper and debugging

```bash
pip install -r requirements-dev.txt
pytest                                       # offline tests

python -m scrapers.<platform>               # real run, writes data/<platform>_data.json
python -m scrapers.<platform> --verbose     # + every HTTP request (status, size, time), scope counts, retries
python -m scrapers.<platform> --limit 5     # quick test on 5 programs (implies --dry-run)
python -m scrapers.<platform> --only SLUG    # a single program (repeatable). Implies --dry-run.
python -m scrapers.<platform> --dry-run     # full run, writes nothing
python -m scrapers.<platform> --only SLUG --dump-html /tmp/dump   # also save the raw HTML of each page
```

Progress is always printed to stderr, one line per page, before the request is made, so a
hang is immediately visible:

```
20:44:53 INFO    Fetching 324 program pages
20:44:53 INFO    [1/324] https://hackenproof.com/programs/near-protocol
20:44:54 INFO    [2/324] https://hackenproof.com/programs/okx
...
20:51:02 INFO    [hackenproof] done in 409s: 324 programs, 2311 in-scope targets, 540 out-of-scope targets (file updated)
```

When a request is retried (HTTP 429/5xx), a `WARNING Retrying ...` line says so, otherwise a
backoff looks like a frozen process. In GitHub Actions, run the workflow manually
(**Run workflow**) and tick **debug** to get the verbose output.

Exit codes: `0` success, `1` failure (robots.txt disallowed, suspicious result, unexpected
error), `130` interrupted.

## Good citizenship

* `robots.txt` is read and honoured; a platform that forbids automated access is not scraped.
* Identifiable User-Agent, 1 request/second, retries with backoff.
* **Safety net**: if a run returns 0 programs or fewer than 50 % of the previous run, the
  existing file is not overwritten and the workflow fails (visible in the Actions tab).
  A page that fails keeps its previous entry, so a flaky page does not create diff noise.

## Adding a platform

Create `scrapers/<name>.py` with a `crawl(limit=None)` function returning a list of programs
built with `make_program(...)`, use `progress(i, total, url)` before each request, and end
with `run_cli("<name>", crawl)`. Then copy `.github/workflows/hackenproof.yml`, changing the
name, the module and the cron minute.

## GitHub note

On a public repository with no activity for 60 days, GitHub disables scheduled workflows.
Data commits count as activity as long as the scrapers keep working.
