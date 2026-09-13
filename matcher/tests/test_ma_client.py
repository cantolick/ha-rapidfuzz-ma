"""Covers the media_type filtering in fetch_in_progress_audiobooks — the one
piece of client-side logic on top of the raw MA call worth locking in."""
import asyncio

import ma_client


def test_filters_out_podcast_episodes(monkeypatch):
    async def _fake_call(command, args):
        assert command == "music/in_progress_items"
        return [
            {"name": "The Hobbit", "uri": "lib://1", "media_type": "audiobook"},
            {"name": "Some Podcast Ep 4", "uri": "lib://2", "media_type": "podcast_episode"},
        ]

    monkeypatch.setattr(ma_client, "_call", _fake_call)
    result = asyncio.run(ma_client.fetch_in_progress_audiobooks())
    assert len(result) == 1
    assert result[0]["name"] == "The Hobbit"


def test_non_list_response_returns_empty(monkeypatch):
    async def _fake_call(command, args):
        return {"error": "unexpected shape"}

    monkeypatch.setattr(ma_client, "_call", _fake_call)
    result = asyncio.run(ma_client.fetch_in_progress_audiobooks())
    assert result == []
