"""Le invarianti della riga del cassetto, lette sul sorgente.

Compagno di ``test_launcher_rank_client.py``, che prova il motore sotto node.
Qui si guardano le tre proprietà del *foglio* che il passo 3 esiste per
ottenere, e che si perderebbero senza rumore:

* **difetto 02** — la ``description`` che il gateway manda per ogni skill e
  ogni Jenny App arriva davvero alla riga, invece di essere buttata per un
  nome troncato;
* **difetto 05** — un guasto compare *nella* riga e su una riga sola, non in un
  blocco che alza la cella (nella griglia di oggi l'errore porta la riga da 100
  a 147 px);
* **difetto 07** — digitare **non** ricostruisce l'elenco. È il difetto più
  facile da reintrodurre: basta chiamare ``_render()`` invece di
  ``_renderList()`` nell'ascoltatore del campo, e non si nota finché non si
  prova su un telefono con 68 voci e 47 icone base64.

Asserzioni sul sorgente, nello stile del resto di ``tests/webui/``.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"


def _src(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  (?:async )?{name}\([^)]*\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return body.group(1)


# ── difetto 02 — la descrizione arriva alla riga ────────────────────────────

def test_the_gateway_description_reaches_the_entry() -> None:
    body = _method(_src("mobile-apps.js"), "launcherEntries")
    assert "app.description" in body, "la description delle Jenny App si perde"
    assert "_skillUserSummary(skill)" in body, (
        "le skill hanno description e user_summary: entrambi passano da qui"
    )
    assert "app.packageName" in body, (
        "le app Android non hanno descrizione: al suo posto va il pacchetto, "
        "che è un dato vero — non un testo inventato"
    )


def test_the_row_prints_the_secondary_line() -> None:
    body = _method(_src("mobile-launcher.js"), "_buildRow")
    assert "launcher-row-desc" in body
    assert "entry.problem || entry.description" in body, (
        "un guasto ha la precedenza sulla descrizione: è quello che spiega cosa fare"
    )


def test_the_secondary_line_is_searchable() -> None:
    """3.1: si cerca su nome **e** descrizione. Se `searchText` non arrivasse
    alla voce, la ricerca tornerebbe a essere solo sui nomi senza fallire."""
    body = _method(_src("mobile-apps.js"), "launcherEntries")
    assert body.count("searchText:") == 3, "una delle tre categorie non è cercabile"


# ── difetto 05 — il guasto non deforma la riga ──────────────────────────────

def test_the_error_line_stays_on_one_line() -> None:
    css = _src("mobile-style.css")
    desc = re.search(r"\.launcher-row-desc \{(.*?)\}", css, re.S)
    assert desc, ".launcher-row-desc non trovata"
    assert "white-space: nowrap" in desc.group(1), (
        "senza nowrap un errore lungo va a capo e alza la riga: è il difetto 05"
    )
    assert "text-overflow: ellipsis" in desc.group(1)
    assert ".launcher-row-desc--problem" in css, "il guasto non ha un colore proprio"


def test_the_error_is_rendered_as_text_not_html() -> None:
    """I nomi e gli errori arrivano da manifest scritti da un LLM e dal
    PackageManager: la riga si costruisce nel DOM, mai per concatenazione."""
    source = _src("mobile-launcher.js")
    assert "innerHTML" not in source, "il foglio non deve mai scrivere HTML grezzo"
    body = _method(source, "_buildRow")
    assert body.count("textContent") >= 3


def test_the_icon_src_accepts_only_data_images() -> None:
    body = _method(_src("mobile-launcher.js"), "_buildRow")
    assert "entry.icon.startsWith('data:image/')" in body, (
        "`src` accetta anche schemi che eseguono, e questo valore viene da fuori"
    )


# ── difetto 07 — digitare non ricostruisce l'elenco ─────────────────────────

def test_typing_reorders_and_never_rebuilds() -> None:
    source = _src("mobile-launcher.js")
    typing = _method(source, "_onQueryChanged")
    assert "_renderList()" in typing
    assert "_render()" not in typing, (
        "il percorso del tasto non deve passare da _render(): rileggerebbe le "
        "liste e ricostruirebbe le righe a ogni carattere (difetto 07)"
    )
    assert "_syncEntries" not in typing


def test_rows_are_cached_by_key_and_reused() -> None:
    source = _src("mobile-launcher.js")
    sync = _method(source, "_syncEntries")
    assert "cached.sig === sig" in sync, (
        "senza il confronto della firma ogni apps_list_changed butterebbe via "
        "le icone base64 già decodificate per riscriverle identiche"
    )
    render = _method(source, "_renderList")
    assert "this._rows.get(entry.key).el" in render, (
        "la lista deve rimettere in fila i nodi esistenti, non crearne di nuovi"
    )


def test_the_list_is_written_once_per_keystroke() -> None:
    render = _method(_src("mobile-launcher.js"), "_renderList")
    assert render.count("this.list.replaceChildren") == 3, (
        "una sola scrittura per ramo (caricamento, nessun risultato, risultati)"
    )
    assert "appendChild" not in render, "appendere una riga per volta è N reflow"


# ── il tocco ────────────────────────────────────────────────────────────────

def test_activation_is_delegated_to_one_listener_on_the_list() -> None:
    """Le righe si rimettono in fila a ogni tasto: un listener per riga li
    moltiplicherebbe per il numero di ricostruzioni."""
    source = _src("mobile-launcher.js")
    row = _method(source, "_buildRow")
    assert "addEventListener" not in row
    assert "this.list?.addEventListener('click'" in source


def test_the_launch_policy_lives_with_the_data_owner() -> None:
    """"Aprire" significa tre cose diverse nei tre spazi di nomi, e sono già
    decise nella scheda: una seconda copia divergerebbe al primo caso
    particolare (una skill locked, una Jenny App rotta)."""
    apps = _src("mobile-apps.js")
    body = _method(apps, "activateEntry")
    assert "this.launchAndroidApp(entry.id)" in body
    assert "this.openApp(entry.id)" in body
    assert "showSkillSheet" in body and "_openSkillFile" in body
    launcher = _method(_src("mobile-launcher.js"), "_activate")
    assert "activateEntry(entry)" in launcher
    assert "api." not in launcher, "il foglio non parla con la rete (D5)"


def test_only_an_android_launch_closes_the_sheet() -> None:
    """Una app Android porta via il task; una Jenny App si apre *sopra* il
    foglio e Indietro ci riporta (1.7)."""
    body = _method(_src("mobile-launcher.js"), "_activate")
    assert "if (entry.kind === 'android') this.close();" in body


def test_usage_is_recorded_before_the_launch() -> None:
    """``launchAndroidApp`` porta via il task: dopo di lei non è detto che
    questo JS giri ancora."""
    body = _method(_src("mobile-launcher.js"), "_activate")
    assert body.index("this._usage.record(") < body.index("activateEntry(entry)")


def test_rows_carry_button_semantics_from_birth() -> None:
    """Le celle della scheda sono `<div>` a cui si appiccica `tabindex`/`role`
    a ogni ridisegno; qui la riga *è* un pulsante per Tab e per TalkBack."""
    body = _method(_src("mobile-launcher.js"), "_buildRow")
    assert "setAttribute('role', 'button')" in body
    assert "setAttribute('tabindex', '0')" in body


# ── i18n ────────────────────────────────────────────────────────────────────

def test_no_hardcoded_strings_in_the_sheet() -> None:
    source = _src("mobile-launcher.js")
    for key in ("launcher.recent", "launcher.results", "launcher.noResults"):
        assert f"'{key}'" in source, f"{key} non usata"
    # Il placeholder e le etichette statiche stanno nell'HTML, non nel JS.
    html = (ROOT / "jenny" / "templates" / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'data-i18n-placeholder="launcher.searchPlaceholder"' in html
    assert 'data-i18n-aria="launcher.clearSearch"' in html


def test_the_search_field_does_not_autofocus() -> None:
    """D6: su un telefono con tastiera software l'autofocus alzerebbe la
    tastiera e si mangerebbe il foglio."""
    html = (ROOT / "jenny" / "templates" / "ui" / "index.html").read_text(encoding="utf-8")
    field = re.search(r'<input class="launcher-search".*?>', html, re.S)
    assert field, "campo di ricerca non trovato"
    assert "autofocus" not in field.group(0)
    open_body = _method(_src("mobile-launcher.js"), "open")
    assert "this.search.focus" not in open_body
