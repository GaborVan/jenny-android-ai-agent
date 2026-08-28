"""Raggiungere la pagina precedente quando la chat non si può scorrere.

`setupInfiniteScroll` chiede la pagina più vecchia su un evento `scroll` con
`scrollTop === 0`. Un contenitore che non trabocca non emette nessun evento
`scroll`: la pagina esiste, il client *sa* che esiste (`hasMoreHistory` è vero,
il cursore ce l'ha), e non c'è gesto che possa chiederla.

Finché la prima pagina era lunga il caso non si vedeva. Da quando il confine di
`/new` è il pavimento della cronologia visibile è lo **stato normale subito dopo
un reset**: tre righe a schermo e la conversazione di prima irraggiungibile —
cioè la stessa cancellazione apparente che quel disegno esiste per non fare, e
una smentita di quel che la conferma del comando promette («si rilegge scorrendo
in su»).

Il bottone compare solo in quello stato e sparisce da solo quando il gesto torna
possibile: è un rimedio all'assenza dell'evento, non un secondo modo di fare la
stessa cosa.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHAT_JS = ASSETS / "mobile-chat.js"

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _member(source: str, name: str) -> str:
    m = re.search(
        rf"\n  ((?:async |get )?{re.escape(name)}\([^)]*\)\s*\{{.*?)\n  \}}",
        source,
        re.S,
    )
    assert m, f"{name} non trovato"
    return m.group(1) + "\n  }"


_HARNESS = """
import assert from 'node:assert/strict';

const i18n = { t: (key) => 'i18n:' + key };

let nodes = [];
const document = {
  createElement() {
    const el = { className: '', textContent: '', type: '', disabled: false, handlers: {} };
    el.addEventListener = (type, fn) => { el.handlers[type] = fn; };
    el.remove = () => {
      const i = nodes.indexOf(el);
      if (i !== -1) nodes.splice(i, 1);
    };
    return el;
  },
};
const withClass = (cls) =>
  nodes.filter((n) => String(n.className).split(/\\s+/).includes(cls));

function makeChat({ scrollHeight, clientHeight, hasMoreHistory }) {
  nodes = [];
  const chat = {
    hasMoreHistory,
    loadedMore: 0,
    // Quel che il metodo vero fa per mettere un nodo in cima.
    _insertAtTop(node) { nodes.unshift(node); },
    async loadMoreHistory() { chat.loadedMore++; },
    __ENSURE__,
  };
  chat.chatArea = {
    scrollHeight,
    clientHeight,
    querySelector(selector) {
      return withClass(selector.replace('.', ''))[0] || null;
    },
  };
  return chat;
}

const rows = () => withClass('chat-history-more');
"""


def _harness() -> str:
    chat = CHAT_JS.read_text(encoding="utf-8")
    return _HARNESS.replace("__ENSURE__", _member(chat, "_ensureHistoryReach"))


def _run_js(script: str) -> None:
    source = _harness() + "\n" + script
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_the_button_appears_when_there_is_more_but_nothing_to_scroll() -> None:
    """Lo stato subito dopo `/new`: tre righe a schermo, una sessione sopra."""
    _run_js("""
      const chat = makeChat({ scrollHeight: 400, clientHeight: 400, hasMoreHistory: true });
      chat._ensureHistoryReach();
      assert.equal(rows().length, 1, 'senza appiglio la pagina precedente è irraggiungibile');
      assert.equal(rows()[0].textContent, 'i18n:chat.loadPrevious',
                   'la riga non passa da i18n');
    """)


def test_no_button_when_the_chat_can_already_be_scrolled() -> None:
    """Con il gesto disponibile il bottone sarebbe un secondo modo di fare lo stesso."""
    _run_js("""
      const chat = makeChat({ scrollHeight: 2000, clientHeight: 400, hasMoreHistory: true });
      chat._ensureHistoryReach();
      assert.equal(rows().length, 0);
    """)


def test_no_button_when_there_is_nothing_before() -> None:
    _run_js("""
      const chat = makeChat({ scrollHeight: 400, clientHeight: 400, hasMoreHistory: false });
      chat._ensureHistoryReach();
      assert.equal(rows().length, 0);
    """)


def test_it_disappears_once_the_chat_has_grown() -> None:
    """Sparisce da sé: è il rimedio a un'assenza, e l'assenza è finita."""
    _run_js("""
      const chat = makeChat({ scrollHeight: 400, clientHeight: 400, hasMoreHistory: true });
      chat._ensureHistoryReach();
      assert.equal(rows().length, 1);
      chat.chatArea.scrollHeight = 3000;
      chat._ensureHistoryReach();
      assert.equal(rows().length, 0, 'il bottone è rimasto dopo che scorrere è tornato possibile');
    """)


def test_it_is_not_stacked_twice() -> None:
    """`_ensureHistoryReach` gira dopo ogni pagina: due righe identiche sono un difetto."""
    _run_js("""
      const chat = makeChat({ scrollHeight: 400, clientHeight: 400, hasMoreHistory: true });
      chat._ensureHistoryReach();
      chat._ensureHistoryReach();
      chat._ensureHistoryReach();
      assert.equal(rows().length, 1);
    """)


def test_tapping_it_asks_for_the_older_page() -> None:
    _run_js("""
      const chat = makeChat({ scrollHeight: 400, clientHeight: 400, hasMoreHistory: true });
      chat._ensureHistoryReach();
      const btn = rows()[0];
      assert.ok(btn.handlers.click, 'il bottone non ascolta il tocco');
      await btn.handlers.click();
      assert.equal(chat.loadedMore, 1);
      assert.equal(btn.disabled, false, 'resta disabilitato dopo un giro finito');
    """)
