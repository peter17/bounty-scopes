"""Offline tests. HTML is SYNTHETIC, rebuilt from the text of https://bugbop.com/public-programs
and https://bugbop.com/programs/<uuid> as converted by a fetch tool (not raw HTML). Validate with
`python -m scrapers.bugbop --dump-html DIR` on the real site."""
import json
import logging

import pytest

from scrapers import bugbop, common

GLEAM = "419648da-8643-4f02-8c86-d63bc500867c"
LEDA = "7cd5f471-e6ea-4700-ba1f-ab591b9c2595"
FLEET = "b5f2f20e-fe4d-466b-a474-6db65b4d2bb3"
SYNC = "d5bd18a3-fd0e-45d6-8eb8-99ebb3c6222d"


def card(pid, name, url, access, bounty, paid="None"):
    return f"""<li><a href="https://bugbop.com/programs/{pid}"><img src="logo.png"></a>
    <h2><a href="https://bugbop.com/programs/{pid}">{name}</a></h2>
    <a href="{url}">{url}</a><div>{access}</div><div>Bounty: {bounty}</div>
    <div>Bounties paid: {paid}</div></li>"""


LISTING_HTML = (
    "<html><body><nav><a href='/public-programs'>Public Programs</a></nav><ol>"
    + card(GLEAM, "Gleam.io's Bugbop program", "https://gleam.io", "Public", "$0 - $1200", "42 (avg: $186)")
    + card(LEDA, "Leda Bug Bounty Program", "https://getleda.com", "Restricted", "Paused", "12 (avg: $41)")
    + card(FLEET, "Fleet vulnerability disclosure program", "https://github.com/fleetdm/fleet", "Public",
           "Swag or Store Credit")
    + card(SYNC, "SyncEzy Bug Bounty Program", "https://SyncEzy.com", "Public", "$0 - $100", "10 (avg: $47)")
    + "</ol><footer><a href='/terms'>Terms</a></footer></body></html>"
)

GLEAM_HTML = """
<html><head><title>Gleam.io's Bugbop program</title></head><body>
<h2>Program Description &amp; Scope</h2>
<div class="prose"><p>No technology is perfect. If you found an issue, tell us.</p>
<h1>Disclosure Policy</h1><ul><li>Let us know as soon as possible.</li></ul>
<h1>Exclusions</h1><p>While researching, we'd like to ask you to refrain from:</p>
<ul><li>Denial of service</li><li>Spamming</li>
<li>When raising XSS, check that the domain is able to attack our main domains. We use preview.gleam.io to prevent custom user HTML performing XSS.</li></ul>
<h1>Reward guidelines</h1><ol><li>Interesting - $50-300: E.g. we configured our system badly</li></ol></div>
<h2>Rewards</h2>
<table><thead><tr><th>Severity</th><th>Bounty Range</th></tr></thead><tbody>
<tr><td>Critical</td><td>$600 - $1200</td></tr><tr><td>High</td><td>$300 - $600</td></tr>
<tr><td>Medium</td><td>$50 - $300</td></tr><tr><td>Low</td><td>$0</td></tr></tbody></table>
<h3>About</h3><ul><li><a href="/about">About</a></li></ul>
</body></html>
"""

SCOPED_HTML = """
<html><head><title>Acme</title></head><body>
<h2>Program Description &amp; Scope</h2>
<div><p>Welcome. Our docs live at https://docs.acme.com for reference.</p>
<h1>Scope</h1><ul><li>app.acme.com</li><li>*.api.acme.com</li><li>https://github.com/acme/cli</li></ul>
<h1>Out of Scope</h1><ul><li>blog.acme.com</li><li>Third-party services</li></ul>
<h1>Rules</h1><p>In scope: status.acme.com</p><p>Not in scope: legacy.acme.com</p>
<h1>Exclusions</h1><ul><li>Denial of service</li><li>staging.acme.com</li></ul></div>
<h2>Rewards</h2><table><tr><td>Critical</td><td>$500</td></tr></table>
</body></html>
"""

LOGIN_WALL_HTML = "<html><head><title>Log in | Bugbop</title></head><body><h1>Log in</h1></body></html>"


def targets(p, key="in_scope"):
    return [(e["target"], e["type"]) for e in p["targets"][key]]


# ------------------------------------------------------------------ listing


def test_parse_listing_cards():
    cards = bugbop.parse_listing(LISTING_HTML)
    assert list(cards) == [GLEAM, LEDA, FLEET, SYNC]
    assert cards[GLEAM] == {"name": "Gleam.io's Bugbop program", "visibility": "Public",
                            "bounty_text": "$0 - $1200", "url": "https://gleam.io"}
    assert cards[LEDA]["visibility"] == "Restricted" and cards[LEDA]["bounty_text"] == "Paused"
    assert cards[FLEET]["bounty_text"] == "Swag or Store Credit"
    assert cards[FLEET]["url"] == "https://github.com/fleetdm/fleet"


def test_parse_listing_ignores_logo_links_and_nav():
    assert len(bugbop.parse_listing(LISTING_HTML)) == 4


# ------------------------------------------------------------------ detail


def test_prose_urls_are_not_targets_only_main_url_is():
    c = bugbop.parse_listing(LISTING_HTML)[GLEAM]
    p = bugbop.parse_program(GLEAM_HTML, GLEAM, c)
    assert targets(p) == [("https://gleam.io", "web-application")]  # NOT preview.gleam.io
    assert p["targets"]["out_of_scope"] == []  # "Exclusions" = vulnerability classes, ignored
    assert (p["min_bounty"], p["max_bounty"]) == (0, 1200)
    assert p["public"] is True and p["disabled"] is False and p["managed"] is None
    assert p["name"] == "Gleam.io's Bugbop program" and p["id"] == GLEAM


def test_paused_program_takes_bounty_from_rewards_table_and_is_disabled_and_restricted():
    c = bugbop.parse_listing(LISTING_HTML)[LEDA]
    p = bugbop.parse_program(GLEAM_HTML, LEDA, c)
    assert p["disabled"] is True and p["public"] is False
    assert (p["min_bounty"], p["max_bounty"]) == (0, 1200)


def test_swag_program_has_no_bounty_and_github_url_is_other():
    c = bugbop.parse_listing(LISTING_HTML)[FLEET]
    p = bugbop.parse_program(LOGIN_WALL_HTML, FLEET, c)
    assert (p["min_bounty"], p["max_bounty"]) == (None, None)
    assert targets(p) == [("https://github.com/fleetdm/fleet", "other")]


def test_explicit_scope_headings_are_read():
    p = bugbop.parse_program(SCOPED_HTML, "acme", {"name": "Acme", "url": "https://acme.com",
                                                   "visibility": "Public", "bounty_text": "$0 - $500"})
    assert targets(p) == [
        ("https://acme.com", "web-application"),
        ("app.acme.com", "web-application"),
        ("*.api.acme.com", "wildcard"),   # "Scope" section
        ("https://github.com/acme/cli", "other"),
        ("status.acme.com", "web-application"),  # explicit "In scope:" line under an unknown heading
    ]
    assert [e["target"] for e in p["targets"]["out_of_scope"]] == [
        "blog.acme.com", "Third-party services", "legacy.acme.com"]  # NOT staging.acme.com (Exclusions) nor docs.acme.com (intro)
    assert all("docs.acme.com" not in t for t, _ in targets(p))


def test_login_wall_keeps_main_url_and_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="scrapers"):
        p = bugbop.parse_program(LOGIN_WALL_HTML, LEDA, bugbop.parse_listing(LISTING_HTML)[LEDA])
    assert targets(p) == [("https://getleda.com", "web-application")]
    assert "no 'Program Description' section" in caplog.text


def test_schema_keys_match_reference():
    p = bugbop.parse_program(GLEAM_HTML, GLEAM)
    assert list(p) == ["id", "name", "public", "disabled", "managed", "min_bounty", "max_bounty", "targets"]


# ------------------------------------------------------------------ crawl / CLI

PAGES = {
    bugbop.LIST_URL: LISTING_HTML,
    f"{bugbop.LIST_URL}?page=2": LISTING_HTML,  # parameter ignored => stop
    **{f"https://bugbop.com/programs/{pid}": GLEAM_HTML for pid in (GLEAM, LEDA, FLEET, SYNC)},
}


class FakeFetcher:
    requested: list[str] = []

    def __init__(self, *a, **k):
        pass

    def get(self, url):
        FakeFetcher.requested.append(url)
        return PAGES[url]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    FakeFetcher.requested = []
    monkeypatch.setattr(bugbop, "Fetcher", FakeFetcher)
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    return tmp_path


def test_crawl_progress_and_results(fake, caplog):
    with caplog.at_level(logging.INFO, logger="scrapers"):
        out = bugbop.crawl()
    assert [p["id"] for p in out] == [GLEAM, LEDA, FLEET, SYNC]
    assert f"[1/4] https://bugbop.com/programs/{GLEAM}" in caplog.text
    assert f"[4/4] https://bugbop.com/programs/{SYNC}" in caplog.text


def test_cli_only_and_limit_are_dry_runs(fake):
    common.run_cli("bugbop", bugbop.crawl, ["--only", LEDA])
    assert not (fake / "bugbop_data.json").exists()
    common.run_cli("bugbop", bugbop.crawl, ["--limit", "2"])
    assert not (fake / "bugbop_data.json").exists()


def test_cli_full_run_writes_sorted_json(fake):
    common.run_cli("bugbop", bugbop.crawl, [])
    data = json.loads((fake / "bugbop_data.json").read_text())
    assert [p["id"] for p in data] == sorted([GLEAM, LEDA, FLEET, SYNC])
