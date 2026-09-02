package com.flagdizero.jenny

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject

/**
 * Servizio che ascolta le notifiche di sistema (NotificationListenerService):
 * dà a Jenny le "orecchie" sugli altri app — leggere notifiche attive
 * (codici 2FA, messaggi, stati) e dismissarle.
 *
 * Come per UiAutomationService: lo istanzia Android quando l'utente concede
 * l'accesso alle notifiche (Impostazioni → Notifiche → Accesso alle
 * notifiche). Il ponte verso Python è [NotificationsBridge], che delega qui via
 * [instance].
 *
 * Ritorno: JSON. Lista notifiche → `{"ok":true,"notifications":[{key,
 * package,title,text,postTimeMs},...]}`; dismiss → `{"ok":true}` o errore.
 *
 * Confine di fiducia: le notifiche possono contenere dati personali (codici
 * usa-e-getta inclusi). L'accesso è concesso a mano dall'utente ed è la ragion
 * d'essere del servizio; i formatter dell'activity stream (Python) non
 * ripetono MAI il contenuto.
 */
class NotificationListenerBridge : NotificationListenerService() {

    companion object {
        private const val TAG = "NotificationListenerBridge"

        @Volatile
        var instance: NotificationListenerBridge? = null
            private set

        private const val MAX_TEXT_CHARS = 300
    }

    override fun onListenerConnected() {
        super.onListenerConnected()
        instance = this
        Log.i(TAG, "NotificationListener connected")
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        // Reattività opzionale in futuro; oggi il servizio è on-demand.
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification?) {
        // No-op: nessuna logica reattiva.
    }

    override fun onDestroy() {
        if (instance === this) {
            instance = null
        }
        super.onDestroy()
    }

    /** Lista JSON delle notifiche attive (package, title, text, postTimeMs). */
    fun getActiveNotifications(): String {
        return try {
            val out = JSONArray()
            for (sbn in activeNotifications) {
                val extras = sbn.notification?.extras ?: continue
                val title = extras.getCharSequence("android.title")?.toString()
                val text = extras.getCharSequence("android.text")?.toString()
                val item = JSONObject()
                    .put("key", sbn.key)
                    .put("package", sbn.packageName ?: "")
                    .put("postTimeMs", sbn.postTime)
                if (!title.isNullOrBlank()) item.put("title", title.take(MAX_TEXT_CHARS))
                if (!text.isNullOrBlank()) item.put("text", text.take(MAX_TEXT_CHARS))
                out.put(item)
            }
            JSONObject()
                .put("ok", true)
                .put("notifications", out)
                .toString()
        } catch (e: Exception) {
            Log.w(TAG, "getActiveNotifications failed", e)
            errorJson("read_failed: ${e.message}")
        }
    }

    /** Rimuove una notifica per chiave (dal dump di getActiveNotifications). */
    fun dismissNotification(key: String): String {
        return try {
            val ok = runCatching { cancelNotification(key) }.getOrDefault(false)
            if (ok) okJson() else errorJson("dismiss_failed")
        } catch (e: Exception) {
            Log.w(TAG, "dismissNotification failed", e)
            errorJson("dismiss_failed: ${e.message}")
        }
    }

    private fun okJson(): String = JSONObject().put("ok", true).toString()

    private fun errorJson(reason: String): String =
        JSONObject().put("ok", false).put("error", reason).toString()
}
