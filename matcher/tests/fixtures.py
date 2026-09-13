"""A small, made-up catalog covering the code paths worth locking down:
plain title match, a numbered series tie, a series with a default-title
override, an alias-only book, and an author lookup."""
from catalog import build_catalog

RAW_BOOKS = [
    {"name": "Diary of a Wimpy Kid", "uri": "lib://1", "authors": ["Jeff Kinney"]},
    {"name": "Rodrick Rules | Diary of a Wimpy Kid", "uri": "lib://2", "authors": ["Jeff Kinney"]},
    {"name": "Harry Potter and the Sorcerer's Stone (Book 1)", "uri": "lib://3"},
    {"name": "Harry Potter and the Chamber of Secrets (Book 2)", "uri": "lib://4"},
    {"name": "Percy Jackson and the Lightning Thief", "uri": "lib://5", "authors": ["Rick Riordan"]},
    {"name": "The Hobbit", "uri": "lib://6"},
]


def make_catalog():
    return build_catalog(RAW_BOOKS)


RAW_TRACKS = [
    {"name": "Shake It Off", "uri": "music://track1", "artists": ["Taylor Swift"]},
    {"name": "Blank Space", "uri": "music://track2", "artists": ["Taylor Swift"]},
]


def make_music_catalog():
    return build_catalog(RAW_TRACKS)
