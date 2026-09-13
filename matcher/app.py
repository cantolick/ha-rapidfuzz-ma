"""Local audiobook title matcher — replaces the Cloudflare Worker.

Fetches its own catalogs from Music Assistant at startup (one per configured
playlist, plus the full library for requests with no playlist named), so Home
Assistant only ever sends a tiny request: { utterance, playlist, mode }.
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

import absclient
import core
import ma_client
import responses
from catalog import build_catalog

# playlist name -> list of MA library playlist item_ids to union (e.g. a
# "teens" playlist that should also include everything in "kids", so new
# kids content doesn't need to be hand-duplicated into teens). Configured via
# env var, not hardcoded — a prebuilt/pulled image has to be configurable
# without a rebuild. See .env.example for the expected JSON shape.
try:
    PLAYLIST_SOURCES: dict = json.loads(os.environ.get("PLAYLIST_SOURCES_JSON", "{}"))
except json.JSONDecodeError as e:
    print(f"WARNING: PLAYLIST_SOURCES_JSON is not valid JSON ({e}) — no named "
          f"playlists configured; only the full-library (no-playlist) catalog will work.")
    PLAYLIST_SOURCES = {}

FULL_LIBRARY_KEY = "__all__"  # used instead of None so the cache file (plain
# JSON, string keys only) round-trips without special-casing

CACHE_PATH = Path(os.environ.get("CACHE_PATH", "/data/catalog_cache.json"))
STARTUP_RETRY_ATTEMPTS = 3
STARTUP_RETRY_DELAY_SECS = 5

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


def _catalog_for(playlist: Optional[str]) -> list[dict]:
    catalog = _catalogs.get(playlist) if playlist else None
    if catalog is None:
        catalog = _catalogs.get(FULL_LIBRARY_KEY, [])
    return catalog


@app.post("/match")
def match(req: MatchRequest):
    if req.books is not None:
        if not req.books:
            # Explicitly-empty list (e.g. "no audiobooks currently in
            # progress") is not "books wasn't supplied" — don't silently
            # fall through to searching the full playlist/library catalog.
            return {"error": "no_match"}
        catalog = build_catalog([{"name": b.title, "uri": b.uri} for b in req.books])
    else:
        catalog = _catalog_for(req.playlist)
    if not catalog:
        return {"error": "no_match"}

    if req.mode == "list":
        return {"response": core.list_summary(catalog)}

    return core.resolve(req.utterance, catalog, mode=req.mode)


class AssistRequest(BaseModel):
    utterance: str
    # The HA trigger id (search/resume/list) that fired, if any — advisory
    # only. Classification is re-derived from the utterance text below so
    # there's one source of truth for "what kind of request is this," not
    # one copy in the HA blueprint's trigger patterns and another here.
    hint: Optional[str] = None
    playlist: Optional[str] = None
    device_id: Optional[str] = None
    conversation_id: Optional[str] = None
    language: str = "en"
    max_options: int = 2


_LIST_PHRASES = ("what books", "which books", "what audiobooks", "what do i have")
_RESUME_PHRASES = (
    "resume", "continue", "where i left", "where was i",
    "was i listening", "was i reading",
)
# The HA trigger's "play/read/listen to ..." pattern matches any request
# starting with those verbs, not just books (see README) — so a search that
# comes up empty is ambiguous: a real book request that missed, or a non-book
# request ("play Taylor Swift") that should never have landed here. Whether
# the utterance names a book-ish noun at all is the cheapest signal for
# telling those apart — not proof, but enough to avoid confidently telling
# someone "I couldn't find that book" when they never asked for one.
_BOOK_SIGNAL_WORDS = {"book", "audiobook", "story", "chapter"}


def _mentions_book(utterance: str) -> bool:
    return bool(set(utterance.lower().split()) & _BOOK_SIGNAL_WORDS)


def _classify_intent(utterance: str, hint: Optional[str]) -> str:
    u = utterance.lower()
    if any(p in u for p in _LIST_PHRASES):
        return "list"
    if any(p in u for p in _RESUME_PHRASES):
        return "resume"
    if hint in ("search", "resume", "list"):
        return hint
    return "search"


def _envelope(outcome: str, speech: str, *, handled: bool = True, media: Optional[dict] = None,
              options: Optional[list] = None, continue_conversation: bool = False,
              debug: Optional[dict] = None) -> dict:
    return {
        "schema": 1,
        "outcome": outcome,
        "handled": handled,
        "speech": speech,
        "media": media,
        "options": options or [],
        "continue_conversation": continue_conversation,
        "debug": debug or {},
    }


def _envelope_from_match_result(result: dict, intent: str, *, resume: bool, debug: dict) -> dict:
    if "uri" in result:
        speech = responses.resume(result["title"]) if resume else responses.play(result["title"])
        media = {"uri": result["uri"], "title": result["title"], "enqueue": "replace"}
        return _envelope("play", speech, media=media, debug={**debug, "confidence": result.get("confidence")})
    if "disambiguation" in result:
        titles = [d["title"] for d in result["disambiguation"]]
        return _envelope(
            "clarify", responses.clarify(titles),
            options=result["disambiguation"], continue_conversation=True, debug=debug,
        )
    return _envelope("not_found", responses.not_found(), debug=debug)


@app.post("/v1/assist")
async def assist(req: AssistRequest):
    """Single entry point for the HA blueprint: classify, look up, match, and
    return a small TTS-ready envelope. HA still owns executing playback and
    transport controls (pause/stop/next chapter) — this only decides *what*
    to play or say. See README for the full response contract."""
    intent = _classify_intent(req.utterance, req.hint)
    debug_base = {"intent": intent, "stale": _catalogs_stale}

    if intent == "list":
        catalog = _catalog_for(req.playlist)
        titles = [c["title"] for c in catalog[:4]]
        return _envelope(
            "info", responses.list_summary(titles),
            debug={**debug_base, "catalog_size": len(catalog)},
        )

    if intent == "resume":
        try:
            in_progress_raw = await absclient.fetch_in_progress_audiobooks()
        except Exception as e:
            return _envelope("unavailable", responses.unavailable(), debug={**debug_base, "reason": str(e)})
        if not in_progress_raw:
            return _envelope("not_found", responses.nothing_in_progress(), debug=debug_base)
        catalog = build_catalog(in_progress_raw)
        result = core.resolve(req.utterance, catalog, mode="resume")
        return _envelope_from_match_result(
            result, intent, resume=True, debug={**debug_base, "catalog_size": len(catalog)},
        )

    # search
    catalog = _catalog_for(req.playlist)
    if not catalog:
        return _envelope("unavailable", responses.unavailable(), debug=debug_base)
    result = core.resolve(req.utterance, catalog, mode="search")
    debug = {**debug_base, "catalog_size": len(catalog)}
    if result == {"error": "no_match"} and not _mentions_book(req.utterance):
        # No match, and nothing in the phrase even suggests a book was
        # meant — likely a non-book "play ___" request that the HA trigger
        # over-broadly caught. Stay silent instead of speaking a "couldn't
        # find that book" apology for a request that was never about a book.
        return _envelope("passthrough", "", handled=False, debug={**debug, "reason": "no book-signal word"})
    return _envelope_from_match_result(result, intent, resume=False, debug=debug)


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
