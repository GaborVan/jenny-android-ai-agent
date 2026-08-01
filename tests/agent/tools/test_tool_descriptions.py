from jenny.agent.tools.apply_patch import ApplyPatchTool
from jenny.agent.tools.exec_session import ListExecSessionsTool, WriteStdinTool
from jenny.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
from jenny.agent.tools.python_exec import PythonExecTool
from jenny.agent.tools.search import FindFilesTool, GrepTool


def test_coding_tool_descriptions_steer_editing_priority() -> None:
    apply_patch = ApplyPatchTool().description.lower()
    edit_file = EditFileTool().description.lower()
    write_file = WriteFileTool().description.lower()

    assert "default tool for code edits" in apply_patch
    assert "multi-file" in apply_patch
    assert "dry_run=true" in apply_patch
    assert "edit_file only for small exact replacements" in apply_patch

    assert "small, exact replacement" in edit_file
    assert "copied from read_file" in edit_file
    assert "prefer apply_patch" in edit_file

    assert "replace an entire file" in write_file
    assert "prefer apply_patch" in write_file


def test_coding_tool_descriptions_steer_discovery_and_shell_usage() -> None:
    read_file = ReadFileTool().description.lower()
    find_files = FindFilesTool().description.lower()
    grep = GrepTool().description.lower()
    exec_tool = PythonExecTool().description.lower()
    write_stdin = WriteStdinTool().description.lower()
    list_sessions = ListExecSessionsTool().description.lower()

    assert "find_files/list_dir first" in read_file
    assert "before editing" in read_file
    assert "use for workspace file discovery" in find_files
    assert "use for workspace content searches" in grep

    assert "tests, builds" in exec_tool
    assert "prefer dedicated tools" in exec_tool
    assert "calculations, data processing" in exec_tool
    assert "output is truncated" in exec_tool

    assert "do not use this to start new executions" in write_stdin
    assert "wait_for" in write_stdin
    assert "recover a session_id" in list_sessions
