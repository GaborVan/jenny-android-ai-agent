package com.flagdizero.jenny

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Build
import android.util.Log
import org.json.JSONObject

/**
 * Ponte per gli appunti di sistema (ClipboardManager), esposto a Python via
 * Chaquopy (``jclass``) — stesso pattern di LocationBridge: classe semplice
 * costruita col Context, istanza cachata in ``runtime/clipboard.py``.
 *
 * Limiti Android: su Android 10+ (API 29+) il sistema limita la lettura degli
 * appunti alle app con focus o alla IME predefinita; fuori da quelle condizioni
 * la lettura ritorna testo vuoto o un errore chiaro. La scrittura è sempre
 * permessa.
 *
 * Ritorno: JSON. Lettura → `{"ok":true,"text":...}` (o `{"ok":true,"text":""}`
 * quando vuoto); scrittura → `{"ok":true}`.
 */
class ClipboardBridge(context: Context) {

    companion object {
        private const val TAG = "ClipboardBridge"
        private const val MAX_TEXT_CHARS = 10_000
    }

    private val appContext = context.applicationContext
    private val cm = appContext.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager

    fun getClipboard(): String {
        val manager = cm ?: return """{"ok":false,"error":"clipboard_unavailable"}"""
        return try {
            val clip = manager.primaryClip ?: return """{"ok":true,"text":""}"""
            if (clip.itemCount == 0) {
                return """{"ok":true,"text":""}"""
            }
            val text = clip.getItemAt(0).coerceToText(appContext)?.toString().orEmpty()
            if (text.isEmpty()) {
                """{"ok":true,"text":""}"""
            } else {
                JSONObject()
                    .put("ok", true)
                    .put("text", text.take(MAX_TEXT_CHARS))
                    .toString()
            }
        } catch (e: SecurityException) {
            Log.w(TAG, "clipboard read blocked by Android", e)
            """{"ok":false,"error":"clipboard_read_blocked","hint":"Android 10+ lets apps read the clipboard only while focused or as the default IME. Ask the user to open Jenny (focus) and retry."}"""
        } catch (e: Exception) {
            Log.w(TAG, "getClipboard failed", e)
            """{"ok":false,"error":"clipboard_read_failed"}"""
        }
    }

    fun setClipboard(text: String): String {
        val manager = cm ?: return """{"ok":false,"error":"clipboard_unavailable"}"""
        return try {
            manager.setPrimaryClip(ClipData.newPlainText("jenny", text))
            """{"ok":true}"""
        } catch (e: Exception) {
            Log.w(TAG, "setClipboard failed", e)
            """{"ok":false,"error":"clipboard_write_failed"}"""
        }
    }
}
