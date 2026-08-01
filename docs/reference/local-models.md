# Local models

Running Jenny against a self-hosted model server — Ollama, LM Studio, vLLM, llama.cpp, or anything else that speaks the OpenAI Chat Completions API — instead of a hosted provider.

There's nothing Jenny-specific about self-hosted inference: you configure it exactly like any other `openai_compat` provider (see [Providers and models](./providers.md)). What's different is the network path, because Jenny runs on the phone, not on the same machine as your model server.

## The offline loop, and why it isn't quite "offline"

Jenny itself needs no internet access to run — the gateway, the WebUI, and the agent loop are entirely local to the phone. But the *provider call* still has to reach wherever your model server lives, over the network, from the phone. If that server is your own laptop or a box on your LAN, the phone has to be able to open a connection to it — same as any other app making an HTTP request. There is no special local-inference mode that keeps traffic off the network; a self-hosted `openai_compat` endpoint is called the exact same way a hosted one would be.

Concretely, the endpoint must be **reachable from the phone itself** — not from a desktop browser, not from the machine running the model server. If your phone and your model server aren't on the same network (or connected through a VPN), the request will simply fail to connect, the same as pointing a browser at an address it can't route to.

## Cleartext HTTP is blocked except on loopback

Android's network security config on Jenny only allows plaintext (unencrypted) `http://` traffic to `127.0.0.1` and `localhost`. Every other destination — including a `192.168.x.x` LAN address, a Tailscale IP, or a plain hostname — is refused unless it's `https://`. This is enforced at the OS/network layer, before Jenny's own code ever sees the request; it isn't a Jenny setting you can flip.

Practically, this means:

| Endpoint location | Works with plain `http://`? | What you need |
|---|---|---|
| `http://127.0.0.1:PORT` or `http://localhost:PORT` | Yes | Only reachable if the model server runs *on the phone itself* — not a typical setup. |
| `http://192.168.x.x:PORT` (LAN) | **No** | Put a TLS-terminating reverse proxy in front of the model server (self-signed certs work, but the phone must trust the CA — a public/valid cert is the path of least friction), or otherwise serve it over `https://`. |
| `http://<tailscale-ip>:PORT` | **No** | Same requirement: HTTPS. Tailscale itself doesn't change the cleartext rule — it just changes the routing. |
| `https://anything` | Yes | No special handling needed beyond a valid TLS chain the phone trusts. |

If you're used to running Ollama or LM Studio with their default plain-HTTP listener and pointing a desktop app at it directly, that setup will not work unmodified from the phone — you'll need HTTPS in front of it.

## The Tailscale note: SSRF whitelist does not apply here

Jenny has an SSRF (server-side request forgery) protection layer that blocks its own tools (`web_fetch`, `download_file`, `python_exec`'s HTTP helpers) from reaching private/loopback/CGNAT address ranges, with `security.ssrfWhitelist` as the escape hatch (commonly used for a Tailscale range like `100.64.0.0/10`).

**That whitelist has nothing to do with provider calls.** LLM provider requests go through a completely separate code path from the agent's tools and are never checked against the SSRF filter at all — private-range and CGNAT addresses are not blocked for provider traffic in the first place. So if you're setting up a Tailscale-reachable Ollama box as your provider, you do not need to touch `ssrfWhitelist` for the provider connection itself; that setting only matters if you also want the agent's own tools (not the model call) to reach addresses in that range. The thing that *does* gate a Tailscale-hosted provider is the HTTPS requirement above — Tailscale gives you private, authenticated routing, but Android's network security config still refuses plaintext HTTP to it.

## `10.0.2.2` is emulator-only

If you've seen `10.0.2.2` in older examples pointing at a host machine's Ollama or vLLM instance, that address only means anything inside the Android emulator — it's the emulator's special alias for "the machine running the emulator." On a real phone, `10.0.2.2` is just an unreachable address like any other; it resolves to nothing on your actual network. On a real device you need the model server's actual LAN IP (or a Tailscale/VPN address, or a public hostname), reachable per the HTTPS rule above.

## Configuring it

Same provider entry shape as any other `openai_compat` provider — via Settings → Model → API keys, or directly in `config.json`:

```json
{
  "providers": {
    "providers": [
      {
        "name": "home-ollama",
        "format": "openai_compat",
        "apiKey": "EMPTY",
        "apiBase": "https://ollama.example-tailnet.ts.net:11434/v1"
      }
    ],
    "default": "home-ollama"
  }
}
```

A few notes specific to self-hosted servers:

- **`apiKey` is still required.** Most self-hosted servers don't check it, but Jenny's provider factory refuses to start a provider with no key at all — use a placeholder like `"EMPTY"`.
- **Model IDs must match exactly what the server serves.** Ollama and LM Studio use their own model-tag naming (e.g. `llama3.1:8b`); copy the exact tag the server reports, not a generic model name.
- **`apiType`** defaults to `"auto"`, which for a non-`api.openai.com` base always means Chat Completions — the Responses API auto-detection in "auto" mode only ever triggers for `api.openai.com` directly, so it's irrelevant here regardless of setting.
- Prompt caching's explicit `cache_control` markers only apply on OpenRouter with Claude-named models (see [Providers and models](./providers.md#prompt-caching-whats-actually-happening)) — a local server gets none of that; any caching your server does is its own business, not something Jenny requests.

## Honest end-to-end status

<!-- TODO: verify on-device (O-6): confirm actual reachability of a LAN/Tailscale self-hosted openai_compat endpoint from the phone, including the HTTPS requirement in practice, before promising this flow works out of the box. -->

The pieces above — network security config behavior, the SSRF/provider-path separation, and `10.0.2.2` being emulator-only — are all verified against the current code. What hasn't been verified end-to-end on a real device is the full loop of a phone reaching a self-hosted server over LAN or Tailscale with a real TLS certificate in place and getting a working chat response back. Treat this page as "how it's built to work," and expect to troubleshoot certificate trust on first setup.

## See also

- [Providers and models](./providers.md) — provider fields, formats, and the fields you'd set for any endpoint (including self-hosted ones).
- [Security model](../internals/security-model.md) — the SSRF filter and what it does and doesn't cover.
- [Troubleshooting](../using/troubleshooting.md) — "URL blocked" / SSRF errors and connection-refused symptoms.
