"""Bugzero tests. The list items and the drawify detail are REAL API responses (trimmed). The
Out of Scope entry, the wildcard and the unknown type are SYNTHETIC: the real API only showed
{"type": "Domain", "submission": "In Scope"} so far."""
import json
import logging

import pytest

from scrapers import bugzero, common

LIST = [
    {"name": "Drawify", "handle": "drawify", "minimum_bounties": 5,
     "rewards": {"bug_bounty": True, "swag": True, "hall_of_fame": True}, "safeharbor": "Full"},
    {"name": "Webtelescope ", "handle": "webtelescope_", "minimum_bounties": 10,
     "rewards": {"swag": True, "hall_of_fame": True, "bug_bounty": True}, "safeharbor": "Full"},
    {"name": "SCoRe Lab", "handle": "scorelab", "minimum_bounties": "",
     "rewards": {"bug_bounty": False, "swag": True, "hall_of_fame": True}, "safeharbor": "Partial"},
]

REAL_DRAWIFY = {
    "generalData": {"org_name": "Drawify", "org_handle": "drawify", "org_website": "https://drawify.com"},
    "programData": {
        "policy": "### Policy of Drawify\n\n#### \\[Introduction\\]\n\nMention the name of your organization...",
        "scope": [{"type": "Domain", "identifier": "drawify.com", "submission": "In Scope", "bounty": "Yes",
                   "threat_level": "Critical", "instruction": "drawify.com", "reference": "https://drawify.com/about-us"}],
    },
}
SYNTHETIC = {
    "generalData": {"org_name": " Webtelescope "},
    "programData": {"scope": [
        {"type": "Domain", "identifier": "*.webtelescope.com", "submission": "In Scope"},
        {"type": "Domain", "identifier": "app.webtelescope.com", "submission": "In Scope"},
        {"type": "Mobile App", "identifier": "com.webtelescope.app", "submission": "In Scope"},
        {"type": "Domain", "identifier": "old.webtelescope.com", "submission": "Out of Scope"},
        {"type": "Hardware", "identifier": "badge-reader", "submission": "In Scope"},
        {"type": "Domain", "identifier": "  ", "submission": "In Scope"},
    ]},
}
EMPTY = {"generalData": {"org_name": "SCoRe Lab"}, "programData": {"scope": []}}


def targets(p, key="in_scope"):
    return [(e["target"], e["type"]) for e in p["targets"][key]]


def test_real_drawify_program():
    p = bugzero.build_program(LIST[0], REAL_DRAWIFY)
    assert (p["id"], p["name"]) == ("drawify", "Drawify")
    assert targets(p) == [("drawify.com", "web-application")]
    assert p["targets"]["out_of_scope"] == []
    assert (p["public"], p["disabled"], p["managed"]) == (True, False, None)


def test_policy_template_is_not_scope():
    p = bugzero.build_program(LIST[0], REAL_DRAWIFY)
    assert all("organization" not in e["target"].lower() for e in p["targets"]["in_scope"])


def test_bounty_is_null_while_currency_is_unconfirmed(monkeypatch):
    assert bugzero.minimum_bounty(LIST[0]) is None
    assert bugzero.build_program(LIST[0], REAL_DRAWIFY)["min_bounty"] is None
    monkeypatch.setattr(bugzero, "MINIMUM_BOUNTY_CURRENCY", "USD")
    assert bugzero.minimum_bounty(LIST[0]) == 5
    assert bugzero.minimum_bounty(LIST[2]) is None  # "" = no bounty
    p = bugzero.build_program(LIST[1], SYNTHETIC)
    assert (p["min_bounty"], p["max_bounty"]) == (10, None)  # the API has no maximum


def test_scope_split_types_and_blank_identifier(caplog):
    bugzero._seen_unknown_types.clear()
    with caplog.at_level(logging.INFO, logger="scrapers"):
        p = bugzero.build_program(LIST[1], SYNTHETIC)
    assert p["name"] == "Webtelescope"  # stripped, taken from generalData
    assert targets(p) == [
        ("*.webtelescope.com", "wildcard"),
        ("app.webtelescope.com", "web-application"),
        ("com.webtelescope.app", "mobile-application"),
        ("badge-reader", "hardware"),
    ]
    assert targets(p, "out_of_scope") == [("old.webtelescope.com", "web-application")]
    assert "Unknown Bugzero scope type 'Hardware'" in caplog.text


def test_empty_scope_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="scrapers"):
        p = bugzero.build_program(LIST[2], EMPTY)
    assert p["targets"] == {"in_scope": [], "out_of_scope": []}
    assert "scorelab: no in-scope target" in caplog.text


def test_schema_keys_match_reference():
    p = bugzero.build_program(LIST[0], None)
    assert list(p) == ["id", "name", "public", "disabled", "managed", "min_bounty", "max_bounty", "targets"]


# ------------------------------------------------------------------ crawl / CLI

BASE = "https://us-central1-bug-zero.cloudfunctions.net"
RESPONSES = {
    f"{BASE}/api/programs": LIST,
    f"{BASE}/api/organization/drawify": REAL_DRAWIFY,
    f"{BASE}/api/organization/webtelescope_": SYNTHETIC,
    f"{BASE}/api/organization/scorelab": EMPTY,
}


class FakeFetcher:
    requested: list[str] = []

    def __init__(self, *a, **k):
        pass

    def get_json(self, url):
        FakeFetcher.requested.append(url)
        return json.loads(json.dumps(RESPONSES[url]))


@pytest.fixture
def fake(monkeypatch, tmp_path):
    FakeFetcher.requested = []
    monkeypatch.setattr(bugzero, "Fetcher", FakeFetcher)
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    return tmp_path


def test_crawl_progress(fake, caplog):
    with caplog.at_level(logging.INFO, logger="scrapers"):
        out = bugzero.crawl()
    assert [p["id"] for p in out] == ["drawify", "webtelescope_", "scorelab"]
    assert f"[1/3] {BASE}/api/organization/drawify" in caplog.text
    assert f"[3/3] {BASE}/api/organization/scorelab" in caplog.text


def test_cli_only_and_limit_are_dry_runs(fake):
    common.run_cli("bugzero", bugzero.crawl, ["--only", "drawify"])
    common.run_cli("bugzero", bugzero.crawl, ["--limit", "1"])
    assert not (fake / "bugzero_data.json").exists()


def test_cli_full_run_writes_sorted_json(fake):
    common.run_cli("bugzero", bugzero.crawl, [])
    data = json.loads((fake / "bugzero_data.json").read_text())
    assert [p["id"] for p in data] == sorted(p["id"] for p in data) and len(data) == 3
