"""I tre orologi dell'innesco: chi viene giardinato a questo tick.

Passo **T4.3** di ``roadmap/taccuino-passi.md``. Ogni cancello è provato come
**unico impedimento** — con gli altri due aperti — perché una guardia che non può
scattare non è una guardia, ed è la stessa lezione già scritta in
``agent/autocompact.py`` (là togliere il filtro non faceva cadere nessun test,
perché la sola chiave che ci arrivava era comunque ammessa). Qui vale verbatim:
tre condizioni in ``and``, e un test che non isola non prova niente.

Il cancello che conta di più è il **fermo**, nella sua seconda metà: ``run_gardener``
gira su una chiave sua e non condivide il lock della conversazione del progetto,
quindi «turno in volo» è tutto ciò che tiene utente e giardiniere dallo scrivere
la mappa nello stesso momento.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.agent.gardener_schedule import pick_project
from jenny.agent.gardener_state import GardenerState, write_state

_NOW = datetime(2026, 8, 23, 21, 0, 0)

# Un tick con i cancelli aperti: nessuna passata recente, nessuno che parla.
_OPEN = {"idle_min": 30, "min_hours_between_passes": 6}


def _project(workspace: Path, name: str, *, lines: int = 2) -> Path:
    root = workspace / "wikis" / name
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text(f"# {name}\n", encoding="utf-8")
    journal = root / "raw" / "journal"
    journal.mkdir(parents=True)
    if lines:
        (journal / "20260823.md").write_text(
            "# 2026-08-23\n\n" + "".join(f"- 09:0{i} — fatto {i}\n" for i in range(lines)),
            encoding="utf-8",
        )
    return root


class _Sessions:
    """``SessionManager`` ridotto a quel che la selezione chiede."""

    def __init__(self, updated: dict[str, datetime] | None = None) -> None:
        self._updated = updated or {}

    def read_session_metadata(self, key: str):
        stamp = self._updated.get(key)
        return {"updated_at": stamp.isoformat()} if stamp else None


def _pick(workspace: Path, sessions=None, **kw):
    return pick_project(
        workspace,
        sessions=sessions or _Sessions(),
        now=_NOW,
        **{**_OPEN, **kw},
    )


# ── Il caso normale ──────────────────────────────────────────────────────────


def test_a_project_with_new_lines_and_nobody_talking_is_picked(tmp_path):
    _project(tmp_path, "viaggio")

    pick = _pick(tmp_path)

    assert pick is not None
    assert pick.store.name == "viaggio" and pick.delta_lines == 2


def test_no_projects_means_nothing_to_do(tmp_path):
    (tmp_path / "wikis").mkdir()
    assert _pick(tmp_path) is None


def test_a_folder_that_is_not_a_project_is_not_considered(tmp_path):
    (tmp_path / "wikis" / "appunti").mkdir(parents=True)
    assert _pick(tmp_path) is None


# ── Cancello 1: il delta ─────────────────────────────────────────────────────


def test_no_new_lines_no_pass(tmp_path):
    _project(tmp_path, "viaggio", lines=0)
    assert _pick(tmp_path) is None


def test_a_journal_already_read_no_pass(tmp_path):
    """Il caso vero del secondo tick: le righe ci sono, ma sono già state lette.
    Se questo cancello cadesse, ogni mezz'ora ripartirebbe una passata sullo
    stesso materiale — cioè il degrado, a pagamento."""
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(cursor={"raw/journal/20260823.md": 4}))

    assert _pick(tmp_path) is None


# ── Cancello 2: il fermo ─────────────────────────────────────────────────────


def test_a_conversation_that_just_spoke_blocks_the_pass(tmp_path):
    _project(tmp_path, "viaggio")
    sessions = _Sessions({"project:viaggio": _NOW - timedelta(minutes=5)})

    assert _pick(tmp_path, sessions=sessions) is None


def test_the_same_conversation_gone_quiet_lets_it_through(tmp_path):
    _project(tmp_path, "viaggio")
    sessions = _Sessions({"project:viaggio": _NOW - timedelta(minutes=45)})

    assert _pick(tmp_path, sessions=sessions) is not None


def test_a_turn_in_flight_blocks_the_pass(tmp_path):
    """**Il cancello che conta.** Il giardiniere non condivide il lock della
    conversazione del progetto: senza questo, utente e giardiniere possono
    riscrivere la mappa nello stesso momento e l'ultimo che salva cancella
    l'altro. Non è una rifinitura del fermo — è il caso peggiore, e il fermo da
    solo non lo copre (una sessione ferma da un'ora può avere un turno lungo in
    corso in questo istante)."""
    _project(tmp_path, "viaggio")
    sessions = _Sessions({"project:viaggio": _NOW - timedelta(hours=2)})

    assert _pick(tmp_path, sessions=sessions,
                 active_session_keys=("project:viaggio",)) is None


def test_another_project_being_busy_does_not_block_this_one(tmp_path):
    _project(tmp_path, "viaggio")

    assert _pick(tmp_path, active_session_keys=("project:altro",)) is not None


def test_a_project_never_talked_to_counts_as_quiet(tmp_path):
    """Nessun metadato vuol dire che quella conversazione non è mai esistita:
    non c'è niente che stia parlando."""
    _project(tmp_path, "viaggio")

    assert _pick(tmp_path, sessions=_Sessions()) is not None


def test_an_unreadable_timestamp_does_not_freeze_a_project_forever(tmp_path):
    """Un ``updated_at`` corrotto non deve valere «sta parlando adesso»: sarebbe
    un progetto mai più giardinato per un guasto che non si vede."""
    _project(tmp_path, "viaggio")

    class _Broken(_Sessions):
        def read_session_metadata(self, key):
            return {"updated_at": "non-una-data"}

    assert _pick(tmp_path, sessions=_Broken()) is not None


def test_idle_zero_means_the_clock_is_off(tmp_path):
    _project(tmp_path, "viaggio")
    sessions = _Sessions({"project:viaggio": _NOW})

    assert _pick(tmp_path, sessions=sessions, idle_min=0) is not None


# ── Cancello 3: la distanza ──────────────────────────────────────────────────


def test_a_recent_pass_blocks_the_next_one(tmp_path):
    """La lezione del degrado del Dream come numero: un secondo giro ravvicinato
    sulla stessa materia è quello che rimpasta invece di aggiungere."""
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(
        cursor={}, last_run_at=(_NOW - timedelta(hours=2)).isoformat()
    ))

    assert _pick(tmp_path) is None


def test_an_old_pass_does_not(tmp_path):
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(
        cursor={}, last_run_at=(_NOW - timedelta(hours=7)).isoformat()
    ))

    assert _pick(tmp_path) is not None


def test_a_project_never_gardened_goes_first(tmp_path):
    """Il caso da servire per primo è il progetto nuovo, e un cursore perso non
    deve poterlo bloccare per sei ore."""
    _project(tmp_path, "nuovo")
    root = _project(tmp_path, "vecchio")
    write_state(root, GardenerState(
        cursor={}, last_run_at=(_NOW - timedelta(hours=7)).isoformat()
    ))

    pick = _pick(tmp_path)

    assert pick is not None and pick.store.name == "nuovo"


def test_distance_zero_means_the_clock_is_off(tmp_path):
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(cursor={}, last_run_at=_NOW.isoformat()))

    assert _pick(tmp_path, min_hours_between_passes=0) is not None


# ── Una per tick, la meno recente ────────────────────────────────────────────


def test_only_one_project_is_picked_per_tick(tmp_path):
    """Otto progetti con righe nuove farebbero otto turni LLM di fila su un
    telefono. Il tetto a uno rende il costo di un tick prevedibile per
    costruzione; gli altri aspettano mezz'ora, che su un lavoro con sei ore di
    distanza minima non è un ritardo."""
    for name in ("alfa", "beta", "gamma"):
        _project(tmp_path, name)

    pick = _pick(tmp_path)

    assert pick is not None
    assert pick.store.name in {"alfa", "beta", "gamma"}


def test_the_least_recently_gardened_wins(tmp_path):
    for name, hours in (("alfa", 7), ("beta", 30), ("gamma", 12)):
        root = _project(tmp_path, name)
        write_state(root, GardenerState(
            cursor={}, last_run_at=(_NOW - timedelta(hours=hours)).isoformat()
        ))

    pick = _pick(tmp_path)

    assert pick is not None and pick.store.name == "beta"


def test_the_choice_is_deterministic_on_a_tie(tmp_path):
    """A pari merito decide il nome: due tick sullo stesso stato devono scegliere
    lo stesso progetto, altrimenti un guasto non si riproduce."""
    for name in ("gamma", "alfa", "beta"):
        _project(tmp_path, name)

    assert _pick(tmp_path).store.name == "alfa"
    assert _pick(tmp_path).store.name == "alfa"


# ── L'ordine dei cancelli, che è il costo ────────────────────────────────────


def test_the_journal_is_not_even_opened_when_a_cheaper_gate_is_shut(tmp_path, monkeypatch):
    """L'ordine dei tre cancelli non è estetico: distanza e fermo si decidono con
    due letture piccole, il delta vuole aprire i diari. In un'installazione con
    otto progetti fermi un tick deve toccare pochi byte, e questo è il test che
    lo tiene vero quando qualcuno riordinerà i controlli."""
    root = _project(tmp_path, "viaggio")
    write_state(root, GardenerState(
        cursor={}, last_run_at=(_NOW - timedelta(minutes=10)).isoformat()
    ))
    opened: list[str] = []
    from jenny.agent import gardener_schedule as mod

    real = mod.GardenerStore.read_delta

    def _spy(self):
        opened.append(self.name)
        return real(self)

    monkeypatch.setattr(mod.GardenerStore, "read_delta", _spy)

    assert _pick(tmp_path) is None
    assert opened == [], "il diario è stato letto per un progetto già escluso"


@pytest.mark.parametrize("blocked", ["delta", "idle", "active", "distance"])
def test_each_gate_alone_is_enough_to_stop_a_pass(tmp_path, blocked):
    """Il test riassuntivo: ogni cancello, da solo, con gli altri aperti."""
    root = _project(tmp_path, "viaggio", lines=0 if blocked == "delta" else 2)
    sessions = _Sessions(
        {"project:viaggio": _NOW} if blocked == "idle" else {}
    )
    if blocked == "distance":
        write_state(root, GardenerState(
            cursor={}, last_run_at=(_NOW - timedelta(hours=1)).isoformat()
        ))
    active = ("project:viaggio",) if blocked == "active" else ()

    assert _pick(tmp_path, sessions=sessions, active_session_keys=active) is None


def test_with_every_gate_open_it_runs(tmp_path):
    """Il controllo del test sopra: senza di questo, un ``pick_project`` che
    ritorna sempre ``None`` passerebbe tutti e quattro."""
    _project(tmp_path, "viaggio")

    assert _pick(tmp_path) is not None


def test_it_uses_the_configured_wikis_folder(tmp_path):
    root = tmp_path / "progetti" / "viaggio"
    (root / "wiki").mkdir(parents=True)
    (root / "raw" / "journal").mkdir(parents=True)
    (root / "raw" / "journal" / "20260823.md").write_text("- 09:00 — x\n", encoding="utf-8")

    pick = pick_project(
        tmp_path, sessions=_Sessions(), now=_NOW, wikis_dir_name="progetti", **_OPEN
    )

    assert pick is not None and pick.store.name == "viaggio"


def test_a_session_manager_that_raises_does_not_stop_the_tick(tmp_path):
    """Se leggere i metadati alza, il tick non deve morire: il giardiniere
    salterebbe per sempre su un guasto altrui."""
    _project(tmp_path, "viaggio")

    class _Angry:
        def read_session_metadata(self, key):
            raise RuntimeError("disco pieno")

    assert _pick(tmp_path, sessions=SimpleNamespace(
        read_session_metadata=_Angry().read_session_metadata
    )) is not None
