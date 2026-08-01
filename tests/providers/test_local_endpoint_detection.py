"""Tests for _is_local_endpoint detection and keepalive configuration."""


from jenny.providers.openai_compat_provider import (
    OpenAICompatProvider,
    _is_local_endpoint,
)


class TestIsLocalEndpoint:
    """Test the _is_local_endpoint helper."""

    def test_none(self):
        assert _is_local_endpoint(None) is False

    def test_empty(self):
        assert _is_local_endpoint("") is False

    def test_localhost(self):
        assert _is_local_endpoint("http://localhost:1234/v1") is True

    def test_localhost_https(self):
        assert _is_local_endpoint("https://localhost:8080/v1") is True

    def test_loopback_127(self):
        assert _is_local_endpoint("http://127.0.0.1:11434/v1") is True

    def test_ipv6_loopback(self):
        assert _is_local_endpoint("http://[::1]:1234/v1") is True

    def test_public_api(self):
        assert _is_local_endpoint("https://api.openai.com/v1") is False

    def test_openrouter(self):
        assert _is_local_endpoint("https://openrouter.ai/api/v1") is False

    def test_case_insensitive(self):
        assert _is_local_endpoint("http://LOCALHOST:1234/v1") is True

    def test_trailing_slash(self):
        assert _is_local_endpoint("http://127.0.0.1:8080/v1/") is True

    def test_public_hostname_containing_localhost_is_not_local(self):
        assert _is_local_endpoint("https://notlocalhost.example/v1") is False

    def test_public_hostname_containing_private_ip_prefix_is_not_local(self):
        assert _is_local_endpoint("https://api10.example.com/v1") is False

    def test_url_without_scheme(self):
        assert _is_local_endpoint("127.0.0.1:8080/v1") is True


class TestLocalKeepaliveConfig:
    """Verify that local endpoints get keepalive_expiry=0."""

    async def test_localhost_disables_keepalive(self):
        provider = OpenAICompatProvider(
            api_key="test", api_base="http://localhost:11434/v1", default_model="",
        )
        await provider._ensure_client()
        pool = provider._http_client._transport._pool
        assert pool._keepalive_expiry == 0

    async def test_cloud_keeps_default_keepalive(self):
        provider = OpenAICompatProvider(
            api_key="test", api_base="https://api.openai.com/v1", default_model="",
        )
        await provider._ensure_client()
        pool = provider._http_client._transport._pool
        assert pool._keepalive_expiry == 5.0
