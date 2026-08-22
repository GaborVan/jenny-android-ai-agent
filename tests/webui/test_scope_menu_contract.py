"""La tendina dello scope sta nello schermo, scorre, e mette in cima l'ultima.

Tre guasti visti sul telefono, tutti e tre invisibili finche' i progetti erano
due:

1. il chip usciva a spigolo vivo sul tema `kyoto`, perche' prendeva
   ``--radius-seg`` — che e' il raggio dei controlli segmentati, e quel tema lo
   porta a 2px. La pillola distingue il chip (che si apre) dalle etichette
   squadrate intorno: non puo' dipendere dal tema;
2. il pannello cresceva con l'elenco e a sette wiki usciva dallo schermo
   dall'alto. Quel che spariva era la testa, cioe' la voce "personale" con cui
   si torna indietro — e non c'era modo di raggiungerla, perche' non scorreva;
3. l'ordine era quello alfabetico della discovery, mentre ogni riga stampa da
   quanto la wiki non si muove. Il progetto su cui si stava lavorando finiva
   dove capitava la sua iniziale.

Asserzioni sul sorgente, come ``test_thinking_scroll_contract.py``: la WebUI non
ha un runner JS con DOM.
"""

from __future__ import annotations

import re
from pathlib import Path

UI_DIR = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"
CSS = UI_DIR / "assets" / "mobile-style.css"
CHIP_JS = UI_DIR / "assets" / "shared" / "scope-chip.js"


def _css() -> str:
    return CSS.read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    """Il corpo della prima regola per ``selector``."""
    body = re.search(rf"^{re.escape(selector)} \{{(.*?)^\}}", _css(), re.S | re.M)
    assert body, f"regola {selector} non trovata in mobile-style.css"
    return body.group(1)


def _chip() -> str:
    return CHIP_JS.read_text(encoding="utf-8")


def _method(name: str) -> str:
    body = re.search(rf"\n  (?:async )?{name}\([^)]*\)\s*\{{(.*?)\n  \}}", _chip(), re.S)
    assert body, f"{name} non trovato in scope-chip.js"
    return body.group(1)


# ── 1. Il chip e' una pillola ovunque ─────────────────────────────────────


def test_the_chip_takes_its_radius_from_a_token_no_theme_redefines() -> None:
    # Sulle sole dichiarazioni: il commento accanto nomina --radius-seg per dire
    # perche' non si usa piu'.
    radii = re.findall(r"border-radius:\s*([^;]+);", _rule(".scope-chip"))
    assert radii == ["var(--radius-pill)"], (
        f"il chip deve restare una pillola in ogni tema, non {radii}"
    )


def test_no_theme_squares_the_pill() -> None:
    """La regola sopra tiene solo finche' `--radius-pill` resta uno solo.

    Se un tema lo ridefinisce siamo daccapo, con la differenza che stavolta la
    causa e' scritta a un capo del file e l'effetto all'altro.
    """
    definitions = re.findall(r"--radius-pill:\s*([^;]+);", _css())
    assert definitions == ["999px"], (
        f"--radius-pill non e' piu' un valore unico: {definitions} — un tema puo' squadrare il chip"
    )


# ── 2. Il pannello ha un tetto, e sotto il tetto scorre ───────────────────


def test_the_menu_is_capped_and_lays_out_as_a_column() -> None:
    rule = _rule(".scope-menu")
    assert "max-height:" in rule, "senza tetto il pannello esce dallo schermo dall'alto"
    assert "flex-direction: column" in rule
    assert ".scope-menu.open { display: flex; }" in _css(), (
        "con `display: block` il figlio che scorre non riceve l'altezza rimasta"
    )


def test_only_the_project_list_scrolls() -> None:
    rule = _rule(".scope-menu-scroll")
    assert "overflow-y: auto" in rule
    # Senza `min-height: 0` un figlio flex non scende sotto il suo contenuto:
    # il riquadro non scorre e il pannello sfonda il tetto.
    assert "min-height: 0" in rule, "un figlio flex senza min-height:0 non cede: niente scroll"
    assert "flex: 1 1 auto" in rule, "e' la lista che deve assorbire l'eccedenza, non il pannello"


def test_the_two_anchors_stay_out_of_the_scroller() -> None:
    """"Personale" e "nuovo" sono le due voci che si cercano senza guardare."""
    body = _method("_renderMenu")
    scroller = re.search(r"list\.className = 'scope-menu-scroll'", body)
    assert scroller, "l'elenco dei progetti non ha un contenitore suo"
    personal = body.index("scope.personalSection")
    listed = body.index("list.className")
    new = body.index("scope.newProject")
    assert personal < listed < new, "la lista che scorre deve stare in mezzo alle due voci fisse"
    for pinned in ("this._item({\n      name: this.personalLabel", "add.classList.add"):
        assert pinned in body
    assert "list.appendChild(item)" in body, "i progetti vanno dentro il riquadro che scorre"


def test_the_open_menu_shows_where_you_are() -> None:
    body = _method("_renderMenu")
    assert "activeEl?.scrollIntoView({ block: 'nearest' })" in body, (
        "con l'elenco piu' lungo del riquadro il progetto attivo puo' restare sotto la piega"
    )


# ── 3. Dal piu' recente ───────────────────────────────────────────────────


def test_projects_are_ordered_by_the_same_field_the_rows_print() -> None:
    body = _method("_loadProjects")
    assert ".sort((a, b) => (b.modified || 0) - (a.modified || 0)" in body, (
        "l'elenco deve scendere dal piu' recente, con lo stesso `modified` che ogni riga stampa"
    )
    assert "a.name.localeCompare(b.name)" in body, (
        "senza spareggio due mtime uguali danno un ordine diverso a ogni apertura"
    )
