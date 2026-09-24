"""Covers the /v1/assist envelope logic in app.py — specifically the
passthrough-vs-not_found distinction, which lives in app.py rather than
core.py since it's about how to *phrase* a no-match, not how to *find* one.
"""
import asyncio

import app
import ma_client
from fixtures import make_catalog, make_music_catalog


def _assist(utterance, hint=None, with_music=False):
    app._catalogs = {app.FULL_LIBRARY_KEY: make_catalog()}
    if with_music:
        app._catalogs[app.MUSIC_CATALOG_KEY] = make_music_catalog()
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


def test_resume_when_music_assistant_unreachable_is_unavailable(monkeypatch):
    async def _raise(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ma_client, "fetch_in_progress_audiobooks", _raise)
    result = _assist("resume my audiobook", hint="resume")
    assert result["outcome"] == "unavailable"


def test_resume_with_nothing_in_progress_is_not_found(monkeypatch):
    async def _empty(*args, **kwargs):
        return []

    monkeypatch.setattr(ma_client, "fetch_in_progress_audiobooks", _empty)
    result = _assist("resume my audiobook", hint="resume")
    assert result["outcome"] == "not_found"


def test_resume_with_in_progress_book_plays_it(monkeypatch):
    async def _in_progress(*args, **kwargs):
        return [{"name": "The Hobbit", "uri": "lib://6"}]

    monkeypatch.setattr(ma_client, "fetch_in_progress_audiobooks", _in_progress)
    result = _assist("resume my audiobook", hint="resume")
    assert result["outcome"] == "play"
    assert result["media"]["title"] == "The Hobbit"


def test_non_book_request_without_music_catalog_is_still_passthrough():
    # Regression guard: no music catalog configured at all should behave
    # exactly like before the music fallback existed.
    result = _assist("play taylor swift", hint="search", with_music=False)
    assert result["outcome"] == "passthrough"


def test_non_book_request_falls_back_to_music_catalog_when_configured():
    # Two Taylor Swift tracks in the fixture catalog -> an artist-only
    # request can't pick one, so this should clarify, not passthrough or
    # crash. The key assertion is *not* passthrough: the music catalog was
    # actually consulted instead of giving up silently.
    result = _assist("play taylor swift", hint="search", with_music=True)
    assert result["outcome"] == "clarify"
    titles = {o["title"] for o in result["options"]}
    assert titles == {"Shake It Off", "Blank Space"}


def test_specific_track_title_plays_from_music_catalog():
    result = _assist("play shake it off", hint="search", with_music=True)
    assert result["outcome"] == "play"
    assert result["media"]["title"] == "Shake It Off"


def test_book_request_never_touches_music_catalog():
    # A real book match should win outright — music fallback only kicks in
    # after the book catalog comes up empty.
    result = _assist("play the wimpy kid book", hint="search", with_music=True)
    assert result["outcome"] == "play"
    assert result["media"]["title"] == "Diary of a Wimpy Kid"


def test_punctuated_book_word_is_recognised_as_a_book_request():
    # STT output ends with a period ("...book."); a missed match must still
    # apologise rather than going silent as if it weren't a book request.
    result = _assist("Play the wombat chronicles book.", hint="search")
    assert result["outcome"] == "not_found"


def test_not_found_repeats_what_was_heard():
    # Speech-to-text mishears titles ("Diary Vindicate"); echoing the
    # transcription lets the speaker notice and retry.
    result = _assist("Play Diary Vindicate, book 99.", hint="search")
    assert result["outcome"] == "not_found"
    assert 'I couldn\'t find "Diary Vindicate, book 99".' == result["speech"]


def test_heard_phrase_strips_only_the_leading_verb():
    assert app._heard_phrase("Play the Hobbit.") == "the Hobbit"
    assert app._heard_phrase("please listen to Hatchet book!") == "Hatchet book"
    assert app._heard_phrase("play") is None
