"""Il lato della mascotte non è più una preferenza, è dove l'hai lasciata.

Lo scrive ``mobile-jenny.js`` quando lei atterra dopo un lancio, e lo rilegge
all'avvio. Tre invarianti reggono quel giro e nessuna di loro romperebbe
rumorosamente: si parte da sinistra, la scelta manuale non deve tornare nelle
impostazioni (cambierebbe da sola al primo lancio, cioè mentirebbe), e
``setMascotSide`` non deve emettere ``mascotchange`` — quell'evento passa da
``_applyMascotPrefs`` a ``setMode`` a ``_abortFlight``, quindi ucciderebbe il
volo nell'istante esatto in cui lei sceglie il bordo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

UI_ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"


def _fn_body(source: str, name: str) -> str:
    """Corpo di una funzione top-level (indentata a zero, chiusa da ``}`` in colonna 1)."""
    match = re.search(rf"^export function {name}\([^)]*\)\s*\{{\n(.*?)^\}}", source, re.M | re.S)
    assert match, f"{name}() non trovata in shared/mascot.js"
    return match.group(1)


def _mascot_js() -> str:
    return (UI_ASSETS / "shared" / "mascot.js").read_text("utf-8")


def test_default_side_is_left():
    body = _fn_body(_mascot_js(), "mascotSide")
    returns = re.findall(r"return\s+(.+?);", body)
    assert returns, "mascotSide() non ritorna nulla"
    # L'unico ritorno è il ternario: il ramo di fallback è il default.
    assert returns[-1].endswith("'left'"), (
        f"il default della mascotte non è più il lato sinistro: {returns[-1]!r}"
    )


def test_side_key_is_not_the_legacy_preference_key():
    """Chi aveva scelto 'destra' quando era un'impostazione riparte da sinistra."""
    source = _mascot_js()
    key = re.search(r"^const SIDE_KEY = '([^']+)'", source, re.M)
    legacy = re.search(r"^const LEGACY_SIDE_KEY = '([^']+)'", source, re.M)
    assert key and legacy, "le chiavi del lato non sono più dichiarate in testa al modulo"
    assert key.group(1) != legacy.group(1)


def test_set_mascot_side_does_not_broadcast():
    body = _fn_body(_mascot_js(), "setMascotSide")
    assert "dispatchEvent" not in body, (
        "setMascotSide emette di nuovo 'mascotchange': l'evento aborta il volo in corso "
        "(mobile-jenny.js#_applyMascotPrefs -> setMode -> _abortFlight)"
    )


def test_settings_no_longer_offer_the_side_choice():
    settings = (UI_ASSETS / "mobile-settings.js").read_text("utf-8")
    assert "data-mascot-side" not in settings
    assert "setMascotSide" not in settings
    for locale in ("it", "en"):
        keys = json.loads((UI_ASSETS / "i18n" / f"{locale}.json").read_text("utf-8"))["settings"]
        assert not [k for k in keys if k.startswith("mascotSide")], (
            f"stringhe della scelta del lato ancora in {locale}.json"
        )
