import core
from fixtures import make_catalog


def test_exact_title_match_is_confident():
    catalog = make_catalog()
    result = core.resolve("the hobbit", catalog)
    assert result["title"] == "The Hobbit"
    assert result["confidence"] in ("high", "medium")


def test_filler_stripped_short_phrase_hits_series_default():
    catalog = make_catalog()
    result = core.resolve("play the wimpy kid book", catalog)
    assert result["title"] == "Diary of a Wimpy Kid"


def test_explicit_book_number_wins_within_series():
    catalog = make_catalog()
    result = core.resolve("harry potter book 2", catalog)
    assert result["title"] == "Harry Potter and the Chamber of Secrets (Book 2)"
    assert result["confidence"] == "high"


def test_no_match_for_unrelated_request():
    catalog = make_catalog()
    result = core.resolve("what's the weather like today", catalog)
    assert result == {"error": "no_match"}


def test_empty_catalog_is_no_match():
    assert core.resolve("anything", []) == {"error": "no_match"}


def test_resume_with_no_title_words_picks_first_in_progress():
    # Caller is expected to pre-sort the catalog most-recent-first for resume
    # — core.resolve just trusts catalog[0] when there's nothing to fuzzy-match.
    catalog = make_catalog()
    result = core.resolve("resume where I left off", catalog, mode="resume")
    assert result["uri"] == catalog[0]["uri"]


def test_resume_still_matches_a_named_title():
    catalog = make_catalog()
    result = core.resolve("continue the hobbit", catalog, mode="resume")
    assert result["title"] == "The Hobbit"


def test_metadata_prefilter_narrows_by_author_to_a_single_match():
    # Percy Jackson is the only entry with "Rick Riordan" as an author, so an
    # author-only request (no title words survive filler-stripping) should
    # resolve directly instead of falling into disambiguation.
    catalog = make_catalog()
    result = core.resolve("a book by rick riordan", catalog)
    assert result["title"] == "Percy Jackson and the Lightning Thief"


def test_metadata_prefilter_narrows_to_multiple_authors_disambiguates():
    # Both Wimpy Kid entries share "Jeff Kinney" as an author and neither
    # title word survives — should offer both, not silently pick one.
    catalog = make_catalog()
    result = core.resolve("a book by jeff kinney", catalog)
    assert "disambiguation" in result
    titles = {d["title"] for d in result["disambiguation"]}
    assert titles == {"Diary of a Wimpy Kid", "Rodrick Rules | Diary of a Wimpy Kid"}


def test_list_summary_lists_titles():
    catalog = make_catalog()
    summary = core.list_summary(catalog)
    assert "Diary of a Wimpy Kid" in summary


def test_list_summary_empty_catalog():
    assert core.list_summary([]) == "You don't have any audiobooks available right now."


def test_low_confidence_tie_declines_instead_of_disambiguating():
    # Live-testing found: "play taylor swift" against a real catalog scored
    # two completely unrelated titles into a tie with EACH OTHER (58-60,
    # both "low" confidence by confidence_label's own scale) and confidently
    # offered them as "did you mean X or Y?" — neither had anything to do
    # with the query. A low top score should decline regardless of how many
    # candidates tied against each other.
    catalog = make_catalog()
    result = core.resolve("play taylor swift", catalog)
    assert result == {"error": "no_match"}


def test_metadata_prefilter_no_match_returns_full_catalog_unchanged():
    catalog = make_catalog()
    query, filtered = core.metadata_prefilter("harry potter", catalog)
    assert filtered == catalog
    assert query == "harry potter"


def test_speech_to_text_punctuation_does_not_defeat_filler_stripping():
    # Found live: Home Assistant's STT always ends a sentence with a period,
    # so "Play Hatchet book." left a stray "book." in the query. Without the
    # period the same request reduces cleanly to "hatchet".
    assert core.strip_filler("Play Hatchet book.") == core.strip_filler("Play Hatchet book") == "hatchet"


def test_punctuated_request_with_book_word_still_matches():
    catalog = make_catalog()
    for utterance in ("Play the Hobbit book.", "Play The Hobbit, book!", "play the hobbit"):
        assert core.resolve(utterance, catalog)["title"] == "The Hobbit", utterance


def test_words_keeps_internal_apostrophes_and_hyphens():
    assert core.words("Play Charlotte's Web, Spider-Man!") == ["play", "charlotte's", "web", "spider-man"]


def test_garbled_series_name_with_a_book_number_still_finds_the_book():
    # Found live: STT heard "Diary of a Wimpy Kid, book 16" as "Diary
    # Vindicate, book 16." — the series word survives, the number is clear.
    catalog = make_catalog()
    assert core.resolve("Play Dairy Vindicate, book 2.", catalog)["title"] == "Rodrick Rules | Diary of a Wimpy Kid"
    assert core.resolve("Play Hairy Potter, book 2.", catalog)["title"] == "Harry Potter and the Chamber of Secrets (Book 2)"


def test_series_fallback_needs_both_a_series_word_and_a_number():
    catalog = make_catalog()
    assert core.resolve("Play Dairy Vindicate.", catalog) == {"error": "no_match"}
    assert core.resolve("Play Taylor Swift, book 2.", catalog) == {"error": "no_match"}
