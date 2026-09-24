"""Spoken-response phrasing for /v1/assist.

One place for all TTS-facing text, so adding or changing a response shape
(match confirmation, disambiguation, no-match, list summary, ...) never
requires touching the Home Assistant blueprint's YAML — it's a Python
change here instead.
"""
from __future__ import annotations


def _join_titles(titles: list[str]) -> str:
    if not titles:
        return ""
    if len(titles) == 1:
        return titles[0]
    return ", ".join(titles[:-1]) + f", and {titles[-1]}"


def play(title: str) -> str:
    return f"Playing {title}."


def resume(title: str) -> str:
    return f"Picking up {title} where you left off."


def clarify(titles: list[str]) -> str:
    options = titles[:2]
    if len(options) < 2:
        return f"Did you mean {options[0]}?" if options else "I found a few close matches."
    return f"I found a couple — did you mean {options[0]}, or {options[1]}?"


def not_found(heard: str | None = None) -> str:
    # Repeating what speech-to-text actually heard lets the speaker notice
    # a mis-transcription ("Diary Vindicate") and simply try again.
    if heard:
        return f'I couldn\'t find "{heard}".'
    return "I couldn't find a book called that."


def nothing_in_progress() -> str:
    return "You don't have any books in progress right now."


def list_summary(titles: list[str]) -> str:
    if not titles:
        return "You don't have any audiobooks available right now."
    return f"You have some great stories to choose from, like {_join_titles(titles[:4])}."


def unavailable() -> str:
    return "I can't reach the book library right now."


def service_error() -> str:
    return "Something went wrong looking that up."
