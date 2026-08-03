"""Composition root esplicito del gateway (estratto da ``gateway_runtime._run_gateway``).

`GatewayContainer` costruisce l'intero grafo di oggetti del gateway in un unico
posto auditabile e ne possiede lo stato di runtime. Sostituisce le closure e le
variabili ``nonlocal`` (``agent``/``message_tool``) della vecchia god-function con
attributi d'istanza e metodi; i getter late-binding usati da ``CronDispatcher``
diventano ``lambda: self._agent`` — stesso contratto onboarding→cron di prima.

`DeferredAgentActivator` (l'attesa dell'onboarding + creazione differita
dell'agent) vive qui come metodo `_wait_and_create_agent`, così il contratto
nonlocal è incapsulato invece che sparso in closure.

Comportamento invariato rispetto a ``_run_gateway``: stessi oggetti, stesso
ordine di costruzione, stesso drain ordinato allo shutdown.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from jenny import __logo__, __version__
from jenny.config.schema import Config


class GatewayContainer:
    """Costruisce e avvia il grafo del gateway; possiede lo stato di runtime."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.port = config.gateway.port

        # Stato di runtime (ex-nonlocal). `agent`/`message_tool` sono riassegnati
        # dall'onboarding tramite set_agent/set_message_tool.
        self._agent: Any = None
        self._message_tool: Any = None
        self.onboarding_event = asyncio.Event()

        # Collaboratori popolati da build().
        self.bus: Any = None
        self.runtime_events: Any = None
        self.provider: Any = None
        self.session_manager: Any = None
        self.cron: Any = None
        self.snapshot: Any = None
        self.channels: Any = None
        self._deliverer: Any = None
        self._deliver_to_channel: Any = None

    # -- accessor late-binding (usati dai getter di CronDispatcher) ----------

    def set_agent(self, new_agent: Any) -> None:
        self._agent = new_agent

    def set_message_tool(self, mt: Any) -> None:
        self._message_tool = mt

    def _webui_runtime_model_name(self) -> str | None:
        if not self._agent:
            return None
        model = getattr(self._agent, "model", None)
        if isinstance(model, str):
            stripped = model.strip()
            return stripped or None
        return None

    def _on_settings_changed(self) -> None:
        """Hot-reload model/provider when WebUI settings change."""
        if not self._agent:
            return
        try:
            from jenny.config.loader import load_config as _reload_config
            from jenny.providers.factory import make_provider as _make_provider

            new_config = _reload_config()
            new_provider = _make_provider(new_config)
            new_model = new_config.agents.defaults.model
            new_ctx = new_config.agents.defaults.context_window_tokens
            old_model = getattr(self._agent, "model", None)
            old_provider = getattr(self._agent, "provider", None)
            old_base = getattr(old_provider, "api_base", None)
            new_base = getattr(new_provider, "api_base", None)
            # GenerationSettings è un dataclass frozen, quindi il confronto è per
            # valore: serve perché un cambio di max_tokens / temperature /
            # reasoning_effort lascia model e api_base identici, e senza questo
            # la guardia scartava proprio l'aggiornamento richiesto.
            old_generation = getattr(old_provider, "generation", None)
            new_generation = getattr(new_provider, "generation", None)
            if (
                new_model == old_model
                and new_base == old_base
                and new_generation == old_generation
            ):
                return
            self._agent._apply_provider_switch(
                new_provider, new_model, new_ctx,
                # Un cambio dei soli parametri di generazione non è un cambio di
                # modello: pubblicarlo come tale farebbe annunciare alla UI uno
                # switch verso il modello che era già attivo.
                publish_update=new_model != old_model,
            )
            logger.info(
                "Hot-reloaded after settings change: model={!r} provider={!r}",
                new_model,
                type(new_provider).__name__,
            )
        except Exception:
            logger.exception("Failed to hot-reload after settings change")

    def _telegram_targets(self) -> list[tuple[str, str]]:
        """Target extra per il fan-out proattivo: la chat Telegram accoppiata.

        Legge lo stato vivo del canale (aggiornato al pairing a caldo), non la
        config in memoria che potrebbe essere stantia.
        """
        dispatcher = self.channels
        if dispatcher is None:
            return []
        channel = dispatcher.channels.get("telegram")
        chat_id = getattr(channel, "paired_chat_id", None)
        if isinstance(chat_id, str) and chat_id:
            return [("telegram", chat_id)]
        return []

    async def _snapshot_before_dream(self) -> None:
        """Checkpoint del workspace prima che Dream riscriva la memoria."""
        if self.snapshot:
            await self.snapshot.snapshot_now("pre_dream")

    # -- costruzione del grafo ----------------------------------------------

    def build(self) -> None:
        """Costruisce l'intero grafo di oggetti (composition point)."""
        from jenny.bus.queue import MessageBus
        from jenny.bus.runtime_events import RuntimeEventBus
        from jenny.channels.dispatcher import WebSocketDispatcher
        from jenny.channels.ui_query import UiQueryCoordinator
        from jenny.cron.service import CronService
        from jenny.cron.types import CronJob, CronPayload, CronSchedule
        from jenny.providers.factory import make_provider
        from jenny.runtime.cron_dispatch import CronDispatcher
        from jenny.runtime.delivery import ChannelDeliverer
        from jenny.session.manager import SessionManager
        from jenny.utils.helpers import sync_workspace_templates

        config = self.config
        logger.info(
            "{} Starting jenny gateway version {} on port {}...",
            __logo__, __version__, self.port,
        )
        sync_workspace_templates(config.workspace_path)

        # Backpressure su dispositivi memory-constrained (Android): code limitate.
        # I delta di streaming/progress usano try_publish_outbound (scartabili),
        # i messaggi finali usano publish_outbound (bloccante).
        self.bus = MessageBus(inbound_maxsize=256, outbound_maxsize=512)
        self.runtime_events = RuntimeEventBus()
        # RPC vista corrente (tool ui_view): condiviso tra agente e canale WS,
        # che girano nello stesso event loop.
        self.ui_query = UiQueryCoordinator()
        try:
            self.provider = make_provider(config)
        except (ValueError, RuntimeError) as exc:
            # Allow gateway to start without provider for onboarding.
            logger.warning("{}", exc)
            logger.info("Gateway starting without provider - complete onboarding to configure.")
            self.provider = None
        self.session_manager = SessionManager(config.workspace_path)

        cron_store_path = config.workspace_path / "cron" / "jobs.json"
        self.cron = CronService(cron_store_path)

        # Versioning locale del workspace: snapshot automatici di sistema.
        # Lo store vive FUORI dal workspace (sibling), così lo swap atomico
        # del ripristino non porta via la storia insieme al workspace.
        from jenny.config.paths import get_data_dir
        from jenny.snapshot.engine import SnapshotEngine
        from jenny.snapshot.locations import snapshots_dir_for
        from jenny.snapshot.service import SnapshotService

        self.snapshot = SnapshotService(
            SnapshotEngine(
                config.workspace_path,
                snapshots_dir_for(config.workspace_path),
                exclude_globs=config.snapshots.exclude_globs,
                # Esclusione per path reale: vale anche quando il runtime dir
                # ha ancora il nome legacy (.minijenny).
                exclude_dirs=(get_data_dir() / "logs",),
            ),
            config.snapshots,
        )

        # Il ChannelDeliverer va costruito prima dell'agente: fornisce la
        # callback ``_deliver_to_channel`` che ``_instantiate_agent`` collega al
        # tool ``message``. Non dipende dall'agente (``_telegram_targets`` è una
        # lambda valutata a posteriori), quindi l'ordine è sicuro.
        self._deliverer = ChannelDeliverer(
            bus=self.bus,
            session_manager=self.session_manager,
            extra_targets=self._telegram_targets,
        )
        self._deliver_to_channel = self._deliverer.deliver

        if self.provider:
            self._agent = self._instantiate_agent(config, self.provider)

        self.channels = WebSocketDispatcher(
            config,
            self.bus,
            session_manager=self.session_manager,
            snapshot_service=self.snapshot,
            ui_query=self.ui_query,
            webui_runtime_model_name=self._webui_runtime_model_name,
            onboarding_event=self.onboarding_event,
            on_settings_changed=self._on_settings_changed,
        )

        if self.channels.enabled:
            logger.info("WebSocket channel enabled")
        else:
            logger.warning("WebSocket channel not enabled")

        cron_status = self.cron.status()
        if cron_status["jobs"] > 0:
            logger.info("Cron: {} scheduled jobs", cron_status["jobs"])

        hb_cfg = config.gateway.heartbeat
        if hb_cfg.enabled:
            logger.info("Heartbeat: every {}s", hb_cfg.interval_s)
        else:
            logger.info("Heartbeat: disabled")

        # Cron dispatch: getter late-binding per agent/message_tool (riassegnati
        # dall'onboarding), stesso contratto del vecchio closure on_cron_job.
        self.cron.on_job = CronDispatcher(
            get_agent=lambda: self._agent,
            config=config,
            cron=self.cron,
            get_message_tool=lambda: self._message_tool,
            deliver_to_channel=self._deliver_to_channel,
            heartbeat_cfg=hb_cfg,
            snapshot_before_dream=self._snapshot_before_dream,
        ).dispatch

        # Register Dream system job (idempotent on restart).
        dream_cfg = config.agents.defaults.dream
        if dream_cfg.enabled:
            self.cron.register_system_job(CronJob(
                id="dream",
                name="dream",
                schedule=dream_cfg.build_schedule(),
                payload=CronPayload(kind="system_event"),
            ))
            logger.info("Dream: {}", dream_cfg.describe_schedule())
        else:
            logger.info("Dream: disabled")

        # Register Heartbeat system job (idempotent on restart).
        if hb_cfg.enabled:
            self.cron.register_system_job(CronJob(
                id="heartbeat",
                name="heartbeat",
                schedule=CronSchedule(
                    kind="every",
                    every_ms=hb_cfg.interval_s * 1000,
                    tz=config.agents.defaults.timezone,
                ),
                payload=CronPayload(kind="system_event"),
            ))


    def _instantiate_agent(self, config: Config, provider: Any) -> Any:
        """Costruisce e cabla un ``AgentLoop`` (wiring condiviso build/onboarding).

        Crea l'agente via ``AgentLoop.from_config``, iscrive il
        ``WebuiTurnCoordinator`` e collega la callback di consegna al tool
        ``message``. Non registra l'agente: i chiamanti restano responsabili di
        pubblicarlo (assegnazione diretta in ``build`` vs ``set_agent`` nel ramo
        onboarding) e di avviarne il run loop. Richiede che
        ``self._deliver_to_channel`` sia già impostato.
        """
        from jenny.agent.loop import AgentLoop
        from jenny.agent.token_usage import TokenUsageHook
        from jenny.agent.tools.message import MessageTool
        from jenny.session.webui_turns import WebuiTurnCoordinator

        agent = AgentLoop.from_config(
            config, self.bus,
            provider=provider,
            cron_service=self.cron,
            session_manager=self.session_manager,
            runtime_events=self.runtime_events,
            ui_query=self.ui_query,
            hooks=[TokenUsageHook(timezone_name=config.agents.defaults.timezone)],
        )
        WebuiTurnCoordinator(
            bus=self.bus,
            sessions=self.session_manager,
            schedule_background=lambda coro: agent._schedule_background(coro),
        ).subscribe(self.runtime_events)
        message_tool = agent.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_send_callback(self._deliver_to_channel)
            self.set_message_tool(message_tool)
        return agent

    # -- onboarding: creazione differita dell'agent (DeferredAgentActivator) --

    async def _wait_and_create_agent(self) -> None:
        """Wait for onboarding to complete, then create and start the agent."""
        logger.info("Waiting for onboarding to complete...")
        await self.onboarding_event.wait()
        logger.info("Onboarding signal received, creating agent...")

        try:
            from jenny.config.loader import load_config as _reload_config
            from jenny.providers.factory import make_provider as _make_provider

            new_config = _reload_config()
            provider = _make_provider(new_config)
        except Exception:
            logger.exception("Failed to create provider after onboarding")
            return

        new_agent = self._instantiate_agent(new_config, provider)
        self.set_agent(new_agent)

        logger.info("Agent created, starting run loop...")
        await new_agent.run()

    # -- orchestrazione dei task + shutdown ordinato -------------------------

    async def run(self) -> None:
        try:
            await self.cron.start()
            await self.snapshot.start()
            tasks = [self.channels.start()]
            if self._agent:
                tasks.append(self._agent.run())
            else:
                tasks.append(self._wait_and_create_agent())
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        except Exception:
            logger.opt(exception=True).error("Gateway crashed unexpectedly")
            raise
        finally:
            self.cron.stop()
            self.snapshot.stop()
            if self._agent:
                # Drain ordinato: attende turni/subagent/consolidation in volo
                # PRIMA del flush_all(), così nessun writer di session.messages
                # è attivo durante il flush.
                await self._agent.shutdown()
            await self.channels.stop()
            from jenny.agent.tools.exec_session import DEFAULT_EXEC_SESSION_MANAGER

            DEFAULT_EXEC_SESSION_MANAGER.shutdown()
            if self._agent:
                flushed = self._agent.sessions.flush_all()
                if flushed:
                    logger.info("Shutdown: flushed {} session(s) to disk", flushed)
            # Snapshot finale DOPO il flush: cattura le sessioni già scritte.
            try:
                await self.snapshot.snapshot_now("shutdown")
            except Exception:
                logger.exception("Shutdown snapshot failed")
