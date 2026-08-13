"""Light, testable normalization of a spoken transcript before the LLM.

Casual/dialectal speech-to-text is noisy: numbers come out as words
("twenty", "عشرين"), units are colloquial ("ملم", "بوصة"), and a few
dimension terms have dialect spellings. This step maps those to canonical
forms so the v1.0 prompt bundle gets clean input. It is a small curated
mapping + a bounded number-word folder — **not** a model, and every entry
is unit-tested.

Design constraints:
- **Conservative.** It only rewrites tokens it is confident about; anything
  else passes through untouched (the LLM is perfectly capable of Arabic).
- **Idempotent.** ``normalize(normalize(x)) == normalize(x)`` — digits and
  canonical unit tokens are not themselves number/unit words.
- **Word-scoped.** Substitutions act on whole whitespace tokens, never on
  substrings, so a unit word can't be spotted inside an unrelated word.

Known limitation (documented, not a bug): number words fused to an Arabic
preposition (e.g. ``بعشرين`` = "with twenty") are left alone rather than
risk stripping a leading ب/ل/ك from a real word. Standalone number words,
English compounds, and the ``و``-conjunction form are handled.
"""

from __future__ import annotations

import re

# Harakat (U+064B–U+0652), superscript alef (U+0670) and tatweel (U+0640).
_DIACRITICS = re.compile(r"[ً-ْٰـ]")

# Arabic-Indic (U+0660–0669) and extended/Persian (U+06F0–06F9) digits → ASCII.
_ARABIC_DIGITS = {0x0660 + i: str(i) for i in range(10)}
_ARABIC_DIGITS.update({0x06F0 + i: str(i) for i in range(10)})

# Fold hamzated-alef and alef-maksura spelling variants to their bare form so
# a single dictionary key matches every common spelling (أربعة/اربعة, على/علي).
_LETTER_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي"})

# --- number words -----------------------------------------------------------
# Values 0–90 (+ hundred/thousand). Both MSA and common Levantine/Egyptian
# dialect spellings are listed explicitly (after diacritic strip + letter fold).
_NUMBERS: dict[str, int] = {
    # English
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000,
    # Arabic ones (MSA + dialect)
    "صفر": 0,
    "واحد": 1, "وحده": 1, "وحدة": 1,
    "اثنين": 2, "اثنان": 2, "اتنين": 2, "تنين": 2,
    "ثلاثة": 3, "ثلاثه": 3, "تلاتة": 3, "تلاته": 3, "ثلاث": 3,
    "اربعة": 4, "اربعه": 4, "اربع": 4,
    "خمسة": 5, "خمسه": 5, "خمس": 5,
    "ستة": 6, "سته": 6, "ست": 6,
    "سبعة": 7, "سبعه": 7, "سبع": 7,
    "ثمانية": 8, "ثمانيه": 8, "تمانية": 8, "تمانيه": 8, "تمنية": 8,
    "تسعة": 9, "تسعه": 9, "تسع": 9,
    "عشرة": 10, "عشره": 10, "عشر": 10,
    # Arabic teens (single-word dialect forms; MSA two-word teens fold via sum)
    "حداشر": 11, "احداشر": 11,
    "اطناشر": 12, "اتناشر": 12, "اثناشر": 12,
    "تلاتاشر": 13, "ثلاثطعشر": 13,
    "اربعتاشر": 14, "اربعطاشر": 14,
    "خمستاشر": 15, "خمسطاشر": 15,
    "ستاشر": 16, "ستطاشر": 16,
    "سبعتاشر": 17, "سبعطاشر": 17,
    "تمنتاشر": 18, "تمنطاشر": 18,
    "تسعتاشر": 19, "تسعطاشر": 19,
    # Arabic tens
    "عشرين": 20,
    "ثلاثين": 30, "تلاتين": 30,
    "اربعين": 40, "خمسين": 50, "ستين": 60, "سبعين": 70,
    "ثمانين": 80, "تمانين": 80, "تسعين": 90,
    # Arabic hundreds / thousand
    "مئة": 100, "مائة": 100, "مية": 100, "ميه": 100,
    "مئتين": 200, "ميتين": 200,
    "الف": 1000, "الاف": 1000,
}

# --- units (spoken → canonical short form) ----------------------------------
_UNITS: dict[str, str] = {
    "mm": "mm", "millimeter": "mm", "millimeters": "mm", "millimetre": "mm",
    "millimetres": "mm",
    "ملم": "mm", "مليمتر": "mm", "ميليمتر": "mm", "ملي": "mm", "مم": "mm",
    "cm": "cm", "centimeter": "cm", "centimeters": "cm", "centimetre": "cm",
    "centimetres": "cm",
    "سم": "cm", "سانتيمتر": "cm", "سنتيمتر": "cm", "سانتي": "cm",
    "inch": "inch", "inches": "inch",
    "انش": "inch", "بوصة": "inch", "بوصه": "inch",
}

# --- dimension/connector dialect terms → canonical Arabic -------------------
# Deliberately tiny and high-confidence; the LLM already understands standard
# Arabic, so this only fixes clearly colloquial forms.
_TERMS: dict[str, str] = {
    "تخانة": "سماكة", "تخانه": "سماكة", "تخينة": "سماكة", "تخن": "سماكة",
    "قطره": "قطر",
    "سنون": "اسنان", "سنة": "سن",
}

_CONJUNCTIONS = {"و", "and"}
_TENS = {20, 30, 40, 50, 60, 70, 80, 90}
_ONES = {1, 2, 3, 4, 5, 6, 7, 8, 9}
_MULTIPLIERS = {100, 1000}


def _fold(token: str) -> str:
    return token.translate(_LETTER_FOLD)


def _number_value(token: str) -> int | None:
    return _NUMBERS.get(token)


def _eval_run(values: list[int]) -> int:
    """Combine a run of number-word values (order-agnostic for tens+unit).

    Handles English tens-then-unit ("twenty five" → 25), Arabic unit-then-tens
    ("خمسة وعشرين" → 25), and hundred/thousand multipliers ("three hundred").
    """
    total = 0
    current = 0
    for v in values:
        if v == 100:
            current = (current or 1) * 100
        elif v == 1000:
            current = (current or 1) * 1000
            total += current
            current = 0
        else:
            current += v
    return total + current


def _glued_waw_number(token: str) -> int | None:
    """Value of a ``و``-prefixed number token (وعشرين → 20), else None."""
    if token.startswith("و"):
        return _number_value(token[1:])
    return None


def _combines(last: int, nxt: int, *, conj: bool) -> bool:
    """Whether ``nxt`` joins the running number after ``last``.

    With an explicit conjunction (Arabic ``خمسة وعشرين`` → 25) always combine.
    Without one, combine only in unambiguous positional patterns; crucially,
    two bare adjacent numbers with no conjunction (Arabic ``اثنين عشرين`` =
    "two, twenty" = a module and a tooth count) stay SEPARATE.
    """
    if conj:
        return True
    if nxt in _MULTIPLIERS:  # "three hundred", "خمس مئة"
        return True
    if last in _TENS and nxt in _ONES:  # English "twenty five" = 25
        return True
    if last in _ONES and nxt == 10:  # ones + ten = teens ("اثنين عشرة" = 12)
        return True
    return last in _MULTIPLIERS and nxt < last  # "hundred twenty"


def _scan_number_run(folded: list[str], i: int) -> tuple[list[int], int]:
    """From index ``i``, collect a combinable number-word run.

    Returns ``(values, next_index)``. ``values`` is empty if the token at
    ``i`` does not begin a number. A ``و``/"and" conjunction (standalone or
    glued, e.g. ``وعشرين``) forces a combine; otherwise only positional
    patterns combine (see :func:`_combines`), so unconjoined adjacent numbers
    end the run and are emitted separately.
    """
    v0 = _number_value(folded[i])
    if v0 is None:
        return [], i
    values = [v0]
    k = i + 1
    pending_conj = False
    while k < len(folded):
        tok = folded[k]
        # standalone conjunction: consume only if a number follows
        if tok in _CONJUNCTIONS:
            if k + 1 < len(folded) and (
                _number_value(folded[k + 1]) is not None
                or _glued_waw_number(folded[k + 1]) is not None
            ):
                pending_conj = True
                k += 1
                continue
            break
        glued = _glued_waw_number(tok)
        if glued is not None:  # وعشرين — conjunction is implicit
            values.append(glued)
            k += 1
            pending_conj = False
            continue
        nxt = _number_value(tok)
        if nxt is None or not _combines(values[-1], nxt, conj=pending_conj):
            break
        values.append(nxt)
        k += 1
        pending_conj = False
    return values, k


def _map_word(folded: str) -> str | None:
    """Unit/term rewrite for a single non-number token, or None to keep it."""
    if folded in _UNITS:
        return _UNITS[folded]
    if folded in _TERMS:
        return _TERMS[folded]
    return None


def normalize(text: str) -> str:
    """Normalize a spoken transcript into cleaner input for the LLM.

    Removes Arabic diacritics/tatweel, converts Arabic-Indic digits, folds
    spoken number words to digits, and canonicalizes unit/dimension terms.
    Whitespace is collapsed; punctuation tokens pass through. Idempotent.
    """
    if not text or not text.strip():
        return ""
    # 1. character-level cleanup
    text = _DIACRITICS.sub("", text)
    text = text.translate(_ARABIC_DIGITS)
    # separate Arabic/ASCII commas so they don't glue to tokens
    text = text.replace("،", " ، ").replace(",", " , ")

    raw = text.split()
    folded = [_fold(t) for t in raw]

    out: list[str] = []
    i = 0
    while i < len(raw):
        values, j = _scan_number_run(folded, i)
        if values:
            out.append(str(_eval_run(values)))
            i = j
            continue
        replacement = _map_word(folded[i])
        out.append(replacement if replacement is not None else raw[i])
        i += 1
    return " ".join(out)
