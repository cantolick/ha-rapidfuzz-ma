# Development notes

This is the deeper "why does it work this way" and "how do I hack on this"
reference. If you just want to run the service and import the blueprint,
see [README.md](README.md) — you shouldn't need anything here for that.

## Design notes on specific behaviors

### The general-music fallback ("play Taylor Swift")

A "play X" request that doesn't look book-related and doesn't match the
book catalog would otherwise get a silent `passthrough` (see the outcome
table in the README's API section) — better than a wrong-sounding "I
couldn't find that book," but it still doesn't play the song. The service
always attempts to fetch your Music Assistant music library (whatever
providers you have configured — Apple Music, Spotify, etc.) at startup and
tries it as a fallback before giving up; if you have no music providers
configured, or your MA version doesn't support the endpoint, that fetch
just fails quietly (a warning in the logs) and passthrough behaves exactly
as before — no separate flag to turn on.

Artist-only requests ("play Taylor Swift") and track-title requests ("play
Shake It Off") both work through the exact same matching logic already used
for audiobook titles and authors — `catalog.py` treats a track's `artists`
field the same way it treats an audiobook's `authors` field, so there's no
separate music-matching code path to maintain. This is scoped to Music
Assistant's `track` media type specifically, which is structurally separate
from `audiobook` in MA's data model — an Audiobookshelf-backed library
isn't reachable from this fallback. Confirmed against a live server's
schema (`<host>:8095/api-docs`, MA 2.10.2): `Track` has the `artists` field
this relies on, and `music/tracks/library_items` is a real, listed command.
The book catalog is always tried first; music is only consulted when the
book search comes up empty.

**Fixed, found via live testing:** a short, generic non-book query used to
produce a false `clarify` (two unrelated titles tied against each other,
not against the query) instead of reaching passthrough at all — found
against a real 67-title catalog ("play taylor swift" → disambiguated
between two unrelated audiobooks, both scoring "low" confidence but tied
against each other). Root cause was in `core.resolve()`, not the
passthrough logic itself: the single-match branch already declined
low-confidence results, but the tied/disambiguation branches never checked
confidence at all. Fixed by moving the confidence gate up to run once,
before any tie/series/disambiguation logic, so a single weak match, a
same-series tie, and a bare multi-way tie are all covered by one check.
See the regression test in `matcher/tests/test_core.py` using the exact
real titles/query from live testing.

### How "resume" finds what's in progress

Music Assistant has a
[dedicated endpoint for exactly this](https://www.music-assistant.io/api/#get-in-progress-items-audiobooks-podcast-episodes-),
`music/in_progress_items` — a purpose-built "what's in progress" query, not
the generic library listing with an `order_by` sort the HA community has
[reported unreliable for Audiobookshelf-backed libraries](https://community.home-assistant.io/t/continue-audiobook-from-music-assistant/940483)
in an earlier version of this project. Confirmed against a live server's
schema: `Audiobook` has no `last_played` field at all (`Track` does,
`Audiobook` doesn't) — so that community-reported bug wasn't a subtle
sorting issue, it was ordering by a field that doesn't exist for that media
type. `fully_played` and `resume_position_ms` are real, confirmed fields on
`Audiobook`, and `media_type` (confirmed enum: `audiobook` vs.
`podcast_episode`) is used to filter out in-progress podcasts, since MA's
docs describe this endpoint as covering both together. No second,
Audiobookshelf-specific connection needed — `resume` goes through the same
Music Assistant connection as everything else here (a prior version of this
project added a direct Audiobookshelf connection for this; reverted in
favor of finding the right mechanism within the existing MA connection).
Music Assistant 2.7.0+ exposes these live schemas yourself at
`http://your-ma-host:8095/api-docs` (or `https://beta.music-assistant.io/api/`
for beta versions) if you want to verify any of this against your own
server.

**Known live-tested gaps, not yet fixed:**
- Nothing sorts the candidates `music/in_progress_items` returns — `core.resolve`'s
  resume path trusts `catalog[0]` is most-recent, which was never confirmed
  to be the endpoint's actual ordering.
- The progress lookup (under whatever account `MA_TOKEN` belongs to) and
  the playback call (`music_assistant.play_media`, which resolves its own
  user from the HA calling context) may resolve different Music Assistant
  user identities independently — Audiobookshelf progress is per-user.
  `resume_position_ms` is fetched but never used or forwarded anywhere;
  "picking up where you left off" currently just hopes MA restores position
  server-side for whichever account it happens to pick.

### Why `passthrough` exists

The default `search_commands` trigger matches any "play/read/listen to
..." phrase, including non-book ones like "play Taylor Swift" (see the
Home Assistant Blueprint section in the README for the house-wide
blast-radius reasoning behind that default). Once that reaches the
service, a plain no-match would speak "I couldn't find that book" — a
confusing answer to someone who never asked for one. The service checks
whether the utterance contains a book-ish word ("book," "audiobook,"
"story," "chapter") before deciding: contains one → `not_found` (apologize,
a real book request just missed); doesn't → the music library gets tried
first, and only falls through to `passthrough` (stay silent) if that
doesn't find anything either. It's a cheap heuristic, not a fix for the
underlying trigger over-match — tightening `search_commands` is still the
real fix if this comes up often in your household.

### Other known open items (from a live-testing pass, not yet fixed)

- No `continue_on_error` or post-play verification on the blueprint's
  `music_assistant.play_media` step — a failure (e.g. a stale cached URI
  Music Assistant no longer recognizes) skips straight past the speech step
  silently instead of reporting anything.
- No check that the picked player isn't following a sync-group leader,
  which can redirect playback to a different room than the one you'd
  expect.
- Disambiguation (`clarify`) speaks a question, but the automation doesn't
  actually listen for a follow-up answer — `continue_conversation` and
  `conversation_id` are defined in the schema and currently unused.
- `music/tracks/library_items`'s and `music/in_progress_items`'s exact
  response envelope shape (bare list vs. something wrapped) is inferred
  from every other MA command in this file, not independently confirmed.

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

## Verifying facts against your own Music Assistant server

Several details above are stated as *confirmed*, not guessed — verified
against a live server's schema during development, not just inferred from
naming conventions. If you're changing anything that touches the Music
Assistant API, verify it the same way rather than guessing:

- `http://<ma-host>:8095/api-docs` (MA 2.7.0+) — landing page linking to the
  schema browser and the Commands Reference.
- `http://<ma-host>:8095/api-docs/swagger` — full data-model schemas
  (`Track`, `Audiobook`, etc.) with every field, type, and enum value.
- `http://<ma-host>:8095/api-docs/commands` — the full list of callable
  `command` strings, searchable. The OpenAPI spec at `/api-docs/openapi.json`
  does **not** list these — it only documents the generic `POST /api`
  envelope.
- Public docs (no server needed): https://www.music-assistant.io/api/ (or
  https://beta.music-assistant.io/api/ for beta versions) — has
  copy-pasteable curl + HA `rest_command` examples for common commands.
