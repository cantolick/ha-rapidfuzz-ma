"""Direct Audiobookshelf client for in-progress lookups.

Why this goes straight to Audiobookshelf instead of asking Music Assistant
for progress: Music Assistant's own `last_played` / `last_played_desc`
ordering has been reported broken for Audiobookshelf-backed libraries (see
https://community.home-assistant.io/t/continue-audiobook-from-music-assistant/940483),
so "resume my book" can't be answered reliably from MA's own metadata alone.
What people in that thread actually got working is querying Audiobookshelf's
`/api/me/items-in-progress` endpoint directly, then handing playback back to
`music_assistant.play_media`/the matcher's own catalog machinery via the URI
convention Audiobookshelf-backed MA libraries use:
`audiobookshelf--<instance_id>://audiobook/<item_id>`.

This makes the "resume" intent specific to an Audiobookshelf-backed Music
Assistant setup. If you're on a different audiobook provider, `resume`
returns "unavailable" (see app.py) — the `search` and `list` intents don't
depend on any of this and work against any MA-backed playlist catalog
regardless of provider.
"""
from __future__ import annotations

import os

import httpx

ABS_URL = os.environ.get("ABS_URL", "")
ABS_TOKEN = os.environ.get("ABS_TOKEN", "")
ABS_INSTANCE_ID = os.environ.get("ABS_INSTANCE_ID", "")

CONFIGURED = bool(ABS_URL and ABS_TOKEN and ABS_INSTANCE_ID)


def build_ma_uri(item_id: str, instance_id: str) -> str:
    return f"audiobookshelf--{instance_id}://audiobook/{item_id}"


def _extract_title(item: dict) -> str:
    return (
        (item.get("media") or {}).get("metadata", {}).get("title")
        or item.get("title")
        or "Untitled"
    )


async def fetch_in_progress_audiobooks(limit: int = 10) -> list[dict]:
    """Returns [{"name": ..., "uri": ...}, ...] in Audiobookshelf's own
    most-recent-first order — already shaped for catalog.build_catalog().

    Raises RuntimeError if ABS_URL/ABS_TOKEN/ABS_INSTANCE_ID aren't set, or
    the underlying httpx exception if the request itself fails; app.py's
    /v1/assist catches either and reports "unavailable" rather than crashing.
    """
    if not CONFIGURED:
        raise RuntimeError(
            "ABS_URL/ABS_TOKEN/ABS_INSTANCE_ID not configured — resume "
            "requires a direct Audiobookshelf connection (see README)."
        )

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{ABS_URL}/api/me/items-in-progress",
            params={"limit": limit},
            headers={"Authorization": f"Bearer {ABS_TOKEN}"},
        )
        resp.raise_for_status()
        data = resp.json()

    items = data.get("libraryItems", []) if isinstance(data, dict) else []
    return [
        {"name": _extract_title(item), "uri": build_ma_uri(item["id"], ABS_INSTANCE_ID)}
        for item in items
        if item.get("id")
    ]
