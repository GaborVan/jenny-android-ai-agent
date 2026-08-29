"""Test della sessione di navigazione interattiva (tool ``browser_*``).

Cosa coprono e cosa no: il motore che decide ruoli, visibilita' e riduzione vive
nella pagina (``res/raw/browser_agent.js``) e non e' raggiungibile da qui. Questi
test coprono il contratto Python — decodifica, ciclo di vita, concorrenza,
validazione dell'URL — e le regole del motore si verificano sul telefono.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from jenny.agent.tools import browser
from jenny.agent.tools.browser import (
    BrowserCloseTool,
    BrowserDoTool,
    BrowserOpenTool,
    BrowserReadTool,
    BrowserSnapshotTool,
    _decode,
)
from jenny.config.tool_schemas import AndroidWebBrowserConfig


@pytest.fixture(autouse=True)
def _clean_state():
    browser.reset_browser_state()
    yield
    browser.reset_browser_state()


class FakeBridge:
    """Sta al posto della classe Kotlin: stessi metodi, risposte controllate."""

    def __init__(self, context=None):
        self.context = context
        self.calls: list[tuple] = []
        self.closed = 0
        self.snapshot_payload = {
            "url": "https://esempio.test/",
            "title": "Esempio",
            "version": 1,
            "refs": 2,
            "total": 5,
            "chars": 60,
            "mode": "full",
            "text": '- searchbox "Cerca" [ref=1:e0]\n- button "Vai" [ref=1:e1]',
        }
        self.act_payload = {"results": [{"i": 0, "action": "click", "ok": True}], "failed": False}

    # I metodi del motore tornano il valore JS, cioe' JSON **codificato due volte**.
    def _js(self, obj):
        return json.dumps(json.dumps(obj, ensure_ascii=False), ensure_ascii=False)

    def open(self, url, timeout):
        self.calls.append(("open", url, timeout))
        return json.dumps({"ok": True, "settled": True, "url": url, "title": "Esempio"})

    def snapshot(self, mode, filt, max_chars, timeout):
        self.calls.append(("snapshot", mode, filt, max_chars, timeout))
        return self._js(dict(self.snapshot_payload, mode=mode))

    def act(self, steps_json, timeout):
        self.calls.append(("act", steps_json, timeout))
        return self._js(self.act_payload)

    def read(self, ref, max_chars, timeout):
        self.calls.append(("read", ref, max_chars, timeout))
        return self._js({"url": "https://esempio.test/", "chars": 3, "truncated": False, "text": "ciao"})

    def close(self):
        self.closed += 1
        return json.dumps({"ok": True})


def _install(monkeypatch, bridge_cls=FakeBridge):
    holder = {}

    def factory():
        def make(context):
            b = bridge_cls(context)
            holder["bridge"] = b
            return b
        return make

    monkeypatch.setattr(browser, "_resolve_bridge_class", factory)
    return holder


def _allow_url(monkeypatch):
    """Lascia passare la validazione dell'URL.

    Serve perche' ``validate_url_target`` **risolve il nome**: un host finto non
    risolve e il tool si fermerebbe prima del bridge, cioe' il test misurerebbe
    la rete invece del codice. I due casi di rifiuto (loopback, schema non
    http) passano dalla funzione vera e non usano questo.
    """
    import jenny.security.network as net

    monkeypatch.setattr(net, "validate_url_target", lambda url, **kw: (True, ""))


def _tool(cls, **over):
    cfg = AndroidWebBrowserConfig(**over)
    return cls(android_context=object(), cfg=cfg)


class TestDecode:
    def test_doppia_codifica_del_motore(self):
        raw = json.dumps(json.dumps({"a": 1}))
        assert _decode(raw) == {"a": 1}

    def test_codifica_singola_di_kotlin(self):
        assert _decode(json.dumps({"ok": True})) == {"ok": True}

    def test_spazzatura_diventa_errore_non_eccezione(self):
        out = _decode("<html>oops</html>")
        assert "error" in out

    def test_niente_dal_bridge(self):
        assert "error" in _decode(None)


class TestOpen:
    async def test_url_non_valido_non_tocca_il_bridge(self, monkeypatch):
        holder = _install(monkeypatch)
        out = await _tool(BrowserOpenTool).execute(url="http://127.0.0.1:8080/")
        assert out.startswith("Error:")
        assert "bridge" not in holder

    async def test_schema_non_http_rifiutato(self, monkeypatch):
        _install(monkeypatch)
        out = await _tool(BrowserOpenTool).execute(url="file:///etc/passwd")
        assert out.startswith("Error:")

    async def test_apre_e_restituisce_gia_lo_snapshot(self, monkeypatch):
        _allow_url(monkeypatch)
        holder = _install(monkeypatch)
        out = await _tool(BrowserOpenTool).execute(url="https://esempio.test/")
        assert "ref=1:e0" in out
        assert "treat as data" in out          # banner di contenuto non fidato
        metodi = [c[0] for c in holder["bridge"].calls]
        assert metodi == ["open", "snapshot"]  # un turno solo, non due

    async def test_errore_di_apertura_non_chiede_lo_snapshot(self, monkeypatch):
        _allow_url(monkeypatch)
        class Rotto(FakeBridge):
            def open(self, url, timeout):
                return json.dumps({"error": "WebView error: net::ERR_NAME_NOT_RESOLVED"})

        holder = _install(monkeypatch, Rotto)
        out = await _tool(BrowserOpenTool).execute(url="https://esempio.test/")
        assert out.startswith("Error:")
        assert [c[0] for c in holder["bridge"].calls] == []


class TestSnapshot:
    async def test_default_e_la_differenza(self, monkeypatch):
        holder = _install(monkeypatch)
        await _tool(BrowserSnapshotTool).execute()
        assert holder["bridge"].calls[0][1] == "diff"

    async def test_modo_sconosciuto_ricade_su_differenza(self, monkeypatch):
        holder = _install(monkeypatch)
        await _tool(BrowserSnapshotTool).execute(mode="pieno")
        assert holder["bridge"].calls[0][1] == "diff"

    async def test_il_tetto_arriva_dalla_config(self, monkeypatch):
        holder = _install(monkeypatch)
        await _tool(BrowserSnapshotTool, max_snapshot_chars=777).execute()
        assert holder["bridge"].calls[0][3] == 777


class TestDo:
    async def test_senza_passi_non_tocca_il_bridge(self, monkeypatch):
        holder = _install(monkeypatch)
        out = await BrowserDoTool(object(), AndroidWebBrowserConfig()).execute(steps=[])
        assert out.startswith("Error:")
        assert "bridge" not in holder

    async def test_passi_inoltrati_come_json_e_poi_una_differenza(self, monkeypatch):
        holder = _install(monkeypatch)
        steps = [{"action": "type", "ref": "1:e0", "text": "meteo"}, {"action": "click", "ref": "1:e1"}]
        out = await _tool(BrowserDoTool).execute(steps=steps)
        calls = holder["bridge"].calls
        assert json.loads(calls[0][1]) == steps
        assert calls[1][1] == "diff"
        assert "0. click: ok" in out

    async def test_un_passo_fallito_si_vede(self, monkeypatch):
        class ConErrore(FakeBridge):
            def __init__(self, context=None):
                super().__init__(context)
                self.act_payload = {
                    "results": [{"i": 0, "action": "click", "ok": False,
                                 "error": 'ref "1:e3" e\' della versione 1, lo snapshot corrente e\' 2'}],
                    "failed": True,
                }

        _install(monkeypatch, ConErrore)
        out = await _tool(BrowserDoTool).execute(steps=[{"action": "click", "ref": "1:e3"}])
        assert "FALLITO" in out
        assert "versione" in out


class TestCicloDiVita:
    async def test_timeout_butta_la_sessione(self, monkeypatch):
        class Lenta(FakeBridge):
            def snapshot(self, mode, filt, max_chars, timeout):
                import time
                time.sleep(0.4)
                return "{}"

        _install(monkeypatch, Lenta)
        out = await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=-9.9)
        assert "error" in out
        assert browser._BROWSER_INSTANCE is None   # sessione buttata, non lasciata appesa

    async def test_eccezione_butta_la_sessione(self, monkeypatch):
        class Esplode(FakeBridge):
            def snapshot(self, *a):
                raise RuntimeError("renderer morto")

        _install(monkeypatch, Esplode)
        out = await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=1)
        assert "error" in out
        assert browser._BROWSER_INSTANCE is None

    async def test_close_chiude_e_dimentica(self, monkeypatch):
        _allow_url(monkeypatch)
        holder = _install(monkeypatch)
        await _tool(BrowserOpenTool).execute(url="https://esempio.test/")
        b = holder["bridge"]
        await _tool(BrowserCloseTool).execute()
        assert b.closed == 1
        assert browser._BROWSER_INSTANCE is None

    async def test_la_sessione_si_riusa_fra_chiamate(self, monkeypatch):
        _allow_url(monkeypatch)
        holder = _install(monkeypatch)
        await _tool(BrowserOpenTool).execute(url="https://esempio.test/")
        first = holder["bridge"]
        await _tool(BrowserSnapshotTool).execute()
        assert holder["bridge"] is first

    def test_reset_ricrea_il_lucchetto(self):
        old = browser._BROWSER_LOCK
        browser.reset_browser_state()
        assert browser._BROWSER_LOCK is not old


class TestConcorrenza:
    @pytest.mark.parametrize(
        "cls", [BrowserOpenTool, BrowserSnapshotTool, BrowserDoTool, BrowserReadTool, BrowserCloseTool]
    )
    def test_nessuno_e_parallelizzabile(self, cls):
        """`read_only` da solo non basta: e' `exclusive` che li tiene in fila.

        La sessione e' una pagina condivisa e mutabile — due chiamate nello stesso
        batch descriverebbero stati diversi.
        """
        t = _tool(cls)
        assert t.exclusive is True
        assert t.concurrency_safe is False


class TestRegistrazione:
    def test_il_modulo_e_nella_lista_fissa(self):
        from jenny.agent.tools.loader import _HARDCODED_TOOL_MODULES

        assert "browser" in _HARDCODED_TOOL_MODULES

    def test_i_cinque_nomi(self):
        assert [c.name for c in browser.TOOLS] == [
            "browser_open", "browser_snapshot", "browser_do", "browser_read", "browser_close",
        ]

    def test_spenti_senza_android(self):
        ctx = SimpleNamespace(android_context=None, config=SimpleNamespace(android_web=None))
        assert BrowserOpenTool.enabled(ctx) is False
        assert BrowserOpenTool.disabled_reason(ctx) is None


class TestChiusuraPerInattivita:
    """La sessione viva tiene ~100 MB: se nessuno la chiude, la chiude il guardiano."""

    async def test_una_sessione_dimenticata_si_chiude(self, monkeypatch):
        monkeypatch.setattr(browser, "_IDLE_POLL_S", 0.02)
        holder = _install(monkeypatch)
        await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=5, idle_s=0.1)
        b = holder["bridge"]
        assert browser._BROWSER_INSTANCE is not None
        await asyncio.sleep(0.35)
        assert browser._BROWSER_INSTANCE is None
        assert b.closed == 1

    async def test_l_attivita_rimanda_la_chiusura(self, monkeypatch):
        monkeypatch.setattr(browser, "_IDLE_POLL_S", 0.02)
        _install(monkeypatch)
        for _ in range(6):
            await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=5, idle_s=0.2)
            await asyncio.sleep(0.05)
        assert browser._BROWSER_INSTANCE is not None

    async def test_un_solo_guardiano_per_sessione(self, monkeypatch):
        monkeypatch.setattr(browser, "_IDLE_POLL_S", 0.02)
        _install(monkeypatch)
        await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=5, idle_s=5)
        first = browser._IDLE_TASK
        await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=5, idle_s=5)
        assert browser._IDLE_TASK is first

    async def test_il_reset_lo_ferma(self, monkeypatch):
        monkeypatch.setattr(browser, "_IDLE_POLL_S", 0.02)
        _install(monkeypatch)
        await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=5, idle_s=5)
        task = browser._IDLE_TASK
        browser.reset_browser_state()
        await asyncio.sleep(0.05)
        assert task.cancelled() or task.done()
        assert browser._IDLE_TASK is None

    async def test_niente_guardiano_se_spento(self, monkeypatch):
        _install(monkeypatch)
        await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=5, idle_s=0)
        assert browser._IDLE_TASK is None
