from vault.ai.fallback import (
    clean_series_title_fallback,
    clean_title_fallback,
    sanitize_filename,
)


def test_sanitize_filename():
    assert sanitize_filename("Intro: To Python / Part 1*?<>|") == "Intro To Python Part 1"
    assert sanitize_filename("...hidden...") == "hidden"
    assert sanitize_filename("Clean File Name") == "Clean File Name"


def test_clean_title_fallback():
    raw_1 = "MIT 6.006 Lecture 01: Algorithmic Thinking [1080p]"
    assert clean_title_fallback(raw_1, index=1) == "01. Algorithmic Thinking"

    raw_2 = "Lec 4 - Intro to Binary Trees (Fall 2023)"
    assert clean_title_fallback(raw_2, index=4) == "04. Intro to Binary Trees"

    raw_3 = "Building a Full Stack App - freeCodeCamp.org"
    cleaned_3 = clean_title_fallback(raw_3, index=1)
    assert "01. Building a Full Stack App" == cleaned_3


def test_clean_series_title_fallback():
    raw_series = "MIT 6.006 Introduction to Algorithms (Fall 2011) [Full Playlist]"
    title, instructor = clean_series_title_fallback(raw_series)
    assert "MIT 6.006 Introduction to Algorithms" in title
