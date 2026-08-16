"""Un messaggio dell'utente riarma l'escalation dell'heartbeat.

La decisione: **se ha scritto a Jenny, l'avviso l'ha letto.** Da lì in poi un
guasto ancora aperto torna a essere una notizia, e nessun tetto temporale serve
a dirlo — lo dice l'utente stesso, presentandosi.

Perché serve. Su un controllo *delegato* ``escalated`` era di fatto definitivo:
``resolve_pending_delegations`` lo conserva, il ramo che pota per omissione non
lo raggiunge mai, ``tasks_due_for_escalation`` lo salta e ``already_warned_block``
dice al modello "di questi non parlare, qualunque cosa trovi". L'unica uscita
automatica è un ``CHECK_OK`` che il modello può non scrivere mai — e un
follow-up senza marcatore è lo *stato normale* di un controllo delegato sano
misurato sul device (v. ``roadmap/heartbeat-escalation-amnesia.md``).

Le due mitigazioni misurate, che questo file tiene ferme perché senza di loro la
funzione è una seccatura invece di una correzione:

- **il riarmo azzera la sequenza.** Il tetto "un avviso ogni 90 minuti" che la
  soglia di 3 guasti dovrebbe dare non esiste durante un guasto prolungato: la
  sequenza è già a 12 dopo sei ore e la soglia non si riattraversa più. Senza
  l'azzeramento un task riarmato è dovuto al run *successivo* — 30 minuti — e
  tre chiacchiere in una nottata di guasto valgono tre avvisi.
- **un turno di cron non è l'utente.** Le righe ``role:"user"`` della sessione
  unificata sono anche il modo in cui un ``reminder`` schedulato si persiste
  (``jenny/cron/session_turns.py``): senza il filtro, un promemoria delle 09:00
  riarmerebbe ogni avviso ogni mattina senza nessuno davanti allo schermo.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jenny.agent.turn_types import TurnOutcome
from jenny.cron.could_not_check import ESCALATE_AFTER_FAILURES
from jenny.cron.heartbeat_tasks import (
    parse_heartbeat_tasks,
    rearm_after_user_message,
    tasks_already_warned,
    tasks_due_for_escalation,
)
from jenny.cron.service import CronService
from jenny.cron.session_turns import CRON_HISTORY_META
from jenny.cron.types import (
    CronJob,
    CronJobState,
    CronPayload,
    CronSchedule,
    CronTaskCheckState,
)
from jenny.runtime.cron_dispatch import CronDispatcher
from jenny.session.keys import UNIFIED_SESSION_KEY, session_key_for_channel
from jenny.session.manager import Session, last_user_message_ms

_WATERBOT = (
    "- Ogni ciclo, controlla l'umidità delle piante e avvisami solo se una è sotto il 15%."
)

_ESCALATION_HEAD = "These recurring tasks have now failed to run"
_SILENCE_HEAD = "The user has ALREADY been told"

# Un istante fisso: il riarmo confronta due timbri, e con l'orologio vero i due
# possono cadere nello stesso millisecondo.
_T0_MS = 1_755_000_000_000
_CYCLE_MS = 1_800_000


def _heartbeat_md(*tasks: str) -> str:
    return "# Heartbeat Tasks\n\n## Active Tasks\n\n" + "\n".join(tasks) + "\n"


def _escalated_labels(prompt: str) -> list[str]:
    lines = prompt.splitlines()
    start = next(i for i, line in enumerate(lines) if _ESCALATION_HEAD in line)
    labels: list[str] = []
    for line in lines[start + 1:]:
        if not line.startswith("- "):
            break
        labels.append(line.split(". ", 1)[1])
    return labels


class _FakeSessions:
    """Sessioni vere: il lettore del timbro utente è ciò che si sta provando."""

    def __init__(self) -> None:
        self.by_key: dict[str, Session] = {}

    def get_or_create(self, key: str) -> Session:
        return self.by_key.setdefault(key, Session(key=key))

    def save(self, _session: Session) -> None:
        pass


class _FakeHeartbeatAgent:
    """Segue il prompt: parla se glielo si chiede, tace se gli si dice di tacere."""

    def __init__(self) -> None:
        self.sessions = _FakeSessions()
        self.prompts: list[str] = []
        self.messages: list[str] = []
        self.broken: dict[int, str] = {}

    async def process_direct_outcome(self, prompt: str, **_kwargs: Any) -> TurnOutcome:
        self.prompts.append(prompt)
        marks = "\n".join(
            f"CHECK_FAILED {number}: {reason}" for number, reason in sorted(self.broken.items())
        )
        if _ESCALATION_HEAD in prompt and self.broken:
            self.messages.append("Non riesco più a eseguire: " + ", ".join(_escalated_labels(prompt)))
            return TurnOutcome.spoke_via_tool(final_text=marks)
        return TurnOutcome.silent(final_text=marks)

    def evict_pruned_sessions(self, keys: list[str]) -> None:  # pragma: no cover
        pass


class _Harness:
    """``CronService`` + ``CronDispatcher`` veri, con un orologio che si guida."""

    def __init__(self, tmp_path: Path, content: str) -> None:
        self.file = tmp_path / "HEARTBEAT.md"
        self.file.write_text(content, encoding="utf-8")
        self.store_path = tmp_path / "cron" / "jobs.json"
        self.now_ms = _T0_MS
        self.agent = _FakeHeartbeatAgent()
        self.service = CronService(self.store_path)
        self.service.on_job = CronDispatcher(
            get_agent=lambda: self.agent,
            config=SimpleNamespace(workspace_path=tmp_path),
            cron=self.service,
            heartbeat_cfg=SimpleNamespace(keep_recent_messages=8),
            now_ms=lambda: self.now_ms,
        ).dispatch
        self.service.register_system_job(
            CronJob(
                id="heartbeat",
                name="heartbeat",
                schedule=CronSchedule(kind="every", every_ms=_CYCLE_MS),
                payload=CronPayload(kind="system_event"),
            )
        )

    async def cycles(self, count: int) -> None:
        for _ in range(count):
            self.now_ms += _CYCLE_MS
            await self.service.run_job("heartbeat")

    def user_says(
        self,
        text: str = "ciao",
        *,
        session_key: str = UNIFIED_SESSION_KEY,
        role: str = "user",
        **extra: Any,
    ) -> None:
        """Una riga in sessione con il timbro dell'orologio del test.

        L'orologio avanza di un minuto prima: un utente scrive *dopo* l'avviso,
        e con due timbri identici il confronto non direbbe niente né in un senso
        né nell'altro.
        """
        self.now_ms += 60_000
        session = self.agent.sessions.get_or_create(session_key)
        session.messages.append(
            {
                "role": role,
                "content": text,
                "timestamp": datetime.fromtimestamp(self.now_ms / 1000).isoformat(),
                **extra,
            }
        )

    @property
    def state(self) -> CronJobState:
        job = self.service.get_job("heartbeat")
        assert job is not None
        return job.state

    def entry_for(self, index: int = 0):
        tasks = parse_heartbeat_tasks(self.file.read_text(encoding="utf-8"))
        return self.state.task_checks.get(tasks[index].id)


@pytest.fixture
def broken(tmp_path: Path) -> _Harness:
    harness = _Harness(tmp_path, _heartbeat_md(_WATERBOT))
    harness.agent.broken = {1: "hps irraggiungibile"}
    return harness


class TestTheUserComingBackReArmsTheAlert:
    async def test_a_user_message_brings_the_alert_back(self, broken: _Harness) -> None:
        """Il test che vale gli altri: guasto ancora aperto, utente tornato."""
        await broken.cycles(ESCALATE_AFTER_FAILURES)
        assert len(broken.agent.messages) == 1

        broken.user_says()
        await broken.cycles(ESCALATE_AFTER_FAILURES)

        assert len(broken.agent.messages) == 2

    async def test_without_a_user_message_the_alert_stays_one(self, broken: _Harness) -> None:
        """La garanzia che questo file non deve rompere: un guasto, un avviso."""
        await broken.cycles(ESCALATE_AFTER_FAILURES + 12)

        assert len(broken.agent.messages) == 1

    async def test_the_silence_block_goes_away_the_moment_the_user_speaks(
        self, broken: _Harness
    ) -> None:
        """I due blocchi sono complementari: se uno smette di valere, dal prompt
        deve sparire subito — un "di questi non parlare" su un task che sta per
        essere riannunciato è la contraddizione che questo meccanismo evita."""
        await broken.cycles(ESCALATE_AFTER_FAILURES)
        await broken.cycles(1)
        assert _SILENCE_HEAD in broken.agent.prompts[-1]

        broken.user_says()
        await broken.cycles(1)

        assert _SILENCE_HEAD not in broken.agent.prompts[-1]
        assert _ESCALATION_HEAD not in broken.agent.prompts[-1]


class TestTheRateIsStillCapped:
    async def test_a_re_armed_task_needs_the_full_streak_again(self, broken: _Harness) -> None:
        """Mitigazione misurata: la sequenza riparte da zero.

        Senza questo il task riarmato è dovuto al run successivo, perché la
        soglia (3) è già stata attraversata da un pezzo e la sequenza vale 4, 5,
        12… Trenta minuti invece di novanta, per ogni messaggio dell'utente.
        """
        await broken.cycles(ESCALATE_AFTER_FAILURES)
        broken.user_says()

        for _ in range(ESCALATE_AFTER_FAILURES - 1):
            await broken.cycles(1)
            assert len(broken.agent.messages) == 1, "riavvisato prima della soglia"

        await broken.cycles(1)
        assert len(broken.agent.messages) == 2

    async def test_three_messages_in_the_same_window_are_still_one_alert(
        self, broken: _Harness
    ) -> None:
        """Chi chiacchiera durante un guasto non deve pagarne il conto."""
        await broken.cycles(ESCALATE_AFTER_FAILURES)

        broken.user_says("una")
        await broken.cycles(1)
        broken.user_says("due")
        await broken.cycles(1)
        broken.user_says("tre")
        await broken.cycles(1)

        assert len(broken.agent.messages) == 2


class TestWhatIsNotAUser:
    async def test_a_scheduled_reminder_does_not_re_arm(self, broken: _Harness) -> None:
        """``jenny/agent/loop.py`` persiste un ``reminder`` cron come riga
        ``role:"user"`` nella sessione unificata. Senza il filtro, un promemoria
        delle 09:00 riarmerebbe ogni avviso ogni mattina."""
        await broken.cycles(ESCALATE_AFTER_FAILURES)

        broken.user_says("Alle 9 ricordami le vitamine", **{CRON_HISTORY_META: True})
        await broken.cycles(ESCALATE_AFTER_FAILURES + 3)

        assert len(broken.agent.messages) == 1

    async def test_an_assistant_message_does_not_re_arm(self, broken: _Harness) -> None:
        """Jenny che scrive non è l'utente che legge — ed è ciò che rende
        inutilizzabile ``session.updated_at``: si muove anche quando è lei a
        scrivere l'avviso stesso."""
        await broken.cycles(ESCALATE_AFTER_FAILURES)

        broken.user_says("ecco l'avviso", role="assistant")
        await broken.cycles(ESCALATE_AFTER_FAILURES + 3)

        assert len(broken.agent.messages) == 1

    async def test_a_message_from_telegram_re_arms_too(self, broken: _Harness) -> None:
        """Il canale non conta: ogni canale mappa sulla conversazione unica.

        Fissato apposta — se un domani ``session_key_for_channel`` smettesse di
        collassare tutto su ``unified:default``, il riarmo da Telegram sparirebbe
        in silenzio e nessun altro test se ne accorgerebbe.
        """
        assert session_key_for_channel("telegram", "4242") == UNIFIED_SESSION_KEY
        await broken.cycles(ESCALATE_AFTER_FAILURES)

        broken.user_says("ci sei?", session_key=session_key_for_channel("telegram", "4242"))
        await broken.cycles(ESCALATE_AFTER_FAILURES)

        assert len(broken.agent.messages) == 2


class TestTheStoreOnTheDevice:
    async def test_a_pre_upgrade_entry_never_re_arms(self, tmp_path: Path) -> None:
        """La voce già sul telefono ha ``escalated`` e non ha il timbro: "abbiamo
        parlato, non si sa quando". Riarmarla farebbe partire un avviso nel
        momento esatto in cui l'APK atterra, per un guasto vecchio di ore."""
        store = tmp_path / "cron" / "jobs.json"
        store.parent.mkdir(parents=True)
        tasks = parse_heartbeat_tasks(_heartbeat_md(_WATERBOT))
        store.write_text(
            json.dumps(
                {
                    "version": 1,
                    "jobs": [
                        {
                            "id": "heartbeat",
                            "name": "heartbeat",
                            "schedule": {"kind": "every", "everyMs": _CYCLE_MS},
                            "payload": {"kind": "system_event"},
                            "state": {
                                # Senza la scadenza, ``register_system_job``
                                # butta lo stato e riparte da zero: il telefono
                                # ce l'ha, e senza di lei questo test proverebbe
                                # tutt'altro.
                                "nextRunAtMs": _T0_MS + _CYCLE_MS,
                                "taskChecks": {
                                    tasks[0].id: {
                                        "consecutiveCouldNotCheck": 12,
                                        "sinceMs": _T0_MS - 6 * 3_600_000,
                                        "escalated": True,
                                        "label": tasks[0].label,
                                    }
                                }
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        harness = _Harness(tmp_path, _heartbeat_md(_WATERBOT))
        harness.agent.broken = {1: "hps irraggiungibile"}
        harness.user_says()
        await harness.cycles(ESCALATE_AFTER_FAILURES + 3)

        assert harness.agent.messages == []

    async def test_the_stamp_survives_a_restart(self, broken: _Harness) -> None:
        """Se il timbro non arriva su disco, ogni riavvio riporta la voce allo
        stato "abbiamo parlato, non si sa quando" e il riarmo non scatta più."""
        await broken.cycles(ESCALATE_AFTER_FAILURES)
        entry = broken.entry_for()
        assert entry is not None
        stamped = entry.escalated_at_ms
        assert stamped is not None

        reloaded = CronService(broken.store_path)
        job = reloaded.get_job("heartbeat")
        assert job is not None
        assert list(job.state.task_checks.values())[0].escalated_at_ms == stamped


class TestTheStampItself:
    async def test_a_second_alert_re_stamps(self, broken: _Harness) -> None:
        """Altrimenti lo stesso messaggio dell'utente riarma anche il ciclo dopo:
        il timbro vecchio resterebbe più vecchio di lui per sempre."""
        await broken.cycles(ESCALATE_AFTER_FAILURES)
        first = broken.entry_for()
        assert first is not None and first.escalated_at_ms is not None

        broken.user_says()
        await broken.cycles(ESCALATE_AFTER_FAILURES)

        second = broken.entry_for()
        assert second is not None and second.escalated_at_ms is not None
        assert second.escalated_at_ms > first.escalated_at_ms

    async def test_a_recovery_forgets_the_stamp_with_the_entry(self, broken: _Harness) -> None:
        await broken.cycles(ESCALATE_AFTER_FAILURES)
        broken.agent.broken = {}
        await broken.cycles(1)

        assert broken.entry_for() is None


class TestTheTwoBlocksStayDisjoint:
    """La proprietà che tiene insieme il prompt, ripresa dopo un riarmo.

    ``test_a_task_is_never_both_due_and_already_warned`` la fissa sullo stato a
    riposo; qui la si guarda nell'unico momento nuovo in cui potrebbe rompersi,
    cioè quando ``escalated`` smette di valere.
    """

    def _state(self) -> tuple[CronJobState, list]:
        tasks = parse_heartbeat_tasks(_heartbeat_md(_WATERBOT))
        state = CronJobState(
            task_checks={
                tasks[0].id: CronTaskCheckState(
                    consecutive_could_not_check=12,
                    escalated=True,
                    escalated_at_ms=_T0_MS,
                    label=tasks[0].label,
                )
            }
        )
        return state, tasks

    def test_before_the_user_speaks_it_is_only_already_warned(self) -> None:
        state, tasks = self._state()

        assert tasks_already_warned(state, tasks) == [tasks[0]]
        assert tasks_due_for_escalation(state, tasks) == []

    def test_after_the_user_speaks_it_is_in_neither_list_yet(self) -> None:
        """Riarmato *e* con la sequenza azzerata: non più zittito, non ancora
        dovuto. È la mezz'ora di silenzio che la mitigazione compra."""
        state, tasks = self._state()

        assert rearm_after_user_message(state, user_spoke_at_ms=_T0_MS + 1) == [tasks[0].label]

        assert tasks_already_warned(state, tasks) == []
        assert tasks_due_for_escalation(state, tasks) == []

    def test_a_message_older_than_the_alert_changes_nothing(self) -> None:
        state, tasks = self._state()

        assert rearm_after_user_message(state, user_spoke_at_ms=_T0_MS - 1) == []

        assert tasks_already_warned(state, tasks) == [tasks[0]]

    def test_nobody_spoke_at_all(self) -> None:
        state, tasks = self._state()

        assert rearm_after_user_message(state, user_spoke_at_ms=None) == []

        assert tasks_already_warned(state, tasks) == [tasks[0]]


class TestTheLastUserMessageReader:
    """Il lettore da solo: ``jenny/session/manager.py``."""

    def test_no_session_at_all(self) -> None:
        assert last_user_message_ms(None) is None

    def test_a_session_with_no_user_rows(self) -> None:
        session = Session(key=UNIFIED_SESSION_KEY)
        session.add_message("assistant", "ciao")
        assert last_user_message_ms(session) is None

    def test_an_empty_session(self) -> None:
        assert last_user_message_ms(Session(key=UNIFIED_SESSION_KEY)) is None

    def test_it_reads_the_last_one_and_not_the_first(self) -> None:
        session = Session(key=UNIFIED_SESSION_KEY)
        first = datetime(2026, 8, 16, 9, 0, 0)
        last = first + timedelta(hours=3)
        session.messages = [
            {"role": "user", "content": "a", "timestamp": first.isoformat()},
            {"role": "assistant", "content": "b", "timestamp": last.isoformat()},
            {"role": "user", "content": "c", "timestamp": last.isoformat()},
        ]

        assert last_user_message_ms(session) == int(last.timestamp() * 1000)

    def test_a_cron_row_is_skipped_and_the_real_one_behind_it_wins(self) -> None:
        session = Session(key=UNIFIED_SESSION_KEY)
        human = datetime(2026, 8, 16, 9, 0, 0)
        reminder = human + timedelta(hours=3)
        session.messages = [
            {"role": "user", "content": "a", "timestamp": human.isoformat()},
            {
                "role": "user",
                "content": "promemoria",
                "timestamp": reminder.isoformat(),
                CRON_HISTORY_META: True,
            },
        ]

        assert last_user_message_ms(session) == int(human.timestamp() * 1000)

    def test_a_row_without_a_usable_timestamp_is_not_a_crash(self) -> None:
        session = Session(key=UNIFIED_SESSION_KEY)
        good = datetime(2026, 8, 16, 9, 0, 0)
        session.messages = [
            {"role": "user", "content": "a", "timestamp": good.isoformat()},
            {"role": "user", "content": "b", "timestamp": "ieri sera"},
            {"role": "user", "content": "c"},
            {"role": "user", "content": "d", "timestamp": None},
        ]

        assert last_user_message_ms(session) == int(good.timestamp() * 1000)
