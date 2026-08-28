"""La tabella dei comandi e l'alfabeto con cui la WebUI la disegna.

``BUILTIN_COMMAND_SPECS`` porta un'icona per comando da sempre, ma fino alla
0.9.0 nessuno la disegnava: ``as_dict()`` non aveva un consumatore in tutto il
repo. Con la tendina del composer quel campo diventa visibile, e si e' scoperto
che cinque nomi su tredici erano di **Lucide** (``square-pen``, ``sprout``,
``brush-cleaning``, ``file-pen``, ``circle-help``) mentre il font impacchettato e'
**Tabler**: in interfaccia sarebbero stati cinque quadrati vuoti.

Un nome sbagliato non rompe niente e non logga niente — e' esattamente il tipo di
difetto che sopravvive a una review e si vede solo sul telefono. Questo file e'
il posto dove si nota: confronta la tabella con il CSS del font vero, quello che
finisce nell'APK.
"""

from __future__ import annotations

import re
from pathlib import Path

from jenny.command.builtin import BUILTIN_COMMAND_SPECS

_UI = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"
_TABLER_CSS = (
    _UI / "assets" / "vendor" / "@tabler" / "icons-webfont@3.19.0" / "dist" / "tabler-icons.min.css"
)


def _tabler_icon_names() -> set[str]:
    css = _TABLER_CSS.read_text(encoding="utf-8")
    # Le classi del webfont sono `.ti-<nome>:before{content:"\\xxxx"}`.
    return set(re.findall(r"\.ti-([a-z0-9-]+):before", css))


def test_every_command_icon_exists_in_the_bundled_font() -> None:
    available = _tabler_icon_names()
    assert available, "il CSS del font Tabler non e' stato letto: path cambiato?"

    missing = {
        spec.command: spec.icon
        for spec in BUILTIN_COMMAND_SPECS
        if spec.icon not in available
    }

    assert not missing, (
        f"icone non presenti nel font Tabler impacchettato: {missing}. "
        "I nomi vanno presi da Tabler (senza il prefisso 'ti-'), non da Lucide: "
        "un nome inesistente rende un quadrato vuoto, in silenzio."
    )


def test_specs_are_serializable_for_the_webui() -> None:
    """La rotta ``/api/webui/commands`` serve esattamente questo dizionario."""
    for spec in BUILTIN_COMMAND_SPECS:
        payload = spec.as_dict()
        assert payload["command"].startswith("/")
        assert payload["title"]
        assert payload["description"]
        assert payload["icon"]
        # Il client filtra su questo valore: uno sconosciuto nasconderebbe il
        # comando in ogni scope, o lo mostrerebbe in tutti.
        assert payload["scope"] in ("any", "project")


def test_project_only_commands_are_the_two_that_expand_in_the_turn() -> None:
    """``/tidy`` e ``/init``, e nessun altro.

    Sono i due che non passano dal router perche' si espandono nel turno di un
    progetto (``agent/loop.py``): fuori da un progetto non hanno un soggetto, e
    offrirli comunque vorrebbe dire proporre due voci che non fanno niente.
    """
    project_only = {spec.command for spec in BUILTIN_COMMAND_SPECS if spec.scope == "project"}
    assert project_only == {"/tidy", "/init"}
