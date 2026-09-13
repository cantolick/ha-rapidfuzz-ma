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


async def fetch_in_progress_audiobooks() -> list[dict]:
    """Audiobooks with real, unfinished playback progress, most-recent first.

    Filters and sorts client-side instead of asking Music Assistant to do it
    via `order_by` — the HA community has reported MA's own `last_played` /
    `last_played_desc` ordering as unreliable for Audiobookshelf-backed
    libraries:
    https://community.home-assistant.io/t/continue-audiobook-from-music-assistant/940483

    That report is about the *query*, not confirmed to be about the
    underlying per-item field values — so this works around it by reusing
    the same `music/audiobooks/library_items` call the catalog already
    trusts, and doing the "in progress, most recent" filtering ourselves.

    VERIFY BEFORE RELYING ON THIS: if the field names below
    (`resume_position_ms`, `fully_played`, `last_played`) don't match what
    your server actually returns, or if those per-item values are
    themselves inaccurate (not just MA's sort of them), this will still
    misbehave — check a real response from your own instance first.
    """
    items = await fetch_full_library()
    in_progress = [
        b for b in items
        if not b.get("fully_played") and (b.get("resume_position_ms") or 0) > 0
    ]
    in_progress.sort(key=lambda b: b.get("last_played") or "", reverse=True)
    return in_progress


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
