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
Voice Preview → HA automation (blueprint) → this service → Music Assistant → speaker
```

1. Home Assistant's conversation trigger only fires on book/audiobook
   phrasings (see [Home Assistant Blueprint](#home-assistant-blueprint) below)
   — everything else the satellite hears falls straight through to Home
   Assistant's normal Assist pipeline, untouched.
2. For a matched phrase, HA sends one request to this service's `/v1/assist`
   endpoint: `{ utterance, hint, playlist }`.
3. The service fetches and caches Music Assistant playlists **itself**, at
   startup — Home Assistant never needs to pass a book list at all.
4. `rapidfuzz` matches the utterance against the cached catalog. Series names
   ("harry potter"), explicit book numbers ("book 2," "the second one"), and
   character/series aliases that don't literally appear in a title ("narnia" →
   *The Lion, the Witch, and the Wardrobe*) are all handled without an LLM.
   `rapidfuzz` is what decides *which* book a fuzzy phrase resolves to — that
   part doesn't change no matter how the request reaches the service or how
   playback gets triggered.
5. A confident single match plays directly. A handful of close candidates come
   back as a short disambiguation list instead of a guess. Nothing close enough
   returns a clean no-match — declining beats confidently playing the wrong
   book. The service also returns ready-to-speak text for every outcome; HA
   speaks it via `set_conversation_response` if `speech_enabled` is on.
6. HA plays the result using the native `music_assistant.play_media` action,
   targeted at whichever Music-Assistant-backed player you picked when you set
   up the automation.

## Quick start

```bash
cd matcher
cp .env.example .env   # fill in your Music Assistant URL + token
docker compose up -d --build
```

**Or pull the pre-built image instead of building from source** — a GitHub
Actions workflow publishes `matcher/` to GitHub Container Registry on every
push (after the test suite passes), so a deploy target (a NAS, for example)
never needs the source at all:

```bash
cd matcher
cp .env.example .env
docker compose -f docker-compose.nas.yml up -d
```

```bash
curl -X POST http://localhost:8010/v1/assist \
  -H "Content-Type: application/json" \
  -d '{"utterance": "play the wimpy kid book", "playlist": "kids"}'
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
| `MUSIC_ENABLED` | `true` to also fetch a general music library (tracks) and fall back to it for non-book "play X" requests — see below. Default `false`. |

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

**Turning "play Taylor Swift" from silence into an actual answer:** by
default, a "play X" request that doesn't look book-related and doesn't match
the book catalog gets a silent `passthrough` (see the outcome table below) —
better than a wrong-sounding "I couldn't find that book," but it still
doesn't play the song. Set `MUSIC_ENABLED=true` to fetch your Music
Assistant music library (whatever providers you have configured — Apple
Music, Spotify, etc.) at startup and try it as a fallback before giving up.
Artist-only requests ("play Taylor Swift") and track-title requests ("play
Shake It Off") both work through the exact same matching logic already used
for audiobook titles and authors — `catalog.py` treats a track's `artists`
field the same way it treats an audiobook's `authors` field, so there's no
separate music-matching code path to maintain. This is scoped to Music
Assistant's `track` media type specifically, which is structurally separate
from `audiobook` in MA's data model — an Audiobookshelf-backed library
shouldn't be reachable from this fallback at all, though that's worth
confirming against your own instance (see `ma_client.fetch_music_tracks`)
before relying on it. The book catalog is always tried first; music is only
consulted when the book search comes up empty.

**How "resume" avoids Music Assistant's reported last-played bug:** the HA
community has
[reported MA's own `last_played`/`last_played_desc` ordering as unreliable](https://community.home-assistant.io/t/continue-audiobook-from-music-assistant/940483)
for Audiobookshelf-backed libraries — asking MA to *sort* by last-played via
that query parameter comes back wrong. Rather than adding a second,
Audiobookshelf-specific connection to work around that, `resume` reuses the
same `music/audiobooks/library_items` call the catalog already trusts and
does the "in progress, most recent" filtering and sorting itself in Python,
avoiding MA's specific `order_by` query path. This assumes the per-item
progress fields Music Assistant returns (`resume_position_ms`,
`fully_played`, `last_played`) are themselves accurate even though sorting
by them server-side isn't — not confirmed from here. **Check your own
server's response before relying on this**: Music Assistant 2.7.0+ exposes
live API docs at `http://your-ma-host:8095/api-docs` (or
`https://beta.music-assistant.io/api/` for beta versions) — look at what
`music/audiobooks/library_items` actually returns and adjust the field
names in `ma_client.fetch_in_progress_audiobooks` if they don't match.

## API

**`POST /v1/assist`** — the current endpoint; what the v2 blueprint calls.
```json
{ "utterance": "play harry potter book 2", "hint": "search", "playlist": "kids" }
```
`hint` is the HA trigger id (`search` / `resume` / `list`) if you have one —
advisory only. The service re-classifies from the utterance text itself, so
there's one source of truth for "what kind of request is this," not one copy
in the HA blueprint's trigger patterns and a second one here.

Always returns the same envelope shape:
```json
{
  "schema": 1,
  "outcome": "play",
  "handled": true,
  "speech": "Playing Harry Potter and the Chamber of Secrets.",
  "media": { "uri": "...", "title": "...", "enqueue": "replace" },
  "options": [],
  "continue_conversation": false,
  "debug": { "intent": "search", "catalog_size": 84, "stale": false }
}
```

| `outcome` | Meaning | `media` |
|---|---|---|
| `play` | Confident match (or resume target) | present |
| `clarify` | A couple of close ties — `speech` already phrases the question, `options` holds the raw titles/uris | absent |
| `not_found` | Nothing close enough, and the phrase itself suggests a book was meant | absent |
| `passthrough` | No match, and nothing in the phrase suggests a book was meant either (`handled: false`, `speech: ""`) — see below | absent |
| `info` | Response to "what books do I have" | absent |
| `unavailable` | Catalog empty, or (for resume) Music Assistant unreachable | absent |

`speech` is always present and always safe to speak verbatim. `media` is
present if and only if `outcome == "play"` — that's the only structural check
the blueprint needs to make.

**Why `passthrough` exists:** the default `search_commands` trigger matches
any "play/read/listen to ..." phrase, including non-book ones like "play
Taylor Swift" (see the known trade-off above). Once that reaches the
service, a plain no-match would speak "I couldn't find that book" — a
confusing answer to someone who never asked for one. The service checks
whether the utterance contains a book-ish word ("book," "audiobook,"
"story," "chapter") before deciding: contains one → `not_found` (apologize,
a real book request just missed); doesn't → `passthrough` (stay silent),
unless `MUSIC_ENABLED` is on, in which case the music library gets tried
first — see below. It's a cheap heuristic, not a fix for the underlying
trigger over-match — tightening `search_commands` per the note above is
still the real fix if this comes up often in your household.

**`POST /match`** — the original endpoint, still present for the
[legacy v1 blueprint](blueprints/legacy/audiobook_voice_handler_v1.yaml) or
any direct integration built against it.
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

**Only book/audiobook phrasings are intercepted.** The automation's
conversation trigger is scoped to specific request shapes — "play/read/listen
to/start ...", "resume my audiobook," "what books do I have," "pause/stop the
book," "next chapter." Anything else the satellite hears (lights, timers,
weather, general chat) isn't matched by this automation at all, so it falls
straight through to Home Assistant's normal Assist pipeline — including Nabu
Casa Cloud speech-to-text/text-to-speech and whatever conversation agent you
already have configured — completely unaffected by this blueprint.

The "play/read/listen to ..." trigger is a blueprint input (`search_commands`)
specifically because it's the one prone to false positives — the default
matches any request starting with those verbs, so "play some music" also goes
to the matcher and will likely come back as `not_found`. Tighten it yourself
(e.g. `"(play|read) [me] [the] {utterance} (book|story)"`) if that's a problem
in your household; no YAML editing required, just change the input.

Setup asks for four things:
- **Voice Satellite Device** — which device this automation listens to.
- **Music Assistant Player** — the media_player entity to play on and control
  (pause, next chapter). Filtered to Music-Assistant-backed players only.
  ⚠️ **If pause/next chapter don't work**, you likely need a *different*
  entity than you'd expect: a Voice PE's own native media_player is not the
  same entity Music Assistant plays through, and picking the wrong one will
  silently do nothing (see [this thread](https://community.home-assistant.io/t/continue-audiobook-from-music-assistant/940483)
  for the exact trap). Check Settings → Devices & Services → Music Assistant
  → the relevant player entity, and confirm it's the one showing live
  playback state (title, position) before assuming the automation is broken.
- **Playlist Name** — which `PLAYLIST_SOURCES_JSON` key to search.
- **Enable Spoken Responses** — off by default; see below.

**Spoken responses are off by default.** Some Home Assistant Voice Preview
devices on ESPHome 26.6.0/26.6.5 crash when playing any TTS audio — an
upstream firmware regression
([esphome/home-assistant-voice-pe#613](https://github.com/esphome/home-assistant-voice-pe/issues/613)),
not something specific to this project. There's also an open
[Home Assistant core issue](https://github.com/home-assistant/core/issues/138166)
where trigger-scoped `variables:` can interact badly with
`set_conversation_response` — test on one device before flipping
`speech_enabled` on fleet-wide. Once it's safe to enable, match confirmations,
disambiguation prompts, no-match apologies, and the book-list summary are all
spoken through the same Assist pipeline (Nabu Casa Cloud TTS, if that's what
you use) — no separate TTS integration needed.

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Fcantolick%2Fha-rapidfuzz-ma%2Fmain%2Fblueprints%2Faudiobook_voice_handler.yaml)

Or manually: Settings → Automations & Scenes → Blueprints → Import Blueprint,
paste:
```
https://raw.githubusercontent.com/cantolick/ha-rapidfuzz-ma/main/blueprints/audiobook_voice_handler.yaml
```

**Prerequisite**: one `rest_command` service needs to already exist in your
Home Assistant config — `book_assist`, pointed at your running matcher
instance's `/v1/assist` endpoint. See [`examples/rest_commands.yaml`](examples/rest_commands.yaml)
for the exact shape to copy into your config.

### Migrating from v1

The v2 blueprint is a real rewrite, not a drop-in — re-importing it over an
existing v1 automation needs a few minutes of reconfiguration, not just a
click:
- `queue_id` and `control_target` are gone; there's one `ma_player` input now.
- The required `rest_command` changes from three (`book_match`,
  `ma_in_progress_audiobooks`, `ma_play_audiobook`) to one (`book_assist`).
- `tts_target` is gone entirely — spoken responses go through
  `set_conversation_response` instead of a dedicated announcement target.

If you'd rather not migrate yet, [`blueprints/legacy/audiobook_voice_handler_v1.yaml`](blueprints/legacy/audiobook_voice_handler_v1.yaml)
is the exact frozen v1 blueprint and keeps working against `/match` as before.

## Testing

```bash
cd matcher
pip install -r requirements-dev.txt
pytest tests/ -v
```

`matcher/tests/` covers the pure matching core (`core.py`) against a small
in-memory catalog — filler stripping, series/book-number tie-breaking,
metadata-based author lookups, disambiguation, and the resume-with-no-title
fallback — with no FastAPI app or Music Assistant connection required. CI
runs this suite on every push to `matcher/**` and blocks the Docker publish
step if it fails.

## Project layout

```
matcher/
  app.py               # FastAPI service — request handling, intent classification
  core.py              # pure matching logic (filler stripping, scoring, tie-breaking)
  responses.py         # spoken-response phrasing for every /v1/assist outcome
  catalog.py           # title normalization, series/alias data
  ma_client.py         # minimal Music Assistant API client (catalog fetch, in-progress lookup)
  tests/               # pytest suite for core.py
  Dockerfile
  docker-compose.yml
  docker-compose.nas.yml
blueprints/
  audiobook_voice_handler.yaml          # current (v2) importable HA automation blueprint
  legacy/
    audiobook_voice_handler_v1.yaml     # frozen v1, still works against /match
examples/
  rest_commands.yaml   # copy-paste rest_command config for either blueprint version
```

Home Assistant automations/scripts and any environment-specific config live
outside this public repo.
