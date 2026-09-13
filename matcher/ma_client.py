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


async def fetch_in_progress_audiobooks(limit: int = 20) -> list[dict]:
    """Audiobooks with real, unfinished playback progress, via Music
    Assistant's own dedicated endpoint for exactly this:
    https://www.music-assistant.io/api/#get-in-progress-items-audiobooks-podcast-episodes-

    This replaced an earlier version that fetched the full audiobook
    library and filtered/sorted "in progress" client-side, working around a
    community-reported bug in the generic library query's `order_by`.
    Confirmed against a live server's schema (<host>:8095/api-docs/swagger,
    MA 2.10.2): the `Audiobook` object has no `last_played` field at all —
    `Track` does, `Audiobook` doesn't — so sorting audiobooks by
    `last_played_desc` wasn't a subtle bug, it was ordering by a field that
    doesn't exist for that media type. `fully_played` and
    `resume_position_ms` are real, confirmed fields on `Audiobook`, and
    `name`/`uri` match every other MA item already used in this file.

    The endpoint's own docs describe it as covering audiobooks *and*
    podcast episodes together; `media_type` is a confirmed field on both
    (enum value `"audiobook"` vs `"podcast_episode"`), so filtered to
    audiobooks only — a kids' audiobook player has no reason to offer to
    resume a podcast.

    Not independently confirmed: the exact response envelope for this
    specific command (a bare list, same as every other endpoint here, is
    assumed but not shown on the docs page).
    """
    result = await _call("music/in_progress_items", {"limit": limit})
    items = result if isinstance(result, list) else []
    return [item for item in items if item.get("media_type") == "audiobook"]


async def fetch_music_tracks(limit: int = 500) -> list[dict]:
    """All tracks across whatever music providers are configured in Music
    Assistant (Apple Music, Spotify, etc.) — a different media type than
    audiobooks in MA's data model, not just a differently-filtered view of
    the same list, so this should never surface Audiobookshelf content.

    Called unconditionally at startup (see app.py) and allowed to fail —
    a server with no music providers configured, or an older MA version
    without this endpoint, just means the music fallback in /v1/assist has
    nothing to fall back to, not a broken deployment.

    Confirmed against a live server (<host>:8095/api-docs, MA 2.10.2): the
    command is real (listed in the Commands Reference under "Music"), and
    the `Track` schema has an `artists` field (list of `Artist`/
    `ItemMapping` objects, each with a `name`) matching what `catalog.py`
    reads. The `{limit, summary}` args mirror `music/audiobooks/library_items`
    by convention, not independently confirmed for this specific command.
    """
    result = await _call(
        "music/tracks/library_items",
        {"limit": limit, "summary": False},
    )
    return result if isinstance(result, list) else []
