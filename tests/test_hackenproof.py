"""Offline tests. The HTML below is SYNTHETIC: it reproduces the structure observed
(as converted text) on https://hackenproof.com/programs/near-protocol, not the real raw
HTML. The first real run in GitHub Actions remains the actual validation."""
import json
import logging

import pytest

from scrapers import common, hackenproof

PROGRAM_HTML = """
<html><head><title>NEAR Protocol: Program Info | HackenProof</title></head><body>
<h1>NEAR Protocol: Program info</h1>
<div>Live</div><div>Program is active now</div>
<h3>In scope</h3>
<table><thead><tr><th>Target</th><th>Type</th><th>Severity</th></tr></thead><tbody>
<tr><td><div>https://github.com/near/nearcore</div><button>Copy</button><span>Copied</span></td>
    <td>Protocol</td><td>Critical</td></tr>
<tr><td><div>https://app.example.com</div><button>Copy</button></td><td>Web</td><td>High</td></tr>
<tr><td><div>https://app.example.com</div><button>Copy</button></td><td>Web</td><td>High</td></tr>
</tbody></table>
<h3>Out of scope</h3>
<table><thead><tr><th>Target</th><th>Type</th></tr></thead><tbody>
<tr><td><div>blog.example.com</div></td><td>Web</td></tr>
</tbody></table>
<div>Rewards</div><div>Range of bounty</div><div>$100 - $1,000,000</div>
<table><tr><th>Severity</th><th>Reward</th></tr><tr><td>Critical</td><td>$200,000</td></tr></table>
</body></html>
"""

LISTING_HTML = """
<a href="/programs/near-protocol">NEAR</a><a href="/programs/okx">OKX</a>
<a href="/companies/parity">Parity</a><a href="/programs/near-protocol">dup</a>
<a href="/programs?page=2">next</a><a href="/blog/foo">blog</a>
"""

PAGES = {
    hackenproof.LIST_URL: LISTING_HTML,
    f"{hackenproof.LIST_URL}?page=2": LISTING_HTML,  # parameter ignored => stop
    "https://hackenproof.com/companies/parity": '<a href="/programs/parity-prog">x</a>',
    "https://hackenproof.com/programs/near-protocol": PROGRAM_HTML,
    "https://hackenproof.com/programs/okx": PROGRAM_HTML,
    "https://hackenproof.com/programs/parity-prog": PROGRAM_HTML,
}


class FakeFetcher:
    requested: list[str] = []

    def __init__(self, *a, **k):
        pass

    def get(self, url):
        FakeFetcher.requested.append(url)
        return PAGES[url]


@pytest.fixture
def fake_fetcher(monkeypatch):
    FakeFetcher.requested = []
    monkeypatch.setattr(hackenproof, "Fetcher", FakeFetcher)
    return FakeFetcher


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    return tmp_path


# ------------------------------------------------------------------ parsing


def test_parse_listing():
    progs, comps = hackenproof.parse_listing(LISTING_HTML)
    assert progs == ["near-protocol", "okx"]
    assert comps == ["parity"]


def test_parse_program():
    p = hackenproof.parse_program(PROGRAM_HTML, "near-protocol")
    assert p["id"] == "near-protocol"
    assert p["name"] == "NEAR Protocol"
    assert (p["min_bounty"], p["max_bounty"]) == (100, 1_000_000)
    assert p["disabled"] is False and p["public"] is True and p["managed"] is None
    # "Copy/Copied" button text stripped, duplicate removed, types mapped
    assert p["targets"]["in_scope"] == [
        {"target": "https://github.com/near/nearcore", "type": "protocol"},
        {"target": "https://app.example.com", "type": "web-application"},
    ]
    assert p["targets"]["out_of_scope"] == [{"target": "blog.example.com", "type": "web-application"}]


def test_schema_keys_match_reference():
    p = hackenproof.parse_program(PROGRAM_HTML, "x")
    assert list(p) == ["id", "name", "public", "disabled", "managed",
                       "min_bounty", "max_bounty", "targets"]
    assert list(p["targets"]) == ["in_scope", "out_of_scope"]


def test_unknown_type_is_slugified():
    assert hackenproof.map_type("Smart Contract") == "smart-contract"
    assert hackenproof.map_type("Exotic Thing!") == "exotic-thing"


def test_paused_program_flagged():
    html = PROGRAM_HTML.replace("Program is active now", "Program is paused")
    assert hackenproof.parse_program(html, "x")["disabled"] is True


def test_empty_scope_logs_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="scrapers"):
        hackenproof.parse_program("<html><title>X | HackenProof</title></html>", "empty")
    assert "no in-scope target found" in caplog.text


# ------------------------------------------------------------------ writing


def _progs(n):
    return [common.make_program(id=f"p{i:03d}", name=f"P{i}") for i in range(n)]


def test_write_is_deterministic_and_idempotent(tmp_data):
    assert common.write_platform_data("t", list(reversed(_progs(3)))) is True
    assert common.write_platform_data("t", _progs(3)) is False  # same content, different order
    ids = [p["id"] for p in json.loads((tmp_data / "t_data.json").read_text())]
    assert ids == sorted(ids)


def test_refuses_empty_and_big_drop(tmp_data):
    common.write_platform_data("t", _progs(20))
    before = (tmp_data / "t_data.json").read_text()
    with pytest.raises(common.SuspiciousDrop):
        common.write_platform_data("t", [])
    with pytest.raises(common.SuspiciousDrop):
        common.write_platform_data("t", _progs(5))
    assert (tmp_data / "t_data.json").read_text() == before  # file untouched


# ------------------------------------------------------------------ crawl + progress


def test_crawl_end_to_end(fake_fetcher, tmp_data):
    out = hackenproof.crawl()
    assert [p["id"] for p in out] == ["near-protocol", "okx", "parity-prog"]


def test_crawl_prints_progress_lines(fake_fetcher, tmp_data, caplog):
    with caplog.at_level(logging.INFO, logger="scrapers"):
        hackenproof.crawl()
    lines = caplog.text
    assert "[1/1] https://hackenproof.com/companies/parity" in lines
    assert "[1/3] https://hackenproof.com/programs/near-protocol" in lines
    assert "[3/3] https://hackenproof.com/programs/parity-prog" in lines
    assert "Listing page 1:" in lines


def test_crawl_limit_only_fetches_n_program_pages(fake_fetcher, tmp_data):
    out = hackenproof.crawl(limit=1)
    assert [p["id"] for p in out] == ["near-protocol"]
    fetched_programs = [u for u in fake_fetcher.requested if "/programs/" in u and "?page" not in u]
    assert fetched_programs == ["https://hackenproof.com/programs/near-protocol"]


# ------------------------------------------------------------------ CLI


def test_cli_limit_implies_dry_run_and_writes_nothing(fake_fetcher, tmp_data, caplog):
    with caplog.at_level(logging.INFO, logger="scrapers"):
        common.run_cli("hackenproof", hackenproof.crawl, ["--limit", "1"])
    assert not (tmp_data / "hackenproof_data.json").exists()
    assert "dry run, nothing written" in caplog.text
    assert "done in" in caplog.text


def test_cli_normal_run_writes_file(fake_fetcher, tmp_data):
    common.run_cli("hackenproof", hackenproof.crawl, [])
    data = json.loads((tmp_data / "hackenproof_data.json").read_text())
    assert [p["id"] for p in data] == ["near-protocol", "okx", "parity-prog"]


def test_cli_only_implies_dry_run_and_skips_listing(fake_fetcher, tmp_data):
    common.run_cli("hackenproof", hackenproof.crawl, ["--only", "okx"])
    assert not (tmp_data / "hackenproof_data.json").exists()
    assert fake_fetcher.requested == ["https://hackenproof.com/programs/okx"]


def test_cli_dry_run_flag(fake_fetcher, tmp_data):
    common.run_cli("hackenproof", hackenproof.crawl, ["--dry-run"])
    assert not (tmp_data / "hackenproof_data.json").exists()


def test_cli_error_exits_nonzero_with_hint(tmp_data, caplog):
    def boom(limit=None, only=None):
        raise ValueError("kaboom")

    with pytest.raises(SystemExit) as exc, caplog.at_level(logging.ERROR, logger="scrapers"):
        common.run_cli("t", boom, [])
    assert exc.value.code == 1
    assert "kaboom" in caplog.text and "--verbose" in caplog.text
