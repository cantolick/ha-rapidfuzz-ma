"""Build a searchable book catalog from raw Music Assistant playlist titles.

Each entry gets: a cleaned title, a best-effort series key + book number
(parsed from the title text or overridden below for series that don't name
themselves in the title), and any extra search aliases for character/series
names that never appear in the title text at all.
"""
import json
import re

# Series that ARE identifiable from the title/subtitle text itself, via a
# substring that appears somewhere in every book of that series.
SERIES_MARKERS = {
    "diary of a wimpy kid": "wimpy kid",
    "harry potter": "harry potter",
    "percy jackson": "percy jackson",
    "warriors: omen of the stars": "warriors",
    "little house": "little house",
    "land of stories": "land of stories",
}

# The book that should win a same-series tie when nothing distinguishes the
# request further — for series where MA's metadata doesn't tag every book
# with a "Book N" number (many Wimpy Kid entries have no number in the title
# at all, despite having a real position in the actual series), the explicit
# series-default title is more reliable than book-number parsing alone.
SERIES_DEFAULT_TITLE = {
    "wimpy kid": "Diary of a Wimpy Kid",
}

# title -> (series, book_number, [extra aliases]) overrides, for books whose
# series/character names never appear in the title text at all, or whose
# book number needs a manual correction.
OVERRIDES = {
    # Real Diary of a Wimpy Kid publication order, for the ~10 entries whose
    # MA title doesn't include "Book N" at all (confirmed against the 9 titles
    # that DO have a number parsed already — Third Wheel=7, Double Down=11,
    # Getaway=12, Wrecking Ball=14, Deep End=15, Big Shot=16, Diper Overlode=17,
    # No Brainer=18, Hot Mess=19 — all match real order, so this is trustworthy).
    "Diary of a Wimpy Kid": ("wimpy kid", 1, []),
    "Rodrick Rules | Diary of a Wimpy Kid": ("wimpy kid", 2, []),
    "The Diary of a Wimpy Kid | The Last Straw": ("wimpy kid", 3, []),
    "Diary of a Wimpy Kid: Dog Days": ("wimpy kid", 4, []),
    "Diary of a Wimpy Kid: The Ugly Truth": ("wimpy kid", 5, []),
    "Diary of a Wimpy Kid: Cabin Fever": ("wimpy kid", 6, []),
    "Diary of a Wimpy Kid: Hard Luck": ("wimpy kid", 8, []),
    "Diary of a Wimpy Kid: The Long Haul": ("wimpy kid", 9, []),
    "Diary of a Wimpy Kid: Old School": ("wimpy kid", 10, []),
    "Diary of a Wimpy Kid: The Meltdown": ("wimpy kid", 13, []),

    "The Lost Hero": ("percy jackson", 3, ["heroes of olympus", "lost hero"]),
    "The Ruins of Gorlan": ("rangers apprentice", 1, ["ranger's apprentice", "rangers apprentice", "gorlan"]),
    "The Lion, the Witch, and the Wardrobe (Unabridged) | The Chronicles of Narnia": (
        "narnia", 1, ["narnia", "lion witch wardrobe"]
    ),
    "The Hobbit": (None, None, ["bilbo", "middle earth"]),
    "Fantastic Mr. Fox": (None, None, ["mr fox", "the fox one"]),
    "Sherlock Holmes: The Hound of the Baskervilles (Unabridged)": (
        None, None, ["sherlock holmes", "hound of the baskervilles"]
    ),
}

BOOK_NUM_RE = re.compile(r"(?:book|#)\s*(\d+)", re.IGNORECASE)
UNABRIDGED_RE = re.compile(r"\s*\(Unabridged\)")


def clean_title(raw: str) -> str:
    return UNABRIDGED_RE.sub("", raw).strip()


def _named_values(value: object) -> list[str]:
    """Extract names from MA's string or entity-list metadata shapes."""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []

    names = []
    for item in value:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            name = item["name"].strip()
            if name:
                names.append(name)
    return names


def _metadata_aliases(book: dict) -> dict[str, list[str]]:
    """Return searchable MA metadata without depending on one provider shape."""
    metadata = book.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    fields = {
        "authors": book.get("authors"),
        "narrators": book.get("narrators"),
        "collections": metadata.get("collections"),
        "performers": metadata.get("performers"),
        "genres": metadata.get("genres"),
    }
    aliases = {key: _named_values(value) for key, value in fields.items()}

    description = metadata.get("description")
    if isinstance(description, str) and description.strip():
        aliases["description"] = [description.strip()]
    else:
        aliases["description"] = []
    return aliases


def build_catalog(raw_books: list[dict]) -> list[dict]:
    catalog = []
    for b in raw_books:
        raw_name = b["name"]
        title = clean_title(raw_name)
        uri = b["uri"]

        series, book_number, extra_aliases = None, None, []
        lname = raw_name.lower()

        for marker, series_key in SERIES_MARKERS.items():
            if marker in lname:
                series = series_key
                break

        m = BOOK_NUM_RE.search(raw_name)
        if m:
            book_number = int(m.group(1))

        if raw_name in OVERRIDES:
            o_series, o_num, o_aliases = OVERRIDES[raw_name]
            if o_series is not None:
                series = o_series
            if o_num is not None:
                book_number = o_num
            extra_aliases = o_aliases

        metadata_aliases = _metadata_aliases(b)
        searchable_metadata = [
            value
            for field in ("authors", "narrators", "collections", "performers")
            for value in metadata_aliases[field]
            if value not in extra_aliases
        ]

        # search_text is what fuzzy matching actually runs against. Keep the
        # structured fields too, so exact metadata prematching can be added
        # without parsing the flattened string later.
        search_text = " ".join([title] + extra_aliases + searchable_metadata)

        catalog.append({
            "title": title,
            "uri": uri,
            "series": series,
            "book_number": book_number,
            "aliases": extra_aliases,
            "metadata": metadata_aliases,
            "search_text": search_text,
        })
    return catalog


if __name__ == "__main__":
    raw = json.load(open("playlist_current.json"))
    catalog = build_catalog(raw)
    json.dump(catalog, open("catalog.json", "w"), indent=2)
    print(f"built catalog: {len(catalog)} books")
    series_counts = {}
    for c in catalog:
        if c["series"]:
            series_counts[c["series"]] = series_counts.get(c["series"], 0) + 1
    print("series detected:", series_counts)
