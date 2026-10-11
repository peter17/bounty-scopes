"""CrowdSwarm tests. tests/fixtures/crowdswarm_get-all.json is the REAL get-all response (the
fields the scraper does not read are trimmed), including the real XSS payloads found in the
`comments`."""
import json
import logging
from pathlib import Path

import pytest

from scrapers import common, crowdswarm

REAL = json.loads((Path(__file__).parent / "fixtures" / "crowdswarm_get-all.json").read_text(encoding="utf-8"))
SADAQAH, CROWDSWARM = REAL["list"]


def targets(p, key="in_scope"):
    return [(e["target"], e["type"]) for e in p["targets"][key]]


def test_real_crowdswarm_program():
    p = crowdswarm.build_program(CROWDSWARM)
    assert (p["id"], p["name"]) == ("GtyQa6QJV0OMuUv", "Crowdswarm - Bug Bounty Platform")
    assert targets(p) == [("https://www.crowdswarm.io/", "web-application"),
                          ("https://app.crowdswarm.io/", "web-application")]
    assert p["targets"]["out_of_scope"] == []
    assert (p["public"], p["disabled"], p["managed"]) == (True, False, None)


def test_real_sadaqah_program_scope_sections_with_indented_markdown_lists():
    p = crowdswarm.build_program(SADAQAH)
    assert targets(p) == [
        ("https://sadaqah.io", "web-application"),
        ("https://community.sadaqah.io", "web-application"),
        ("https://analytics.sadaqah.io", "web-application"),
        ("https://sadaqah.io/blog/", "web-application"),
        ("3.23.218.108", "ip-address"),                                   # "### Scope IP Address"
        ("Any S3 Buckets Detecting Sensitive Classified Information", "other"),  # "### Scope S3 Buckets"
    ]


def test_rules_are_not_scope():
    """'NOT ALLOWED' activities, 'Non-qualifying Vulnerabilities' and legal terms are rules."""
    for item in (SADAQAH, CROWDSWARM):
        p = crowdswarm.build_program(item)
        everything = " ".join(t for t, _ in targets(p) + targets(p, "out_of_scope")).lower()
        for rule in ("clickjacking", "spf", "dns vulnerabilities", "malware", "3rd party", "legal"):
            assert rule not in everything
    assert crowdswarm.build_program(CROWDSWARM)["targets"]["out_of_scope"] == []


def test_comments_are_never_read_or_stored():
    out = json.dumps([crowdswarm.build_program(i) for i in REAL["list"]])
    for payload in ("alert(3)", "onload", "<script>", "SQL Injection", "Armen", "sehajpreet"):
        assert payload not in out


def test_bounty_is_null_until_currency_is_confirmed(monkeypatch):
    assert crowdswarm.REWARD_CURRENCY is None
    p = crowdswarm.build_program(SADAQAH)
    assert (p["min_bounty"], p["max_bounty"]) == (None, None)
    monkeypatch.setattr(crowdswarm, "REWARD_CURRENCY", "USD")
    p = crowdswarm.build_program(SADAQAH)
    assert (p["min_bounty"], p["max_bounty"]) == (100, 250)


def test_explicit_out_of_scope_heading_and_in_scope_line_under_rules():
    item = {"id": "x", "title": "X", "details": (
        "intro https://prose.example.com\n### Scope\n- app.x.com\n### Out of Scope\n- old.x.com\n"
        "### Rules\nIn scope: extra.x.com\n### Non-qualifying\n- staging.x.com\n")}
    p = crowdswarm.build_program(item)
    assert targets(p) == [("app.x.com", "web-application"), ("extra.x.com", "web-application")]
    assert targets(p, "out_of_scope") == [("old.x.com", "web-application")]


def test_no_scope_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="scrapers"):
        p = crowdswarm.build_program({"id": "y", "title": "Y", "details": "Just rules, https://x.example.com"})
    assert p["targets"]["in_scope"] == [] and "no in-scope target" in caplog.text


def test_schema_keys_match_reference():
    p = crowdswarm.build_program(SADAQAH)
    assert list(p) == ["id", "name", "public", "disabled", "managed", "min_bounty", "max_bounty", "targets"]


# ------------------------------------------------------------------ crawl / CLI

URL = "https://app.crowdswarm.io/api/v1.0/public-program/get-all"


class FakeFetcher:
    posts: list[tuple[str, dict]] = []

    def __init__(self, *a, **k):
        pass

    def post_json(self, url, payload):
        FakeFetcher.posts.append((url, payload))
        return json.loads(json.dumps(REAL))  # totalCount 2: a single page is enough


@pytest.fixture
def fake(monkeypatch, tmp_path):
    FakeFetcher.posts = []
    monkeypatch.setattr(crowdswarm, "Fetcher", FakeFetcher)
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    return tmp_path


def test_crawl_single_page_no_detail_requests(fake, caplog):
    with caplog.at_level(logging.INFO, logger="scrapers"):
        out = crowdswarm.crawl()
    assert [p["id"] for p in out] == ["RaHeZNP2i0GdAgn", "GtyQa6QJV0OMuUv"]
    assert FakeFetcher.posts == [(URL, {"take": 10, "skip": 0})]
    assert "Page 1: POST" in caplog.text and "2 programs so far" in caplog.text


def test_crawl_paginates_with_skip_until_total(monkeypatch, fake):
    big = {"totalCount": 25, "list": [{"id": f"id{i}", "title": f"P{i}", "details": "### Scope\n- a.com"} for i in range(25)]}

    class Paged(FakeFetcher):
        def post_json(self, url, payload):
            FakeFetcher.posts.append((url, payload))
            s, t = payload["skip"], payload["take"]
            return {"totalCount": 25, "list": big["list"][s:s + t]}

    monkeypatch.setattr(crowdswarm, "Fetcher", Paged)
    out = crowdswarm.crawl()
    assert len(out) == 25
    assert [p["skip"] for _, p in FakeFetcher.posts] == [0, 10, 20]


def test_cli_only_is_dry_run_and_full_run_writes(fake):
    common.run_cli("crowdswarm", crowdswarm.crawl, ["--only", "GtyQa6QJV0OMuUv"])
    assert not (fake / "crowdswarm_data.json").exists()
    common.run_cli("crowdswarm", crowdswarm.crawl, [])
    data = json.loads((fake / "crowdswarm_data.json").read_text())
    assert [p["id"] for p in data] == sorted(p["id"] for p in data) and len(data) == 2
