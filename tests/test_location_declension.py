"""Regression: core.location.resolve_offset used the nominative form of
Russian city names as the ONLY dict key ("уфа", "москва", "тула", ...), but
substring-matched against the raw message text. For feminine/soft-sign
Russian nouns, the case ending REPLACES the final letter rather than
appending to it, so the nominative form is never a substring of the most
natural way to state a location ("я в уфе") — "уфа" is not a substring of
"уфе". This broke resolve_offset for most cities in the dict that don't
have a separate short alias (мск/спб/... rescue Moscow/Petersburg, but Ufa,
Kazan, Tula, Samara, Perm, Tyumen, Yaroslavl, Warsaw, Ankara, Kamchatka,
Udmurtia, Bashkiria had no rescue and silently failed).

Live report: "я в уфе" at night → CITY_TZ substring match failed, offset
fell through to the Haiku fallback (see test_update_user_tz_haiku_fallback.py),
and matched_city stayed None so the weather widget's city never updated —
it kept showing the stale/raw location.
"""
from __future__ import annotations

import core.location as loc


def test_declension_regression_natural_v_x_phrasing():
    """'я в X' (locative/dative case) must resolve for every fixed city."""
    cases = [
        ("я в уфе", 5),
        ("я в москве", 3),
        ("я в казани", 3),
        ("я в туле", 3),
        ("я в самаре", 4),
        ("я в перми", 5),
        ("я в тюмени", 5),
        ("я в башкирии", 5),
        ("я в удмуртии", 5),
        ("я в ярославле", 3),
        ("я в варшаве", 1),
        ("я в анкаре", 3),
        ("я на камчатке", 12),
    ]
    for text, expected_offset in cases:
        offset, city = loc.resolve_offset(text)
        assert offset == expected_offset, f"{text!r} -> offset {offset}, expected {expected_offset}"
        assert city is not None, f"{text!r} -> matched_city is None, expected a match"


def test_nominative_forms_still_work():
    """The fix must not break the plain nominative form these cities already had."""
    for text, expected_offset in [("уфа", 5), ("москва", 3), ("тула", 3), ("казань", 3)]:
        assert loc.resolve_offset(text)[0] == expected_offset
