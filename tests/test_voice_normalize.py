"""Tests for the voice dialect-normalization table (v1.1).

Weighted heavily: this is the layer most likely to silently corrupt a spoken
request. The invariant that matters most is that it never *merges* two
distinct numbers that a speaker meant separately (a module and a tooth count),
and never rewrites a substring inside an unrelated word.
"""

from __future__ import annotations

import pytest

from swpilot.voice.normalize import normalize


class TestEnglishNumbers:
    @pytest.mark.parametrize(
        ("spoken", "expected"),
        [
            ("twenty", "20"),
            ("twenty five", "25"),
            ("one hundred", "100"),
            ("hundred twenty", "120"),
            ("three hundred", "300"),
            ("sixteen", "16"),
            ("eight", "8"),
        ],
    )
    def test_english_numbers(self, spoken: str, expected: str) -> None:
        assert normalize(spoken) == expected

    def test_english_in_context(self) -> None:
        assert normalize("a plate one hundred by fifty by ten") == "a plate 100 by 50 by 10"


class TestArabicNumbers:
    @pytest.mark.parametrize(
        ("spoken", "expected"),
        [
            ("عشرين", "20"),
            ("ستاشر", "16"),  # dialect sixteen
            ("خمسة وعشرين", "25"),  # unit + tens with conjunction
            ("خمسة و عشرين", "25"),  # conjunction as a separate token
            ("واحد وعشرين", "21"),
            ("اثنين عشرة", "12"),  # ones + ten = teen
            ("مية وعشرين", "120"),
            ("ثلاثين", "30"),
            ("تلاتين", "30"),  # dialect thirty
        ],
    )
    def test_arabic_numbers(self, spoken: str, expected: str) -> None:
        assert normalize(spoken) == expected

    def test_diacritics_are_ignored(self) -> None:
        assert normalize("عِشْرين") == "20"

    def test_arabic_indic_digits_convert(self) -> None:
        assert normalize("قطر ٢٠ ملم") == "قطر 20 mm"


class TestNoBadMerges:
    """The core safety property of the number folder."""

    def test_two_bare_adjacent_numbers_stay_separate(self) -> None:
        # "module two, twenty teeth" — NOT 22. Arabic needs a و to combine.
        assert normalize("موديول اثنين عشرين سن") == "موديول 2 20 سن"

    def test_english_bare_ones_then_tens_stay_separate(self) -> None:
        # "two twenty" (2 then 20) is not 22 in English either
        assert normalize("two twenty") == "2 20"

    def test_gear_request_numbers_are_distinct(self) -> None:
        got = normalize("ترس موديول اثنين عشرين سن تجويف ستاشر وخابور")
        assert "2" in got.split()
        assert "20" in got.split()
        assert "16" in got.split()
        # the module and tooth count never collapse into one token
        assert "22" not in got.split()


class TestUnitsAndTerms:
    @pytest.mark.parametrize(
        ("spoken", "expected"),
        [
            ("ملم", "mm"),
            ("مليمتر", "mm"),
            ("مم", "mm"),
            ("mm", "mm"),
            ("سم", "cm"),
            ("بوصة", "inch"),
            ("inch", "inch"),
        ],
    )
    def test_units(self, spoken: str, expected: str) -> None:
        assert normalize(spoken) == expected

    def test_dialect_thickness_term(self) -> None:
        assert normalize("تخانة عشرة") == "سماكة 10"


class TestRobustness:
    def test_idempotent(self) -> None:
        samples = [
            "twenty five mm plate",
            "ترس موديول اثنين عشرين سن تجويف ستاشر",
            "قطر ٢٠ ملم",
            "already 20 mm and 16",
        ]
        for s in samples:
            once = normalize(s)
            assert normalize(once) == once

    def test_digits_pass_through(self) -> None:
        assert normalize("100 by 50 by 10 mm") == "100 by 50 by 10 mm"

    def test_no_substring_rewrite(self) -> None:
        # a unit/number word must not be matched inside a larger word
        assert normalize("commander") == "commander"  # contains "and"
        assert normalize("summit") == "summit"

    def test_empty_and_whitespace(self) -> None:
        assert normalize("") == ""
        assert normalize("   ") == ""

    def test_non_number_words_preserved(self) -> None:
        assert normalize("بدي ترس") == "بدي ترس"

    def test_lone_conjunction_not_consumed(self) -> None:
        # a trailing "and"/"و" with no following number is left as prose
        assert normalize("twenty and") == "20 and"
        assert normalize("عشرين و") == "20 و"
