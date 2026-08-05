"""La politica di rendering del pannello subagent, eseguita davvero.

``assets/shared/subagent-policy.js`` è di proposito senza DOM e senza import: è
la sola forma in cui queste regole si possono *eseguire* da qui invece che
descriverle con una regex. Il modulo viene valutato da node (già richiesto dal
type check con ``npx pyright``) e le asserzioni sono in JS, sullo stesso oggetto
che la WebUI usa a runtime.

Le tre regole che questi test difendono, in ordine di quanto costa perderle:

1. Nulla di un turno passato viene mai renderizzato. Il server serve sempre
   ``recent`` (lo consumano il tool ``subagent_status`` e GET /api/subagents), e
   dopo un reload quella coda contiene i job dei turni precedenti: se il pannello
   li ripescasse, l'utente ritroverebbe card morte sopra il composer per sempre.
2. Una card terminale lingera per il turno corrente e sparisce a ``turn_end``.
3. La matrice delle azioni per stato: Rilancia non deve comparire su un job
   riuscito né a un tap su un job fallito.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

POLICY_JS = (
    Path(__file__).resolve().parents[2]
    / "jenny" / "templates" / "ui" / "assets" / "shared" / "subagent-policy.js"
)

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _run_js(script: str) -> str:
    """Valuta il modulo di policy seguito da ``script``, con ``assert`` di node."""
    source = (
        POLICY_JS.read_text(encoding="utf-8")
        + "\nimport assert from 'node:assert/strict';\n"
        + script
    )
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return proc.stdout


def test_module_is_pure_no_dom_and_no_imports() -> None:
    """Se il modulo prende dipendenze, questi test smettono di poterlo eseguire."""
    source = POLICY_JS.read_text(encoding="utf-8")
    assert "import " not in source, "la policy deve restare senza dipendenze"
    assert "document" not in source and "window" not in source, "la policy non tocca il DOM"


def test_a_reload_with_nothing_running_renders_an_empty_panel() -> None:
    """Il caso che ha motivato la regola: reload a turno finito.

    ``liveIds`` è vuoto (client appena avviato), ``running`` è vuoto e il server
    serve comunque i terminati dei turni passati: zero card, quindi pannello
    assente.
    """
    _run_js("""
      const snapshot = {
        running: [],
        recent: [
          { task_id: 'old1', state: 'done' },
          { task_id: 'old2', state: 'failed' },
        ],
      };
      const view = saVisibleCards(snapshot, new Set());
      assert.deepEqual(view.running, []);
      assert.deepEqual(view.lingering, []);
      assert.equal(view.running.length + view.lingering.length, 0);
    """)


def test_a_terminated_card_lingers_only_for_the_current_turn() -> None:
    """Vivo → terminato (lingera) → turn_end (sparisce)."""
    _run_js("""
      let live = new Set();

      // 1. Il subagent gira: una card viva, nessuna terminale.
      let view = saVisibleCards({ running: [{ task_id: 't1', state: 'running' }], recent: [] }, live);
      live = view.liveIds;
      assert.equal(view.running.length, 1);
      assert.deepEqual(view.lingering, []);

      // 2. Termina: il server lo sposta in `recent`, la card lingera perché la
      //    transizione è stata osservata qui.
      const terminated = { running: [], recent: [{ task_id: 't1', state: 'done' }] };
      view = saVisibleCards(terminated, live);
      live = view.liveIds;
      assert.deepEqual(view.lingering.map(e => e.task_id), ['t1']);

      // 3. turn_end azzera l'insieme dei vivi: la card non c'è più, e non torna
      //    nemmeno al poll successivo che riporta lo stesso `recent`.
      live = new Set();
      view = saVisibleCards(terminated, live);
      assert.deepEqual(view.lingering, []);
      view = saVisibleCards(terminated, view.liveIds);
      assert.deepEqual(view.lingering, []);
    """)


def test_turn_end_never_drops_a_subagent_that_outlived_the_turn() -> None:
    """Un job può sopravvivere al turno che l'ha lanciato: resta, e potrà lingerare."""
    _run_js("""
      const alive = { running: [{ task_id: 't1', state: 'running' }], recent: [] };
      // turn_end: `liveIds` azzerato, poi ri-render sullo stesso snapshot.
      const view = saVisibleCards(alive, new Set());
      assert.equal(view.running.length, 1);
      assert.ok(view.liveIds.has('t1'), 'un vivo si re-iscrive da sé');
      // E quando poi terminerà, la sua card lingera come le altre.
      const after = saVisibleCards({ running: [], recent: [{ task_id: 't1', state: 'failed' }] }, view.liveIds);
      assert.deepEqual(after.lingering.map(e => e.task_id), ['t1']);
    """)


def test_only_the_witnessed_terminations_linger() -> None:
    """Fra i terminati di `recent` passa solo quello visto vivo in questo turno."""
    _run_js("""
      const view = saVisibleCards({
        running: [],
        recent: [
          { task_id: 'seen', state: 'done' },
          { task_id: 'from-an-old-turn', state: 'done' },
        ],
      }, new Set(['seen']));
      assert.deepEqual(view.lingering.map(e => e.task_id), ['seen']);
    """)


def test_action_matrix_per_state_and_surface() -> None:
    """Chi porta cosa, e dove. Il perché di ogni riga sta nel modulo."""
    _run_js("""
      // running: solo Stop sulla card, un tap, nessuna deviazione dalla modale.
      assert.deepEqual(saActions('running', 'card'), ['stop']);
      assert.deepEqual(saActions('running', 'modal'), ['stop']);

      // stalled: è l'unico caso per cui Rilancia esiste, e resta prominente.
      assert.deepEqual(saActions('stalled', 'card'), ['stop', 'restart']);
      assert.deepEqual(saActions('stalled', 'modal'), ['stop', 'restart']);

      // failed: niente sulla card, Rilancia raggiungibile solo nella modale.
      assert.deepEqual(saActions('failed', 'card'), []);
      assert.deepEqual(saActions('failed', 'modal'), ['restart']);

      // done/cancelled: nessuna azione da nessuna parte.
      for (const state of ['done', 'cancelled']) {
        assert.deepEqual(saActions(state, 'card'), [], state);
        assert.deepEqual(saActions(state, 'modal'), [], state);
      }

      // Uno stato che il backend guadagnasse domani non inventa bottoni.
      assert.deepEqual(saActions('teleporting', 'card'), []);
      assert.deepEqual(saActions('teleporting', 'modal'), []);
      assert.deepEqual(saActions(undefined, 'card'), []);
    """)


def test_relaunch_is_never_offered_on_a_successful_job() -> None:
    _run_js("""
      for (const surface of ['card', 'modal']) {
        assert.ok(!saActions('done', surface).includes('restart'), surface);
      }
      assert.ok(!saActions('failed', 'card').includes('restart'));
    """)


def test_terminal_states_match_the_backend_lifecycle() -> None:
    _run_js("""
      assert.deepEqual([...SA_TERMINAL_STATES].sort(), ['cancelled', 'done', 'failed']);
      for (const state of SA_TERMINAL_STATES) assert.ok(saIsTerminal(state), state);
      for (const state of ['running', 'stalled']) assert.ok(!saIsTerminal(state), state);
    """)


def test_a_malformed_snapshot_never_throws() -> None:
    """Lo snapshot arriva da un frame WS: una forma inattesa non deve rompere la vista."""
    _run_js("""
      for (const snapshot of [null, undefined, {}, { running: 'nope', recent: 3 },
                              { running: [null], recent: [null, { }] }]) {
        const view = saVisibleCards(snapshot, null);
        assert.ok(Array.isArray(view.running));
        assert.ok(Array.isArray(view.lingering));
      }
    """)
