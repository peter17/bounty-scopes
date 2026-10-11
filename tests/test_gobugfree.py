"""Offline tests. HTML is SYNTHETIC, rebuilt from the text of https://gobugfree.com/programs/bbh
(as converted by a fetch tool). The first real run remains the actual validation."""
import json
import logging

import pytest

from scrapers import common, gobugfree

BBH_HTML = """
<html><body><nav><a href="/programs">Programs</a></nav>
<h1>GObugfree</h1>
<h2>Description</h2><p>We get hackers working for you!</p>
<h2>Rules</h2><p><strong>Rules</strong></p><p>Only listed domains are in scope.</p>
<h2>Scopes</h2>
<p><strong>Not in scope</strong>:</p>
<ul>
<li>Third-party services and products such as Fidentity (ID verification) and Gmail etc.</li>
<li>id.gobugfree.com: Missing ratelimit on forms, known issues from Keycloak
    <a href="https://github.com/keycloak/keycloak/issues">Issues</a> and
    <a href="https://github.com/keycloak/keycloak/security/advisories">Security Advisories</a></li>
</ul>
<p><strong>Targets</strong>:</p>
<ul>
<li><p>Website: gobugfree.com</p></li>
<li><p>Backend: backend.gobugfree.com</p></li>
<li><p>IAM: id.gobugfree.com (only misconfigurations are in scope)</p></li>
</ul>
<h2>Procedure</h2><ol><li>Register / Login @ GObugfree</li></ol>
<h2>Bounty Levels</h2>
<table><thead><tr><th>Severity</th><th>Bounty</th></tr></thead><tbody>
<tr><td>Critical</td><td>CHF 2000-3000</td></tr><tr><td>High</td><td>CHF 1000-2000</td></tr>
<tr><td>Low</td><td>CHF 100-500</td></tr></tbody></table>
<h2>Leaderboard</h2><table><tr><td>1</td><td>simioni</td><td>145</td></tr></table>
<footer><a href="/programs/trust">ISO</a></footer>
</body></html>
"""

VDP_HTML = """
<html><body><h1>GObugfree (Vulnerability Disclosure Program)</h1>
<h2>Rules</h2><p>This is a Vulnerability Disclosure Program, no bug bounties are paid out.</p>
<h2>Scopes</h2>
<p>Not in scope: All (sub) domains and services that are not explicitly listed, are not in scope<br>
In scope::</p>
<p>\u2022 *.gobugfree.com<br>\u2022 *.gobugfree.io</p>
<h2>Bounty Levels</h2><table><tr><th>Severity</th><th>Bounty</th></tr></table>
</body></html>
"""

HIDDEN_HTML = "<html><body><h1>Raiffeisen</h1><h2>Description</h2><p>A Swiss bank.</p></body></html>"

LISTING_HTML = """
<a href="/programs/bbh"><div>GObugfree</div><span>Bug Bounty</span></a>
<a href="/programs/gobugfree-vdp"><div>GObugfree VDP</div><span>Vulnerability Disclosure Program</span></a>
<a href="/programs/raiffeisen-bb">Raiffeisen</a>
<a href="/programs/health-net">Health Net <span>Paused</span></a>
<a href="/programs/ffhs">FFHS <span>EDU Program: Only for @ffhs.ch</span></a>
<a href="/programs/some-ctf">Event <span>CTF on-site event</span></a>
<a href="/programs/trust">ISO 27001</a><a href="/programs/bbh">dup</a>
"""


def test_parse_listing_skips_non_programs_and_keeps_tags():
    cards = gobugfree.parse_listing(LISTING_HTML)
    assert list(cards) == ["bbh", "gobugfree-vdp", "raiffeisen-bb", "health-net", "ffhs", "some-ctf"]
    assert "Paused" in cards["health-net"] and "Vulnerability Disclosure" in cards["gobugfree-vdp"]


def test_parse_bounty_program():
    p = gobugfree.parse_program(BBH_HTML, "bbh", "GObugfree Bug Bounty")
    assert p["name"] == "GObugfree"
    assert (p["min_bounty"], p["max_bounty"]) == (100, 3000)
    in_t = [(e["target"], e["type"]) for e in p["targets"]["in_scope"]]
    assert in_t == [
        ("gobugfree.com", "web-application"),
        ("backend.gobugfree.com", "web-application"),
        ("id.gobugfree.com", "web-application"),
        ("IAM: id.gobugfree.com (only misconfigurations are in scope)", "other"),
    ]
    out_t = [e["target"] for e in p["targets"]["out_of_scope"]]
    assert out_t[0].startswith("Third-party services")
    # partial exclusion kept as ONE text entry, no clean "id.gobugfree.com" out of scope
    assert len(out_t) == 2 and out_t[1].startswith("id.gobugfree.com: Missing ratelimit")
    assert "Issues and Security Advisories" in out_t[1]  # link text preserved, no stray line breaks


def test_parse_vdp_program_has_no_bounty_and_br_separated_items():
    p = gobugfree.parse_program(VDP_HTML, "gobugfree-vdp", "Vulnerability Disclosure Program")
    assert (p["min_bounty"], p["max_bounty"]) == (None, None)
    assert [e["target"] for e in p["targets"]["in_scope"]] == ["*.gobugfree.com", "*.gobugfree.io"]
    assert p["targets"]["out_of_scope"][0]["target"].startswith("All (sub) domains")


def test_hidden_scope_is_kept_with_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="scrapers"):
        p = gobugfree.parse_program(HIDDEN_HTML, "raiffeisen-bb", "Raiffeisen")
    assert p["name"] == "Raiffeisen" and p["targets"] == {"in_scope": [], "out_of_scope": []}
    assert "no 'Scopes' section" in caplog.text


NETPLUS_HTML = """
<html><body><h1>netplus</h1><h2>Scopes</h2>
<p>Not in scope: All other domains and subdomains (as e.g. webmail).</p>
<p>Following endpoints are out of scope:</p>
<p>https://viamotion.netplus.ch/vod/data_nfs/archived/hospitality_video/{ID}/_/box-dash/{ID}.mpd</p>
<ul><li>my.dev.netplus.ch: Portal</li><li>dev.netplus.tv: Portal</li></ul>
<h2>Procedure</h2></body></html>
"""

PRIVATE_HTML = """
<html><body><h1>Abacus</h1><h2>Scopes</h2><p>In scope</p><ul><li>https://x.example.com/</li></ul>
<h2>Procedure</h2><ol><li>Get invited to this private bug bounty program</li></ol></body></html>
"""


def test_ambiguous_scope_is_left_empty_instead_of_publishing_wrong_exclusions(caplog):
    # netplus lists its targets after a "Not in scope" line and never writes "In scope":
    # publishing them as out of scope would be wrong, so nothing is published.
    with caplog.at_level(logging.WARNING, logger="scrapers"):
        p = gobugfree.parse_program(NETPLUS_HTML, "netplus", "netplus")
    assert p["targets"] == {"in_scope": [], "out_of_scope": []}
    assert "needs manual review" in caplog.text


def test_invite_only_program_is_not_public():
    p = gobugfree.parse_program(PRIVATE_HTML, "abacus-research", "")
    assert p["public"] is False
    assert [e["target"] for e in p["targets"]["in_scope"]] == ["https://x.example.com/"]


def test_paused_and_restricted_flags():
    paused = gobugfree.parse_program(HIDDEN_HTML, "x", "Health Net Paused")
    edu = gobugfree.parse_program(HIDDEN_HTML, "y", "FFHS EDU Program: Only for @ffhs.ch")
    assert paused["disabled"] is True and paused["public"] is True
    assert edu["public"] is False and edu["disabled"] is False


def test_schema_keys_match_reference():
    p = gobugfree.parse_program(BBH_HTML, "bbh")
    assert list(p) == ["id", "name", "public", "disabled", "managed", "min_bounty", "max_bounty", "targets"]


PAGES = {
    gobugfree.LIST_URL: LISTING_HTML,
    "https://gobugfree.com/programs/bbh": BBH_HTML,
    "https://gobugfree.com/programs/gobugfree-vdp": VDP_HTML,
    "https://gobugfree.com/programs/raiffeisen-bb": HIDDEN_HTML,
    "https://gobugfree.com/programs/health-net": HIDDEN_HTML,
    "https://gobugfree.com/programs/ffhs": HIDDEN_HTML,
}


class FakeFetcher:
    def __init__(self, *a, **k):
        pass

    def get(self, url):
        return PAGES[url]  # KeyError if the scraper requests anything unexpected (CTF, trust)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(gobugfree, "Fetcher", FakeFetcher)
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    return tmp_path


def test_crawl_skips_ctf_and_logs_progress(fake, caplog):
    with caplog.at_level(logging.INFO, logger="scrapers"):
        out = gobugfree.crawl()
    assert [p["id"] for p in out] == ["bbh", "gobugfree-vdp", "raiffeisen-bb", "health-net", "ffhs"]
    assert "[1/5] https://gobugfree.com/programs/bbh" in caplog.text
    assert "skipping some-ctf (CTF event)" in caplog.text
    ffhs = next(p for p in out if p["id"] == "ffhs")
    hn = next(p for p in out if p["id"] == "health-net")
    assert ffhs["public"] is False and hn["disabled"] is True


def test_cli_limit_is_dry_run(fake):
    common.run_cli("gobugfree", gobugfree.crawl, ["--limit", "2"])
    assert not (fake / "gobugfree_data.json").exists()


def test_cli_full_run_writes_sorted_json(fake):
    common.run_cli("gobugfree", gobugfree.crawl, [])
    data = json.loads((fake / "gobugfree_data.json").read_text())
    assert [p["id"] for p in data] == sorted(p["id"] for p in data)


# ------------------------------------------------------------------ structure regressions

ABACUS_LIKE_HTML = """
<html><body><h1>Abacus Research</h1>
<h2>Rules</h2><p>Rules text</p>
<h2>Scopes</h2>
<div class="scopes">
  <h3>In scope</h3>
  <div class="asset"><h3>Abacus-ERP</h3>
    <div>The abacus software can be launched via a client software or directly in the browser. Those are
    called different environments "Abacus-ERP" and "Abacus-ERP Browser Edition". The functionalities are
    segregated into smaller "programs" that can be started through the AbaMenu. All programs have a Letter
    + Number combination (e.g. A11, Q908, J6311 etc.) The Abacus ERP has a enormous amount of functionality
    with over 500 different "programs". To start the ULC Abacus program you need the client software
    "abaclient". It can be installed for free. https://downloads.abacus.ch/downloads/abaclient</div>
    <div>Launch URL - https://bug-bounty1-1.shop.abanet.io/</div></div>
  <div class="asset"><h3>MyAbacus Portal</h3>
    <div>Server: https://bug-bounty1-1.shop.abanet.io:443<br>Mandant: 9999</div></div>
  <ul><li>Abacus API
      <ul><li>Odata API: https://apihub.abacus.ch/odata</li></ul></li>
      <li>Not in Scope
      <div>All (sub) domains and services that are not explicitly listed, are not in scope.</div>
      <ul><li>DeepBox</li><li>DeepMail</li></ul></li></ul>
</div>
<h2>Procedure</h2><ol><li>Get invited to this private bug bounty program</li></ol>
<h2>Bounty Levels</h2><table><tr><th>Severity</th><th>Bounty</th></tr>
<tr><td>Critical</td><td>CHF 2600-5000</td></tr><tr><td>Low</td><td>CHF 50-150</td></tr></table>
</body></html>
"""

BCN_LIKE_HTML = """
<html><body><h1>Banque Cantonale (Vulnerability Disclosure Program)</h1>
<h2>Rules</h2><p>This is a Vulnerability Disclosure Program, no bug bounties are paid out.</p>
<h2>Scopes</h2>
<p>In scope:</p><ul><li>Public facing applications and systems of BCN</li></ul>
<p>If you want to report any findings, please make sure that they are not eligible for our program.</p>
<p>Not in scope:</p><ul><li>Third-party hosted systems</li></ul>
<h2>Bounty Levels</h2><table><tr><th>Severity</th><th>Bounty</th></tr></table>
</body></html>
"""


def test_h3_titles_and_divs_do_not_end_the_scope_section():
    p = gobugfree.parse_program(ABACUS_LIKE_HTML, "abacus-research", "")
    assert [e["target"] for e in p["targets"]["in_scope"]] == [
        "https://bug-bounty1-1.shop.abanet.io/",
        "https://bug-bounty1-1.shop.abanet.io:443",
        "https://apihub.abacus.ch/odata",
    ]
    out = [e["target"] for e in p["targets"]["out_of_scope"]]
    assert out[0].startswith("All (sub) domains") and "DeepBox" in out and "DeepMail" in out
    # the long paragraph's download link is prose, not a target
    assert all("abaclient" not in e["target"] for e in p["targets"]["in_scope"])
    assert p["public"] is False  # invite-only
    assert (p["min_bounty"], p["max_bounty"]) == (50, 5000)


def test_text_before_a_nested_list_is_not_lost():
    # "Not in Scope" is the own text of an <li> that also contains nested blocks.
    p = gobugfree.parse_program(ABACUS_LIKE_HTML, "x", "")
    assert not any("DeepBox" in e["target"] for e in p["targets"]["in_scope"])


def test_vdp_scope_statement_is_kept_as_other():
    p = gobugfree.parse_program(BCN_LIKE_HTML, "bcn-vdp", "Vulnerability Disclosure Program")
    assert p["targets"]["in_scope"] == [
        {"target": "Public facing applications and systems of BCN", "type": "other"}
    ]
    assert [e["target"] for e in p["targets"]["out_of_scope"]] == ["Third-party hosted systems"]
    assert (p["min_bounty"], p["max_bounty"]) == (None, None)


def test_cli_only_fetches_just_that_program(fake):
    fetched = []

    class Spy(FakeFetcher):
        def get(self, url):
            fetched.append(url)
            return super().get(url)

    import scrapers.gobugfree as g
    g.Fetcher = Spy
    try:
        common.run_cli("gobugfree", gobugfree.crawl, ["--only", "bbh"])
    finally:
        g.Fetcher = FakeFetcher
    assert fetched == [gobugfree.LIST_URL, "https://gobugfree.com/programs/bbh"]
    assert not (fake / "gobugfree_data.json").exists()


# ------------------------------------------------------------------ REAL page fixtures

def _fixture(name):
    from pathlib import Path
    return (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


def test_real_bcn_vdp_page():
    """Raw HTML of https://gobugfree.com/programs/bcn-vdp (structured asset list, no domain)."""
    p = gobugfree.parse_program(_fixture("gobugfree_bcn-vdp.html"), "bcn-vdp", "Vulnerability Disclosure Program")
    assert p["name"] == "Banque Cantonale Neuchâtel (Vulnerability Disclosure Program)"
    assert p["targets"]["in_scope"] == [
        {"target": "Public facing applications and systems of BCN", "type": "other"}
    ]
    assert p["targets"]["out_of_scope"] == []
    assert (p["min_bounty"], p["max_bounty"]) == (None, None)  # VDP
    assert p["public"] is True and p["disabled"] is False


def test_real_page_is_split_in_labelled_sections():
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(_fixture("gobugfree_bcn-vdp.html"), "html.parser")
    ids = [s["aria-labelledby"] for s in soup.find_all("section", attrs={"aria-labelledby": True})]
    assert {"description", "rules", "scopes", "legal"} <= set(ids)


def test_real_netplus_page():
    """Raw HTML of https://gobugfree.com/programs/netplus: free-text exclusions above a list of
    structured assets. The assets must stay in scope; the paragraph above must not leak onto them."""
    p = gobugfree.parse_program(_fixture("gobugfree_netplus.html"), "netplus", "")
    assert [(e["target"], e["type"]) for e in p["targets"]["in_scope"]] == [
        ("my.dev.netplus.ch", "web-application"),
        ("dev.netplus.tv", "web-application"),
        ("caius.dev.netplus.ch", "api"),
        ("my.sweez.ch", "web-application"),
    ]
    out = [e["target"] for e in p["targets"]["out_of_scope"]]
    assert out[0].startswith("All other domains and subdomains")
    assert out[1:] == [
        "https://viamotion.netplus.ch/vod/data_nfs/archived/hospitality_video/{ID}/_/box-dash/{ID}.mpd",
        "https://viamotionhsi.netplus.ch/vod/data_nfs/archived/hospitality_video/{ID}/_/box-dash/{ID}.mpd",
        "https://my.dev.netplus.ch/hsi/sms/*",  # note inside an asset description, "*" kept
    ]
    assert (p["min_bounty"], p["max_bounty"]) == (75, 6000)
    assert p["public"] is True


def test_real_netplus_no_target_is_both_in_and_out_of_scope():
    p = gobugfree.parse_program(_fixture("gobugfree_netplus.html"), "netplus", "")
    ins = {e["target"] for e in p["targets"]["in_scope"]}
    outs = {e["target"] for e in p["targets"]["out_of_scope"]}
    assert not ins & outs
