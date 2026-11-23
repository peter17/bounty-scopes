"""Offline tests on SYNTHETIC pages that reproduce the data-* hooks of
https://app.gobugfree.com/public/engagements/<org>/<slug> (checked 2026-11-23)."""
import json
import logging
from pathlib import Path

import pytest

from scrapers import common, gobugfree


def _page(asset_html="", tags=("Bug Bounty", "Public"), badge="Active", bounty_cells=()):
    """Minimal page with the same data-* hooks as the real ones."""
    tag_html = "".join(f'<span class="gbf-program-hero-tag">{t}</span> ' for t in tags)
    rows = "".join(f'<tr><td>x</td><td>y</td><td>{c}</td></tr>' for c in bounty_cells)
    table = f'<div data-bounty-table="CHF"><table><tbody>{rows}</tbody></table></div>' if rows else ""
    return (
        f'<h1 class="sr-only">Prog</h1><h2 class="gbf-program-hero-title">Prog</h2>'
        f'<span class="gbf-badge">{badge}</span>{tag_html}'
        f'<section data-program-scope><div>{asset_html}</div></section>{table}'
    )


def _asset(title, desc="", tag="Web"):
    return (
        f'<div data-scope-asset="{title}"><div><span class="gbf-tag">{tag}</span> <span>{title}</span></div>'
        f'<div data-scope-asset-description><div data-prose>{desc}</div></div></div>'
    )


# ------------------------------------------------------------------ listing

def test_parse_listing_separates_public_and_login_only_programs():
    html = (
        f'<a href="{APP}/abacus/abacus-research">A</a><a href="{APP}/abacus/abacus-research">dup</a>'
        f'<a href="/login?redirectTo={APP}/ffhs-org/ffhs">F</a><a href="/trust">ISO</a>'
        f'<a href="https://other.example/public/engagements/x/y">other host</a>'
    )
    public, private = gobugfree.parse_listing(html)
    assert public == {"abacus-research": f"{APP}/abacus/abacus-research"}
    assert private == ["ffhs"]


# ------------------------------------------------------------------ typical pages

def test_bounty_program_like_abacus():
    html = _page(
        _asset("Abacus-ERP", "<p>Launch URL - https://erp.example.com/</p>")
        + _asset("Abacus API", "<p><strong>Odata API</strong></p><p>https://apihub.example.com/odata</p>")
        + _asset("Not in Scope", "<p>All (sub) domains and services that are not explicitly listed, are not in scope.</p>"),
        bounty_cells=("CHF 2,600 \u2013 5,000", "CHF 50 \u2013 150"),
    )
    p = gobugfree.parse_program(html, "abacus-research")
    assert p["name"] == "Prog" and p["public"] is True and p["disabled"] is False
    assert (p["min_bounty"], p["max_bounty"]) == (50, 5000)
    assert [(e["target"], e["type"]) for e in p["targets"]["in_scope"]] == [
        ("https://erp.example.com/", "web-application"),
        ("https://apihub.example.com/odata", "api"),
    ]
    out = [e["target"] for e in p["targets"]["out_of_scope"]]
    assert len(out) == 1 and out[0].startswith("All (sub) domains")


def test_remark_in_title_keeps_target_and_full_line():
    p = gobugfree.parse_program(_page(_asset("IAM: id.example.com (only misconfigurations are in scope)")), "x")
    assert [(e["target"], e["type"]) for e in p["targets"]["in_scope"]] == [
        ("id.example.com", "web-application"),
        ("IAM: id.example.com (only misconfigurations are in scope)", "other"),
    ]


def test_vdp_without_domain_keeps_the_statement():
    html = _page(_asset("Public facing applications and systems of BCN"), tags=("VDP", "Public"))
    p = gobugfree.parse_program(html, "bcn-vdp")
    assert p["targets"]["in_scope"] == [
        {"target": "Public facing applications and systems of BCN", "type": "other"}
    ]
    assert (p["min_bounty"], p["max_bounty"]) == (None, None)


def test_mobile_and_api_assets():
    html = _page(
        _asset("socket.example.ch") + _asset("api.example.ch", tag="API")
        + _asset("Android App", tag="Mobile") + _asset("iOS App", tag="Mobile")
    )
    p = gobugfree.parse_program(html, "x")
    assert {(e["target"], e["type"]) for e in p["targets"]["in_scope"]} == {
        ("socket.example.ch", "web-application"),
        ("api.example.ch", "api"),
        ("Android App", "mobile-application-android"),
        ("iOS App", "mobile-application-ios"),
    }


def test_schema_keys_match_reference():
    p = gobugfree.parse_program(_page(_asset("a.example.com")), "x")
    assert list(p) == ["id", "name", "public", "disabled", "managed", "min_bounty", "max_bounty", "targets"]


# ------------------------------------------------------------------ synthetic edge cases

def test_not_in_scope_asset_goes_out_of_scope_without_leaking():
    html = _page(
        _asset("*.example.com") + _asset("Not in Scope", "<p>blog.example.com<br>staging.example.com</p>")
        + _asset("shop.example.org")
    )
    p = gobugfree.parse_program(html, "x")
    assert [e["target"] for e in p["targets"]["in_scope"]] == ["*.example.com", "shop.example.org"]
    assert [e["target"] for e in p["targets"]["out_of_scope"]] == ["blog.example.com", "staging.example.com"]


def test_info_assets_are_not_targets():
    html = _page(_asset("Source Code", "<p>https://github.com/acme/app</p>") + _asset("app.example.com"))
    p = gobugfree.parse_program(html, "x")
    assert [e["target"] for e in p["targets"]["in_scope"]] == ["app.example.com"]


def test_mobile_types():
    html = _page(
        _asset("eID App (Android / iOS)", tag="Mobile")
        + _asset("ch.example.mycard: Android App", tag="Mobile")
        + _asset("g-*.0.example.ch")
    )
    got = {(e["target"], e["type"]) for e in gobugfree.parse_program(html, "x")["targets"]["in_scope"]}
    assert got == {
        ("eID App (Android / iOS)", "mobile-application"),
        ("ch.example.mycard", "mobile-application-android"),
        ("g-*.0.example.ch", "wildcard"),
    }


def test_status_visibility_and_bounty_flags():
    paused = gobugfree.parse_program(_page(_asset("a.example.com"), badge="Paused"), "x")
    private = gobugfree.parse_program(_page(_asset("a.example.com"), tags=("Bug Bounty", "Private")), "x")
    bounty = gobugfree.parse_program(_page(_asset("a.example.com"), bounty_cells=("CHF 2,400 – 5,000", "CHF 50 – 150")), "x")
    vdp = gobugfree.parse_program(_page(_asset("a.example.com"), tags=("VDP", "Public"), bounty_cells=("CHF 1 – 2",)), "x")
    assert paused["disabled"] is True and paused["public"] is True
    assert private["public"] is False and private["disabled"] is False
    assert (bounty["min_bounty"], bounty["max_bounty"]) == (50, 5000)
    assert (vdp["min_bounty"], vdp["max_bounty"]) == (None, None)


def test_page_without_scope_assets_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="scrapers"):
        p = gobugfree.parse_program(_page(), "x")
    assert p["targets"] == {"in_scope": [], "out_of_scope": []}
    assert "no scope asset found" in caplog.text


# ------------------------------------------------------------------ crawl

APP = "https://app.gobugfree.com/public/engagements"
LISTING = f"""
<a href="{APP}/o/bbh">BBH</a><a href="{APP}/o/vdp">VDP</a><a href="{APP}/o/bbh">dup</a>
<a href="/login?redirectTo={APP}/o/secret">Secret</a><a href="/trust">ISO</a>
"""
PAGES = {
    gobugfree.LIST_URL: LISTING,
    f"{APP}/o/bbh": _page(_asset("a.example.com")),
    f"{APP}/o/vdp": _page(_asset("*.example.org"), tags=("VDP", "Public")),
}


class FakeFetcher:
    robots = []

    def __init__(self, base_url, *a, **k):
        FakeFetcher.robots.append((base_url, k.get("respect_robots", True)))

    def get(self, url):
        return PAGES[url]  # KeyError if the scraper requests anything unexpected (login-only program)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    FakeFetcher.robots = []
    monkeypatch.setattr(gobugfree, "Fetcher", FakeFetcher)
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    return tmp_path


def test_crawl_skips_login_only_programs_and_logs_progress(fake, caplog):
    with caplog.at_level(logging.INFO, logger="scrapers"):
        out = gobugfree.crawl()
    assert [p["id"] for p in out] == ["bbh", "vdp"]
    assert "skipping secret (login required)" in caplog.text
    assert f"[1/2] {APP}/o/bbh" in caplog.text


def test_robots_is_honoured_for_the_listing_only(fake):
    gobugfree.crawl()
    assert FakeFetcher.robots == [("https://gobugfree.com", True), ("https://app.gobugfree.com", False)]


def test_cli_limit_is_dry_run(fake):
    common.run_cli("gobugfree", gobugfree.crawl, ["--limit", "1"])
    assert not (fake / "gobugfree_data.json").exists()


def test_cli_full_run_writes_sorted_json(fake):
    common.run_cli("gobugfree", gobugfree.crawl, [])
    data = json.loads((fake / "gobugfree_data.json").read_text())
    assert [p["id"] for p in data] == ["bbh", "vdp"]
