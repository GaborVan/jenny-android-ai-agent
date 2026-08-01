from jenny.agent.memory import MemoryStore


def test_build_dream_tools_registers_restricted_toolset(tmp_path):
    """build_dream_tools() è l'unica sorgente reale dei tool Dream.

    Espone la sola superficie di editing su file (read/edit/write/apply_patch)
    e nessuno strumento generico (list_dir, python_exec, message)."""
    manager = MemoryStore(workspace=tmp_path)
    registry = manager.build_dream_tools()

    names = set(registry.tool_names)
    assert "read_file" in names
    assert "edit_file" in names
    assert "write_file" in names
    assert "apply_patch" in names
    assert "list_dir" not in names
    assert "python_exec" not in names
    assert "message" not in names
