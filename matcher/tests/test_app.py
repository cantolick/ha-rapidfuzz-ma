"""Covers the /v1/assist envelope logic in app.py — specifically the
passthrough-vs-not_found distinction, which lives in app.py rather than
core.py since it's about how to *phrase* a no-match, not how to *find* one.
"""
import asyncio

import app
from fixtures import make_catalog


def _assist(utterance, hint=None):
    app._catalogs = {app.FULL_LIBRARY_KEY: make_catalog()}
    app._catalogs_stale = False
    return asyncio.run(app.assist(app.AssistRequest(utterance=utterance, hint=hint)))


def test_non_book_play_request_is_silent_passthrough():
    # The HA trigger's "play ..." pattern also catches non-book requests
    # like this one — nothing here suggests a book was meant, so this
    # should stay silent rather than saying "I couldn't find that book".
    result = _assist("play taylor swift", hint="search")
    assert result["outcome"] == "passthrough"
    assert result["handled"] is False
    assert result["speech"] == ""
    assert result["media"] is None


def test_real_book_request_that_misses_is_not_found():
    # "book" is the signal word here — nothing in the catalog matches "the
    # wombat chronicles", but the phrase itself says this was a book ask.
    result = _assist("play the wombat chronicles book", hint="search")
    assert result["outcome"] == "not_found"
    assert "book" in result["speech"].lower()


def test_matching_book_request_still_plays():
    result = _assist("play the wimpy kid book", hint="search")
    assert result["outcome"] == "play"
    assert result["media"]["title"] == "Diary of a Wimpy Kid"


def test_list_intent_returns_info():
    result = _assist("what books do i have", hint="list")
    assert result["outcome"] == "info"
    assert result["media"] is None


def test_resume_without_audiobookshelf_configured_is_unavailable():
    result = _assist("resume my audiobook", hint="resume")
    assert result["outcome"] == "unavailable"
