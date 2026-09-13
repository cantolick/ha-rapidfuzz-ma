"""Pure title-matching core.

Extracted out of app.py so the resolution logic — filler stripping, metadata
prefiltering, fuzzy scoring, series/tie-breaking — can be unit tested against
a plain in-memory catalog, with no FastAPI app, no Music Assistant, and no
module-level cache state involved.

Behavior matches the original inline version in app.py, with two fixes made
during extraction:
  - metadata_prefilter no longer does an `entry in matches` scan (an O(n^2)
    deep-dict-equality check per candidate) to test "have I already matched
    this entry" — it tracks matched indices in a set instead.
  - the pre-prefilter `search_texts` list is no longer computed twice; the
    original computed it once before metadata_prefilter (dead — immediately
    superseded by the post-prefilter catalog) and once after.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from catalog import SERIES_DEFAULT_TITLE


@dataclass(frozen=True)
class MatchConfig:
    tie_margin: int = 4
    score_cutoff: int = 55
    filler_words: frozenset = field(default_factory=lambda: frozenset({
        "play", "the", "a", "an", "book", "please", "can", "you", "i", "by", "narrated",
        "want", "to", "hear", "listen", "story", "audiobook",
        "resume", "continue", "where", "left", "off", "was", "listening", "at",
    }))


DEFAULT_CONFIG = MatchConfig()

ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20,
}
NUMBER_RE = re.compile(r"\b(\d+)\b")


def confidence_label(score: float) -> str:
    if score >= 85:
        return "high"
    if score >= 70:
        return "medium"
    return "low"


def strip_filler(text: str, config: MatchConfig = DEFAULT_CONFIG) -> str:
    words = [w for w in text.lower().split() if w not in config.filler_words]
    return " ".join(words) or text


def normalized_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def metadata_prefilter(query: str, catalog: list[dict]) -> tuple[str, list[dict]]:
    """Narrow candidates when the utterance names an indexed person/group."""
    normalized_query = normalized_text(query)
    matched_indices: set[int] = set()
    matched_values: set[str] = set()
    for i, entry in enumerate(catalog):
        for field_name in ("authors", "narrators", "collections"):
            for value in entry.get("metadata", {}).get(field_name, []):
                normalized_value = normalized_text(value)
                if normalized_value and normalized_value in normalized_query:
                    matched_indices.add(i)
                    matched_values.add(normalized_value)
                    break
            if i in matched_indices:
                break

    if not matched_indices:
        return query, catalog

    matches = [catalog[i] for i in sorted(matched_indices)]

    # Remove the matched metadata phrase before title scoring. A request such
    # as "a book by Jeff Kinney" should rank the title words, not repeat the
    # author's name for every candidate.
    title_query = normalized_query
    for value in sorted(matched_values, key=len, reverse=True):
        title_query = re.sub(rf"\b{re.escape(value)}\b", " ", title_query)
    title_query = " ".join(title_query.split())
    return title_query, matches


def extract_book_number(utterance: str) -> int | None:
    lower = utterance.lower()
    for word, num in ORDINAL_WORDS.items():
        if word in lower:
            return num
    m = NUMBER_RE.search(lower)
    return int(m.group(1)) if m else None


def best_score(query: str, text: str, **_ignored) -> float:
    # WRatio empirically misranks short-phrase-vs-long-title matches here
    # (scored an unrelated Harry Potter title at 85 against a Wimpy Kid
    # request that scored 51) — partial_ratio and token_set_ratio both rank
    # correctly on the same real test data, so use the better of the two.
    return max(
        fuzz.partial_ratio(query, text),
        fuzz.token_set_ratio(query, text),
    )


def list_summary(catalog: list[dict]) -> str:
    sample = [c["title"] for c in catalog[:4]]
    if not sample:
        return "You don't have any audiobooks available right now."
    joined = ", ".join(sample[:-1]) + (
        f", and {sample[-1]}" if len(sample) > 1 else sample[0]
    )
    return f"You have some great stories to choose from, like {joined}."


def resolve(
    utterance: str,
    catalog: list[dict],
    *,
    mode: str = "search",
    config: MatchConfig = DEFAULT_CONFIG,
) -> dict:
    """Match an utterance against a catalog.

    Returns one of:
      {"uri", "title", "confidence"}      — a confident match
      {"disambiguation": [{"title","uri"}, ...]}  — a handful of close ties
      {"error": "no_match"}               — nothing close enough

    `mode == "resume"` changes only one thing: when the utterance has no
    title words left after filler-stripping (a bare "resume"/"continue"),
    the caller is expected to have already pre-filtered `catalog` down to
    in-progress items sorted most-recent-first, so `catalog[0]` is returned
    directly instead of a no_match.
    """
    if not catalog:
        return {"error": "no_match"}

    query = strip_filler(utterance, config)
    query, catalog = metadata_prefilter(query, catalog)
    if not query:
        if len(catalog) == 1:
            e = catalog[0]
            return {"uri": e["uri"], "title": e["title"], "confidence": "high"}
        return {
            "disambiguation": [
                {"title": e["title"], "uri": e["uri"]} for e in catalog[:3]
            ]
        }

    search_texts = [c["search_text"] for c in catalog]
    results = process.extract(
        query, search_texts, scorer=best_score, limit=len(search_texts),
        score_cutoff=config.score_cutoff,
    )
    if not results:
        if mode == "resume":
            # A bare "resume" / "continue" / "where I left off" has no title
            # words to fuzzy-match at all — that's expected, not a failure.
            # The candidate list here is already pre-filtered to in-progress
            # items, so the most-recently-active one is simply the first.
            e = catalog[0]
            return {"uri": e["uri"], "title": e["title"], "confidence": "high"}
        return {"error": "no_match"}

    top_score = results[0][1]
    tied = [catalog[idx] for (_text, score, idx) in results if top_score - score <= config.tie_margin]

    if len(tied) == 1:
        e = tied[0]
        if confidence_label(top_score) == "low":
            return {"error": "no_match"}
        return {"uri": e["uri"], "title": e["title"], "confidence": confidence_label(top_score)}

    series_keys = {e["series"] for e in tied}
    if len(series_keys) == 1 and None not in series_keys:
        series = next(iter(series_keys))

        # An explicit book number ("book 2", "the second one") wins over any
        # default — search the WHOLE series in the catalog, not just the tied
        # subset, since the actual target may not have scored into the tie
        # group at all (e.g. "harry potter book 2" may score book 2 highest
        # outright, leaving nothing else close enough to "tie" with it).
        requested_num = extract_book_number(utterance)
        if requested_num is not None:
            numbered_match = next(
                (c for c in catalog if c["series"] == series and c["book_number"] == requested_num),
                None,
            )
            if numbered_match:
                return {"uri": numbered_match["uri"], "title": numbered_match["title"], "confidence": "high"}

        default_title = SERIES_DEFAULT_TITLE.get(series)
        default_match = next((e for e in tied if e["title"] == default_title), None)
        if default_match:
            pick = default_match
        else:
            numbered = [e for e in tied if e["book_number"] is not None]
            pick = min(numbered, key=lambda e: e["book_number"]) if numbered else tied[0]
        return {"uri": pick["uri"], "title": pick["title"], "confidence": "high"}

    return {
        "disambiguation": [
            {"title": e["title"], "uri": e["uri"]} for e in tied[:3]
        ]
    }
