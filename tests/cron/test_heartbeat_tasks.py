"""Le parti pure di B13: come si legge ``HEARTBEAT.md`` e come si nomina un task.

``HEARTBEAT.md`` è un file libero, scritto a mano dall'utente: non ha id, non ha
uno schema, e non gliene vogliamo imporre uno. Tutto quello che questo modulo
deve garantire è che lo stesso task, fra un run e l'altro, si riconosca — e che
quando *non* si riconosce sbagli dalla parte giusta (sequenza che riparte da
zero, mai una sequenza ereditata dal task sbagliato).
"""

from __future__ import annotations

from jenny.cron.could_not_check import (
    CouldNotCheckMark,
    could_not_check_reason,
    parse_could_not_check_marks,
    parse_delegated_marks,
)
from jenny.cron.heartbeat_tasks import (
    attribute_marks,
    escalation_block,
    followup_block,
    parse_heartbeat_tasks,
    pending_tasks,
    record_followup_outcomes,
    record_task_outcomes,
    resolve_pending_delegations,
    task_index_block,
    tasks_due_for_escalation,
)
from jenny.cron.types import CronJobState, CronTaskCheckState
from jenny.runtime.cron_dispatch import heartbeat_has_active_tasks

_WATERBOT = (
    "- Ogni ciclo, controlla l'umidità delle piante e avvisami solo sotto il 15%. "
    "Se hps è irraggiungibile salta il ciclo in silenzio."
)
_VITAMINE = "- Alle 9 ricordami le vitamine."


def _file(*tasks: str) -> str:
    body = "\n".join(tasks)
    return f"# Heartbeat Tasks\n\n<!-- un commento -->\n\n## Active Tasks\n\n{body}\n"


class TestReadingTheFile:
    def test_each_bullet_is_one_task(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))

        assert [t.index for t in tasks] == [1, 2]
        assert tasks[0].label.startswith("Ogni ciclo, controlla l'umidità")
        assert tasks[1].label == "Alle 9 ricordami le vitamine."

    def test_an_indented_line_continues_the_task_above_it(self) -> None:
        """Un sotto-punto è parte del task, non un task suo."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, "  - solo se il sole è alto"))

        assert len(tasks) == 1
        assert "solo se il sole è alto" in tasks[0].text

    def test_a_prose_paragraph_is_a_task_too(self) -> None:
        """Non tutti scrivono elenchi puntati: un paragrafo vale come task."""
        tasks = parse_heartbeat_tasks(
            _file("Controlla la posta\ne dimmi se c'è qualcosa di urgente.", "", "Poi taci.")
        )

        assert [t.label for t in tasks] == ["Controlla la posta", "Poi taci."]

    def test_headers_comments_and_other_sections_are_not_tasks(self) -> None:
        content = (
            "# Heartbeat Tasks\n\n"
            "<!--\nquesta è una spiegazione\nsu più righe\n-->\n\n"
            "## Done\n\n- roba vecchia\n\n"
            "## Active Tasks\n\n"
            f"{_VITAMINE}\n"
        )

        assert [t.label for t in parse_heartbeat_tasks(content)] == [
            "Alle 9 ricordami le vitamine."
        ]

    def test_a_file_with_only_headers_has_no_tasks(self) -> None:
        assert parse_heartbeat_tasks("# Heartbeat Tasks\n\n## Active Tasks\n\n") == []

    def test_the_two_readers_of_this_file_agree(self) -> None:
        """``heartbeat_has_active_tasks`` decide se il turno parte; questo modulo
        decide cosa contiene. Se divergessero, il turno girerebbe su un file che
        qui risulta vuoto — o non girerebbe su uno che qui ha dei task."""
        for content in (
            _file(_WATERBOT),
            _file(_WATERBOT, _VITAMINE),
            "# Heartbeat Tasks\n\n## Active Tasks\n\n",
            "# Heartbeat\n\nnothing here\n",
            "## Active Tasks\n\n<!-- solo un commento -->\n",
        ):
            assert heartbeat_has_active_tasks(content) is bool(parse_heartbeat_tasks(content))


class TestTaskIdentity:
    def test_the_same_task_keeps_its_id_across_reads(self) -> None:
        first = parse_heartbeat_tasks(_file(_WATERBOT))[0]
        second = parse_heartbeat_tasks(_file(_WATERBOT))[0]

        assert first.id == second.id

    def test_moving_a_task_in_the_file_does_not_change_its_id(self) -> None:
        """Riordinare l'elenco è la modifica più frequente: non deve contare."""
        before = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        after = parse_heartbeat_tasks(_file(_VITAMINE, _WATERBOT))

        assert before[0].id == after[1].id
        assert after[1].index == 2

    def test_reformatting_without_changing_the_words_keeps_the_id(self) -> None:
        """Il marcatore di lista, il checkbox e gli spazi non sono il task."""
        plain = parse_heartbeat_tasks(_file("- Alle 9 ricordami le vitamine."))[0]
        starred = parse_heartbeat_tasks(_file("* [ ]   Alle 9  ricordami le vitamine."))[0]

        assert plain.id == starred.id

    def test_rewording_a_task_gives_it_a_new_id(self) -> None:
        """Il modo di sbagliare che abbiamo scelto: chi riscrive un task riparte
        da zero. Meglio un avviso in ritardo di K cicli che un avviso che parla
        di un controllo che non esiste più."""
        before = parse_heartbeat_tasks(_file("- Alle 9 ricordami le vitamine."))[0]
        after = parse_heartbeat_tasks(_file("- Alle 9 ricordami le vitamine e l'acqua."))[0]

        assert before.id != after.id

    def test_a_very_long_task_gets_a_short_label(self) -> None:
        task = parse_heartbeat_tasks(_file("- " + "parola " * 60))[0]

        assert len(task.label) <= 90
        assert task.label.endswith("…")


class TestTheMarker:
    def test_a_numbered_marker_carries_the_task_and_the_reason(self) -> None:
        assert parse_could_not_check_marks("CHECK_FAILED 2: hps unreachable") == [
            CouldNotCheckMark("2", "hps unreachable")
        ]

    def test_one_turn_can_declare_several_failed_tasks(self) -> None:
        text = "CHECK_FAILED 1: import rotto\nCHECK_FAILED 3: timeout"

        assert [(m.ref, m.reason) for m in parse_could_not_check_marks(text)] == [
            ("1", "import rotto"),
            ("3", "timeout"),
        ]

    def test_the_shapes_a_model_writes_without_thinking_are_all_read(self) -> None:
        for line in ("CHECK_FAILED #2: x", "CHECK_FAILED [2]: x", "CHECK_FAILED 2. x",
                     "**CHECK_FAILED 2: x**", "- CHECK_FAILED 2 - x"):
            assert parse_could_not_check_marks(line) == [CouldNotCheckMark("2", "x")], line

    def test_the_monitor_form_without_a_number_still_works(self) -> None:
        """B8 non si tocca: un monitor ha un controllo solo e non numera niente."""
        assert parse_could_not_check_marks("CHECK_FAILED: hps down") == [
            CouldNotCheckMark(None, "hps down")
        ]
        assert could_not_check_reason("CHECK_FAILED: hps down") == "hps down"
        assert could_not_check_reason("tutto a posto") is None

    def test_a_reason_that_starts_with_a_number_is_not_a_task_number(self) -> None:
        assert could_not_check_reason("CHECK_FAILED: 3 letture su 3 fallite") == (
            "3 letture su 3 fallite"
        )


class TestAttributingAMarkToATask:
    def test_with_one_task_a_marker_without_a_number_is_unambiguous(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))

        reasons, unattributed = attribute_marks(tasks, [CouldNotCheckMark(None, "hps down")])

        assert reasons == {tasks[0].id: "hps down"}
        assert unattributed == []

    def test_with_two_tasks_a_marker_without_a_number_blames_nobody(self) -> None:
        """Incolpare il task sbagliato produrrebbe un avviso su un controllo sano."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))

        reasons, unattributed = attribute_marks(tasks, [CouldNotCheckMark(None, "boh")])

        assert reasons == {}
        assert unattributed == ["boh"]

    def test_a_number_that_does_not_exist_blames_nobody(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))

        reasons, unattributed = attribute_marks(tasks, [CouldNotCheckMark("7", "boh")])

        assert reasons == {}
        assert unattributed == ["boh"]


class TestTheStateSelfHeals:
    def _state_with(self, task_id: str, count: int) -> CronJobState:
        return CronJobState(
            task_checks={task_id: CronTaskCheckState(consecutive_could_not_check=count)}
        )

    def test_a_task_that_runs_again_loses_its_entry(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = self._state_with(tasks[0].id, 2)

        outcome = record_task_outcomes(
            state, tasks, [], now_ms=1, escalating=[], spoke=False
        )

        assert outcome.any_failure is False
        assert state.task_checks == {}

    def test_a_task_deleted_from_the_file_is_pruned(self) -> None:
        """Altrimenti lo store cresce a ogni task che l'utente cancella."""
        gone = parse_heartbeat_tasks(_file("- un task che non c'è più"))[0]
        state = self._state_with(gone.id, 2)
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))

        record_task_outcomes(state, tasks, [], now_ms=1, escalating=[], spoke=False)

        assert state.task_checks == {}

    def test_the_streak_of_the_broken_task_grows_alone(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState()

        for _ in range(3):
            record_task_outcomes(
                state,
                tasks,
                [CouldNotCheckMark("1", "hps down")],
                now_ms=7,
                escalating=[],
                spoke=False,
            )

        assert list(state.task_checks) == [tasks[0].id]
        entry = state.task_checks[tasks[0].id]
        assert entry.consecutive_could_not_check == 3
        assert entry.since_ms == 7
        assert entry.label.startswith("Ogni ciclo")

    def test_escalation_is_due_one_run_before_the_threshold(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = self._state_with(tasks[0].id, 1)
        assert tasks_due_for_escalation(state, tasks) == []

        state = self._state_with(tasks[0].id, 2)
        assert tasks_due_for_escalation(state, tasks) == [tasks[0]]

    def test_a_task_already_escalated_is_not_due_again(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = CronJobState(
            task_checks={
                tasks[0].id: CronTaskCheckState(
                    consecutive_could_not_check=9, escalated=True
                )
            }
        )

        assert tasks_due_for_escalation(state, tasks) == []


class TestADelegatedTaskWaitsForItsVerdict:
    """Il turno che delega non ha l'esito: `spawn` ritorna subito."""

    def test_the_two_markers_do_not_read_each_other(self) -> None:
        text = "CHECK_DELEGATED 1: leggi hps\nCHECK_FAILED 2: sveglia non impostata"

        assert [(m.ref, m.reason) for m in parse_delegated_marks(text)] == [
            ("1", "leggi hps")
        ]
        assert [(m.ref, m.reason) for m in parse_could_not_check_marks(text)] == [
            ("2", "sveglia non impostata")
        ]

    def test_a_delegated_task_keeps_its_entry_instead_of_being_pruned(self) -> None:
        """Senza questo, ogni giro azzererebbe la sequenza prima del verdetto."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState(
            task_checks={tasks[0].id: CronTaskCheckState(consecutive_could_not_check=2)}
        )

        outcome = record_task_outcomes(
            state,
            tasks,
            [],
            now_ms=11,
            escalating=[],
            spoke=False,
            delegated=[CouldNotCheckMark("1", "leggi hps")],
        )

        assert outcome.any_failure is False
        assert outcome.pending == [tasks[0]]
        entry = state.task_checks[tasks[0].id]
        assert entry.consecutive_could_not_check == 2
        assert entry.pending_since_ms == 11

    def test_the_verdict_from_the_announce_turn_counts_a_failure(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState(
            task_checks={tasks[0].id: CronTaskCheckState(pending_since_ms=11)}
        )

        outcome = record_followup_outcomes(
            state,
            tasks,
            [CouldNotCheckMark(None, "import di wb_probe fallito")],
            now_ms=12,
            escalating=[],
            spoke=False,
        )

        assert outcome.failed == [tasks[0]]
        entry = state.task_checks[tasks[0].id]
        assert entry.consecutive_could_not_check == 1
        assert entry.pending_since_ms is None

    def test_the_announce_turn_never_prunes(self) -> None:
        """Ha in mano un risultato, non il file: dedurre da qui che gli altri
        task sono sani cancellerebbe sequenze che nessuno ha smentito."""
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))
        state = CronJobState(
            task_checks={
                tasks[0].id: CronTaskCheckState(pending_since_ms=11),
                tasks[1].id: CronTaskCheckState(consecutive_could_not_check=2),
            }
        )

        record_followup_outcomes(
            state, tasks, [], now_ms=12, escalating=[], spoke=False
        )

        assert state.task_checks[tasks[1].id].consecutive_could_not_check == 2

    def test_a_verdict_that_never_arrives_is_resolved_in_favour_of_the_task(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))
        state = CronJobState(
            task_checks={
                tasks[0].id: CronTaskCheckState(
                    consecutive_could_not_check=2, pending_since_ms=11, label="Ogni ciclo"
                )
            }
        )

        assert resolve_pending_delegations(state) == ["Ogni ciclo"]
        assert state.task_checks == {}
        assert pending_tasks(state, tasks) == []

    def test_the_announce_block_names_only_the_pending_checks(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))

        block = followup_block([tasks[0]], [])

        assert "1. Ogni ciclo" in block
        assert "vitamine" not in block
        assert "CHECK_FAILED <number>:" in block
        assert "EXACTLY ONCE" not in block

    def test_the_announce_block_carries_the_escalation_when_it_is_due(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT))

        block = followup_block([tasks[0]], [tasks[0]])

        assert "EXACTLY ONCE" in block


class TestThePromptFragments:
    def test_the_index_block_names_every_task_by_number(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))

        block = task_index_block(tasks)

        assert "1. Ogni ciclo" in block
        assert "2. Alle 9 ricordami le vitamine." in block

    def test_the_index_block_says_the_numbers_are_not_for_the_user(self) -> None:
        block = task_index_block(parse_heartbeat_tasks(_file(_WATERBOT)))

        assert "never in a message to the user" in block

    def test_the_escalation_block_asks_for_one_message_for_all_of_them(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))

        block = escalation_block(tasks)

        assert "EXACTLY ONCE" in block
        assert "never one per task" in block
        assert "no internal file names" in block

    def test_the_escalation_block_lists_only_the_tasks_that_are_due(self) -> None:
        tasks = parse_heartbeat_tasks(_file(_WATERBOT, _VITAMINE))

        block = escalation_block([tasks[1]])

        assert "Alle 9 ricordami le vitamine." in block
        assert "Ogni ciclo" not in block
