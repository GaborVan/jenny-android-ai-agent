"""Gancio pre-scrittura ``write_size_guard`` sui tool write-capable.

Il gancio è opzionale: senza di esso i tool devono comportarsi esattamente come
prima (vedi ``TestNoGuardRegression``, che è il test che conta davvero qui).
"""

from __future__ import annotations

from pathlib import Path

from jenny.agent.tools.apply_patch import ApplyPatchTool
from jenny.agent.tools.file_state import FileStates
from jenny.agent.tools.filesystem import EditFileTool, WriteFileTool, WriteSizeGuard


def _refuse_over(limit: int, seen: list[tuple[Path, str]] | None = None) -> WriteSizeGuard:
    """Guard che rifiuta ogni testo più lungo di ``limit`` caratteri."""

    def guard(path: Path, text: str) -> str | None:
        if seen is not None:
            seen.append((path, text))
        if len(text) > limit:
            return f"Refused: {path.name} would be {len(text)} chars (budget {limit})."
        return None

    return guard


def _refuse_all(seen: list[tuple[Path, str]] | None = None) -> WriteSizeGuard:
    return _refuse_over(-1, seen)


# ---------------------------------------------------------------------------
# Regressione: senza guard nulla cambia
# ---------------------------------------------------------------------------


class TestNoGuardRegression:
    """Il default (``write_size_guard=None``) non deve spostare un bit.

    È la garanzia che l'agente principale, che non passa mai il gancio, resti
    identico a prima: stesso risultato, stesso contenuto su disco, stessi
    contatori.
    """

    async def test_write_file_unchanged(self, tmp_path):
        states = FileStates()
        tool = WriteFileTool(workspace=tmp_path, file_states=states)
        target = tmp_path / "note.txt"

        result = await tool.execute(path=str(target), content="hello world")

        assert result == f"Successfully wrote 11 characters to {target}"
        assert target.read_text(encoding="utf-8") == "hello world"
        assert (states.writes_attempted, states.writes_ok) == (1, 1)
        assert states.writes_refused_budget == 0

    async def test_edit_file_unchanged(self, tmp_path):
        states = FileStates()
        tool = EditFileTool(workspace=tmp_path, file_states=states)
        target = tmp_path / "calc.py"
        target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

        result = await tool.execute(
            path=str(target), old_text="a + b", new_text="a - b"
        )

        assert result.endswith(f"Successfully edited {target}")
        assert target.read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"
        assert (states.writes_attempted, states.writes_ok) == (1, 1)
        assert states.writes_refused_budget == 0

    async def test_apply_patch_unchanged(self, tmp_path):
        states = FileStates()
        tool = ApplyPatchTool(workspace=tmp_path, file_states=states)
        target = tmp_path / "calc.py"
        target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

        result = await tool.execute(
            edits=[
                {
                    "path": "calc.py",
                    "action": "replace",
                    "old_text": "    return a + b",
                    "new_text": "    return a - b",
                }
            ]
        )

        assert "update calc.py" in result
        assert target.read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"
        assert (states.writes_attempted, states.writes_ok) == (1, 1)
        assert states.writes_refused_budget == 0


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


class TestWriteFileGuard:

    async def test_refusal_leaves_file_untouched(self, tmp_path):
        states = FileStates()
        target = tmp_path / "note.txt"
        target.write_text("original", encoding="utf-8")
        tool = WriteFileTool(
            workspace=tmp_path, file_states=states, write_size_guard=_refuse_over(4)
        )

        result = await tool.execute(path=str(target), content="way too long")

        assert result.startswith("Refused: note.txt")
        assert target.read_text(encoding="utf-8") == "original"
        assert states.writes_ok == 0
        # L'intento resta contato: è la semantica di ``_resolve_write``, e Dream
        # ci si appoggia per non avanzare il cursore su un fatto non scritto.
        assert states.writes_attempted == 1
        assert states.writes_refused_budget == 1

    async def test_refusal_does_not_create_the_file(self, tmp_path):
        target = tmp_path / "nested" / "note.txt"
        tool = WriteFileTool(workspace=tmp_path, write_size_guard=_refuse_all())

        result = await tool.execute(path=str(target), content="x")

        assert result.startswith("Refused:")
        assert not target.exists()
        assert not target.parent.exists()

    async def test_guard_that_accepts_writes_normally(self, tmp_path):
        states = FileStates()
        seen: list[tuple[Path, str]] = []
        tool = WriteFileTool(
            workspace=tmp_path, file_states=states, write_size_guard=_refuse_over(100, seen)
        )
        target = tmp_path / "note.txt"

        result = await tool.execute(path=str(target), content="small")

        assert "Successfully wrote" in result
        assert target.read_text(encoding="utf-8") == "small"
        assert seen == [(target.resolve(), "small")]
        assert (states.writes_attempted, states.writes_ok) == (1, 1)
        assert states.writes_refused_budget == 0


# ---------------------------------------------------------------------------
# edit_file — i tre rami, uno per uno
# ---------------------------------------------------------------------------


class TestEditFileGuard:

    async def test_create_branch_refused(self, tmp_path):
        """old_text='' su file assente: il file non deve nascere."""
        states = FileStates()
        target = tmp_path / "new.md"
        tool = EditFileTool(
            workspace=tmp_path, file_states=states, write_size_guard=_refuse_over(3)
        )

        result = await tool.execute(path=str(target), old_text="", new_text="too long")

        assert result.startswith("Refused: new.md")
        assert not target.exists()
        assert (states.writes_attempted, states.writes_ok) == (1, 0)
        assert states.writes_refused_budget == 1

    async def test_create_branch_allowed(self, tmp_path):
        states = FileStates()
        target = tmp_path / "new.md"
        tool = EditFileTool(
            workspace=tmp_path, file_states=states, write_size_guard=_refuse_over(100)
        )

        result = await tool.execute(path=str(target), old_text="", new_text="ok")

        assert result == f"Successfully created {target}"
        assert target.read_text(encoding="utf-8") == "ok"
        assert (states.writes_attempted, states.writes_ok) == (1, 1)
        assert states.writes_refused_budget == 0

    async def test_empty_existing_file_branch_refused(self, tmp_path):
        """old_text='' su file esistente ma vuoto: contenuto invariato."""
        states = FileStates()
        target = tmp_path / "empty.md"
        target.write_text("   \n", encoding="utf-8")
        tool = EditFileTool(
            workspace=tmp_path, file_states=states, write_size_guard=_refuse_over(3)
        )

        result = await tool.execute(path=str(target), old_text="", new_text="too long")

        assert result.startswith("Refused: empty.md")
        assert target.read_text(encoding="utf-8") == "   \n"
        assert (states.writes_attempted, states.writes_ok) == (1, 0)
        assert states.writes_refused_budget == 1

    async def test_empty_existing_file_branch_allowed(self, tmp_path):
        states = FileStates()
        target = tmp_path / "empty.md"
        target.write_text("", encoding="utf-8")
        tool = EditFileTool(
            workspace=tmp_path, file_states=states, write_size_guard=_refuse_over(100)
        )

        result = await tool.execute(path=str(target), old_text="", new_text="filled")

        assert result == f"Successfully edited {target}"
        assert target.read_text(encoding="utf-8") == "filled"
        assert (states.writes_attempted, states.writes_ok) == (1, 1)
        assert states.writes_refused_budget == 0

    async def test_main_branch_refused(self, tmp_path):
        states = FileStates()
        target = tmp_path / "calc.py"
        original = "def add(a, b):\n    return a + b\n"
        target.write_text(original, encoding="utf-8")
        tool = EditFileTool(
            workspace=tmp_path, file_states=states, write_size_guard=_refuse_over(10)
        )

        result = await tool.execute(
            path=str(target), old_text="a + b", new_text="a - b"
        )

        assert result.startswith("Refused: calc.py")
        assert target.read_text(encoding="utf-8") == original
        assert (states.writes_attempted, states.writes_ok) == (1, 0)
        assert states.writes_refused_budget == 1

    async def test_main_branch_sees_post_crlf_text(self, tmp_path):
        """Su un file CRLF il gancio deve pesare i byte veri, non la forma LF."""
        seen: list[tuple[Path, str]] = []
        target = tmp_path / "crlf.txt"
        target.write_bytes(b"alpha\r\nbeta\r\n")
        tool = EditFileTool(workspace=tmp_path, write_size_guard=_refuse_over(100, seen))

        result = await tool.execute(path=str(target), old_text="beta", new_text="gamma")

        assert f"Successfully edited {target}" in result
        assert target.read_bytes() == b"alpha\r\ngamma\r\n"
        assert len(seen) == 1
        guarded_path, guarded_text = seen[0]
        assert guarded_path == target.resolve()
        assert guarded_text == "alpha\r\ngamma\r\n"

    async def test_main_branch_crlf_refusal_keeps_bytes(self, tmp_path):
        states = FileStates()
        target = tmp_path / "crlf.txt"
        target.write_bytes(b"alpha\r\nbeta\r\n")
        # 14 caratteri con CRLF, 12 senza: la soglia distingue i due conteggi.
        tool = EditFileTool(
            workspace=tmp_path, file_states=states, write_size_guard=_refuse_over(13)
        )

        result = await tool.execute(path=str(target), old_text="beta", new_text="gamma")

        assert result.startswith("Refused: crlf.txt")
        assert target.read_bytes() == b"alpha\r\nbeta\r\n"
        assert states.writes_ok == 0
        assert states.writes_refused_budget == 1


# ---------------------------------------------------------------------------
# apply_patch
# ---------------------------------------------------------------------------


class TestApplyPatchGuard:

    async def test_one_over_budget_writes_nothing(self, tmp_path):
        """Tutto-o-niente: un solo file oltre budget aborta l'intera patch."""
        states = FileStates()
        small = tmp_path / "small.txt"
        big = tmp_path / "big.txt"
        small.write_text("small original\n", encoding="utf-8")
        big.write_text("big original\n", encoding="utf-8")
        tool = ApplyPatchTool(
            workspace=tmp_path, file_states=states, write_size_guard=_refuse_over(20)
        )

        result = await tool.execute(
            edits=[
                {
                    "path": "small.txt",
                    "action": "replace",
                    "old_text": "small original",
                    "new_text": "tiny",
                },
                {
                    "path": "big.txt",
                    "action": "replace",
                    "old_text": "big original",
                    "new_text": "x" * 40,
                },
            ]
        )

        assert result.startswith("Refused: big.txt")
        assert small.read_text(encoding="utf-8") == "small original\n"
        assert big.read_text(encoding="utf-8") == "big original\n"
        assert states.writes_ok == 0
        assert states.writes_attempted == 2
        assert states.writes_refused_budget == 1

    async def test_new_file_over_budget_is_not_created(self, tmp_path):
        tool = ApplyPatchTool(workspace=tmp_path, write_size_guard=_refuse_over(5))

        result = await tool.execute(
            edits=[{"path": "fresh.txt", "action": "add", "new_text": "x" * 40}]
        )

        assert result.startswith("Refused: fresh.txt")
        assert not (tmp_path / "fresh.txt").exists()

    async def test_all_within_budget_applies(self, tmp_path):
        states = FileStates()
        seen: list[tuple[Path, str]] = []
        target = tmp_path / "calc.py"
        target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        tool = ApplyPatchTool(
            workspace=tmp_path, file_states=states, write_size_guard=_refuse_over(1000, seen)
        )

        result = await tool.execute(
            edits=[
                {
                    "path": "calc.py",
                    "action": "replace",
                    "old_text": "    return a + b",
                    "new_text": "    return a - b",
                }
            ]
        )

        assert "update calc.py" in result
        assert target.read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"
        assert seen == [(target.resolve(), "def add(a, b):\n    return a - b\n")]
        assert (states.writes_attempted, states.writes_ok) == (1, 1)
        assert states.writes_refused_budget == 0

    async def test_dry_run_over_budget_is_not_refused(self, tmp_path):
        """Semantica scelta: il dry-run non passa dal gancio.

        Non scrive niente, quindi non c'è scrittura da rifiutare; e restituire un
        rifiuto al posto del riepilogo toglierebbe proprio l'informazione — quanto
        crescerebbe il file — che serve per decidere cosa potare.
        """
        states = FileStates()
        seen: list[tuple[Path, str]] = []
        target = tmp_path / "notes.md"
        target.write_text("original\n", encoding="utf-8")
        tool = ApplyPatchTool(
            workspace=tmp_path, file_states=states, write_size_guard=_refuse_over(1, seen)
        )

        result = await tool.execute(
            edits=[{"path": "notes.md", "action": "add", "new_text": "x" * 40}],
            dry_run=True,
        )

        assert result.startswith("Patch dry-run succeeded:")
        assert target.read_text(encoding="utf-8") == "original\n"
        assert seen == []
        assert states.writes_refused_budget == 0
