# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Known-CAD-feature catalog with *computed* supported/coming-soon status.

The catalog lists CAD features a user might ask for, each tied to the ``op``
that implements it (or WOULD implement it in a future version). Whether a
feature is "coming soon" is never hand-maintained: it is computed as
*feature's op not present in the live Command union*. The moment a future op
ships in the schema, its feature automatically flips to supported and the
"coming soon" message stops appearing — zero manual edits, no drift. A CI
test pins this invariant.

Detection is deterministic (bilingual keyword regexes over the request
text); the pydantic validator remains the hard gate on the LLM's output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from swpilot.llm.vocabulary import all_ops


@dataclass(frozen=True)
class CadFeature:
    """One known CAD capability, implemented or planned."""

    key: str  # stable id == the op that does/would implement it
    label_en: str
    label_ar: str
    patterns: tuple[str, ...]  # case-insensitive regexes over the request text
    alternative_en: str = ""  # closest supported route (coming-soon entries)
    alternative_ar: str = ""


# The catalog: implemented features (their op is in the union today) AND
# planned ones (op name reserved for a future version). Membership in
# "coming soon" is COMPUTED from the live schema — never edit a status here.
CATALOG: tuple[CadFeature, ...] = (
    # ---- implemented today (op exists in the Command union) ----
    CadFeature("extrude", "extrude", "بثق", (r"\bextrude\b", r"\bبثق\b")),
    CadFeature("revolve", "revolve (solid of revolution)", "تدوير حول محور",
               (r"\brevolve\b", r"\blathe\b", r"مجسم دوراني", r"تدوير حول")),
    CadFeature("revolve_cut", "revolved cut (e.g. V-groove)", "قصّ دوراني (مجرى V)",
               (r"\brevolve[d]?[ _-]?cut\b", r"\bv[- ]?groove\b", r"قص دوراني", r"مجرى")),
    CadFeature("hole", "holes (incl. counterbore/countersink)", "ثقوب",
               (r"\bhole\b", r"\bثقب\b", r"\bثقوب\b", r"تجويف")),
    CadFeature("fillet", "fillet", "تدوير الحواف",
               (r"\bfillet\b", r"\bround(ed)? edges\b", r"تدوير الحواف")),
    CadFeature("chamfer", "chamfer", "شطف الحواف", (r"\bchamfer\b", r"\bشطف\b")),
    CadFeature("linear_pattern", "linear pattern", "مصفوفة خطية",
               (r"\blinear pattern\b", r"مصفوفة خطية")),
    CadFeature("circular_pattern", "circular pattern", "مصفوفة دائرية",
               (r"\bcircular pattern\b", r"مصفوفة دائرية")),
    CadFeature("involute_spur_gear", "spur gear", "ترس عدل",
               (r"\bspur gear\b", r"\bgear\b", r"\bترس\b", r"\bتروس\b")),
    CadFeature("sprocket_iso", "chain sprocket", "ترس جنزير",
               (r"\bsprocket\b", r"ترس (جنزير|سلسلة)")),
    CadFeature("helix_thread", "cosmetic helical thread", "تسنين شكلي",
               (r"\bthread\b", r"\bتسنين\b", r"\bقلاووظ\b")),
    CadFeature("create_drawing", "2D drawing sheet", "لوحة رسم",
               (r"\bdrawing\b", r"لوحة رسم", r"مخطط")),
    CadFeature("mate", "assembly mates", "قيود تجميع",
               (r"\bmate\b", r"\bassembly\b", r"تجميع", r"مجموعة")),
    # ---- planned (op name reserved; not in the union yet) ----
    CadFeature("sweep", "sweep", "سحب على مسار",
               (r"\bsweep\b", r"\bswept\b", r"سحب على مسار", r"سحب بمسار"),
               "a revolve for circular paths, or straight extrudes",
               "التدوير حول محور للمسارات الدائرية، أو البثق المستقيم"),
    CadFeature("loft", "loft", "نفخ بين مقاطع",
               (r"\bloft(s|ing|ed)?\b", r"نفخ بين مقاطع", r"ربط مقاطع"),
               "stacked extrudes between the profiles",
               "بثق متدرج بين المقاطع"),
    CadFeature("shell", "shell (hollowing)", "تجويف القشرة",
               (r"\bshell(ing|ed)?\b", r"\bhollow(ed)? out\b", r"تفريغ من الداخل", r"قشرة"),
               "a pocket cut with cut_extrude",
               "تفريغ بقصّ cut_extrude"),
    CadFeature("rib", "rib", "ضلع تقوية",
               (r"\brib(s)?\b", r"ضلع تقوية", r"اضلاع تقوية"),
               "a thin extruded plate as the stiffener",
               "لوح بثق رفيع كضلع تقوية"),
    CadFeature("draft", "draft angle", "زاوية سحب القالب",
               (r"\bdraft angles?\b", r"\bdrafted\b",
                r"\bdraft(ed)?\s+(faces?|walls?|sides?|taper)\b",
                r"\bmold draft\b", r"زاوية سحب", r"ميلان قالب"),
               "cut_extrude's draft_angle field for tapered blind cuts",
               "حقل draft_angle في cut_extrude للقصّ المائل المحدود العمق"),
    CadFeature("sheet_metal", "sheet metal (bends/flanges)", "صاج ومجلفن (ثني)",
               (r"\bsheet ?metal\b", r"\bbend(s|ing)?\b", r"\bflanges?\b", r"صاج", r"ثني معدن"),
               "a flat plate now; bends come with the sheet-metal op",
               "لوح مسطح حالياً؛ الثني يأتي مع ميزة الصاج"),
    CadFeature("mirror", "mirror feature", "نسخ بالمرآة",
               (r"\bmirror(ed)?\b", r"نسخ بالمرآة", r"انعكاس"),
               "a pattern, or model both sides explicitly",
               "مصفوفة، أو نمذجة الجهتين صراحة"),
    CadFeature("loft_surface", "surface modeling", "نمذجة سطوح",
               (r"\bsurface model(ing)?\b", r"\bboundary surface\b", r"نمذجة سطوح"),
               "solid features (extrude/revolve)",
               "المجسمات الصلبة (بثق/تدوير)"),
    CadFeature("helical_gear", "helical gear", "ترس حلزوني",
               (r"\bhelical gear\b", r"ترس حلزوني", r"ترس مائل"),
               "an involute spur gear of the same module",
               "ترس عدل بنفس الموديول"),
    CadFeature("bevel_gear", "bevel gear", "ترس مخروطي",
               (r"\bbevel gear\b", r"ترس مخروطي", r"ترس كرونة"),
               "a spur gear pair on parallel axes",
               "زوج تروس عدلة على محاور متوازية"),
    CadFeature("worm_gear", "worm gear", "ترس دودي",
               (r"\bworm( gear)?\b", r"ترس دودي", r"دودة\s*و\s*ترس"),
               "a spur gear + cosmetic thread on the worm blank",
               "ترس عدل + تسنين شكلي على جسم الدودة"),
)


def implemented_ops() -> frozenset[str]:
    """The ops the engine implements right now (from the live union)."""
    return frozenset(all_ops())


def coming_soon() -> tuple[CadFeature, ...]:
    """Catalog features NOT yet backed by a real op — computed, never listed."""
    ops = implemented_ops()
    return tuple(f for f in CATALOG if f.key not in ops)


def supported_features() -> tuple[CadFeature, ...]:
    """Catalog features whose op exists in the live union."""
    ops = implemented_ops()
    return tuple(f for f in CATALOG if f.key in ops)


def detect_unsupported(text: str) -> tuple[CadFeature, ...]:
    """Coming-soon features the request text asks for (deterministic scan).

    Matches each coming-soon feature's bilingual patterns against the raw
    request, case-insensitively. Implemented features never match here by
    construction, so a newly-shipped op silences its message automatically.
    """
    if not text or not text.strip():
        return ()
    hits: list[CadFeature] = []
    for feat in coming_soon():
        for pat in feat.patterns:
            if re.search(pat, text, re.IGNORECASE):
                hits.append(feat)
                break
    return tuple(hits)


def coming_soon_message(feature: CadFeature) -> str:
    """The warm bilingual "coming soon" message for one feature."""
    ar = (
        f"ميزة {feature.label_ar} مش متوفرة بالنسخة الحالية، "
        "رح تكون بالنسخة المتطورة القادمة إن شاء الله 🔜"
    )
    en = (
        f"The {feature.label_en} feature isn't available in the current "
        "version yet — it's planned for the upcoming advanced version."
    )
    lines = [ar, en]
    if feature.alternative_ar:
        lines.append(f"البديل المتاح الآن: {feature.alternative_ar}.")
    if feature.alternative_en:
        lines.append(f"Closest supported alternative: {feature.alternative_en}.")
    return "\n".join(lines)
