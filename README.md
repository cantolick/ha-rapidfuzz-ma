# Audiobook Matcher for Home Assistant + Music Assistant

A small local service that lets a kid ask a [Home Assistant Voice Preview](https://www.home-assistant.io/voice-pe/)
device for an audiobook by name — "play the wimpy kid book," "play harry potter
book 2," "play percy jackson" — and have it actually find and play the right
title from a [Music Assistant](https://www.music-assistant.io/) library, even
when the phrase doesn't exactly match the catalog text.

It started as a Cloudflare Worker calling an LLM to do the matching. It's now a
self-contained fuzzy-matching microservice — no LLM, no cloud dependency, sub-
millisecond matching, and it runs entirely on your own network. [Read the full
story of that pivot and why](https://claude.ai/code/artifact/28beb3fd-6226-4218-8fc8-be45264b689c).

## How it works

```
Voice Preview → HA script → this service → Music Assistant → speaker
```

1. Home Assistant sends `{ utterance, playlist, mode }` to this service.
2. The service fetches and caches Music Assistant playlists **itself**, at
   startup — Home Assistant never needs to pass a book list at all.
3. `rapidfuzz` matches the utterance against the cached catalog. Series names
   ("harry potter"), explicit book numbers ("book 2," "the second one"), and
   character/series aliases that don't literally appear in a title ("narnia" →
   *The Lion, the Witch, and the Wardrobe*) are all handled without an LLM.
4. A confident single match plays directly. A handful of close candidates come
   back as a short disambiguation list instead of a guess. Nothing close enough
   returns a clean no-match — declining beats confidently playing the wrong book.

## Quick start

```bash
cd matcher
cp .env.example .env   # fill in your Music Assistant URL + token
docker compose up -d --build
```

**Or pull the pre-built image instead of building from source** — a GitHub
Actions workflow publishes `matcher/` to GitHub Container Registry on every
push, so a deploy target (a NAS, for example) never needs the source at all:

```bash
cd matcher
cp .env.example .env
docker compose -f docker-compose.nas.yml up -d
```

```bash
curl -X POST http://localhost:8010/match \
  -H "Content-Type: application/json" \
  -d '{"utterance": "play the wimpy kid book", "playlist": "kids", "mode": "search"}'
```

## Configuration

All configuration is environment variables — nothing to edit in the source,
including for a pulled prebuilt image (see `.env.example`):

| Env var | Purpose |
|---|---|
| `MA_URL` | Base URL of your Music Assistant server |
| `MA_TOKEN` | A Music Assistant long-lived API token |
| `PLAYLIST_SOURCES_JSON` | JSON object mapping a playlist name to the MA playlist item_id(s) it's built from |
| `CACHE_PATH` | Where the last-successful catalog is cached (default `/data/catalog_cache.json`) |

`PLAYLIST_SOURCES_JSON` example:
```json
{"kids": ["10"], "teens": ["10", "11"]}
```
A name can list more than one playlist id to compose them together — `teens`
above includes everything in playlist `10` *and* playlist `11`, so a book
curated into `kids` doesn't need to be hand-duplicated into `teens` too.
Omitting `playlist` in a request searches the full Music Assistant library
instead of any named playlist.

If Music Assistant is unreachable at startup (a real scenario — MA and this
service can race to come up after a reboot), the service retries a few times,
then falls back to the last cached catalog rather than crashing or serving
nothing. `/health` reports `"stale": true` when it's running on that cache
instead of a fresh fetch. `POST /refresh` re-fetches on demand.

## API

**`POST /match`**
```json
{ "utterance": "play harry potter book 2", "playlist": "kids", "mode": "search" }
```
Returns one of:
- `{ "uri": "...", "title": "...", "confidence": "high" | "medium" }` — a match
- `{ "disambiguation": [{ "title": "...", "uri": "..." }, ...] }` — ask the user which one
- `{ "error": "no_match" }`
- `{ "response": "..." }` — for `"mode": "list"`, a spoken summary instead of a match

**`GET /health`** / **`POST /refresh`** — see Configuration above.

## Home Assistant Blueprint

`blueprints/audiobook_voice_handler.yaml` is a ready-to-import automation
that wires a Voice Preview / Assist satellite device to this service — no
hand-edited YAML needed on the Home Assistant side.

**Import it**: Settings → Automations & Scenes → Blueprints → Import Blueprint,
paste:
```
https://github.com/cantolick/ha-rapidfuzz-ma/blob/main/blueprints/audiobook_voice_handler.yaml
```

**Prerequisite**: three `rest_command` services need to already exist in your
Home Assistant config — `ma_in_progress_audiobooks`, `ma_play_audiobook`, and
`book_match` (pointed at wherever this service runs). See `rest_commands.yaml`
in a Home Assistant config repo for the expected shape, or write your own —
the blueprint just calls them by name.

Once imported, creating an automation from the blueprint asks for 5 things —
which satellite device it listens to, which entity to speak responses
through, which entity actually controls playback, the Music Assistant queue
ID, and which playlist name to search. Repeat per child/device — that's the
whole "multi-kid" story, no YAML copy-pasting required.

## Project layout

```
matcher/
  app.py            # FastAPI service, matching logic
  catalog.py         # title normalization, series/alias data
  ma_client.py       # minimal Music Assistant API client
  Dockerfile
  docker-compose.yml
blueprints/
  audiobook_voice_handler.yaml   # importable HA automation blueprint
```

Home Assistant automations/scripts and any environment-specific config live
outside this public repo.
