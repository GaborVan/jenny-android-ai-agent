"""Rilevamento della lingua fuori dal runtime Android."""

from jenny.utils.device_locale import detect_device_locale


def test_detect_device_locale_without_chaquopy():
    assert detect_device_locale() is None
