"""Invio outbound del canale WebSocket (estratto da websocket.py).

`OutboundSenderMixin` raccoglie la consegna verso i client: fan-out sicuro
(`_fanout`), buffer di streaming, e tutti i metodi ``send_*``
(delta, reasoning, file-edit, turn-end, goal, session/app/model updates). Mixato
in ``WebSocketChannel``: ``self`` risolve via MRO a stato/collaboratori del canale.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from contextlib import suppress
from pathlib import Path
from typing import Any

from websockets.exceptions import ConnectionClosed

from jenny.bus.events import OUTBOUND_META_AGENT_UI, OutboundMessage
from jenny.config.runtime_env import ws_send_timeout_s
from jenny.runtime.notifier import notify_delivery
from jenny.webui.media_api import media_attachment_kind

# Timeout wall-clock per un singolo `connection.send()`. Modulo-level (non
# per-call) così i test possono monkeypatchare `ws_sender._SEND_TIMEOUT_S` con
# un valore basso senza toccare l'ambiente. Vedi `runtime_env.ws_send_timeout_s`.
_SEND_TIMEOUT_S: float = ws_send_timeout_s()


class OutboundSenderMixin:
    """Metodi di consegna outbound (mixin di WebSocketChannel)."""

    def _drop_stalled_connection(self, connection: Any, *, label: str = "") -> None:
        """Close and discard a connection whose ``send`` timed out (backpressure).

        ``websockets`` applies TCP backpressure: a client with a full send
        buffer blocks ``send`` indefinitely, and the serial outbound dispatcher
        would stall delivery to *every* client behind it. When ``wait_for``
        cancels a blocked ``send``, the frame is left half-written on the wire,
        so the connection is now in an inconsistent state and MUST NOT be
        reused. We therefore schedule a fire-and-forget ``close()`` and remove
        the connection from every subscription set, treating it exactly like a
        ``ConnectionClosed`` (a dead conn — never retried). A held reference
        keeps the close task from being garbage-collected before it runs.
        """
        # Lazy-init the reference set here (the mixin has no __init__) so a held
        # reference keeps each close task alive until it finishes.
        tasks: set[Any] = self.__dict__.setdefault("_stalled_close_tasks", set())
        with suppress(RuntimeError):
            task = asyncio.ensure_future(connection.close())
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        self._cleanup_connection(connection)
        self.logger.warning("connection stalled (send timeout), dropping{}", label)

    async def _fanout(self, conns: list[Any], raw: str, *, label: str = "") -> list[Any]:
        """Deliver *raw* to every connection in *conns*.

        A failure on one connection does not abort
        delivery to the rest: every connection in *conns* is attempted exactly
        once. Connections that raise a non-``ConnectionClosed`` error are
        returned so the caller (``_send_with_retry`` in the dispatcher) can
        retry delivery to just those connections instead of resending to peers
        that already received it — resending to everyone on retry is what
        caused duplicate/garbled messages for multi-connection fan-out.

        Each ``send`` is bounded by ``_SEND_TIMEOUT_S``: a client whose TCP
        buffer is full blocks ``send`` forever, and since the outbound
        dispatcher is serial that would stall delivery to *all* peers. A
        timed-out connection is closed and dropped (like ``ConnectionClosed``),
        NOT added to ``pending`` — it is treated as dead, never retried.
        """
        pending: list[Any] = []
        for connection in conns:
            try:
                await asyncio.wait_for(connection.send(raw), timeout=_SEND_TIMEOUT_S)
            except ConnectionClosed:
                self._cleanup_connection(connection)
                self.logger.warning("connection gone{}", label)
            except TimeoutError:
                # Stalled client: drop it (dead), do NOT append to pending so
                # the dispatcher never retries a connection we just closed.
                self._drop_stalled_connection(connection, label=label)
            except asyncio.CancelledError:
                self._drop_stalled_connection(connection, label=label)
                raise
            except Exception as e:
                self.logger.warning("send failed{} (will retry): {}", label, e)
                pending.append(connection)
        return pending

    async def send_ui_query(self, conn_id: str, correlation_id: str) -> bool:
        """Chiedi la vista corrente alla singola connessione ``conn_id``.

        Mirato a una connessione (non fan-out per chat_id): l'RPC del tool
        ``ui_view`` attende la Future risolta dal frame ``ui_result``. Ritorna
        ``False`` se quella connessione non è (più) presente.
        """
        connection = self._conns_by_id.get(conn_id)
        if connection is None:
            return False
        await self._send_event(connection, "ui_query", correlation_id=correlation_id)
        return True

    def discard_stream_buffer(self, chat_id: str, stream_id: Any) -> None:
        """Drop a stream's buffered text once delivery is permanently abandoned.

        Called by the dispatcher after retries for a ``stream_end`` frame are
        exhausted, so an undeliverable stream doesn't leak its buffer forever.
        """
        self._stream_text_buffers.pop((chat_id, str(stream_id or "")), None)

    async def send(
        self,
        msg: OutboundMessage,
        *,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        if msg.metadata.get("_runtime_model_updated"):
            await self.send_runtime_model_updated(
                model_name=msg.metadata.get("model"),
                model_preset=msg.metadata.get("model_preset"),
                provider=msg.metadata.get("provider"),
            )
            return []
        if msg.metadata.get("_app_data_changed"):
            slug = msg.metadata.get("app_slug")
            if isinstance(slug, str) and slug:
                await self.send_app_data_changed(slug)
            return []
        if msg.metadata.get("_apps_list_changed"):
            await self.send_apps_list_changed()
            return []
        if msg.metadata.get("_user_echo"):
            return await self._send_user_echo(
                msg, only_conns=only_conns, skip_persist=skip_persist
            )

        # Snapshot the subscriber set so ConnectionClosed cleanups mid-iteration are safe.
        conns = list(self._subs.get(msg.chat_id, ()))
        if not conns:
            if (
                msg.metadata.get("_progress")
                or msg.metadata.get("_file_edit_events")
                or msg.metadata.get("_turn_end")
                or msg.metadata.get("_session_updated")
                or msg.metadata.get("_goal_status")
            ):
                self.logger.debug("no active subscribers for chat_id={}", msg.chat_id)
            else:
                self.logger.warning("no active subscribers for chat_id={}", msg.chat_id)
        if msg.metadata.get("_goal_status"):
            if conns:
                status = msg.metadata.get("goal_status")
                if status in ("running", "idle"):
                    started_raw = msg.metadata.get("started_at", msg.metadata.get("goal_started_at"))
                    await self.send_goal_status(
                        msg.chat_id,
                        status,
                        started_at=float(started_raw) if isinstance(started_raw, int | float) else None,
                    )
            return []
        # Signal that the agent has fully finished processing the current turn.
        if msg.metadata.get("_turn_end"):
            lat = msg.metadata.get("latency_ms")
            lat_i = int(lat) if isinstance(lat, (int, float)) else None
            pending = await self.send_turn_end(
                msg.chat_id,
                latency_ms=lat_i,
                metadata=msg.metadata,
                only_conns=only_conns,
                skip_persist=skip_persist,
            )
            if not pending:
                # Only announce the session refresh once turn_end has fully
                # landed — otherwise a retry would re-broadcast it too.
                await self.send_session_updated(msg.chat_id, scope="thread")
            return pending
        if msg.metadata.get("_session_updated"):
            if conns:
                scope = msg.metadata.get("_session_update_scope")
                await self.send_session_updated(
                    msg.chat_id,
                    scope=scope if isinstance(scope, str) else None,
                )
            return []
        if msg.metadata.get("_file_edit_events"):
            edits = msg.metadata.get("_file_edit_events")
            return await self.send_file_edit_events(
                msg.chat_id,
                edits if isinstance(edits, list) else [],
                msg.metadata,
                only_conns=only_conns,
                skip_persist=skip_persist,
            )
        text = msg.content
        # Scarica in locale le immagini remote referenziate (markdown inline o
        # entry `media` con URL) e sostituiscile con path locali, così il resto
        # della pipeline le firma/rifirma come qualsiasi media locale. Idempotente
        # sui retry: il dedup nell'ingest salta il fetch se già presente.
        if text or msg.media:
            text, new_media = await self._media.localize_remote_media(text, msg.media)
            if new_media != msg.media:
                msg = dataclasses.replace(msg, media=new_media)
        wire_text = self._media.rewrite_local_markdown_images(text)
        payload: dict[str, Any] = {
            "event": "message",
            "chat_id": msg.chat_id,
            "text": wire_text,
        }
        if msg.media:
            payload["media"] = msg.media
            urls: list[dict[str, str]] = []
            for entry in msg.media:
                signed = self._media.sign_or_stage_media_path(Path(entry))
                if signed is not None:
                    # kind (image/video/file) guida il rendering nel client;
                    # path locale serve al chip file per l'apertura nativa.
                    name = signed.get("name") or Path(entry).name
                    signed.setdefault("kind", media_attachment_kind(name))
                    signed.setdefault("path", str(entry))
                    urls.append(signed)
            if urls:
                payload["media_urls"] = urls
        origin = msg.metadata.get("origin_channel")
        if isinstance(origin, str) and origin:
            # Provenienza del turno (es. "telegram"): persiste nel transcript e
            # arriva ai client per il badge di origine.
            payload["origin"] = origin
        lat = msg.metadata.get("latency_ms")
        if isinstance(lat, (int, float)):
            payload["latency_ms"] = int(lat)
        if msg.metadata.get("_tool_events"):
            payload["tool_events"] = msg.metadata["_tool_events"]
        agent_ui = msg.metadata.get(OUTBOUND_META_AGENT_UI)
        if agent_ui is not None:
            payload["agent_ui"] = agent_ui
        if msg.metadata.get("_session_boundary"):
            # /new: il client rende un separatore al posto della bolla. Finisce
            # anche nel transcript persistito (payload è ciò che viene salvato),
            # così il confine resta visibile dopo un reload.
            payload["session_boundary"] = True
        # Mark intermediate agent breadcrumbs (tool-call hints, generic
        # progress strings) so WS clients can render them as subordinate
        # trace rows rather than conversational replies.
        if msg.metadata.get("_tool_hint"):
            payload["kind"] = "tool_hint"
        elif msg.metadata.get("_progress"):
            payload["kind"] = "progress"
        phase = "activity" if payload.get("kind") in ("tool_hint", "progress") else "answer"
        if not skip_persist:
            # Persist exactly once per logical send: a retry (skip_persist=True)
            # must never re-append this message to the transcript, or a
            # partial multi-connection failure would duplicate the row.
            self._transcripts.prepare_and_append(
                msg.chat_id,
                payload,
                metadata=msg.metadata,
                phase=phase,
                transcript_overrides={"text": text},
            )
            if phase == "answer" and not payload.get("origin"):
                # Alert di sistema Android (fire-and-forget, no-op fuori da
                # Android): deve partire anche a zero subscriber — è proprio il
                # caso "WebView chiusa" in cui la notifica serve di più. Dentro
                # `not skip_persist` così un retry non ri-squilla; il gate
                # foreground sta nel bridge Kotlin. I messaggi con origin sono
                # proiezioni di turni già consegnati sul canale d'origine
                # (l'utente sta chattando lì): nessuno squillo sul server.
                notify_delivery(text, msg.metadata)
        raw = json.dumps(payload, ensure_ascii=False)
        target_conns = only_conns if only_conns is not None else conns
        if not target_conns:
            return []
        return await self._fanout(target_conns, raw, label=" ")

    async def _send_user_echo(
        self,
        msg: OutboundMessage,
        *,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        """Eco di un messaggio utente entrato da un altro canale (es. Telegram).

        Persiste la riga ``user`` nel transcript (fonte di verità della storia
        WebUI) e la trasmette live ai client connessi con ``origin`` per il
        badge di provenienza. Stessa disciplina di ``send``: persistenza una
        sola volta per invio logico, i retry (skip_persist) rifanno solo la
        consegna alle connessioni mancate.
        """
        text = msg.content or ""
        if not text.strip() and not msg.media:
            return []
        payload: dict[str, Any] = {
            "event": "user",
            "chat_id": msg.chat_id,
            "text": text,
        }
        origin = msg.metadata.get("origin_channel")
        if isinstance(origin, str) and origin:
            payload["origin"] = origin
        if msg.media:
            payload["media_paths"] = [str(p) for p in msg.media if p]
        if not skip_persist:
            self._transcripts.prepare_and_append(
                msg.chat_id,
                payload,
                metadata=msg.metadata,
                phase="user",
            )
        conns = only_conns if only_conns is not None else list(self._subs.get(msg.chat_id, ()))
        if not conns:
            return []
        raw = json.dumps(payload, ensure_ascii=False)
        return await self._fanout(conns, raw, label=" user_echo ")

    async def send_reasoning_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
        *,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        """Push one chunk of model reasoning. Mirrors ``send_delta`` shape so
        clients receive a stream that opens, updates in place, and closes —
        rendered above the active assistant bubble with a shimmer header
        until the matching ``reasoning_end`` arrives.
        """
        if not delta:
            return []
        meta = metadata or {}
        body: dict[str, Any] = {
            "event": "reasoning_delta",
            "chat_id": chat_id,
            "text": delta,
        }
        stream_id = meta.get("_stream_id")
        if stream_id is not None:
            body["stream_id"] = stream_id
        if not skip_persist:
            self._transcripts.prepare_and_append(
                chat_id,
                body,
                metadata=meta,
                phase="reasoning",
            )
        conns = only_conns if only_conns is not None else list(self._subs.get(chat_id, ()))
        if not conns:
            return []
        raw = json.dumps(body, ensure_ascii=False)
        return await self._fanout(conns, raw, label=" reasoning ")

    async def send_reasoning_end(
        self,
        chat_id: str,
        metadata: dict[str, Any] | None = None,
        *,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        """Close the current reasoning stream segment for in-place renderers."""
        meta = metadata or {}
        body: dict[str, Any] = {
            "event": "reasoning_end",
            "chat_id": chat_id,
        }
        stream_id = meta.get("_stream_id")
        if stream_id is not None:
            body["stream_id"] = stream_id
        if not skip_persist:
            self._transcripts.prepare_and_append(
                chat_id,
                body,
                metadata=meta,
                phase="reasoning",
            )
        conns = only_conns if only_conns is not None else list(self._subs.get(chat_id, ()))
        if not conns:
            return []
        raw = json.dumps(body, ensure_ascii=False)
        return await self._fanout(conns, raw, label=" reasoning_end ")

    async def send_file_edit_events(
        self,
        chat_id: str,
        edits: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
        *,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        payload: dict[str, Any] = {
            "event": "file_edit",
            "chat_id": chat_id,
            "edits": edits,
        }
        if not skip_persist:
            self._transcripts.prepare_and_append(
                chat_id,
                payload,
                metadata=metadata,
                phase="activity",
            )
        conns = only_conns if only_conns is not None else list(self._subs.get(chat_id, ()))
        if not conns:
            return []
        raw = json.dumps(payload, ensure_ascii=False)
        return await self._fanout(conns, raw, label=" file_edit ")

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
        *,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        meta = metadata or {}
        stream_key = (chat_id, str(meta.get("_stream_id") or ""))
        is_stream_end = bool(meta.get("_stream_end"))
        if is_stream_end:
            body: dict[str, Any] = {"event": "stream_end", "chat_id": chat_id}
            # Peek, don't pop: a retry after a partial multi-connection
            # failure must still see the full buffered text. The entry is
            # only dropped once delivery has actually fully succeeded (or is
            # permanently abandoned via discard_stream_buffer), so a retry
            # never finalizes with truncated/lost text.
            buffered = list(self._stream_text_buffers.get(stream_key, []))
            if delta:
                buffered.append(delta)
            full_text = "".join(buffered)
            rewritten = self._media.rewrite_local_markdown_images(full_text)
            if delta or rewritten != full_text:
                body["text"] = rewritten
        else:
            body = {
                "event": "delta",
                "chat_id": chat_id,
                "text": delta,
            }
            if not skip_persist:
                self._stream_text_buffers.setdefault(stream_key, []).append(delta)
        if meta.get("_stream_id") is not None:
            body["stream_id"] = meta["_stream_id"]
        if not skip_persist:
            self._transcripts.prepare_and_append(
                chat_id,
                body,
                metadata=meta,
                phase="answer",
            )
        conns = only_conns if only_conns is not None else list(self._subs.get(chat_id, ()))
        if not conns:
            if is_stream_end:
                self._stream_text_buffers.pop(stream_key, None)
            return []
        raw = json.dumps(body, ensure_ascii=False)
        pending = await self._fanout(conns, raw, label=" stream ")
        if is_stream_end and not pending:
            # Fully delivered — safe to release the buffered text now.
            self._stream_text_buffers.pop(stream_key, None)
        return pending

    async def send_turn_end(
        self,
        chat_id: str,
        latency_ms: int | None = None,
        *,
        metadata: dict[str, Any] | None = None,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        """Signal that the agent has fully finished processing the current turn."""
        body: dict[str, Any] = {"event": "turn_end", "chat_id": chat_id}
        if latency_ms is not None:
            body["latency_ms"] = int(latency_ms)
        if not skip_persist:
            self._transcripts.prepare_and_append(
                chat_id,
                body,
                metadata=metadata,
                phase="complete",
            )
        conns = only_conns if only_conns is not None else list(self._subs.get(chat_id, ()))
        if not conns:
            return []
        raw = json.dumps(body, ensure_ascii=False)
        return await self._fanout(conns, raw, label=" turn_end ")

    async def send_goal_status(
        self,
        chat_id: str,
        status: str,
        *,
        started_at: float | None = None,
    ) -> None:
        """Notify subscribed clients that a turn started or finished (wall-clock hint)."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {
            "event": "goal_status",
            "chat_id": chat_id,
            "status": status,
        }
        if status == "running" and started_at is not None:
            body["started_at"] = started_at
        raw = json.dumps(body, ensure_ascii=False)
        # Idempotent refresh-hint: discard pending, no retry (next status replaces it).
        await self._fanout(conns, raw, label=" goal_status ")

    async def send_session_updated(self, chat_id: str, *, scope: str | None = None) -> None:
        """Notify WebUI clients that a session row should refresh."""
        conns = list(self._conn_chats)
        if not conns:
            return
        body: dict[str, Any] = {"event": "session_updated", "chat_id": chat_id}
        if scope:
            body["scope"] = scope
        raw = json.dumps(body, ensure_ascii=False)
        # Idempotent refresh-hint: discard pending, no retry (next update replaces it).
        await self._fanout(conns, raw, label=" session_updated ")

    async def send_app_data_changed(self, slug: str) -> None:
        """Broadcast that a Jenny App's data changed (open app iframes refresh)."""
        conns = list(self._conn_chats)
        if not conns:
            return
        raw = json.dumps({"event": "app_data_changed", "slug": slug}, ensure_ascii=False)
        # Idempotent refresh-hint: discard pending, no retry (next change replaces it).
        await self._fanout(conns, raw, label=" app_data_changed ")

    async def send_apps_list_changed(self) -> None:
        """Broadcast that the Jenny Apps list changed (WebUI grid refreshes)."""
        conns = list(self._conn_chats)
        if not conns:
            return
        raw = json.dumps({"event": "apps_list_changed"}, ensure_ascii=False)
        # Idempotent refresh-hint: discard pending, no retry (next change replaces it).
        await self._fanout(conns, raw, label=" apps_list_changed ")

    async def send_runtime_model_updated(
        self,
        *,
        model_name: Any,
        model_preset: Any = None,
        provider: Any = None,
    ) -> None:
        """Broadcast runtime model changes to every open websocket connection."""
        conns = list(self._conn_chats)
        if not conns or not isinstance(model_name, str) or not model_name.strip():
            return
        body: dict[str, Any] = {
            "event": "runtime_model_updated",
            "model_name": model_name.strip(),
        }
        if isinstance(model_preset, str) and model_preset.strip():
            body["model_preset"] = model_preset.strip()
        if isinstance(provider, str) and provider.strip():
            body["provider"] = provider.strip()
        raw = json.dumps(body, ensure_ascii=False)
        # Idempotent refresh-hint: discard pending, no retry (next update replaces it).
        await self._fanout(conns, raw, label=" runtime_model_updated ")
