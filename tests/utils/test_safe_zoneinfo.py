"""Test per safe_zoneinfo, tzdata_available e validate_timezone_name.

Il caso "tzdata assente" (Android senza il wheel ``tzdata``) è simulato
monkeypatchando ``jenny.utils.helpers.ZoneInfo``, l'unico punto di lookup.
"""

from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

import jenny.utils.helpers as helpers
from jenny.utils.helpers import safe_zoneinfo, tzdata_available, validate_timezone_name


@pytest.fixture
def broken_tzdata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simula l'assenza totale del database tzdata (anche 'UTC' fallisce)."""

    def _raise(key: str) -> None:
        raise ZoneInfoNotFoundError(key)

    monkeypatch.setattr(helpers, "ZoneInfo", _raise)


def test_safe_zoneinfo_valid_name_returns_zoneinfo() -> None:
    tz = safe_zoneinfo("Europe/Rome")
    assert isinstance(tz, ZoneInfo)
    assert str(tz) == "Europe/Rome"


def test_safe_zoneinfo_bogus_name_returns_working_tzinfo() -> None:
    tz = safe_zoneinfo("Not/AZone")
    assert isinstance(tz, tzinfo)
    assert datetime.now(tz).utcoffset() is not None


def test_safe_zoneinfo_without_tzdata_never_raises(broken_tzdata: None) -> None:
    tz = safe_zoneinfo("UTC")
    assert isinstance(tz, tzinfo)
    assert datetime.now(tz).utcoffset() is not None


def test_tzdata_available_reflects_environment(broken_tzdata: None) -> None:
    assert tzdata_available() is False


def test_tzdata_available_true_on_host() -> None:
    assert tzdata_available() is True


def test_validate_timezone_name_degrades_without_tzdata(broken_tzdata: None) -> None:
    # Con tzdata assente accetta qualsiasi nome invece di bloccare il cron.
    assert validate_timezone_name("Europe/Rome") is None
    assert validate_timezone_name("Not/AZone") is None


def test_validate_timezone_name_rejects_unknown_with_tzdata() -> None:
    assert validate_timezone_name("Not/AZone") == "unknown timezone 'Not/AZone'"
    assert validate_timezone_name("Europe/Rome") is None
