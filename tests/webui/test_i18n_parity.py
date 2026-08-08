"""Parità delle chiavi fra i due file i18n della WebUI.

Una chiave presente solo in ``it.json`` (o solo in ``en.json``) non fallisce da
nessuna parte: ``i18n.t()`` ritorna la chiave grezza, e l'utente dell'altra
lingua si ritrova ``subagents.relaunch`` stampato nell'interfaccia. Questo test
è il posto dove quella divergenza si nota.
"""

from __future__ import annotations

import json
from pathlib import Path

_I18N_DIR = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets" / "i18n"


def _flatten(value: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, child in value.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict):
            keys |= _flatten(child, full)
        else:
            keys.add(full)
    return keys


def _load(locale: str) -> dict:
    return json.loads((_I18N_DIR / f"{locale}.json").read_text(encoding="utf-8"))


def test_it_and_en_have_the_same_keys() -> None:
    it_keys = _flatten(_load("it"))
    en_keys = _flatten(_load("en"))
    assert it_keys - en_keys == set(), "chiavi presenti solo in it.json"
    assert en_keys - it_keys == set(), "chiavi presenti solo in en.json"


def test_subagent_panel_strings_exist_in_both_locales() -> None:
    """Il pannello subagent non deve avere stringhe hardcoded nel JS."""
    expected = {
        "subagents.title",
        # Nessuna intestazione "RUNNING"/"RECENT": in carosello il conteggio
        # dell'header e il colore di stato delle card le rendevano superflue.
        # Due varianti del conteggio: senza card terminali "0 appena conclusi"
        # sarebbe rumore, ed è il caso normale.
        "subagents.headCount",
        "subagents.headCountFinished",
        "subagents.untitled",
        "subagents.openDetail",
        "subagents.idle",
        "subagents.attempt",
        "subagents.stalledHint",
        "subagents.autoCapReached",
        "subagents.stop",
        "subagents.relaunch",
        "subagents.stopped",
        "subagents.relaunched",
        "subagents.actionFailed",
    }
    # Nessuna chiave "subagents.empty": il pannello non ha più uno stato vuoto —
    # zero card significa elemento `hidden`, non un placeholder.
    # Etichette della modale di dettaglio: la card mostra una riga, il resto è lì.
    # Manca chi ha perso la sua etichetta nel telaio: stato e orologi stanno nel
    # riepilogo, dove il senso è nell'accostamento e non in una chiave incolonnata
    # ("in corso · 4m 10s · fermo 2s"), e la coda tool non è più un blocco a sé.
    expected |= {
        f"subagents.detail.{field}"
        for field in (
            "type", "attempt", "phase", "iteration", "stopReason",
            "task", "diagnostics", "outcome",
        )
    }
    # Ogni stato e ogni fase che il backend può mandare ha la sua etichetta.
    expected |= {
        f"subagents.state.{state}"
        for state in ("running", "stalled", "done", "failed", "cancelled")
    }
    expected |= {
        f"subagents.phase.{phase}"
        for phase in (
            "initializing",
            "awaiting_tools",
            "tools_completed",
            "final_response",
            "done",
            "error",
        )
    }
    for locale in ("it", "en"):
        keys = _flatten(_load(locale))
        assert expected <= keys, f"chiavi mancanti in {locale}.json: {sorted(expected - keys)}"


def test_dead_subagent_keys_are_gone_from_both_locales() -> None:
    """Una chiave che nessuno legge più è debito: la parità la terrebbe in vita.

    ``subagents.empty`` serviva al placeholder di un pannello vuoto, che non
    esiste più (zero card = elemento ``hidden``). Le quattro etichette della
    modale sono cadute con il telaio: stato e orologi vivono accostati nel
    riepilogo, senza chiave davanti, e "Tool recenti" non è più un blocco perché
    la coda dello snapshot è diventata il ripiego della lista di attività.
    """
    ui_assets = _I18N_DIR.parent
    chat_js = (ui_assets / "mobile-chat.js").read_text(encoding="utf-8")
    for dead in ("subagents.empty", "subagents.detail.state", "subagents.detail.elapsed",
                 "subagents.detail.idle", "subagents.detail.toolEvents"):
        assert dead not in chat_js
        for locale in ("it", "en"):
            assert dead not in _flatten(_load(locale)), f"{dead} è ancora in {locale}.json"


def test_placeholders_match_between_locales() -> None:
    """Gli stessi ``{segnaposto}`` nelle due lingue: uno mancante stampa ``{n}``."""
    import re

    def placeholders(text: str) -> set[str]:
        return set(re.findall(r"\{(\w+)\}", text))

    def flat_strings(value: dict, prefix: str = "") -> dict[str, str]:
        out: dict[str, str] = {}
        for key, child in value.items():
            full = f"{prefix}.{key}" if prefix else key
            if isinstance(child, dict):
                out.update(flat_strings(child, full))
            elif isinstance(child, str):
                out[full] = child
        return out

    it_strings = flat_strings(_load("it"))
    en_strings = flat_strings(_load("en"))
    mismatched = [
        key
        for key, text in it_strings.items()
        if key in en_strings and placeholders(text) != placeholders(en_strings[key])
    ]
    assert mismatched == []
