import json

from jenny.webui.hidden_android_apps import (
    default_hidden_android_apps,
    hidden_android_apps_path,
    read_hidden_android_apps,
    write_hidden_android_apps,
)


def test_defaults_when_file_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)

    state = read_hidden_android_apps()

    assert state == default_hidden_android_apps()
    assert hidden_android_apps_path() == tmp_path / "webui" / "hidden-android-apps.json"


def test_normalizes_partial_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    path = hidden_android_apps_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "packages": [
                    "com.maps",
                    "com.maps",  # duplicate dropped
                    "  com.spaced  ",  # trimmed
                    "",  # empty dropped
                    "bad package!",  # invalid chars dropped
                    123,  # non-string dropped
                ]
            }
        ),
        encoding="utf-8",
    )

    state = read_hidden_android_apps()

    assert state["schema_version"] == 1
    assert state["packages"] == ["com.maps", "com.spaced"]


def test_write_round_trip_and_stamps_updated_at(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)

    written = write_hidden_android_apps({"packages": ["com.a", "com.b", "com.a"]})

    assert written["packages"] == ["com.a", "com.b"]
    assert written["updated_at"] is not None

    reloaded = read_hidden_android_apps()
    assert reloaded["packages"] == ["com.a", "com.b"]
