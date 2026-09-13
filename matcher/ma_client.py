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


async def fetch_in_progress_audiobooks(limit: int = 10) -> list[dict]:
    """Audiobooks (and podcast episodes — see below) with real, unfinished
    playback progress, via Music Assistant's own dedicated endpoint for
    exactly this:
    https://www.music-assistant.io/api/#get-in-progress-items-audiobooks-podcast-episodes-

    This replaced an earlier version that fetched the full audiobook
    library and filtered/sorted "in progress" client-side, working around a
    community-reported bug in the generic library query's `order_by`. This
    dedicated command should already return the correct, ordered list
    without that workaround — it's what the earlier approach should have
    used from the start.

    NOTE: MA's docs describe this as covering audiobooks *and* podcast
    episodes together, with no `media_type` filter argument shown — so a
    podcast episode in progress could show up as a resume candidate
    alongside audiobooks. Not filtered out here since the field MA uses to
    distinguish them isn't confirmed; check <host>:8095/api-docs if that
    becomes a problem in practice.

    VERIFY BEFORE RELYING ON THIS: the command itself is documented, but the
    per-item shape isn't shown on that page — `catalog.build_catalog()`
    expects `name`/`uri` per item, matching every other MA endpoint already
    used in this file, but that's inferred from consistency, not confirmed
    for this specific command.
    """
    result = await _call("music/in_progress_items", {"limit": limit})
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
