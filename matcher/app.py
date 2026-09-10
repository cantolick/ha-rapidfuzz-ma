"""Local audiobook title matcher — replaces the Cloudflare Worker.

Fetches its own catalogs from Music Assistant at startup (one per configured
playlist, plus the full library for requests with no playlist named), so Home
Assistant only ever sends a tiny request: { utterance, playlist, mode }.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel
from rapidfuzz import fuzz, process

import ma_client
from catalog import SERIES_DEFAULT_TITLE, build_catalog

# playlist name -> list of MA library playlist item_ids to union. "teens"
# inherits everything in "kids" automatically — no hand-duplicating entries.
PLAYLIST_SOURCES = {
    "kids": ["10"],
    "teens": ["10", "11"],
}
FULL_LIBRARY_KEY = "__all__"  # used instead of None so the cache file (plain
# JSON, string keys only) round-trips without special-casing

CACHE_PATH = Path(os.environ.get("CACHE_PATH", "/data/catalog_cache.json"))
STARTUP_RETRY_ATTEMPTS = 3
STARTUP_RETRY_DELAY_SECS = 5

TIE_MARGIN = 4
SCORE_CUTOFF = 55
FILLER_WORDS = {
    "play", "the", "a", "an", "book", "please", "can", "you", "i",
    "want", "to", "hear", "listen", "story", "audiobook",
    "resume", "continue", "where", "left", "off", "was", "listening", "at",
}
ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20,
}
NUMBER_RE = re.compile(r"\b(\d+)\b")

_catalogs: dict[str, list[dict]] = {}  # playlist name (FULL_LIBRARY_KEY = no playlist) -> catalog
_catalogs_stale = False  # True if serving a disk-cached catalog, not a fresh MA fetch


async def _fetch_all_catalogs() -> dict:
    """Fetch fresh catalogs from MA. Raises on failure — caller decides whether
    to retry or fall back to the disk cache."""
    catalogs = {}
    for name, item_ids in PLAYLIST_SOURCES.items():
        seen_uris: set = set()
        raw_books: list = []
        for item_id in item_ids:
            for b in await ma_client.fetch_playlist_tracks(item_id):
                if b["uri"] not in seen_uris:
                    seen_uris.add(b["uri"])
                    raw_books.append(b)
        catalogs[name] = build_catalog(raw_books)
    catalogs[FULL_LIBRARY_KEY] = build_catalog(await ma_client.fetch_full_library())
    return catalogs


def _save_cache(catalogs: dict):
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(catalogs))
    except OSError as e:
        print(f"WARNING: could not write catalog cache to {CACHE_PATH}: {e}")


def _load_cache() -> Optional[dict]:
    if not CACHE_PATH.exists():
        return None
    try:
        return json.loads(CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"WARNING: could not read catalog cache at {CACHE_PATH}: {e}")
        return None


async def _load_catalogs():
    global _catalogs, _catalogs_stale

    last_error = None
    for attempt in range(1, STARTUP_RETRY_ATTEMPTS + 1):
        try:
            fresh = await _fetch_all_catalogs()
            _catalogs = fresh
            _catalogs_stale = False
            _save_cache(fresh)
            sizes = {k: len(v) for k, v in _catalogs.items()}
            print(f"Loaded fresh catalogs from Music Assistant: {sizes}")
            return
        except Exception as e:  # MA down, network error, unexpected response shape, etc.
            last_error = e
            if attempt < STARTUP_RETRY_ATTEMPTS:
                print(f"Music Assistant fetch failed (attempt {attempt}/{STARTUP_RETRY_ATTEMPTS}): {e}"
                      f" — retrying in {STARTUP_RETRY_DELAY_SECS}s")
                await asyncio.sleep(STARTUP_RETRY_DELAY_SECS)

    print(f"WARNING: Music Assistant unavailable after {STARTUP_RETRY_ATTEMPTS} attempts "
          f"({last_error}) — falling back to cached catalog")
    cached = _load_cache()
    if cached:
        _catalogs = cached
        _catalogs_stale = True
        sizes = {k: len(v) for k, v in _catalogs.items()}
        print(f"Loaded cached catalogs (STALE — MA was unreachable at startup): {sizes}")
    else:
        _catalogs = {}
        _catalogs_stale = True
        print("ERROR: no cached catalog available either — starting with empty catalogs. "
              "Matching will fail until Music Assistant is reachable and /refresh is called.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _load_catalogs()
    yield


app = FastAPI(lifespan=lifespan)


class Book(BaseModel):
    title: str
    uri: str


class MatchRequest(BaseModel):
    utterance: str
    playlist: Optional[str] = None
    # For requests where the candidate list can't be a static named playlist —
    # currently just the "resume" intent, matching against whatever Music
    # Assistant reports as in-progress right now, which changes every request.
    # Takes priority over `playlist` when both are present.
    books: Optional[list[Book]] = None
    mode: str = "search"


def confidence_label(score: float) -> str:
    if score >= 85:
        return "high"
    if score >= 70:
        return "medium"
    return "low"


def strip_filler(text: str) -> str:
    words = [w for w in text.lower().split() if w not in FILLER_WORDS]
    return " ".join(words) or text


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


@app.post("/match")
def match(req: MatchRequest):
    if req.books:
        catalog = build_catalog([{"name": b.title, "uri": b.uri} for b in req.books])
    else:
        catalog = _catalogs.get(req.playlist) if req.playlist else None
        if catalog is None:
            catalog = _catalogs.get(FULL_LIBRARY_KEY, [])
    if not catalog:
        return {"error": "no_match"}

    search_texts = [c["search_text"] for c in catalog]

    if req.mode == "list":
        sample = [c["title"] for c in catalog[:4]]
        if not sample:
            return {"response": "You don't have any audiobooks available right now."}
        joined = ", ".join(sample[:-1]) + (
            f", and {sample[-1]}" if len(sample) > 1 else sample[0]
        )
        return {"response": f"You have some great stories to choose from, like {joined}."}

    query = strip_filler(req.utterance)
    results = process.extract(
        query, search_texts, scorer=best_score, limit=len(search_texts), score_cutoff=SCORE_CUTOFF
    )
    if not results:
        if req.mode == "resume":
            # A bare "resume" / "continue" / "where I left off" has no title
            # words to fuzzy-match at all — that's expected, not a failure.
            # The candidate list here is already pre-filtered to in-progress
            # items, so the most-recently-active one is simply the first.
            e = catalog[0]
            return {"uri": e["uri"], "title": e["title"], "confidence": "high"}
        return {"error": "no_match"}

    top_score = results[0][1]
    tied = [catalog[idx] for (_text, score, idx) in results if top_score - score <= TIE_MARGIN]

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
        requested_num = extract_book_number(req.utterance)
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


@app.get("/health")
def health():
    return {
        "status": "ok" if _catalogs else "degraded",
        "stale": _catalogs_stale,
        "catalogs": {k: len(v) for k, v in _catalogs.items()},
    }


@app.post("/refresh")
async def refresh():
    await _load_catalogs()
    return {
        "status": "refreshed",
        "stale": _catalogs_stale,
        "catalogs": {k: len(v) for k, v in _catalogs.items()},
    }
