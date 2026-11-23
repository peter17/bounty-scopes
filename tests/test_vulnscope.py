"""Offline tests on SYNTHETIC API responses shaped like https://api.vulnscope.com/api/v1/programs/publics."""
import json
import logging

import pytest

from scrapers import common, vulnscope


def _item(slug="p", targets=(), rewards=(), **extra):
    return {
        "name": slug.title(), "slug": slug, "is_public": True,
        "description": "# prose\nCredentials: hunter2 ops@example.com",
        "rewards": [{"name": n, "amount": a} for n, a in rewards],
        "targets": [{"id": i, "url": None, "description": d} for i, d in enumerate(targets)],
        **extra,
    }


def test_targets_types_and_trailing_slash():
    p = vulnscope.build_program(_item(targets=[
        "https://www.example.cl/", "*.example.cl", "example.net/", "api.example.cl", "10.0.0.1",
    ]))
    assert [(e["target"], e["type"]) for e in p["targets"]["in_scope"]] == [
        ("https://www.example.cl/", "web-application"),
        ("*.example.cl", "wildcard"),
        ("example.net", "web-application"),
        ("api.example.cl", "api"),
        ("10.0.0.1", "ip-address"),
    ]
    assert p["targets"]["out_of_scope"] == []
    assert (p["public"], p["disabled"], p["managed"]) == (True, False, None)


def test_short_target_without_token_is_kept_other_long_prose_dropped():
    p = vulnscope.build_program(_item(targets=["Mobile app", "word " * 30]))
    assert p["targets"]["in_scope"] == [{"target": "Mobile app", "type": "other"}]


def test_bounty_uses_positive_amounts_only():
    paid = vulnscope.build_program(_item(rewards=[("Critical", 1000), ("High", 500), ("Low", 50)]))
    hybrid = vulnscope.build_program(_item(rewards=[("Critical", 300), ("High", 200), ("Medium", 0)]))
    points = vulnscope.build_program(_item(rewards=[("Critical", 0), ("Low", 0)]))
    assert (paid["min_bounty"], paid["max_bounty"]) == (50, 1000)
    assert (hybrid["min_bounty"], hybrid["max_bounty"]) == (200, 300)
    assert (points["min_bounty"], points["max_bounty"]) == (None, None)


def test_unknown_currency_drops_amounts(monkeypatch):
    monkeypatch.setattr(vulnscope, "REWARD_CURRENCY", None)
    p = vulnscope.build_program(_item(rewards=[("Critical", 1000)]))
    assert (p["min_bounty"], p["max_bounty"]) == (None, None)


def test_description_is_never_stored():
    out = json.dumps(vulnscope.build_program(_item(targets=["a.example.cl"])))
    assert "hunter2" not in out and "ops@example.com" not in out


def test_no_target_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="scrapers"):
        p = vulnscope.build_program(_item())
    assert p["targets"]["in_scope"] == [] and "no target found" in caplog.text


# ------------------------------------------------------------------ crawl

LIST = [_item("a"), _item("b"), _item("a")]  # duplicate slug
DETAILS = {
    "a": _item("a", targets=["a.example.cl"]),
    "b": _item("b", targets=["*.b.example.cl"], rewards=[("Critical", 100)]),
}


class FakeFetcher:
    calls: list[str] = []

    def __init__(self, *a, **k):
        pass

    def get_json(self, url):
        FakeFetcher.calls.append(url)
        if url.startswith(f"{vulnscope.LIST_URL}?"):
            return LIST if "page=1&" in url else []
        return DETAILS[url.rsplit("/", 1)[1]]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    FakeFetcher.calls = []
    monkeypatch.setattr(vulnscope, "Fetcher", FakeFetcher)
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    return tmp_path


def test_crawl_dedups_and_fetches_each_detail(fake, caplog):
    with caplog.at_level(logging.INFO, logger="scrapers"):
        out = vulnscope.crawl()
    assert [p["id"] for p in out] == ["a", "b"]
    assert out[1]["max_bounty"] == 100
    assert f"[2/2] {vulnscope.LIST_URL}/b" in caplog.text


def test_cli_only_and_dry_run(fake):
    common.run_cli("vulnscope", vulnscope.crawl, ["--only", "b"])
    assert FakeFetcher.calls[-1] == f"{vulnscope.LIST_URL}/b"
    assert not (fake / "vulnscope_data.json").exists()


def test_cli_full_run_writes_file(fake):
    common.run_cli("vulnscope", vulnscope.crawl, [])
    assert [p["id"] for p in json.loads((fake / "vulnscope_data.json").read_text())] == ["a", "b"]
