"""WhiteHub tests. List items and the nukeviet detail are REAL API responses (fields trimmed to
what the scraper reads). Page 2, the out_of_scope target and the unknown type are SYNTHETIC: the
real API only showed in_scope / source_code so far."""
import json
import logging

import pytest

from scrapers import common, whitehub


def item(alias, org, *, visibility="public", status="open", managed=False, currency="VND",
         rr=(0.0, 0.0), readable=True):
    return {
        "program_id": "1", "org_alias": alias, "status": status, "visibility": visibility,
        "type": visibility, "is_wh_managed": managed, "currency": currency,
        "reward_range": {"min": rr[0], "max": rr[1]}, "is_readable": readable,
        "program_projection": {"alias": alias, "name": alias}, "org": {"alias": alias, "name": org},
    }


REAL_PAGE1 = {
    "count": 16,
    "next": "/v2/bug-bounty/programs?is_owner=0&page=2&sort=last_updated_desc&status=open%2Clocked",
    "previous": None,
    "results": [
        item("nukeviet", "NukeViet"),                                                 # VND 0-0
        item("one-mount", "One Mount Group", rr=(0.0, 20000000.0)),                   # VND amounts
        item("hackme", "Hackme", managed=True, currency="USD", rr=(None, None)),      # null range
        item("locker", "Locker Password Manager", managed=True, currency="USD", rr=(0.0, 500.0)),
        item("finhay-old", "Finhay", status="locked", rr=(0.0, 2500000.0)),           # locked
        item("gpqb", "GPQB", visibility="semi_private", status="locked", managed=True,
             rr=(0.0, 3000000.0), readable=False),                                    # unreadable
    ],
}
SYNTHETIC_PAGE2 = {"count": 16, "next": None, "previous": "/v2/x",
                   "results": [item("acme", "Acme", currency="EUR", rr=(50.0, 5000.0))]}

REAL_NUKEVIET_DETAIL = {
    "program_id": "71", "status": "open", "visibility": "public",
    "metadata": {"en": "## Scope\n- NukeViet's open source project at https://github.com/nukeviet/nukeviet/\n"
                       "- *NukeViet's websites and domains are not covered by this program*"},
    "currency": "VND", "reward_range": {"min": 0.0, "max": 0.0}, "is_wh_managed": False,
    "target": [{"id": 2323, "name": "https://github.com/nukeviet/nukeviet/", "type": "source_code",
                "scope": "in_scope", "link_download": ""}],
}
SYNTHETIC_DETAIL = {"target": [
    {"id": 1, "name": "app.acme.com", "type": "website", "scope": "in_scope"},
    {"id": 2, "name": "*.acme.com", "type": "Wildcard Domain", "scope": "in_scope"},
    {"id": 3, "name": "old.acme.com", "type": "website", "scope": "out_of_scope"},
    {"id": 4, "name": "   ", "type": "website", "scope": "in_scope"},
]}


def targets(p, key="in_scope"):
    return [(e["target"], e["type"]) for e in p["targets"][key]]


# ------------------------------------------------------------------ mapping


def test_real_nukeviet_program():
    p = whitehub.build_program(REAL_PAGE1["results"][0], REAL_NUKEVIET_DETAIL)
    assert p["id"] == "nukeviet" and p["name"] == "NukeViet"
    assert targets(p) == [("https://github.com/nukeviet/nukeviet/", "source-code")]
    assert p["targets"]["out_of_scope"] == []
    # the markdown prose is NOT scope ("websites and domains are not covered" must not leak)
    assert (p["public"], p["disabled"], p["managed"]) == (True, False, False)
    assert (p["min_bounty"], p["max_bounty"]) == (None, None)  # VND is not comparable


def test_bounty_only_in_comparable_currencies():
    by = {i["org_alias"]: whitehub.bounty_range(i) for i in REAL_PAGE1["results"]}
    assert by["one-mount"] == (None, None)   # VND 20,000,000 would be misleading
    assert by["hackme"] == (None, None)      # USD but null range
    assert by["locker"] == (0, 500)
    assert whitehub.bounty_range(SYNTHETIC_PAGE2["results"][0]) == (50, 5000)  # EUR


def test_managed_locked_and_semi_private_flags():
    locker = whitehub.build_program(REAL_PAGE1["results"][3], None)
    finhay = whitehub.build_program(REAL_PAGE1["results"][4], None)
    gpqb = whitehub.build_program(REAL_PAGE1["results"][5], None)
    assert locker["managed"] is True and locker["disabled"] is False
    assert finhay["disabled"] is True and finhay["public"] is True
    assert gpqb["public"] is False and gpqb["disabled"] is True


def test_in_out_scope_split_blank_names_dropped_unknown_type_kept(caplog):
    whitehub._seen_unknown_types.clear()
    with caplog.at_level(logging.INFO, logger="scrapers"):
        p = whitehub.build_program(item("acme", "Acme"), SYNTHETIC_DETAIL)
    assert targets(p) == [("app.acme.com", "web-application"), ("*.acme.com", "wildcard-domain")]
    assert targets(p, "out_of_scope") == [("old.acme.com", "web-application")]
    assert "Unknown WhiteHub target type 'Wildcard Domain'" in caplog.text


def test_schema_keys_match_reference():
    p = whitehub.build_program(REAL_PAGE1["results"][0], None)
    assert list(p) == ["id", "name", "public", "disabled", "managed", "min_bounty", "max_bounty", "targets"]


# ------------------------------------------------------------------ crawl / CLI

LIST1 = "https://api.cystack.net" + whitehub.LIST_PATH
LIST2 = "https://api.cystack.net" + REAL_PAGE1["next"]
DETAIL = "https://api.cystack.net/v2/bug-bounty/programs/{}"
RESPONSES = {LIST1: REAL_PAGE1, LIST2: SYNTHETIC_PAGE2,
             DETAIL.format("nukeviet"): REAL_NUKEVIET_DETAIL}
for alias in ("one-mount", "hackme", "locker", "finhay-old", "acme"):
    RESPONSES[DETAIL.format(alias)] = SYNTHETIC_DETAIL


class FakeFetcher:
    requested: list[str] = []

    def __init__(self, *a, **k):
        pass

    def get_json(self, url):
        FakeFetcher.requested.append(url)
        return json.loads(json.dumps(RESPONSES[url]))  # KeyError on any unexpected URL (e.g. gpqb)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    FakeFetcher.requested = []
    monkeypatch.setattr(whitehub, "Fetcher", FakeFetcher)
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    return tmp_path


def test_crawl_follows_next_skips_unreadable_and_logs_progress(fake, caplog):
    with caplog.at_level(logging.INFO, logger="scrapers"):
        out = whitehub.crawl()
    assert [p["id"] for p in out] == ["nukeviet", "one-mount", "hackme", "locker", "finhay-old", "gpqb", "acme"]
    assert FakeFetcher.requested[:2] == [LIST1, LIST2]
    assert DETAIL.format("gpqb") not in FakeFetcher.requested  # unreadable: no detail request
    assert "[1/7] https://api.cystack.net/v2/bug-bounty/programs/nukeviet" in caplog.text
    assert "gpqb: not readable (semi_private)" in caplog.text


def test_cli_only_is_dry_run(fake):
    common.run_cli("whitehub", whitehub.crawl, ["--only", "nukeviet"])
    assert not (fake / "whitehub_data.json").exists()
    assert FakeFetcher.requested[-1] == DETAIL.format("nukeviet")


def test_cli_full_run_writes_sorted_json(fake):
    common.run_cli("whitehub", whitehub.crawl, [])
    data = json.loads((fake / "whitehub_data.json").read_text())
    assert [p["id"] for p in data] == sorted(p["id"] for p in data)
    assert len(data) == 7
