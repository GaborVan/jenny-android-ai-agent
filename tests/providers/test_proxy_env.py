"""Tests for proxy environment variable handling in OpenAICompatProvider."""

import httpx

from jenny.providers.openai_compat_provider import OpenAICompatProvider


class TestLocalEndpointProxyDisabled:
    """Local endpoints must bypass proxy to avoid routing LAN traffic through it."""

    async def test_local_disables_proxy(self):
        provider = OpenAICompatProvider(
            api_key="test", api_base="http://localhost:11434/v1", default_model="",
        )
        await provider._ensure_client()
        transport = provider._http_client._transport
        assert isinstance(transport, httpx.AsyncHTTPTransport)

    async def test_lan_ip_disables_proxy(self):
        provider = OpenAICompatProvider(
            api_key="test", api_base="http://192.168.8.188:1234/v1", default_model="",
        )
        await provider._ensure_client()
        transport = provider._http_client._transport
        assert isinstance(transport, httpx.AsyncHTTPTransport)


class TestCloudEndpointProxyEnabled:
    """Cloud endpoints must respect proxy env vars for corporate/VPN proxies."""

    async def test_cloud_respects_trust_env(self):
        provider = OpenAICompatProvider(
            api_key="test", api_base="https://api.openai.com/v1", default_model="",
        )
        await provider._ensure_client()
        client = provider._http_client
        assert client._trust_env is True
