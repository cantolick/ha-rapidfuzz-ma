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
    result = await _call(
        "music/audiobooks/library_items",
        {"limit": 500, "summary": False},
    )
    return result if isinstance(result, list) else []
