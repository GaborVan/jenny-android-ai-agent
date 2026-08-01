from jenny.security.workspace_access import default_workspace_scope
from jenny.session.manager import SessionManager
from jenny.webui.workspaces import WebUIWorkspaceController


def _seed_session_scope(sessions, chat_id: str, scope) -> None:
    """Scrive uno scope persistito nella metadata di sessione (setup di test).

    Il writer di produzione (``persist_scope``) e stato rimosso col sottosistema
    di switching; qui replichiamo la scrittura per esercitare la lettura live
    ancora usata da thread/file-preview e sessioni legacy on-device.
    """
    session = sessions.get_or_create(f"websocket:{chat_id}")
    session.metadata["webui"] = True
    session.metadata["workspace_scope"] = scope.metadata()
    sessions.save(session)


def test_webui_default_access_does_not_override_explicit_session_scope(tmp_path) -> None:
    default = tmp_path / "default"
    project = tmp_path / "project"
    default.mkdir()
    project.mkdir()
    sessions = SessionManager(tmp_path / "sessions")
    controller = WebUIWorkspaceController(
        session_manager=sessions,
        default_workspace=default,
        default_restrict_to_workspace=True,
    )
    explicit = default_workspace_scope(project, restrict_to_workspace=False)
    _seed_session_scope(sessions, "explicit-chat", explicit)

    scope = controller.scope_for_session_key("websocket:explicit-chat")

    assert scope.project_path == project.resolve()
    assert scope.access_mode == "full"


def test_scope_for_session_key_reads_metadata_without_full_history(
    tmp_path,
    monkeypatch,
) -> None:
    default = tmp_path / "default"
    project = tmp_path / "project"
    default.mkdir()
    project.mkdir()
    sessions = SessionManager(tmp_path / "sessions")
    controller = WebUIWorkspaceController(
        session_manager=sessions,
        default_workspace=default,
        default_restrict_to_workspace=True,
    )
    explicit = default_workspace_scope(project, restrict_to_workspace=False)
    _seed_session_scope(sessions, "metadata-only", explicit)

    def fail_full_read(_key: str) -> None:
        raise AssertionError("scope lookup should not read full session history")

    monkeypatch.setattr(sessions, "read_session_file", fail_full_read)

    scope = controller.scope_for_session_key("websocket:metadata-only")

    assert scope.project_path == project.resolve()
    assert scope.access_mode == "full"
