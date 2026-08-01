"""Tests for jenny.security.network — SSRF protection and internal URL detection."""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from jenny.security.network import (
    configure_ssrf_whitelist,
    validate_app_server_target,
    validate_url_target,
)


def _fake_resolve(host: str, results: list[str]):
    """Return a getaddrinfo mock that maps the given host to fake IP results."""
    def _resolver(hostname, port, family=0, type_=0):
        if hostname == host:
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)) for ip in results]
        raise socket.gaierror(f"cannot resolve {hostname}")
    return _resolver


# ---------------------------------------------------------------------------
# validate_url_target — scheme / domain basics
# ---------------------------------------------------------------------------

def test_rejects_non_http_scheme():
    ok, err = validate_url_target("ftp://example.com/file")
    assert not ok
    assert "http" in err.lower()


def test_rejects_missing_domain():
    ok, err = validate_url_target("http://")
    assert not ok


# ---------------------------------------------------------------------------
# validate_url_target — blocked private/internal IPs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ip,label", [
    ("127.0.0.1", "loopback"),
    ("127.0.0.2", "loopback_alt"),
    ("10.0.0.1", "rfc1918_10"),
    ("172.16.5.1", "rfc1918_172"),
    ("192.168.1.1", "rfc1918_192"),
    ("169.254.169.254", "metadata"),
    ("0.0.0.0", "zero"),
])
def test_blocks_private_ipv4(ip: str, label: str):
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("evil.com", [ip])):
        ok, err = validate_url_target("http://evil.com/path")
        assert not ok, f"Should block {label} ({ip})"
        assert "private" in err.lower() or "blocked" in err.lower()


def test_blocks_ipv6_loopback():
    def _resolver(hostname, port, family=0, type_=0):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 0, 0, 0))]
    with patch("jenny.security.network.socket.getaddrinfo", _resolver):
        ok, err = validate_url_target("http://evil.com/")
        assert not ok


# ---------------------------------------------------------------------------
# validate_url_target — IPv6-mapped IPv4 bypass prevention
# ---------------------------------------------------------------------------

def _fake_resolve_v6(host: str, results: list[str]):
    """Like _fake_resolve but returns AF_INET6 tuples for IPv6 addresses."""
    def _resolver(hostname, port, family=0, type_=0):
        if hostname == host:
            entries = []
            for ip in results:
                if ":" in ip:
                    entries.append((socket.AF_INET6, socket.SOCK_STREAM, 0, "", (ip, 0, 0, 0)))
                else:
                    entries.append((socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)))
            return entries
        raise socket.gaierror(f"cannot resolve {hostname}")
    return _resolver


def test_blocks_ipv6_mapped_loopback():
    """::ffff:127.0.0.1 must be blocked just like 127.0.0.1."""
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve_v6("evil.com", ["::ffff:127.0.0.1"])):
        ok, err = validate_url_target("http://evil.com/")
        assert not ok
        assert "blocked" in err.lower()


def test_blocks_ipv6_mapped_metadata():
    """::ffff:169.254.169.254 must be blocked just like 169.254.169.254."""
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve_v6("evil.com", ["::ffff:169.254.169.254"])):
        ok, err = validate_url_target("http://evil.com/")
        assert not ok


def test_blocks_ipv6_mapped_rfc1918():
    """::ffff:10.0.0.1 must be blocked just like 10.0.0.1."""
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve_v6("evil.com", ["::ffff:10.0.0.1"])):
        ok, err = validate_url_target("http://evil.com/")
        assert not ok


def test_allows_public_ipv6():
    """Public IPv6 addresses must still be allowed."""
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve_v6("example.com", ["2606:4700::6810:84e5"])):
        ok, err = validate_url_target("http://example.com/")
        assert ok, f"Should allow public IPv6, got: {err}"


# ---------------------------------------------------------------------------
# validate_url_target — allows public IPs
# ---------------------------------------------------------------------------

def test_allows_public_ip():
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("example.com", ["93.184.216.34"])):
        ok, err = validate_url_target("http://example.com/page")
        assert ok, f"Should allow public IP, got: {err}"


def test_allows_normal_https():
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("github.com", ["140.82.121.3"])):
        ok, err = validate_url_target("https://github.com/flagdizero/jenny")
        assert ok


# ---------------------------------------------------------------------------
# validate_url_target — loopback exception
# ---------------------------------------------------------------------------

def test_loopback_exception_allows_literal_localhost_only():
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("localhost", ["127.0.0.1"])):
        ok, _ = validate_url_target("http://localhost:8765/", allow_loopback=True)
        assert ok


def test_loopback_exception_rejects_public_name_resolving_to_loopback():
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("example.com", ["127.0.0.1"])):
        ok, _ = validate_url_target("http://example.com:8765/", allow_loopback=True)
        assert not ok


def test_loopback_exception_rejects_metadata():
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("169.254.169.254", ["169.254.169.254"])):
        ok, _ = validate_url_target("http://169.254.169.254/latest/meta-data/", allow_loopback=True)
        assert not ok


# ---------------------------------------------------------------------------
# SSRF whitelist — allow specific CIDR ranges (#2669)
# ---------------------------------------------------------------------------

def test_blocks_cgnat_by_default():
    """100.64.0.0/10 (CGNAT / Tailscale) is blocked by default."""
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("ts.local", ["100.100.1.1"])):
        ok, _ = validate_url_target("http://ts.local/api")
        assert not ok


def test_whitelist_allows_cgnat():
    """Whitelisting 100.64.0.0/10 lets Tailscale addresses through."""
    configure_ssrf_whitelist(["100.64.0.0/10"])
    try:
        with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("ts.local", ["100.100.1.1"])):
            ok, err = validate_url_target("http://ts.local/api")
            assert ok, f"Whitelisted CGNAT should be allowed, got: {err}"
    finally:
        configure_ssrf_whitelist([])


def test_whitelist_does_not_affect_other_blocked():
    """Whitelisting CGNAT must not unblock other private ranges."""
    configure_ssrf_whitelist(["100.64.0.0/10"])
    try:
        with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("evil.com", ["10.0.0.1"])):
            ok, _ = validate_url_target("http://evil.com/secret")
            assert not ok
    finally:
        configure_ssrf_whitelist([])


def test_whitelist_invalid_cidr_ignored():
    """Invalid CIDR entries are silently skipped."""
    configure_ssrf_whitelist(["not-a-cidr", "100.64.0.0/10"])
    try:
        with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("ts.local", ["100.100.1.1"])):
            ok, _ = validate_url_target("http://ts.local/api")
            assert ok
    finally:
        configure_ssrf_whitelist([])


def test_whitelist_allows_ipv6_mapped_cgnat():
    """Whitelist must work when DNS returns IPv6-mapped CGNAT address."""
    configure_ssrf_whitelist(["100.64.0.0/10"])
    try:
        with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve_v6("ts.local", ["::ffff:100.100.1.1"])):
            ok, err = validate_url_target("http://ts.local/api")
            assert ok, f"Whitelisted IPv6-mapped CGNAT should be allowed, got: {err}"
    finally:
        configure_ssrf_whitelist([])


# ---------------------------------------------------------------------------
# validate_app_server_target — Jenny App server policy (LAN allowed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ip", ["192.168.1.50", "10.0.0.7", "172.16.3.4"])
def test_app_server_allows_private_lan(ip):
    """App servers are user-declared LAN devices: RFC1918 must be allowed."""
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("piante.lan", [ip])):
        ok, err = validate_app_server_target("http://piante.lan:8080/plants")
        assert ok, f"App policy should allow {ip}, got: {err}"


@pytest.mark.parametrize(
    "ip,label",
    [
        ("127.0.0.1", "loopback (gateway self-bridge)"),
        ("169.254.169.254", "link-local / metadata"),
        ("0.0.0.1", "0.0.0.0/8"),
        ("100.100.1.1", "CGNAT"),
    ],
)
def test_app_server_still_blocks_dangerous_ranges(ip, label):
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("srv.lan", [ip])):
        ok, _ = validate_app_server_target("http://srv.lan/x")
        assert not ok, f"App policy must block {label} ({ip})"


def test_app_server_blocks_ipv6_loopback():
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve_v6("srv.lan", ["::1"])):
        ok, _ = validate_app_server_target("http://srv.lan/x")
        assert not ok


def test_app_server_cgnat_whitelist_still_honored():
    """The existing ssrf whitelist (e.g. Tailscale) applies to app servers too."""
    configure_ssrf_whitelist(["100.64.0.0/10"])
    try:
        with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("ts.lan", ["100.100.1.1"])):
            ok, err = validate_app_server_target("http://ts.lan/x")
            assert ok, f"Whitelisted CGNAT should be allowed, got: {err}"
    finally:
        configure_ssrf_whitelist([])


def test_app_server_scheme_check():
    ok, _ = validate_app_server_target("ftp://192.168.1.1/x")
    assert not ok


def test_url_target_policy_unchanged_by_app_policy():
    """validate_url_target must keep blocking RFC1918 (regression guard)."""
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("evil.com", ["192.168.1.50"])):
        ok, _ = validate_url_target("http://evil.com/x")
        assert not ok
