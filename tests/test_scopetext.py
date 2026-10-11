from scrapers import common
from scrapers.scopetext import entries_from_line, extract_tokens, guess_type, split_scope_lines


def pairs(entries):
    return [(e["target"], e["type"]) for e in entries]


def test_extract_tokens_variants():
    text = "see https://a.example.com/x?y=1, *.b.example.org and 10.0.0.1/24 (mail sec@c.example.com)"
    assert extract_tokens(text) == ["https://a.example.com/x?y=1", "*.b.example.org", "10.0.0.1/24"]


def test_file_names_and_abbreviations_are_not_targets():
    assert extract_tokens("open page.html, data.json e.g. etc. i.e.") == []


def test_guess_type():
    assert guess_type("https://apps.apple.com/ua/app/x/id1") == "mobile-application-ios"
    assert guess_type("https://play.google.com/store/apps/details?id=a.b") == "mobile-application-android"
    assert guess_type("*.example.com") == "wildcard"
    assert guess_type("api.example.com") == "api"
    assert guess_type("https://github.com/org/repo") == "other"
    assert guess_type("example.com") == "web-application"


def test_plain_target_has_no_extra_entry():
    assert pairs(entries_from_line("Website: gobugfree.com")) == [("gobugfree.com", "web-application")]


def test_in_scope_remark_adds_full_line():
    line = "IAM: id.x.com (only misconfigurations are in scope)"
    assert pairs(entries_from_line(line, "in")) == [("id.x.com", "web-application"), (line, "other")]


def test_out_of_scope_remark_is_partial_exclusion_only_full_line():
    line = "id.x.com: Missing ratelimit on forms, known issues from Keycloak"
    assert pairs(entries_from_line(line, "out")) == [(line, "other")]


def test_tokenless_line_is_kept_only_out_of_scope():
    line = "Third-party services such as Gmail"
    assert pairs(entries_from_line(line, "out")) == [(line, "other")]
    assert entries_from_line(line, "in") == []  # a title or description, not a target


def test_prose_is_ignored_even_with_url():
    prose = ("Install the client first, it is free and works everywhere. " * 5) + "https://dl.example.com/client"
    assert entries_from_line(prose, "in") == [] and entries_from_line(prose, "out") == []


def test_bare_headers_without_colon():
    i, o = split_scope_lines(["In scope", "app.x.com", "Not in Scope", "All other domains", "legacy.x.com"])
    assert [e["target"] for e in i] == ["app.x.com"]
    assert [e["target"] for e in o] == ["All other domains", "legacy.x.com"]


def test_abacus_like_structure():
    """Real text of gobugfree.com/programs/abacus-research (asset list, bare headers, prose)."""
    prose = ("The abacus software can be launched via a client software or directly in the browser. " * 4
             + "https://downloads.abacus.ch/downloads/abaclient")
    i, o = split_scope_lines([
        "In scope", "Abacus-ERP", prose,
        "Launch URL - https://bug-bounty1-1.shop.abanet.io/",
        "MyAbacus Portal", "Server: https://bug-bounty1-1.shop.abanet.io:443", "Mandant: 9999",
        "Odata API Abacus API on the OData 4.0 Specification, core datasets to allow customers and "
        "3rd Parties to simply integrate into Abacus ERP. https://apihub.abacus.ch/odata C:H I:H A:H for this asset only",
        "Not in Scope",
        "All (sub) domains and services that are not explicitly listed, are not in scope.",
        "DeepBox",
    ])
    assert pairs(i) == [
        ("https://bug-bounty1-1.shop.abanet.io/", "web-application"),
        ("https://bug-bounty1-1.shop.abanet.io:443", "web-application"),
        ("https://apihub.abacus.ch/odata", "api"),
    ]
    assert [e["target"] for e in o] == [
        "All (sub) domains and services that are not explicitly listed, are not in scope.", "DeepBox"]


def test_split_scope_lines_gobugfree_vdp_style():
    i, o = split_scope_lines([
        "Not in scope: All (sub) domains and services that are not explicitly listed",
        "In scope::", "\u2022 *.gobugfree.com", "\u2022 *.gobugfree.io",
    ])
    assert pairs(i) == [("*.gobugfree.com", "wildcard"), ("*.gobugfree.io", "wildcard")]
    assert pairs(o) == [("All (sub) domains and services that are not explicitly listed", "other")]


def test_default_mode_is_in_scope_and_star_wildcard_survives_bullet_stripping():
    i, o = split_scope_lines(["* item *.x.com", "*.y.com", "- z.com"])
    assert [e["target"] for e in i] == ["*.x.com", "*.y.com", "z.com"] and o == []


def test_parse_bounty_range():
    assert common.parse_bounty_range(["CHF 2000-3000", "CHF 100-500"]) == (100, 3000)
    assert common.parse_bounty_range(["$100 - $1,000"]) == (100, 1000)
    assert common.parse_bounty_range(["\u20ac50"]) == (50, 50)
    assert common.parse_bounty_range(["VND 1,000,000"]) == (None, None)  # not comparable
    assert common.parse_bounty_range(["USD 5", "EUR 6"]) == (None, None)  # mixed
    assert common.parse_bounty_range(["100"]) == (None, None)  # no currency


# ------------------------------------------------------------------ structured assets

from scrapers.scopetext import ASSET_BREAK as AB


def test_asset_break_resets_mode_so_free_text_exclusions_do_not_leak():
    i, o = split_scope_lines([
        "Not in scope: All other domains", "Following endpoints are out of scope:",
        "https://old.x.com/a",
        AB, "app.x.com: Portal", "Customer portal",
        AB, "api.x.com: API", "Order API",
    ])
    assert pairs(i) == [("app.x.com", "web-application"), ("api.x.com", "api")]
    assert [e["target"] for e in o] == ["All other domains", "https://old.x.com/a"]


def test_out_of_scope_asset_title_only_affects_that_asset():
    i, o = split_scope_lines([
        AB, "Main app", "https://app.x.com/",
        AB, "Not in Scope", "All (sub) domains that are not listed", "DeepBox",
        AB, "Other app", "https://other.x.com/",
    ])
    assert [e["target"] for e in i] == ["https://app.x.com/", "https://other.x.com/"]
    assert [e["target"] for e in o] == ["All (sub) domains that are not listed", "DeepBox"]


def test_informational_assets_are_skipped():
    i, o = split_scope_lines([
        AB, "Main app", "https://app.x.com/",
        AB, "Source Code", "Source code is available at https://downloads.x.com/v1 for review",
        AB, "Credentials", "user: test@x.com", "app.x.com",
    ])
    assert [e["target"] for e in i] == ["https://app.x.com/"] and o == []


def test_inline_out_of_scope_phrase_with_url_goes_out_without_changing_mode():
    i, o = split_scope_lines([
        AB, "my.x.com: Portal",
        "Note the following endpoint is out of scope: https://my.x.com/hsi/sms/*",
        "other.x.com",
    ])
    assert pairs(i) == [("my.x.com", "web-application"), ("other.x.com", "web-application")]
    assert [e["target"] for e in o] == ["https://my.x.com/hsi/sms/*"]


def test_inline_out_of_scope_sentence_is_kept_as_text():
    i, o = split_scope_lines(["app.x.com", "The parental control is out of scope, as not hacker proof."])
    assert [e["target"] for e in i] == ["app.x.com"]
    assert [e["target"] for e in o] == ["The parental control is out of scope, as not hacker proof."]
