# WebSocket Protocol

Jenny exposes a WebSocket server channel used by the Android WebView UI and any compatible client — this page documents the wire protocol for integrators writing their own client.

## On the Android device, this is not optional

Everything below describes the general-purpose channel as configured through `config.json`'s `websocket` object. On the shipped Android app, the runtime overrides several of these fields at startup regardless of what `config.json` says:

- The gateway always binds host `127.0.0.1` and a single port, **18790**, shared by both the WebSocket upgrade and the HTTP `/api/` and `/webui/` routes — one origin for the WebView to talk to. This overrides `gateway.port` / `websocket.port` from config every time the app starts.
- `websocket.enabled` ends up `true` in practice: the auto-generated `config.json` created on first run writes `"websocket": {"enabled": true, ...}` explicitly, and the startup path additionally fills in `enabled: true` if the key is ever missing. The schema-level default of `enabled: false` (documented below) only applies when the gateway is run detached from the Android runtime — e.g. invoking `run_gateway(...)` yourself for local testing, where you own `config.json` and nothing forces it on.

So: the `enabled: false` default, the `8765` default port, and a custom `host`/`path` are all real and correct for **off-device** use of this channel (running the gateway standalone on a workstation), but not for the Android APK, which always ends up on `ws://127.0.0.1:18790/`.

## Features

- Bidirectional real-time communication over WebSocket
- Streaming support — receive agent responses token by token
- Secret-based authentication (single shared secret for WebSocket and HTTP APIs)
- Single shared chat — every connection joins the unified conversation (`chat_id` always `"default"`)
- TLS/SSL support (WSS) with enforced TLSv1.2 minimum
- Client allow-list via `allowFrom`
- Auto-cleanup of dead connections

## Quick Start (off-device / standalone gateway)

### 1. Configure

Add to `config.json` under the top-level `websocket` object:

```json
{
  "websocket": {
  "enabled": true,
  "host": "127.0.0.1",
  "port": 8765,
  "path": "/",
  "websocketRequiresToken": false,
  "allowFrom": ["*"],
  "streaming": true
  }
}
```

The default `host: 127.0.0.1` is intended for loopback use. External connections can be allowed by setting `host` to `"0.0.0.0"` and configuring `allowFrom` carefully.

### 2. Start the gateway

Use the same entry point the Android runtime uses:

```python
from jenny.android_entry import run_gateway
run_gateway("/path/to/data_dir")
```

Note the argument is a *data directory*, not the workspace itself — the gateway creates and uses `<data_dir>/workspace`. Passing a path that already ends in `workspace` produces a nested `workspace/workspace`.

You should see:

```text
WebSocket server listening on ws://127.0.0.1:8765/
```

(On the Android device this line always reads `ws://127.0.0.1:18790/` instead, per the note above.)

### 3. Connect a client

Connect to `ws://{host}:{port}{path}?client_id={id}&token={secret}` from the Android WebView or another WebSocket client. The wire protocol is described below.

## Connection URL

```text
ws://{host}:{port}{path}?client_id={id}&token={secret}
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `client_id` | No | Identifier for `allowFrom` authorization. Auto-generated as `anon-xxxxxxxxxxxx` if omitted. Truncated to 128 chars. |
| `token` | Conditional | The `token_issue_secret` value. Required when `websocketRequiresToken` is `true` (the default). |

## Wire Protocol

All frames are JSON text. Each message has an `event` field.

### Server → Client

**`ready`** — sent immediately after connection is established:

```json
{
  "event": "ready",
  "chat_id": "default",
  "client_id": "alice"
}
```

**`message`** — full agent response:

```json
{
  "event": "message",
  "chat_id": "default",
  "text": "Hello! How can I help?",
  "media": ["/tmp/image.png"],
  "reply_to": "msg-id"
}
```

`media` and `reply_to` are only present when applicable.

**`delta`** — streaming text chunk (only when `streaming: true`):

```json
{
  "event": "delta",
  "chat_id": "default",
  "text": "Hello",
  "stream_id": "s1"
}
```

**`stream_end`** — signals the end of a streaming segment:

```json
{
  "event": "stream_end",
  "chat_id": "default",
  "stream_id": "s1"
}
```

**`reasoning_delta`** — incremental model reasoning / thinking chunk for the active assistant turn. Mirrors `delta` but targets the reasoning bubble above the answer rather than the answer body:

```json
{
  "event": "reasoning_delta",
  "chat_id": "default",
  "text": "Let me decompose ",
  "stream_id": "r1"
}
```

**`reasoning_end`** — close marker for the active reasoning stream. WebUI uses this to lock the in-place bubble and switch from the shimmer header to a static collapsed state:

```json
{
  "event": "reasoning_end",
  "chat_id": "default",
  "stream_id": "r1"
}
```

Reasoning frames only flow when the channel's `showReasoning` is `true` (default) and the model returns reasoning content (DeepSeek-R1 / Kimi / MiMo / OpenAI reasoning models, Anthropic extended thinking, or inline `<think>` / `<thought>` tags). Models without reasoning produce zero `reasoning_delta` frames.

**`runtime_model_updated`** — broadcast when the gateway runtime model changes, for example after `/model <preset>`:

```json
{
  "event": "runtime_model_updated",
  "model_name": "openai/gpt-4.1-mini",
  "model_preset": "fast"
}
```

`model_preset` is omitted when no named preset is active. WebUI clients use this event to keep the displayed model badge in sync across slash commands, config reloads, and settings changes.

**`attached`** — confirmation for `attach` inbound envelopes (see [The shared chat](#the-shared-chat)):

```json
{"event": "attached", "chat_id": "default"}
```

**`error`** — soft error for malformed inbound envelopes. The connection stays open:

```json
{"event": "error", "detail": "invalid chat_id"}
```

### Client → Server

**Legacy (default chat):** send a plain string, or a JSON object with a recognized text field:

```json
"Hello Jenny!"
```

```json
{"content": "Hello Jenny!"}
```

Recognized fields: `content`, `text`, `message` (checked in that order). Invalid JSON is treated as plain text. These frames route to the shared chat (`default`, announced in `ready`).

**Typed envelopes:** any JSON object with a string `type` field is a typed envelope:

| `type` | Fields | Effect |
|--------|--------|--------|
| `attach` | — | (Re)subscribe to the shared chat, e.g. after a reconnect. Replies with `attached`. Any `chat_id` field is ignored. |
| `message` | `content` | Send `content` on the shared chat. Any `chat_id` field is ignored. |

See [The shared chat](#the-shared-chat) for the full flow.

## Configuration Reference

All fields go under the top-level `websocket` object in `config.json`. These are the schema-level defaults — remember that on the Android device `enabled`, `host`, and `port` are overridden at startup as described above, regardless of what is written here.

### Connection

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable the WebSocket server. Effectively always `true` on the Android device (see above). |
| `host` | string | `"127.0.0.1"` | Bind address. Use `"0.0.0.0"` to accept external connections. Forced to `127.0.0.1` on Android. |
| `port` | int | `8765` | Listen port. Forced to `18790` (shared with HTTP) on Android. |
| `path` | string | `"/"` | WebSocket upgrade path. Trailing slashes are normalized (root `/` is preserved). |
| `maxMessageBytes` | int | `37748736` | Maximum inbound message size in bytes (1 KB – 40 MB). Default (36 MB) is sized to accept up to 4 base64-encoded image attachments at 8 MB each; lower it if the channel only carries text. |

### Authentication

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `websocketRequiresToken` | bool | `true` | When `true`, clients must present `token_issue_secret` as `?token=...` during the WebSocket handshake. Set to `false` to allow unauthenticated connections (only safe for local/trusted networks). |
| `tokenIssueSecret` | string | `""` | Shared secret for WebSocket and HTTP API authentication. If empty, the bootstrap endpoint falls back to localhost-only on `127.0.0.1`/`::1`, and HTTP API routes reject all requests. Must be set for non-loopback deployments. On Android this is auto-generated per install (see [Security Notes](#security-notes)) and should never be edited out. |

### Access Control

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `allowFrom` | list of string | `["*"]` | Allowed `client_id` values. `"*"` allows all; `[]` denies all. |

### Streaming

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `streaming` | bool | `true` | Enable streaming mode. The agent sends `delta` + `stream_end` frames instead of a single `message`. |

### Keep-alive

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `pingIntervalS` | float | `20.0` | WebSocket ping interval in seconds (5 – 300). |
| `pingTimeoutS` | float | `20.0` | Time to wait for a pong before closing the connection (5 – 300). |

### TLS/SSL

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `sslCertfile` | string | `""` | Path to the TLS certificate file (PEM). Both `sslCertfile` and `sslKeyfile` must be set to enable WSS. |
| `sslKeyfile` | string | `""` | Path to the TLS private key file (PEM). Minimum TLS version is enforced as TLSv1.2. |

## Authentication Flow

For production deployments where `websocketRequiresToken: true` (the default, and the effective state on Android), clients authenticate with the shared `tokenIssueSecret` directly.

### How it works

1. The legitimate client (e.g. the Android WebView) reads `token_issue_secret` from the private workspace `config.json`.
2. The client calls `GET /webui/bootstrap` with `Authorization: Bearer <secret>` or `X-Jenny-Auth: <secret>` to receive connection metadata (WebSocket URL, model name, etc.).
3. The client opens the WebSocket with `?token=<secret>&client_id=...`.
4. The same secret is used for all subsequent HTTP API requests via `Authorization: Bearer <secret>`.

On the Android app specifically, Kotlin reads the secret from `config.json` and hands it to the WebView as a URL **fragment** (`#bs=...`), never as a query parameter, so it never ends up in logs or server-side request records; the WebView's JavaScript exchanges it at `/webui/bootstrap` for the actual WebSocket URL.

### Example setup

```json
{
  "websocket": {
  "enabled": true,
  "port": 8765,
  "path": "/ws",
  "tokenIssueSecret": "your-secret-here",
  "websocketRequiresToken": true,
  "allowFrom": ["*"],
  "streaming": true
  }
}
```

Client flow:

1. Read `websocket.token_issue_secret` from the app's private workspace.
2. Call `GET /webui/bootstrap` with `X-Jenny-Auth: your-secret-here`.
3. Connect to the WebSocket with `?client_id=alice&token=your-secret-here`.
4. Call HTTP APIs with `Authorization: Bearer your-secret-here`.

## The shared chat

Every connection participates in the single unified conversation: the server forces `chat_id` to `"default"` on all envelopes and fans outbound events out to every connected client (e.g. multiple Android activities or desktop browser tabs during local testing). There is no per-connection or per-user chat — any client presenting a valid credential sees and shares the same conversation.

### Typical flow

```text
client                                server
  | --- connect -------------------->  |
  | <-- {"event":"ready",              |
  |      "chat_id":"default"}          |
  |                                     |
  | --- {"type":"message",              |
  |      "content":"hi"} ------------>  |
  | <-- {"event":"delta", ...}          |
  | <-- {"event":"stream_end", ...}     |
  |                                     |
  | --- {"type":"attach"} ----------->  |  # after page reload
  | <-- {"event":"attached",            |
  |      "chat_id":"default"}           |
```

### Rules

- Every outbound event carries `chat_id`, always `"default"`.
- Client-supplied `chat_id` fields are ignored: everything routes to the shared chat.
- Errors (invalid envelope, unknown `type`, missing `content`) are soft: the server replies with `{"event":"error","detail":"..."}` and keeps the connection open.

### Backward compatibility

Legacy clients that only send plain text or `{"content": ...}` keep working unchanged: those frames route to the shared chat. No config flag is needed.

### Security boundary

Anyone holding a valid WebSocket auth credential joins the shared conversation and sees its output. This is safe for Jenny's local, single-user model; auth on the handshake is the single line of defense.

## Security Notes

- **Timing-safe comparison**: Secret validation uses `hmac.compare_digest` to prevent timing attacks.
- **Defense in depth**: `allowFrom` is checked at both the HTTP handshake level and the message level.
- **Shared chat**: see [The shared chat](#the-shared-chat). Auth on the WebSocket handshake is the single line of defense; callers who pass it join the unified conversation.
- **TLS enforcement**: When SSL is enabled, TLSv1.2 is the minimum allowed version.
- **Default-secure**: `websocketRequiresToken` defaults to `true`. Explicitly set it to `false` only on trusted networks.
- **Per-install secret on Android**: the app generates `token_issue_secret` once at first boot (`secrets.token_urlsafe(32)`) and writes it into `config.json` with permissions restricted to `0600`. If you strip it out by hand, it is silently regenerated on the next start (unless a `token` field is already set, which is left untouched). It is never transmitted anywhere except between the WebView and the local gateway.

## Media Files

Outbound `message` events may include a `media` field containing local filesystem paths. Remote clients cannot access these files directly — they need either:

- A shared filesystem mount, or
- An HTTP file server serving the Jenny media directory

## Common Patterns

These are off-device / standalone-gateway patterns — they do not apply to the Android app, which always forces `host: 127.0.0.1`, `port: 18790`, `enabled: true`.

### Trusted local network (no auth)

```json
{
  "websocket": {
  "enabled": true,
  "host": "0.0.0.0",
  "port": 8765,
  "websocketRequiresToken": false,
  "allowFrom": ["*"],
  "streaming": true
  }
}
```

### Shared secret (simple auth)

```json
{
  "websocket": {
  "enabled": true,
  "tokenIssueSecret": "my-shared-secret",
  "allowFrom": ["alice", "bob"]
  }
}
```

Clients connect with `?token=my-shared-secret&client_id=alice` and send HTTP APIs with `Authorization: Bearer my-shared-secret`.

### Public endpoint with authentication

```json
{
  "websocket": {
  "enabled": true,
  "host": "0.0.0.0",
  "port": 8765,
  "path": "/ws",
  "tokenIssueSecret": "production-secret",
  "websocketRequiresToken": true,
  "sslCertfile": "/etc/ssl/certs/server.pem",
  "sslKeyfile": "/etc/ssl/private/server-key.pem",
  "allowFrom": ["*"]
  }
}
```

### Custom path

```json
{
  "websocket": {
  "enabled": true,
  "path": "/chat/ws",
  "allowFrom": ["*"]
  }
}
```

Clients connect to `ws://127.0.0.1:8765/chat/ws?client_id=...`. Trailing slashes are normalized, so `/chat/ws/` works the same.

## See also

- [Configuration reference](configuration.md) — full `config.json` field reference, including `websocket.*`.
- [Settings](settings.md) — what is and is not exposed in the WebUI for this channel (none of `websocket.*` is UI-configurable).
- [Security model](../internals/security-model.md) — how the bootstrap secret fits into Jenny's overall security boundaries.
