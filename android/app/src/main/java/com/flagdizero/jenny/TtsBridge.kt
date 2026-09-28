package com.flagdizero.jenny

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import org.json.JSONObject
import java.util.Locale
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Ponte per la sintesi vocale di sistema (`android.speech.tts.TextToSpeech`),
 * esposto a Python via Chaquopy (`jclass`) — stesso pattern di `ClipboardBridge`:
 * classe semplice costruita col Context, istanza cachata in `runtime/tts.py`.
 *
 * Confine di fiducia: il testo viene sintetizzato interamente sul dispositivo
 * dal motore TTS di sistema (es. Google Text-to-Speech). Niente lascia il
 * telefono, e non serve alcun permesso Android: `TextToSpeech` non richiede
 * voci di manifest, quindi `AndroidManifest.xml` resta invariato.
 *
 * `TextToSpeech` va costruito sul thread principale (lo impone l'API Android),
 * quindi la costruzione viene marshalled su `Looper.getMainLooper()`. Il ponte
 * per\u00f2 **non** attende l\u00ec: `BridgeCache` lo costruisce sul thread del loop
 * asyncio di Python, e un'attesa di 5 secondi in quel punto fermerebbe il
 * gateway. L'attesa vive in `awaitEngine()`, chiamata da `speak()` su un worker
 * (`asyncio.to_thread`), e ha un timeout perch\u00e9 senza motore TTS installato
 * `OnInitListener` non arriva mai.
 *
 * Ogni chiamata è avvolta in try/catch: un fallimento Android diventa un JSON
 * di errore, non un'eccezione che attraversa il confine Chaquopy.
 */
class TtsBridge(context: Context) {

    companion object {
        private const val TAG = "TtsBridge"
        private const val INIT_TIMEOUT_S = 5L
        private const val MAX_TEXT_CHARS = 4000
        private const val MIN_RATE = 0.5f
        private const val MAX_RATE = 2.0f
    }

    private val appContext = context.applicationContext
    private val mainHandler = Handler(Looper.getMainLooper())

    // Un solo tentativo di init per istanza, mai due: la prima `speak()` lo
    // avvia, le successive aspettano lo stesso latch.
    private val initLatch = CountDownLatch(1)

    @Volatile
    private var tts: TextToSpeech? = null

    @Volatile
    private var initialized = false

    @Volatile
    private var initStarted = false

    private val utteranceListener = object : UtteranceProgressListener() {
        override fun onStart(utteranceId: String?) {}
        override fun onDone(utteranceId: String?) {}
        @Deprecated("Deprecated in Java")
        override fun onError(utteranceId: String?) {
            Log.w(TAG, "utterance $utteranceId failed")
        }
        override fun onError(utteranceId: String?, errorCode: Int) {
            Log.w(TAG, "utterance $utteranceId failed, code $errorCode")
        }
    }

    /**
     * Avvia l'engine sul thread principale **senza** bloccare chi costruisce il
     * ponte.
     *
     * Il costruttore non deve attendere: `BridgeCache` lo invoca sul thread del
     * loop asyncio di Python, e un'attesa fino a 5 s lì dentro fermerebbe il
     * gateway. L'attesa vive in [awaitEngine], che gira su un worker
     * (`asyncio.to_thread`).
     */
    private fun ensureInitStarted() {
        synchronized(this) {
            if (initStarted) return
            initStarted = true
        }
        mainHandler.post {
            try {
                tts = TextToSpeech(appContext) { status ->
                    initialized = status == TextToSpeech.SUCCESS
                    if (initialized) {
                        tts?.setOnUtteranceProgressListener(utteranceListener)
                    }
                    initLatch.countDown()
                }
            } catch (e: Exception) {
                Log.w(TAG, "TextToSpeech construction failed", e)
                initLatch.countDown()
            }
        }
    }

    /** Attende l'esito dell'init: gira su un worker Chaquopy, non sul loop. */
    private fun awaitEngine(): Boolean {
        ensureInitStarted()
        if (initialized) return true
        try {
            initLatch.await(INIT_TIMEOUT_S, TimeUnit.SECONDS)
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
            return false
        }
        return initialized
    }

    private fun unavailableError(): String {
        return JSONObject()
            .put("ok", false)
            .put("error", "tts_unavailable")
            .put(
                "hint",
                "Install or enable a text-to-speech engine (e.g. Google Text-to-Speech) " +
                    "in Android text-to-speech settings.",
            )
            .toString()
    }

    fun speak(text: String, languageTag: String, rate: Double): String {
        if (text.isBlank()) {
            return """{"ok":false,"error":"empty_text"}"""
        }
        if (text.length > MAX_TEXT_CHARS) {
            return """{"ok":false,"error":"text_too_long"}"""
        }
        if (!awaitEngine()) {
            return unavailableError()
        }
        val engine = tts ?: return unavailableError()
        return try {
            if (languageTag.isNotBlank()) {
                val locale = Locale.forLanguageTag(languageTag)
                val result = engine.setLanguage(locale)
                if (result == TextToSpeech.LANG_MISSING_DATA ||
                    result == TextToSpeech.LANG_NOT_SUPPORTED
                ) {
                    return JSONObject()
                        .put("ok", false)
                        .put("error", "language_not_supported")
                        .put("hint", "The device TTS engine has no voice for '$languageTag'.")
                        .toString()
                }
            }
            val clampedRate = rate.toFloat().coerceIn(MIN_RATE, MAX_RATE)
            engine.setSpeechRate(clampedRate)
            val utteranceId = UUID.randomUUID().toString()
            engine.speak(text, TextToSpeech.QUEUE_ADD, null, utteranceId)
            """{"ok":true}"""
        } catch (e: Exception) {
            Log.w(TAG, "speak failed", e)
            """{"ok":false,"error":"speak_failed"}"""
        }
    }

    fun stop(): String {
        return try {
            tts?.stop()
            """{"ok":true}"""
        } catch (e: Exception) {
            Log.w(TAG, "stop failed", e)
            """{"ok":true}"""
        }
    }

    fun isAvailable(): Boolean = initialized
}
