import pytest

from jenny.config.paths import (
    get_data_dir,
    get_media_dir,
    get_runtime_subdir,
    get_workspace_path,
    set_workspace_dir,
)


@pytest.fixture(autouse=True)
def _reset_workspace_dir(monkeypatch, tmp_path):
    """Ensure no workspace override leaks between tests and provide a default.

    Patch the RuntimeContext instance attr so monkeypatch auto-restores the
    session workspace (set in conftest) after each test — no cross-module leak.
    """
    from pathlib import Path

    from jenny.runtime.context import get_runtime_context

    # Records the session workspace value and restores it after the test.
    monkeypatch.setattr(get_runtime_context(), "workspace_dir", Path(str(tmp_path)))


def test_data_dir_is_inside_workspace() -> None:
    workspace = get_workspace_path()
    assert get_data_dir() == workspace / ".jenny"


def test_data_dir_migrates_legacy_nanobot_dir() -> None:
    workspace = get_workspace_path()
    legacy_dir = workspace / ".nanobot"
    (legacy_dir / "cron").mkdir(parents=True)
    (legacy_dir / "cron" / "marker.txt").write_text("legacy")

    data = get_data_dir()

    assert data == workspace / ".jenny"
    assert not legacy_dir.exists()
    assert (data / "cron" / "marker.txt").read_text() == "legacy"


def test_data_dir_migrates_legacy_minijenny_dir() -> None:
    workspace = get_workspace_path()
    legacy_dir = workspace / ".minijenny"
    (legacy_dir / "cron").mkdir(parents=True)
    (legacy_dir / "cron" / "marker.txt").write_text("legacy")

    data = get_data_dir()

    assert data == workspace / ".jenny"
    assert not legacy_dir.exists()
    assert (data / "cron" / "marker.txt").read_text() == "legacy"


def test_data_dir_does_not_overwrite_existing_new_dir(tmp_path) -> None:
    workspace = get_workspace_path()
    new_dir = workspace / ".jenny"
    new_dir.mkdir()
    (new_dir / "marker.txt").write_text("current")
    legacy_dir = workspace / ".nanobot"
    legacy_dir.mkdir()
    (legacy_dir / "marker.txt").write_text("legacy")

    data = get_data_dir()

    assert data == new_dir
    assert (new_dir / "marker.txt").read_text() == "current"
    assert legacy_dir.exists()


def test_runtime_subdirs_are_under_data_dir() -> None:
    data = get_data_dir()
    assert get_runtime_subdir("cron") == data / "cron"
    assert get_runtime_subdir("logs") == data / "logs"


def test_media_dir_supports_channel_namespace() -> None:
    data = get_data_dir()
    assert get_media_dir() == data / "media"
    assert get_media_dir("websocket") == data / "media" / "websocket"



def test_workspace_path_raises_when_unconfigured(monkeypatch):
    from jenny.runtime.context import get_runtime_context

    monkeypatch.setattr(get_runtime_context(), "workspace_dir", None)

    with pytest.raises(RuntimeError):
        get_workspace_path()


def test_set_workspace_dir_takes_priority(tmp_path):
    workspace_dir = tmp_path / "custom"
    set_workspace_dir(workspace_dir)

    assert get_workspace_path() == workspace_dir
    assert get_data_dir() == workspace_dir / ".jenny"
