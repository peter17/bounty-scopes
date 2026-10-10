# bounty-scopes

Scopes of **public** bug bounty programs from secondary platforms, in the format of
[arkadiyt/bounty-targets-data](https://github.com/arkadiyt/bounty-targets-data)
(schema of `yeswehack_data.json`). One JSON file per platform in `data/`, updated by a
dedicated GitHub Actions workflow, **twice a day**.

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

Types used: `web-application`, `api`, `mobile-application-ios`,
`mobile-application-android`, `smart-contract`, `protocol`, `blockchain`, `application`, `other`
(a type unknown to us is kept as a slug of the platform's own label).

## Platforms

| Platform | File | Workflow | Status |
|---|---|---|---|
| HackenProof | `data/hackenproof_data.json` | `hackenproof.yml` | implemented, to be validated on the first real run |

## Running a scraper and debugging

```bash
pip install -r requirements-dev.txt
pytest                                       # offline tests

python -m scrapers.hackenproof               # real run, writes data/hackenproof_data.json
python -m scrapers.hackenproof --verbose     # + every HTTP request (status, size, time), scope counts, retries
python -m scrapers.hackenproof --limit 5     # quick test on 5 programs (implies --dry-run)
python -m scrapers.hackenproof --dry-run     # full run, writes nothing
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
