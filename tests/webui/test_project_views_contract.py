"""Dentro un progetto, le viste wiki e grafo mostrano quel progetto e basta.

Passo **5** di ``roadmap/progetti-passi.md``.

Fino al 22/08 il chip diceva «sei in patreon» e le due viste continuavano a
mostrare tutte e sette le wiki: la Home della wiki *è* l'elenco degli altri
progetti, e i nodi del grafo home *sono* le altre wiki. È lo stesso elenco che
al 2.2 abbiamo tolto dal prompt di un progetto — «Claude Code non ti parla degli
altri tuoi repository» — rimasto acceso sulla metà che si vede.

L'aggancio ha **un solo scrittore**, lo scope chip, che è anche l'unico a sapere
in che conversazione siamo; le viste lo leggono da ``AppState`` e si
riagganciano quando cambia. Due scrittori sarebbero due risposte alla stessa
domanda, e la vista finirebbe su quella vecchia.

Le due chiusure sono agli imbuti, non alle chiamate: ``loadHome`` (dove
finiscono breadcrumb, ``_index.md`` dell'albero e wikilink) e ``loadGraph``
(dove finiscono header, history e link). Chiudere lì chiude tutte le strade in
una volta, ed è il motivo per cui i test qui sotto guardano quei due corpi.

Asserzioni sul sorgente, come ``test_scope_menu_contract.py``: la WebUI non ha
un runner JS con DOM.

**L'isolamento sta nel client, e questa è una decisione — non una metà
dimenticata** (T4.11, 23/08). ``/api/tree``, ``/api/graph`` e ``/api/page``
servono qualunque wiki che ``discover_wikis`` conosca, quale che sia la
conversazione aperta: l'aggancio non arriva fin là, e non c'è un modo di farglielo
arrivare che non sia peggiore del problema (queste sono route HTTP dietro un
token, senza sessione da cui dedurlo; farlo ricordare al server sarebbe il
secondo scrittore di ``pinnedWiki``, che è quel che il primo test qui sotto vieta;
passarlo come parametro accanto a ``wiki=`` lo farebbe scrivere allo stesso
client che già decide ``wiki=``). E non c'è conseguenza di sicurezza: sono wiki
dello stesso utente, dietro lo stesso token, e il confine di lettura dell'agente
è già tutto il workspace di proposito.

Quel che il server garantisce è il **contenimento** — il nome deve essere una
wiki di ``wikis/``, la pagina deve stare nella ``wiki/`` di quella wiki — e a
tenerlo fermo, con input ostili e non per grep, c'è
``test_wiki_routes_server_scope.py``. Le due metà rispondono a domande diverse:
questa è la vista, quella è il percorso.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

UI_DIR = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"
ASSETS = UI_DIR / "assets"
WIKI_JS = ASSETS / "mobile-wiki.js"
GRAPH_JS = ASSETS / "mobile-graph.js"
APP_JS = ASSETS / "mobile-app.js"
CHIP_JS = ASSETS / "shared" / "scope-chip.js"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _method(path: Path, name: str) -> str:
    """Il corpo di un metodo di classe, indentato di due spazi."""
    body = re.search(rf"\n  (?:async |get )?{name}\([^)]*\)\s*\{{(.*?)\n  \}}", _src(path), re.S)
    assert body, f"{name} non trovato in {path.name}"
    return body.group(1)


# ── Un solo scrittore ─────────────────────────────────────────────────────


def test_only_the_scope_chip_publishes_the_pin() -> None:
    """Due scrittori sono due risposte a «in che progetto siamo», e divergono."""
    writers = [
        path.name
        for path in sorted(ASSETS.rglob("*.js"))
        if re.search(r"AppState\.set\(\s*['\"]pinnedWiki['\"]", _src(path))
    ]
    assert writers == ["scope-chip.js"], (
        "l'aggancio lo pubblica il chip, che è l'unico a sapere in che conversazione "
        f"siamo; trovati invece: {writers}"
    )


def test_the_personal_chat_dissolves_the_pin() -> None:
    """Tornare alla personale rimette la Home: ``null``, non l'ultimo progetto."""
    body = _method(CHIP_JS, "_publishPin")
    assert "kind === 'project'" in body and "null" in body


# ── Gli imbuti, chiusi dove passano tutti ─────────────────────────────────


def test_the_wiki_home_is_unreachable_from_inside_a_project() -> None:
    """Breadcrumb, ``_index.md`` dell'albero e wikilink finiscono tutti qui."""
    body = _method(WIKI_JS, "loadHome")
    guard = body.strip().splitlines()
    head = "\n".join(guard[:6])
    assert "pinnedWiki" in head and "loadWikiPage" in head, (
        "la guardia deve stare in cima a loadHome: sotto una sola riga di fetch, la Home "
        "si sarebbe già disegnata"
    )


def test_the_graph_never_falls_back_to_the_home_graph_inside_a_project() -> None:
    """I nodi del grafo home *sono* le altre wiki: l'aggancio vince sull'argomento."""
    body = _method(GRAPH_JS, "loadGraph")
    head = "\n".join(body.strip().splitlines()[:8])
    assert re.search(r"pinnedWiki", head), "loadGraph deve leggere l'aggancio prima di caricare"
    assert re.search(r"if \(pin\)\s*wiki = pin", head), (
        "deve sovrascrivere l'argomento: header, history e link passano tutti da qui "
        "con la loro idea di quale wiki mostrare"
    )


def test_both_views_follow_a_scope_change_made_while_they_are_off_screen() -> None:
    """Il cambio di progetto avviene in chat: le viste lo scoprono a vista chiusa.

    Visto sul telefono il 22/08: tornando alla personale il grafo restava su
    ``Graph: zz-viste``, perché il suo listener ricaricava solo se il grafo era
    la vista attiva e ``activate()`` ripartiva dal vecchio ``currentWiki``. La
    wiki invece tornava alla Home. Due risposte diverse allo stesso evento.
    """
    graph = _src(GRAPH_JS)
    listener = re.search(r"AppState\.on\('pinnedWiki'.*?\}\);", graph, re.S)
    assert listener, "il grafo non ascolta il cambio di aggancio"
    assert "this.currentWiki = pin" in listener.group(0), (
        "fuori vista il grafo deve comunque spostare currentWiki: è da lì che riparte activate()"
    )

    wiki = _method(WIKI_JS, "_onPinChange")
    assert "_settled = false" in wiki, (
        "fuori vista la wiki deve invalidare la vista disegnata, o activate() la salta"
    )


def test_neither_breadcrumb_offers_a_way_out_that_does_not_work() -> None:
    """Un crumb che non naviga è peggio che assente: promette un'uscita.

    Trovato sul telefono il 22/08 guardando il grafo agganciato: il grafo ha un
    breadcrumb suo (``Wikis / <nome>``), e chiudere solo quello della vista wiki
    lasciava il primo pezzo lì, inerte — ``loadGraph`` lo riagganciava subito.
    """
    for path in (WIKI_JS, GRAPH_JS):
        body = _method(path, "_renderBreadcrumbs")
        home = [ln for ln in body.splitlines() if "data-home" in ln]
        assert home, f"{path.name}: crumb Home non trovato"
        assert "pinnedWiki" in body, (
            f"{path.name}: il crumb Home va omesso quando la vista è agganciata a un progetto"
        )


# ── L'ingresso, e le vie laterali ─────────────────────────────────────────


def test_the_pin_beats_the_url() -> None:
    """Una ``?wiki=`` di un altro progetto resta una vista che non spetta a questa chat."""
    body = _method(WIKI_JS, "_loadInitialView")
    assert "pinnedWiki" in body
    assert re.search(r"pin \|\| params\.get\('wiki'\)", body), (
        "l'aggancio deve precedere l'URL, non ripiegarci sopra"
    )


def test_a_link_out_of_the_project_does_not_switch_project() -> None:
    """Cambiare progetto sotto ai piedi è peggio del rifiuto: si resta e lo si dice."""
    body = _method(WIKI_JS, "loadWikiPage")
    head = "\n".join(body.strip().splitlines()[:10])
    assert "wiki !== pin" in head
    assert "onlyThisProject" in head, "il rifiuto deve essere detto, non silenzioso"
    assert "return;" in head


def test_back_lands_somewhere_instead_of_refusing() -> None:
    """Il rifiuto parlante è per i link: un Indietro deve pur disegnare qualcosa.

    Una entry lasciata da un altro progetto non ci riporta dentro — ma se
    ``popstate`` si limitasse a chiamare il ``loadWikiPage`` che rifiuta, la
    pressione non cambierebbe niente a schermo e sembrerebbe persa.
    """
    body = _src(APP_JS)
    branch = re.search(r"if \(state\.wikiPage\) \{(.*?)\n      \} else if", body, re.S)
    assert branch, "ramo state.wikiPage non trovato in mobile-app.js"
    assert "pinnedWiki" in branch.group(1)
    assert "'index.md'" in branch.group(1)


# ── Il contorno ───────────────────────────────────────────────────────────


def test_the_refusal_is_localised_in_both_languages() -> None:
    for lang in ("it", "en"):
        data = json.loads((ASSETS / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        text = data["wiki"]["onlyThisProject"]
        assert "{name}" in text, f"{lang}: il rifiuto deve nominare il progetto"
