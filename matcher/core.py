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
import string
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from catalog import SERIES_DEFAULT_TITLE, SERIES_MARKERS


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


_EDGE_PUNCTUATION = string.punctuation + "“”‘’"


def words(text: str) -> list[str]:
    """Lowercased whitespace tokens with punctuation trimmed off their edges.

    Speech-to-text output arrives punctuated ("Play Hatchet book."), and a
    token like "book." is neither a filler word nor a book-signal word to a
    plain str.split(). Trimming the edges only keeps internal apostrophes and
    hyphens ("charlotte's", "spider-man") intact.
    """
    return [w for w in (t.strip(_EDGE_PUNCTUATION) for t in text.lower().split()) if w]


def strip_filler(text: str, config: MatchConfig = DEFAULT_CONFIG) -> str:
    kept = [w for w in words(text) if w not in config.filler_words]
    return " ".join(kept) or text


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


def _resolve_fuzzy(
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

    # A low top score means nothing here actually resembles the query, no
    # matter how many candidates happen to tie against EACH OTHER. Gate on
    # confidence before even computing ties, so a single weak match, a
    # same-series tie, and a bare disambiguation tie are all covered by one
    # check instead of three separate ones. Found via live testing: "play
    # taylor swift" against a real catalog scored two completely unrelated
    # titles into a tie with each other (58-60, both "low") and confidently
    # asked "did you mean X or Y?" — neither had anything to do with the
    # query. Offering a choice between candidates that don't resemble the
    # query is worse than declining.
    if confidence_label(top_score) == "low":
        return {"error": "no_match"}

    tied = [catalog[idx] for (_text, score, idx) in results if top_score - score <= config.tie_margin]

    if len(tied) == 1:
        e = tied[0]
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


_SERIES_STOPWORDS = {"of", "a", "an", "the"}


def _series_number_fallback(utterance: str, catalog: list[dict], config: MatchConfig) -> dict | None:
    """Recover "<garbled series name>, book N" when normal matching finds nothing.

    Speech-to-text often mangles a series title ("Diary Vindicate, book 16")
    while getting the number right. If one recognisable series word survives
    (fuzzily — "dairy" still matches "diary") and the request names a book
    number, that's enough to pick the numbered book in that series.
    """
    number = extract_book_number(utterance)
    if number is None:
        return None
    query_tokens = [w for w in words(utterance) if len(w) >= 4 and w not in config.filler_words]
    hits = set()
    for phrase, series_key in SERIES_MARKERS.items():
        marker_tokens = [t for t in words(phrase) if t not in _SERIES_STOPWORDS and len(t) >= 4]
        if any(fuzz.ratio(q, m) >= 80 for q in query_tokens for m in marker_tokens):
            hits.add(series_key)
    if len(hits) != 1:
        return None
    series = next(iter(hits))
    match = next((c for c in catalog if c["series"] == series and c["book_number"] == number), None)
    if match is None:
        return None
    return {"uri": match["uri"], "title": match["title"], "confidence": "medium"}


def resolve(
    utterance: str,
    catalog: list[dict],
    *,
    mode: str = "search",
    config: MatchConfig = DEFAULT_CONFIG,
) -> dict:
    """Match an utterance against a catalog (see _resolve_fuzzy for the shapes).

    Falls back to series-word + book-number recovery when fuzzy matching
    finds nothing.
    """
    result = _resolve_fuzzy(utterance, catalog, mode=mode, config=config)
    if result == {"error": "no_match"}:
        return _series_number_fallback(utterance, catalog, config) or result
    return result
