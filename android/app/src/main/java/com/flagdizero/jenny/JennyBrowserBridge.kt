package com.flagdizero.jenny

import android.content.Context
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.View
import android.webkit.ConsoleMessage
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.webkit.ProfileStore
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import java.io.ByteArrayInputStream
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/**
 * WebView di sessione per i tool ``browser_*``: una pagina che **resta aperta**.
 *
 * Perché non riusa quella di [AgenticSearchBridge]: `fetchUrl` fa `loadUrl` sulla
 * stessa istanza, quindi una `web_fetch` durante una sessione porterebbe via la
 * pagina sotto i piedi. Due WebView costano (~+100 MB misurati sul Titan 2 il
 * 29/08, interamente restituiti alla chiusura), e la seconda esiste solo finché
 * una sessione è aperta.
 *
 * **Invariante che regge tutto: ogni tocco della WebView passa da [handler].**
 * Creazione, `loadUrl`, letture di `url`/`title`, `evaluateJavascript`,
 * `destroy`. Non è pignoleria: la WebView pretende il main thread e lo verifica
 * lei stessa (`checkThread`), quindi un accessore che legge lo stato dal thread
 * chiamante non dà un dato sbagliato, fa crashare l'app.
 *
 * Il [WebViewClient] è installato **una volta sola** alla creazione, non
 * riassegnato a ogni chiamata come in `AgenticSearchBridge.evaluateOnPage`: qui
 * deve sopravvivere alle navigazioni, perché è lui che le vede.
 */
class JennyBrowserBridge(context: Context) {

    companion object {
        private const val TAG = "JennyBrowser"
        private const val DEFAULT_TIMEOUT_SECONDS = 30L
        private const val PROFILE_NAME = "jenny-browser-session"
        private const val SETTLE_QUIET_MS = 400L
        private const val USER_AGENT_MOBILE =
            "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36"

        /**
         * Le stesse reti di ``jenny/security/network.py::_BLOCKED_NETWORKS``.
         *
         * Vivono qui e non solo in Python perché con una sessione interattiva
         * **Python l'indirizzo di un link non lo vede mai**: il modello clicca,
         * Chromium naviga. Questo è l'unico strato che vede dove porta un click,
         * un redirect o una sottorisorsa.
         */
        private val BLOCKED_V4 = listOf(
            "0.0.0.0" to 8, "10.0.0.0" to 8, "100.64.0.0" to 10, "127.0.0.0" to 8,
            "169.254.0.0" to 16, "172.16.0.0" to 12, "192.168.0.0" to 16,
        )
    }

    private val handler = Handler(Looper.getMainLooper())
    private val appContext = context.applicationContext
    private var webView: WebView? = null

    private val generation = AtomicInteger(0)
    private val loading = AtomicBoolean(false)
    private val lastError = AtomicReference<String?>(null)
    private val lastFinishAt = AtomicReference(0L)
    private val snapshotVersion = AtomicInteger(0)
    private val hostVerdicts = ConcurrentHashMap<String, Boolean>()

    // L'ultimo indirizzo rifiutato dalla guardia. Serve a **dirlo**: un blocco
    // muto lascia il modello davanti a un about:blank vuoto, che sembra un sito
    // rotto e invita a riprovare. Misurato sul telefono il 29/08 con un redirect
    // di httpbin verso 192.168.1.1: fermato correttamente, e raccontato come
    // "0 elementi".
    private val lastBlocked = AtomicReference<String?>(null)

    // R.raw.browser_agent e non getIdentifier("browser_agent"): la build di
    // release offusca i nomi delle risorse (nell'APK il file diventa `res/XX.js`),
    // quindi cercarlo per nome a runtime funziona in debug e fallisce dove conta.
    // La costante e risolta a compile time e sopravvive all'offuscamento.
    private val agentJs: String by lazy {
        appContext.resources.openRawResource(R.raw.browser_agent)
            .bufferedReader().use { it.readText() }
    }

    // ------------------------------------------------------------------ guardia

    private fun isBlockedAddress(addr: InetAddress): Boolean {
        if (addr.isLoopbackAddress || addr.isLinkLocalAddress ||
            addr.isSiteLocalAddress || addr.isAnyLocalAddress) return true
        when (addr) {
            is Inet4Address -> {
                val b = addr.address
                val v = ((b[0].toInt() and 0xFF) shl 24) or ((b[1].toInt() and 0xFF) shl 16) or
                    ((b[2].toInt() and 0xFF) shl 8) or (b[3].toInt() and 0xFF)
                for ((net, bits) in BLOCKED_V4) {
                    val n = InetAddress.getByName(net).address
                    val nv = ((n[0].toInt() and 0xFF) shl 24) or ((n[1].toInt() and 0xFF) shl 16) or
                        ((n[2].toInt() and 0xFF) shl 8) or (n[3].toInt() and 0xFF)
                    val mask = if (bits == 0) 0 else (-1 shl (32 - bits))
                    if ((v and mask) == (nv and mask)) return true
                }
            }
            is Inet6Address -> {
                val b = addr.address
                // fc00::/7 (unique local) — fe80::/10 lo copre già isLinkLocalAddress
                if ((b[0].toInt() and 0xFE) == 0xFC) return true
            }
        }
        return false
    }

    /** Verdetto per hostname, con cache. Risolve: **mai dal main thread**. */
    private fun isBlockedHost(host: String): Boolean {
        hostVerdicts[host]?.let { return it }
        val verdict = try {
            InetAddress.getAllByName(host).any { isBlockedAddress(it) }
        } catch (e: Exception) {
            Log.w(TAG, "DNS fallita per $host: ${e.message}")
            true   // in dubbio si blocca
        }
        hostVerdicts[host] = verdict
        return verdict
    }

    /** Controllo sincrono, senza DNS: è tutto ciò che si può fare sul main thread. */
    private fun isBlockedLiteral(uri: Uri): Boolean {
        val host = uri.host ?: return true
        val scheme = (uri.scheme ?: "").lowercase()
        if (scheme != "http" && scheme != "https") return true
        if (!host.any { it.isDigit() || it == ':' }) return false   // non è un IP letterale
        return try { isBlockedAddress(InetAddress.getByName(host)) } catch (e: Exception) { false }
    }

    // ------------------------------------------------------------------ ciclo di vita

    private fun ensureWebViewOnMain() {
        if (webView != null) return
        val wv = WebView(appContext).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.databaseEnabled = true
            settings.setSupportZoom(false)
            settings.builtInZoomControls = false
            settings.displayZoomControls = false
            settings.loadsImagesAutomatically = false
            settings.mediaPlaybackRequiresUserGesture = true
            settings.javaScriptCanOpenWindowsAutomatically = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            settings.userAgentString = USER_AGENT_MOBILE
            visibility = View.GONE
            webChromeClient = object : WebChromeClient() {
                override fun onConsoleMessage(msg: ConsoleMessage?): Boolean {
                    Log.d(TAG, "JS console [${msg?.lineNumber()}] ${msg?.message()}")
                    return super.onConsoleMessage(msg)
                }
            }
            webViewClient = sessionClient()
        }
        // Incognito: cookie e storage separati dal barattolo globale che usa
        // web_fetch, e buttati alla chiusura. Verificato supportato sul Titan 2
        // (WebView 143) il 29/08; dove non c'è, la sessione resta sul profilo
        // di default e lo diciamo a Python invece di fingere isolamento.
        if (WebViewFeature.isFeatureSupported(WebViewFeature.MULTI_PROFILE)) {
            try {
                val p = ProfileStore.getInstance().getOrCreateProfile(PROFILE_NAME)
                WebViewCompat.setProfile(wv, p.name)
            } catch (e: Exception) {
                Log.e(TAG, "profilo non agganciato: ${e.message}")
            }
        }
        webView = wv
    }

    private fun sessionClient(): WebViewClient = object : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
            val uri = request?.url ?: return false
            if (isBlockedLiteral(uri)) {
                Log.w(TAG, "navigazione bloccata (letterale): $uri")
                if (request.isForMainFrame) lastBlocked.set(uri.toString())
                return true
            }
            return false
        }

        // Gira su un thread di lavoro: qui il DNS si può risolvere, ed è l'unico
        // punto che vede *ogni* richiesta, main frame compreso.
        override fun shouldInterceptRequest(
            view: WebView?, request: WebResourceRequest?
        ): WebResourceResponse? {
            val uri = request?.url ?: return null
            val scheme = (uri.scheme ?: "").lowercase()
            if (scheme != "http" && scheme != "https") {
                return WebResourceResponse("text/plain", "utf-8", ByteArrayInputStream(ByteArray(0)))
            }
            val host = uri.host ?: return null
            if (isBlockedHost(host)) {
                Log.w(TAG, "richiesta bloccata: $uri")
                if (request.isForMainFrame) lastBlocked.set(uri.toString())
                return WebResourceResponse("text/plain", "utf-8", ByteArrayInputStream(ByteArray(0)))
            }
            return null
        }

        override fun onPageStarted(view: WebView?, url: String?, favicon: android.graphics.Bitmap?) {
            generation.incrementAndGet()
            loading.set(true)
            lastError.set(null)
        }

        override fun onPageFinished(view: WebView?, url: String?) {
            loading.set(false)
            lastFinishAt.set(System.currentTimeMillis())
        }

        override fun onReceivedError(
            view: WebView?, request: WebResourceRequest?, error: WebResourceError?
        ) {
            if (request?.isForMainFrame == true) {
                lastError.set("WebView error: ${error?.description ?: "unknown"}")
                loading.set(false)
                lastFinishAt.set(System.currentTimeMillis())
            }
        }
    }

    /** Distrugge la sessione e butta il profilo (cookie inclusi). */
    fun close(): String {
        val done = CountDownLatch(1)
        handler.post {
            webView?.stopLoading()
            webView?.destroy()
            webView = null
            done.countDown()
        }
        done.await(10, TimeUnit.SECONDS)
        hostVerdicts.clear()
        if (WebViewFeature.isFeatureSupported(WebViewFeature.MULTI_PROFILE)) {
            try { ProfileStore.getInstance().deleteProfile(PROFILE_NAME) } catch (_: Exception) {}
        }
        return """{"ok":true}"""
    }

    fun isIsolated(): Boolean = WebViewFeature.isFeatureSupported(WebViewFeature.MULTI_PROFILE)

    // ------------------------------------------------------------------ attese

    /**
     * Aspetta la fine del caricamento **più una finestra di quiete**: senza, su
     * una pagina che si ridisegna da sola si fotografa uno stato a metà.
     */
    private fun awaitSettled(timeoutSeconds: Long): Boolean {
        val deadline = System.currentTimeMillis() + timeoutSeconds * 1000
        while (System.currentTimeMillis() < deadline) {
            if (!loading.get()) {
                val quiet = System.currentTimeMillis() - lastFinishAt.get()
                if (quiet >= SETTLE_QUIET_MS) return true
            }
            Thread.sleep(50)
        }
        return false
    }

    private fun evaluate(js: String, timeoutSeconds: Long): String {
        val out = AtomicReference("")
        val done = CountDownLatch(1)
        handler.post {
            val wv = webView
            if (wv == null) { done.countDown(); return@post }
            wv.evaluateJavascript(js) { v -> out.set(v ?: ""); done.countDown() }
        }
        if (!done.await(timeoutSeconds, TimeUnit.SECONDS)) {
            return """{"error":"evaluateJavascript timeout dopo ${timeoutSeconds}s"}"""
        }
        return out.get()
    }

    private fun runAgent(argsJson: String, timeoutSeconds: Long): String =
        evaluate(agentJs.replace("__ARGS__", argsJson), timeoutSeconds)

    private fun currentUrlAndTitle(): Pair<String, String> {
        val out = AtomicReference(Pair("", ""))
        val done = CountDownLatch(1)
        handler.post {
            out.set(Pair(webView?.url ?: "", webView?.title ?: ""))
            done.countDown()
        }
        done.await(5, TimeUnit.SECONDS)
        return out.get()
    }

    // ------------------------------------------------------------------ API

    /** Apre [url] e aspetta che la pagina si sia posata. */
    fun open(url: String, timeoutSeconds: Long = DEFAULT_TIMEOUT_SECONDS): String {
        val uri = Uri.parse(url)
        if (isBlockedLiteral(uri)) return """{"error":"indirizzo non consentito"}"""
        val started = CountDownLatch(1)
        lastBlocked.set(null)
        handler.post {
            ensureWebViewOnMain()
            loading.set(true)
            lastError.set(null)
            webView?.loadUrl(url)
            started.countDown()
        }
        started.await(10, TimeUnit.SECONDS)
        val settled = awaitSettled(timeoutSeconds)
        lastBlocked.getAndSet(null)?.let {
            val msg = "navigazione rifiutata: $it e' un indirizzo di rete privata o " +
                "locale. Se ci sei arrivato da un redirect, il sito di partenza sta " +
                "puntando dentro la rete del telefono."
            return """{"error":${quote(msg)}}"""
        }
        lastError.get()?.let { return """{"error":${quote(it)}}""" }
        val (u, t) = currentUrlAndTitle()
        return """{"ok":true,"settled":$settled,"url":${quote(u)},"title":${quote(t)}}"""
    }

    fun snapshot(
        mode: String, filter: String, maxChars: Int,
        timeoutSeconds: Long = DEFAULT_TIMEOUT_SECONDS,
    ): String {
        val v = snapshotVersion.incrementAndGet()
        val args = """{"op":"snapshot","mode":${quote(mode)},"filter":${quote(filter)},""" +
            """"maxChars":$maxChars,"version":$v}"""
        return runAgent(args, timeoutSeconds)
    }

    /**
     * Esegue i passi di [stepsJson]; se qualcosa ha navigato, aspetta la nuova
     * pagina prima di tornare, così lo snapshot successivo non descrive quella
     * vecchia.
     */
    fun act(stepsJson: String, timeoutSeconds: Long = DEFAULT_TIMEOUT_SECONDS): String {
        val before = generation.get()
        val raw = runAgent("""{"op":"act","steps":$stepsJson}""", timeoutSeconds)
        val deadline = System.currentTimeMillis() + 1200
        while (System.currentTimeMillis() < deadline && generation.get() == before) {
            Thread.sleep(50)
        }
        if (generation.get() != before) awaitSettled(timeoutSeconds)
        return raw
    }

    fun read(ref: String, maxChars: Int, timeoutSeconds: Long = DEFAULT_TIMEOUT_SECONDS): String {
        val args = """{"op":"read","ref":${quote(ref)},"maxChars":$maxChars}"""
        return runAgent(args, timeoutSeconds)
    }

    private fun quote(s: String): String = org.json.JSONObject.quote(s)
}
