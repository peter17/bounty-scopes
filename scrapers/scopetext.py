"""Heuristic extraction of scope targets from free-text program descriptions.

Some platforms (GoBugFree, Bugbop, ...) have no structured scope table: each customer
writes its own list, e.g.

    Not in scope:
    - id.example.com: known issues from Keycloak
    Targets:
    - Website: example.com
    - IAM: id.example.com (only misconfigurations are in scope)
    In scope:: • *.example.com

Rules (deliberately simple and predictable):
  * A line "In scope" / "Targets" / "Not in scope" / "Out of scope" (colon optional) switches the mode;
    text after the colon on the same line is treated as the first item.
  * Every URL, domain, wildcard or IP found in an item becomes one entry
    (`type` guessed from the target itself).
  * An item without any such token becomes one `other` entry holding the text, but only out of
    scope (exclusion rules). In scope it is a title or a description and is dropped.
  * Exception: if no in-scope target at all was found, short token-less lines under an
    explicit "In scope" header are kept as `other` (a VDP whose scope is a statement).
  * Long lines (prose) are ignored: the URLs in explanations are rarely targets.
  * If an item has tokens AND a meaningful remark ("only misconfigurations are in scope"):
      - in scope: clean targets plus the full line as an extra `other` entry;
      - out of scope: only the full line (it is a partial exclusion, not a blanket one).
"""
from __future__ import annotations

import re

_URL = r"https?://[^\s,;<>()\"']+"
_DOMAIN = (
    r"(?<![\w@/.\-])(?:\*\.)?(?:[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?\.)+[a-z]{2,24}"
    r"(?:/[^\s,;<>()\"']*)?"
)
_IP = r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?(?![\w.])"
TOKEN_RE = re.compile(f"(?P<url>{_URL})|(?P<ip>{_IP})|(?P<dom>{_DOMAIN})", re.I)

# "In scope:", "Not in scope: <text>", and also a bare "In scope" / "Not in Scope" line.
_HEADER_RE = re.compile(
    r"^\s*(?P<h>not\s+in[\s-]*scope|out[\s-]*of[\s-]*scope|in[\s-]*scope|targets?)"
    r"(?:\s*:{1,2}\s*(?P<rest>.*))?\s*$",
    re.I,
)

# Longer lines are prose (explanations, rules): their URLs are usually not targets.
MAX_PROSE_WORDS = 40
# Token-less out-of-scope sentences ("All other domains are out of scope") are kept up to here.
MAX_OUT_TEXT_WORDS = 30
# In scope, the full line of a target with a remark is kept only if it is short.
MAX_IN_REMARK_WORDS = 25
# Fallback for VDPs whose scope is a plain statement ("Public facing applications and systems
# of X"): used only when NO in-scope target was found, only under an explicit "In scope"
# header, and only for short lines (longer ones are boilerplate).
MAX_IN_NOTE_WORDS = 12
MAX_IN_NOTES = 5
_BULLET_RE = re.compile(r"^\s*(?:[•·▪●◦\-–—]|\*(?=\s))\s*")
_TRAILING = ".,;:!?)]}"

# Emitted by the scraper before the title of each structured asset: the mode resets there, so
# an "out of scope" paragraph above the list cannot leak onto the assets below it.
ASSET_BREAK = "\u2063<asset>\u2063"

# Assets that carry information for researchers, not targets ("Source Code", "Credentials"...).
_INFO_TITLE_RE = re.compile(
    r"^\s*(source code|documentation|docs|downloads?|credentials|(test )?accounts?|notes?|"
    r"important notes?|information|faq|contact|rules)\s*$",
    re.I,
)

# "... out of scope: <rest>" in the middle of a line.
_INLINE_OUT_RE = re.compile(
    r"\b(?:not\s+in[\s-]*scope|out[\s-]*of[\s-]*scope)\b(?P<colon>\s*:)?\s*(?P<rest>.*)$", re.I
)
# Bare tokens ending in these look like file names, not hosts ("page.html", "data.json").
_FILE_EXTS = {
    "txt", "json", "png", "jpg", "jpeg", "gif", "svg", "pdf", "yml", "yaml", "js",
    "css", "html", "htm", "xml", "csv", "zip", "gz", "exe", "php",
}


def extract_tokens(text: str) -> list[str]:
    seen, out = set(), []
    for m in TOKEN_RE.finditer(text):
        tok = m.group(0).rstrip(_TRAILING)
        if m.lastgroup == "dom" and "/" not in tok and tok.rsplit(".", 1)[-1].lower() in _FILE_EXTS:
            continue
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def guess_type(token: str) -> str:
    low = token.lower()
    host = re.sub(r"^https?://", "", low).split("/")[0]
    if host in ("apps.apple.com", "itunes.apple.com"):
        return "mobile-application-ios"
    if host == "play.google.com":
        return "mobile-application-android"
    if low.startswith("*."):
        return "wildcard"
    if host.startswith("api") or "/api" in low or "graphql" in low:
        return "api"
    if host in ("github.com", "gitlab.com", "bitbucket.org") and "/" in low.split(host, 1)[1]:
        return "other"
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?", host.split("/")[0] if "/" in host else low):
        return "other"
    return "web-application"


def _remark_words(line: str, tokens: list[str]) -> int:
    """Number of words left once tokens and a short 'Label:' prefix are removed."""
    rest = line
    for t in tokens:
        rest = rest.replace(t, " ")
    if ":" in rest:
        head, tail = rest.split(":", 1)
        if len(head.split()) <= 3:
            rest = tail
    return len(re.findall(r"[A-Za-z]{2,}", rest))


def entries_from_line(line: str, mode: str = "in") -> list[dict]:
    """Entries for one scope item.

    * more than MAX_PROSE_WORDS words: prose, ignored;
    * no target token: kept as an `other` entry only out of scope (an exclusion rule);
      in scope it is a title or a description, not a target, so it is dropped;
    * in  + remark: clean targets AND the full line (the target is in scope, with limits);
    * out + remark: only the full line. "id.example.com: known issues from X" is a partial
      exclusion; emitting a clean "id.example.com" out of scope would be wrong.
    """
    line = " ".join(line.split())
    if not line:
        return []
    words = len(line.split())
    if words > MAX_PROSE_WORDS:
        return []
    tokens = extract_tokens(line)
    if not tokens:
        if mode == "out" and words <= MAX_OUT_TEXT_WORDS:
            return [{"target": line, "type": "other"}]
        return []
    has_remark = _remark_words(line, tokens) > 3
    if mode == "out" and has_remark:
        return [{"target": line, "type": "other"}]
    entries = [{"target": t, "type": guess_type(t)} for t in tokens]
    if len(tokens) == 1 and re.search(r":\s*api\s*$", line, re.I):  # "caius.example.ch: API"
        entries[0]["type"] = "api"
    if has_remark and words <= MAX_IN_REMARK_WORDS:
        entries.append({"target": line, "type": "other"})
    return entries


def split_scope_lines(
    lines: list[str], initial_mode: str = "in"
) -> tuple[list[dict], list[dict]]:
    """Turn the raw lines of a scope section into (in_scope, out_of_scope) entries.

    `initial_mode` is "in" (default), "out", or "ignore": in "ignore" nothing is extracted until
    an explicit "In scope" / "Not in scope" header line is met (for prose that may or may not
    contain a scope list).

    `ASSET_BREAK` lines mark the start of a structured asset (title line first): the mode
    resets to "in" for it, or to "skip" if the title is purely informational.
    """
    mode = initial_mode  # "in" | "out" | "skip" | "ignore"
    explicit_in = False  # an "In scope"/"Targets" header was seen
    expect_title = False
    in_scope: list[dict] = []
    out_scope: list[dict] = []
    notes: list[str] = []  # short token-less lines under an explicit in-scope header
    for raw in lines:
        if raw == ASSET_BREAK:
            mode, expect_title = "in", True
            continue
        line = _BULLET_RE.sub("", raw).strip()
        if not line:
            continue
        if expect_title:
            expect_title = False
            if _INFO_TITLE_RE.match(line):
                mode = "skip"
                continue
        if mode == "skip":
            continue
        if mode == "ignore" and not _HEADER_RE.match(line):
            continue
        item = line
        if m := _HEADER_RE.match(line):
            mode = "out" if re.match(r"(not|out)", m.group("h"), re.I) else "in"
            explicit_in = explicit_in or mode == "in"
            item = (m.group("rest") or "").strip()
            if not item:
                continue
            item = _BULLET_RE.sub("", item).strip()
        elif m := _INLINE_OUT_RE.search(line):
            # "Note the following endpoint is out of scope: https://..." / "X is out of scope."
            rest = m.group("rest").strip()
            if not rest and m.group("colon"):
                mode = "out"  # lead-in such as "Following endpoints are out of scope:"
            elif extract_tokens(rest):
                out_scope.extend(entries_from_line(rest, "out"))
            else:
                out_scope.extend(entries_from_line(line, "out"))
            continue
        entries = entries_from_line(item, mode)
        if mode == "out":
            out_scope.extend(entries)
        else:
            in_scope.extend(entries)
            if not entries and explicit_in and len(item.split()) <= MAX_IN_NOTE_WORDS:
                notes.append(item)
    if not in_scope and notes:
        in_scope = [{"target": n, "type": "other"} for n in notes[:MAX_IN_NOTES]]
    return in_scope, out_scope


def entries_from_asset_titles(titles: list[str]) -> list[dict]:
    """Fallback for structured asset lists (one title per asset, no target in the text).

    Used only when no in-scope target was found at all, e.g. a VDP whose single asset is
    "Public facing applications and systems of X". Titles that are out-of-scope headers
    ("Not in Scope") or long sentences are skipped; the result is capped.
    """
    out: list[dict] = []
    for raw in titles:
        title = " ".join(raw.split())
        m = _HEADER_RE.match(title)
        if not title or (m and re.match(r"(not|out)", m.group("h"), re.I)):
            continue
        if len(title.split()) > MAX_IN_NOTE_WORDS:
            continue
        entry = {"target": title, "type": "other"}
        if entry not in out:
            out.append(entry)
    return out[:MAX_IN_NOTES]
