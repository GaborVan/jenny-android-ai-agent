package com.flagdizero.jenny

import android.content.Context
import android.content.Intent
import android.media.AudioFormat
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.ParcelFileDescriptor
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.max

/**
 * Riconoscimento vocale di sistema (`android.speech.SpeechRecognizer`), esposto
 * a Python via Chaquopy (`jclass`) — stesso pattern di `ClipboardBridge`: classe
 * semplice costruita col Context, istanza cachata in `runtime/stt.py`.
 *
 * Serve a una cosa sola: **trascrivere un file audio**, cioè il messaggio
 * vocale che arriva da un altro canale (Telegram), dove non c'è nessun
 * microfono da ascoltare e nessuna WebView che detti. La dettatura dal vivo
 * dentro l'app resta dove stava (`SpeechBridge`, che parla con la WebView): qui
 * non si tocca il microfono, quindi non serve né il foreground-service di tipo
 * microfono né che l'app sia in primo piano.
 *
 * Confine di fiducia: il file viene decodificato **sul dispositivo**
 * (`MediaExtractor` + `MediaCodec`) e consegnato al motore di riconoscimento
 * che l'utente ha installato. Quel motore può essere in locale oppure no:
 * `preferOffline` chiede esplicitamente di restare offline, ma non lo garantisce
 * — chi decide è il servizio. Niente audio passa da Jenny o da un servizio
 * nostro; `jenny/runtime/stt.py` è l'unico chiamante e non è raggiungibile dal
 * modello come tool.
 *
 * Ogni passo è avvolto in try/catch e ogni fallimento è un JSON di errore con un
 * codice stabile: un'eccezione non attraversa mai il confine Chaquopy.
 */
class SttBridge(context: Context) {

    companion object {
        private const val TAG = "SttBridge"
        // Il riconoscimento da file richiede `EXTRA_AUDIO_SOURCE`, cioè Android
        // 13 (API 33). Sotto quella soglia si ritorna un errore esplicito invece
        // di un fallimento opaco.
        private const val FILE_SOURCE_MIN_API = 33
        private const val RESULT_TIMEOUT_S = 60L
        private const val DECODE_TIMEOUT_MS = 30_000L
        private const val MAX_INPUT_BYTES = 20L * 1024 * 1024
        // Buffer di decodifica: 512 KB di PCM per giro, abbastanza da non
        // martellare il disco e piccolo da non pesare sulla memoria.
        private const val PCM_CHUNK_BYTES = 512 * 1024
    }

    private val appContext = context.applicationContext
    private val mainHandler = Handler(Looper.getMainLooper())

    @Volatile
    private var recognizer: SpeechRecognizer? = null

    fun isAvailable(): Boolean = SpeechRecognizer.isRecognitionAvailable(appContext)

    /**
     * Trascrive il file audio a *path*.
     *
     * Ritorna `{"ok":true,"text":...,"language":...}` oppure
     * `{"ok":false,"error":<codice>[, "hint":...][, "code":<n>]}`.
     * Non solleva mai.
     */
    fun transcribeFile(path: String, languageTag: String, preferOffline: Boolean): String {
        val file = File(path)
        if (!file.isFile) {
            return errorJson("file_not_found", "No audio file at $path")
        }
        if (file.length() > MAX_INPUT_BYTES) {
            return errorJson("file_too_large", "Audio file is larger than ${MAX_INPUT_BYTES / (1024 * 1024)} MB")
        }
        if (!isAvailable()) {
            return errorJson(
                "stt_unavailable",
                "No speech recognition service is installed or enabled on this device.",
            )
        }
        if (Build.VERSION.SDK_INT < FILE_SOURCE_MIN_API) {
            return errorJson(
                "stt_file_source_unsupported",
                "Transcribing an audio file needs Android 13 (API 33); this device runs API " +
                    "${Build.VERSION.SDK_INT}. Send text instead, or dictate from the app.",
            )
        }

        val pcm = try {
            File.createTempFile("jenny-stt-", ".pcm", appContext.cacheDir)
        } catch (e: Exception) {
            Log.w(TAG, "could not create temp file", e)
            return errorJson("stt_error", "Could not create a temporary file: ${e.message}")
        }
        return try {
            val decoded = decodeToPcm16(file, pcm)
            recognise(pcm, decoded.first, decoded.second, languageTag, preferOffline)
        } catch (e: Exception) {
            Log.w(TAG, "transcribeFile failed", e)
            errorJson("decode_failed", e.message ?: "Could not decode the audio track")
        } finally {
            if (!pcm.delete()) Log.d(TAG, "temp PCM not deleted: ${pcm.name}")
        }
    }

    /** Annulla una trascrizione in corso (idempotente). */
    fun cancel(): String {
        mainHandler.post {
            try {
                recognizer?.cancel()
            } catch (e: Exception) {
                Log.w(TAG, "cancel failed", e)
            }
        }
        return """{"ok":true}"""
    }

    private fun errorJson(code: String, hint: String? = null): String {
        val obj = JSONObject().put("ok", false).put("error", code)
        if (hint != null) obj.put("hint", hint)
        return obj.toString()
    }

    // ── Decodifica: qualunque formato che Android sa leggere → PCM 16 bit ──

    /**
     * Decodifica la prima traccia audio di *input* in PCM lineare a 16 bit,
     * scritto **grezzo** (senza header WAV) dentro *out*.
     *
     * Ritorna `(sampleRate, channelCount)` del flusso decodificato: sono i
     * valori che vanno dichiarati all'`EXTRA_AUDIO_SOURCE`, e vanno presi
     * dall'**output** del decoder, non dal formato della traccia — un Opus
     * decodifica a 48 kHz anche quando la traccia ne dichiara altri.
     */
    private fun decodeToPcm16(input: File, out: File): Pair<Int, Int> {
        val extractor = MediaExtractor()
        extractor.setDataSource(input.absolutePath)
        try {
            var trackIndex = -1
            var trackFormat: MediaFormat? = null
            for (i in 0 until extractor.trackCount) {
                val fmt = extractor.getTrackFormat(i)
                if (fmt.getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true) {
                    trackIndex = i
                    trackFormat = fmt
                    break
                }
            }
            val format = trackFormat
                ?: throw IllegalStateException("no audio track in ${input.name}")
            extractor.selectTrack(trackIndex)
            val mime = format.getString(MediaFormat.KEY_MIME)
                ?: throw IllegalStateException("audio track without mime type")

            var sampleRate = if (format.containsKey(MediaFormat.KEY_SAMPLE_RATE)) {
                format.getInteger(MediaFormat.KEY_SAMPLE_RATE)
            } else {
                16_000
            }
            var channels = if (format.containsKey(MediaFormat.KEY_CHANNEL_COUNT)) {
                format.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
            } else {
                1
            }

            val decoder = MediaCodec.createDecoderByType(mime)
            try {
                decoder.configure(format, null, null, 0)
                decoder.start()
                FileOutputStream(out).use { sink ->
                    val info = MediaCodec.BufferInfo()
                    var inputDone = false
                    var outputDone = false
                    val deadline = System.currentTimeMillis() + DECODE_TIMEOUT_MS
                    while (!outputDone) {
                        if (System.currentTimeMillis() > deadline) {
                            throw IllegalStateException("decoding timed out")
                        }
                        if (!inputDone) {
                            val inIndex = decoder.dequeueInputBuffer(10_000)
                            if (inIndex >= 0) {
                                val buffer = decoder.getInputBuffer(inIndex)
                                if (buffer == null) {
                                    throw IllegalStateException("decoder gave no input buffer")
                                }
                                val size = extractor.readSampleData(buffer, 0)
                                if (size < 0) {
                                    decoder.queueInputBuffer(
                                        inIndex, 0, 0, 0,
                                        MediaCodec.BUFFER_FLAG_END_OF_STREAM,
                                    )
                                    inputDone = true
                                } else {
                                    decoder.queueInputBuffer(
                                        inIndex, 0, size, max(0, extractor.sampleTime), 0,
                                    )
                                    extractor.advance()
                                }
                            }
                        }
                        when (val outIndex = decoder.dequeueOutputBuffer(info, 10_000)) {
                            MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                                val produced = decoder.outputFormat
                                if (produced.containsKey(MediaFormat.KEY_SAMPLE_RATE)) {
                                    sampleRate = produced.getInteger(MediaFormat.KEY_SAMPLE_RATE)
                                }
                                if (produced.containsKey(MediaFormat.KEY_CHANNEL_COUNT)) {
                                    channels = produced.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
                                }
                            }
                            MediaCodec.INFO_TRY_AGAIN_LATER -> Unit
                            else -> if (outIndex >= 0) {
                                val buffer = decoder.getOutputBuffer(outIndex)
                                if (buffer != null && info.size > 0) {
                                    var written = 0
                                    while (written < info.size) {
                                        val chunk = ByteArray(minOf(PCM_CHUNK_BYTES, info.size - written))
                                        buffer.position(info.offset + written)
                                        buffer.limit(info.offset + written + chunk.size)
                                        buffer.get(chunk)
                                        sink.write(chunk)
                                        written += chunk.size
                                    }
                                }
                                decoder.releaseOutputBuffer(outIndex, false)
                                if ((info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) {
                                    outputDone = true
                                }
                            }
                        }
                    }
                }
            } finally {
                try {
                    decoder.stop()
                } catch (e: Exception) {
                    Log.d(TAG, "decoder stop failed", e)
                }
                decoder.release()
            }
            if (out.length() == 0L) {
                throw IllegalStateException("decoded no audio samples")
            }
            return sampleRate to channels
        } finally {
            extractor.release()
        }
    }

    // ── Riconoscimento del PCM decodificato ───────────────────────────────

    private fun recognise(
        pcm: File,
        sampleRate: Int,
        channels: Int,
        languageTag: String,
        preferOffline: Boolean,
    ): String {
        val source = ParcelFileDescriptor.open(pcm, ParcelFileDescriptor.MODE_READ_ONLY)
        val latch = CountDownLatch(1)
        var failure: String? = null
        var rawCode = 0
        var transcript: String? = null

        mainHandler.post {
            try {
                val created = SpeechRecognizer.createSpeechRecognizer(appContext)
                recognizer = created
                created.setRecognitionListener(object : RecognitionListener {
                    override fun onReadyForSpeech(params: Bundle?) {}
                    override fun onBeginningOfSpeech() {}
                    override fun onRmsChanged(rmsdB: Float) {}
                    override fun onBufferReceived(buffer: ByteArray?) {}
                    override fun onEndOfSpeech() {}
                    override fun onPartialResults(partialResults: Bundle?) {}
                    override fun onEvent(eventType: Int, params: Bundle?) {}

                    override fun onResults(results: Bundle?) {
                        transcript = results
                            ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                            ?.firstOrNull()
                        releaseRecognizer(created)
                        latch.countDown()
                    }

                    override fun onError(error: Int) {
                        failure = errorName(error)
                        rawCode = error
                        releaseRecognizer(created)
                        latch.countDown()
                    }
                })
                val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                    putExtra(
                        RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                        RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
                    )
                    putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE, source)
                    putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_SAMPLING_RATE, sampleRate)
                    putExtra(
                        RecognizerIntent.EXTRA_AUDIO_SOURCE_ENCODING,
                        AudioFormat.ENCODING_PCM_16BIT,
                    )
                    putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_CHANNEL_COUNT, channels)
                    putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, preferOffline)
                    if (languageTag.isNotBlank()) {
                        putExtra(RecognizerIntent.EXTRA_LANGUAGE, languageTag)
                    }
                }
                created.startListening(intent)
            } catch (e: Exception) {
                Log.w(TAG, "startListening failed", e)
                failure = "stt_error"
                releaseRecognizer(recognizer)
                latch.countDown()
            }
        }

        val finished = try {
            latch.await(RESULT_TIMEOUT_S, TimeUnit.SECONDS)
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
            false
        } finally {
            try {
                source.close()
            } catch (e: Exception) {
                Log.d(TAG, "closing audio source failed", e)
            }
        }

        if (!finished) {
            cancel()
            return errorJson("stt_timeout", "The recognition service did not answer in time.")
        }
        val text = transcript?.trim().orEmpty()
        if (text.isNotEmpty()) {
            return JSONObject()
                .put("ok", true)
                .put("text", text)
                .put("language", languageTag)
                .toString()
        }
        val code = failure ?: "stt_no_match"
        val obj = JSONObject().put("ok", false).put("error", code).put("code", rawCode)
        if (code == "stt_no_match") {
            obj.put("hint", "Nothing recognisable was found in the audio.")
        }
        return obj.toString()
    }

    private fun releaseRecognizer(created: SpeechRecognizer?) {
        try {
            created?.destroy()
        } catch (e: Exception) {
            Log.d(TAG, "recognizer destroy failed", e)
        }
        if (recognizer === created) recognizer = null
    }

    /** Mappa il codice di ``SpeechRecognizer`` su un nome stabile per Python. */
    private fun errorName(error: Int): String = when (error) {
        SpeechRecognizer.ERROR_NO_MATCH -> "stt_no_match"
        SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "stt_timeout"
        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "permission_denied"
        SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "stt_network"
        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "stt_busy"
        SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED -> "stt_language_not_supported"
        SpeechRecognizer.ERROR_AUDIO -> "stt_audio"
        SpeechRecognizer.ERROR_CLIENT -> "stt_client"
        SpeechRecognizer.ERROR_SERVER -> "stt_server"
        else -> "stt_error"
    }
}
