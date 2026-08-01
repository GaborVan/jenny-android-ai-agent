"""Tests for the installed-Android-apps WebUI plumbing (bridge access)."""

from __future__ import annotations

import json

import jenny.webui.android_apps_api as api


class FakeBridge:
    def __init__(self, apps=None, launched=True, raises=False):
        self._apps = apps or []
        self._launched = launched
        self._raises = raises
        self.launch_calls: list[str] = []

    def listInstalledApps(self):  # noqa: N802 - mirrors the Kotlin bridge
        if self._raises:
            raise RuntimeError("bridge boom")
        return json.dumps(self._apps)

    def launchApp(self, package_name):  # noqa: N802 - mirrors the Kotlin bridge
        if self._raises:
            raise RuntimeError("bridge boom")
        self.launch_calls.append(package_name)
        return self._launched

    def uninstallApp(self, package_name):  # noqa: N802 - mirrors the Kotlin bridge
        if self._raises:
            raise RuntimeError("bridge boom")
        return True

    def openAppInfo(self, package_name):  # noqa: N802 - mirrors the Kotlin bridge
        if self._raises:
            raise RuntimeError("bridge boom")
        return True


def _with_bridge(monkeypatch, bridge):
    monkeypatch.setattr(api, "get_android_context", lambda: object())
    monkeypatch.setattr(api, "_BRIDGE_INSTANCE", bridge)


class TestNoAndroidContext:
    async def test_list_degrades_to_empty(self, monkeypatch):
        monkeypatch.setattr(api, "get_android_context", lambda: None)
        assert await api.webui_android_apps_payload() == {"apps": []}

    async def test_launch_returns_false(self, monkeypatch):
        monkeypatch.setattr(api, "get_android_context", lambda: None)
        assert await api.launch_android_app("com.example.x") is False

    async def test_uninstall_returns_false(self, monkeypatch):
        monkeypatch.setattr(api, "get_android_context", lambda: None)
        assert await api.uninstall_android_app("com.example.x") is False

    async def test_app_info_returns_false(self, monkeypatch):
        monkeypatch.setattr(api, "get_android_context", lambda: None)
        assert await api.open_android_app_info("com.example.x") is False


class TestWithBridge:
    async def test_list_returns_bridge_payload(self, monkeypatch):
        apps = [{"label": "Mappe", "packageName": "com.maps"}]
        _with_bridge(monkeypatch, FakeBridge(apps=apps))
        assert await api.webui_android_apps_payload() == {"apps": apps}

    async def test_list_swallows_bridge_failure(self, monkeypatch):
        _with_bridge(monkeypatch, FakeBridge(raises=True))
        assert await api.webui_android_apps_payload() == {"apps": []}

    async def test_launch_passes_package_and_returns_bool(self, monkeypatch):
        bridge = FakeBridge(launched=True)
        _with_bridge(monkeypatch, bridge)
        assert await api.launch_android_app("com.maps") is True
        assert bridge.launch_calls == ["com.maps"]

    async def test_launch_swallows_bridge_failure(self, monkeypatch):
        _with_bridge(monkeypatch, FakeBridge(raises=True))
        assert await api.launch_android_app("com.maps") is False

    async def test_uninstall_returns_bool(self, monkeypatch):
        _with_bridge(monkeypatch, FakeBridge())
        assert await api.uninstall_android_app("com.maps") is True

    async def test_uninstall_swallows_bridge_failure(self, monkeypatch):
        _with_bridge(monkeypatch, FakeBridge(raises=True))
        assert await api.uninstall_android_app("com.maps") is False

    async def test_app_info_returns_bool(self, monkeypatch):
        _with_bridge(monkeypatch, FakeBridge())
        assert await api.open_android_app_info("com.maps") is True

    async def test_app_info_swallows_bridge_failure(self, monkeypatch):
        _with_bridge(monkeypatch, FakeBridge(raises=True))
        assert await api.open_android_app_info("com.maps") is False
