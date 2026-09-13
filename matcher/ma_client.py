"""Minimal Music Assistant API client — just enough to fetch playlist tracks
and the full audiobook library at startup."""
import os

import httpx

MA_URL = os.environ.get("MA_URL", "http://music-assistant:8095")  # set via .env / compose env
MA_TOKEN = os.environ.get("MA_TOKEN", "")


async def _call(command: str, args: dict) -> object:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{MA_URL}/api",
            headers={"Authorization": f"Bearer {MA_TOKEN}", "Content-Type": "application/json"},
            json={"command": command, "args": args},
        )
        resp.raise_for_status()
        return resp.json()


async def fetch_playlist_tracks(item_id: str) -> list[dict]:
    result = await _call(
        "music/playlists/playlist_tracks",
        {"item_id": item_id, "provider_instance_id_or_domain": "library"},
    )
    return result if isinstance(result, list) else []


async def fetch_full_library() -> list[dict]:
    # NOTE: capped at 500 items with no pagination — a library bigger than
    # that silently truncates. Fine for a curated kids' playlist; revisit
    # with an offset loop if this is ever pointed at the full audiobook
    # library and that library grows past the cap.
    result = await _call(
        "music/audiobooks/library_items",
        {"limit": 500, "summary": False},
    )
    return result if isinstance(result, list) else []


async def fetch_music_tracks(limit: int = 500) -> list[dict]:
    """All tracks across whatever music providers are configured in Music
    Assistant (Apple Music, Spotify, etc.) — a different media type than
    audiobooks in MA's data model, not just a differently-filtered view of
    the same list, so this should never surface Audiobookshelf content.

    VERIFY BEFORE RELYING ON THIS: `music/tracks/library_items` mirrors the
    naming convention `music/audiobooks/library_items` already uses, but
    isn't independently confirmed against a live server from here. Check a
    real response shape (particularly the `artists` field catalog.py reads
    below) before turning MUSIC_ENABLED on.
    """
    result = await _call(
        "music/tracks/library_items",
        {"limit": limit, "summary": False},
    )
    return result if isinstance(result, list) else []
