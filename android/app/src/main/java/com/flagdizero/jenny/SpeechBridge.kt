package com.flagdizero.jenny

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import android.webkit.JavascriptInterface
import android.webkit.WebView
import androidx.core.content.ContextCompat
import org.json.JSONObject
import java.util.Locale
import java.util.UUID

/**
 * Voce di Jenny: sintesi (TextToSpeech) e riconoscimento (SpeechRecognizer),
 * esposti alla WebUI come SECONDO JS interface (`JennySpeech`, accanto a
 * `JennyNative` — v. MainActivity.loadWebView). A differenza degli altri
 * bridge (Clipboard/Location/Notifier, istanziati da Chaquopy) questo serve
 * sia l'Activity — permesso RECORD_AUDIO, i due motori vogliono girare sul
 * main thread — sia la WebView, per spingere gli esiti asincroni.
 *
 * TTS e STT sono entrambi asincroni lato Android: `speak`/`startListening`
 * ritornano subito un JSON di solo avvio ("richiesta accettata"), mentre gli
 * esiti reali arrivano come callback JS iniettati via `evaluateJavascript`:
 * `window.__jennySpeechResult(json)` per il riconoscimento e
 * `window.__jennySpeechPermission(granted)` per l'esito del permesso
 * microfono chiesto a runtime.
 */
class SpeechBridge(
    private val activity: MainActivity,
    private val webView: WebView,
) {

    companion object {
        private const val TAG = "SpeechBridge"
    }

    private var tts: TextToSpeech? = null

    @Volatile
    private var ttsReady = false

    @Volatile
    private var ttsInitFailed = false

    // Testi accodati mentre il motore TTS è ancora in fase di init (toccato
    // solo dal main thread: sia speak() che il callback di init ci arrivano
    // via runOnUiThread/Looper principale).
    private val pendingUtterances = mutableListOf<String>()

    private var recognizer: SpeechRecognizer? = null

    @Volatile
    private var listening = false

    private fun errorJson(code: String): String =
        JSONObject().put("ok", false).put("error", code).toString()

    /** Lingua per TTS/STT dalla stessa preferenza che sceglie la UI (locale di
     *  default del processo Android) — stessa mappa di I18n.detectLocale(). */
    private fun localeForCurrentLanguage(): Locale {
        return when (Locale.getDefault().language) {
            "it" -> Locale.ITALIAN
            "uk" -> Locale("uk", "UA")
            else -> Locale.US
        }
    }

    private fun pushToJs(js: String) {
        webView.post { webView.evaluateJavascript(js, null) }
    }

    private fun pushSpeechResult(json: String) {
        pushToJs("window.__jennySpeechResult && window.__jennySpeechResult($json)")
    }

    private fun pushPermissionResult(granted: Boolean) {
        pushToJs("window.__jennySpeechPermission && window.__jennySpeechPermission($granted)")
    }

    // ── TTS ──

    private val utteranceListener = object : UtteranceProgressListener() {
        override fun onStart(utteranceId: String?) {}
        override fun onDone(utteranceId: String?) {}
        override fun onError(utteranceId: String?) {
            Log.w(TAG, "TTS utterance failed: $utteranceId")
        }
    }

    private fun initTts() {
        if (tts != null) return
        tts = TextToSpeech(activity) { status ->
            ttsReady = status == TextToSpeech.SUCCESS
            if (ttsReady) {
                tts?.setOnUtteranceProgressListener(utteranceListener)
                val queued = pendingUtterances.toList()
                pendingUtterances.clear()
                val engine = tts
                if (engine != null) queued.forEach { doSpeak(engine, it) }
            } else {
                Log.w(TAG, "TTS init failed: status=$status")
                ttsInitFailed = true
                pendingUtterances.clear()
            }
        }
    }

    private fun doSpeak(engine: TextToSpeech, text: String) {
        val langResult = engine.setLanguage(localeForCurrentLanguage())
        if (langResult == TextToSpeech.LANG_MISSING_DATA || langResult == TextToSpeech.LANG_NOT_SUPPORTED) {
            Log.w(TAG, "TTS language unsupported ($langResult), using engine default")
        }
        engine.speak(text, TextToSpeech.QUEUE_ADD, null, UUID.randomUUID().toString())
    }

    private fun speakOnMainThread(text: String) {
        val engine = tts
        if (engine == null) {
            pendingUtterances.add(text)
            initTts()
            return
        }
        if (!ttsReady) {
            pendingUtterances.add(text)
            return
        }
        doSpeak(engine, text)
    }

    @JavascriptInterface
    fun speak(text: String): String {
        val trimmed = text.trim()
        if (trimmed.isEmpty()) return errorJson("empty_text")
        if (ttsInitFailed) {
            return JSONObject()
                .put("ok", false)
                .put("error", "tts_unavailable")
                .put(
                    "hint",
                    "No text-to-speech engine available. Install/enable one " +
                        "(e.g. Google Text-to-Speech) in Android settings."
                )
                .toString()
        }
        activity.runOnUiThread { speakOnMainThread(trimmed) }
        return """{"ok":true}"""
    }

    @JavascriptInterface
    fun stopSpeaking(): String {
        activity.runOnUiThread {
            try {
                tts?.stop()
            } catch (e: Exception) {
                Log.w(TAG, "stopSpeaking failed", e)
            }
        }
        return """{"ok":true}"""
    }

    // ── STT ──

    private val recognitionListener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) {}
        override fun onBeginningOfSpeech() {}
        override fun onRmsChanged(rmsdB: Float) {}
        override fun onBufferReceived(buffer: ByteArray?) {}
        override fun onEndOfSpeech() {}
        override fun onPartialResults(partialResults: Bundle?) {}
        override fun onEvent(eventType: Int, params: Bundle?) {}

        override fun onResults(results: Bundle?) {
            listening = false
            val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()
            if (text.isNullOrBlank()) {
                pushSpeechResult(errorJson("stt_no_match"))
            } else {
                pushSpeechResult(JSONObject().put("ok", true).put("text", text).toString())
            }
        }

        override fun onError(error: Int) {
            listening = false
            val code = when (error) {
                SpeechRecognizer.ERROR_NO_MATCH -> "stt_no_match"
                SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "stt_timeout"
                SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "permission_denied"
                SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "stt_network"
                SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "stt_busy"
                else -> "stt_error"
            }
            pushSpeechResult(errorJson(code))
        }
    }

    private fun beginListening() {
        try {
            val rec = recognizer ?: SpeechRecognizer.createSpeechRecognizer(activity).also { recognizer = it }
            rec.setRecognitionListener(recognitionListener)
            val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, localeForCurrentLanguage().toLanguageTag())
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
            }
            listening = true
            rec.startListening(intent)
        } catch (e: Exception) {
            Log.w(TAG, "startListening failed", e)
            listening = false
            pushSpeechResult(errorJson("stt_error"))
        }
    }

    @JavascriptInterface
    fun startListening(): String {
        if (!SpeechRecognizer.isRecognitionAvailable(activity)) {
            return errorJson("stt_unavailable")
        }
        if (listening) return errorJson("already_listening")
        val granted = ContextCompat.checkSelfPermission(activity, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (!granted) {
            // Async: il JS deve richiamare startListening() dopo aver ricevuto
            // window.__jennySpeechPermission(true) — non c'è niente da attendere
            // qui, la richiesta di sistema è già in volo.
            activity.requestRecordAudioPermission { ok -> pushPermissionResult(ok) }
            return errorJson("permission_pending")
        }
        activity.runOnUiThread { beginListening() }
        return """{"ok":true}"""
    }

    @JavascriptInterface
    fun cancelListening(): String {
        activity.runOnUiThread {
            try {
                recognizer?.cancel()
            } catch (e: Exception) {
                Log.w(TAG, "cancelListening failed", e)
            }
            listening = false
        }
        return """{"ok":true}"""
    }

    /** Rilascio motori, chiamato da MainActivity.onDestroy (già sul main thread). */
    fun destroy() {
        try {
            recognizer?.destroy()
        } catch (e: Exception) {
            Log.w(TAG, "recognizer destroy failed", e)
        }
        recognizer = null
        try {
            tts?.stop()
            tts?.shutdown()
        } catch (e: Exception) {
            Log.w(TAG, "tts shutdown failed", e)
        }
        tts = null
        ttsReady = false
    }
}
