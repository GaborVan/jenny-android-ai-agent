package com.flagdizero.jenny

import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.util.Log

/**
 * Ponte verso [NotificationListenerBridge], costruito da Python via Chaquopy
 * (stesso pattern di UiAutomationBridge: ``jclass`` + costruttore con Context,
 * istanza cachata in ``runtime/notifications.py``).
 *
 * Il servizio di notifiche lo istanzia Android quando l'utente concede
 * l'accesso (Impostazioni → Notifiche → Accesso alle notifiche): questo ponte
 * NON lo costruisce — delega all'istanza connessa
 * ([NotificationListenerBridge.instance]). Quando manca l'accesso ogni metodo
 * ritorna ``{"ok":false,"error":"service_not_enabled",...}``.
 */
class NotificationsBridge(context: Context) {

    companion object {
        private const val TAG = "NotificationsBridge"
    }

    private val appContext = context.applicationContext

    /** Apre le Impostazioni di accesso alle notifiche. */
    fun openSettings(): String {
        return try {
            val intent = Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            appContext.startActivity(intent)
            """{"ok":true}"""
        } catch (e: Exception) {
            Log.w(TAG, "openSettings failed", e)
            """{"ok":false,"error":"settings_intent_failed"}"""
        }
    }

    fun isEnabled(): Boolean = NotificationListenerBridge.instance != null

    fun getActiveNotifications(): String =
        delegateOrNotEnabled { it.getActiveNotifications() }

    fun dismissNotification(key: String): String =
        delegateOrNotEnabled { it.dismissNotification(key) }

    private fun delegateOrNotEnabled(call: (NotificationListenerBridge) -> String): String {
        val service = NotificationListenerBridge.instance
        if (service == null) {
            return """{"ok":false,"error":"service_not_enabled","hint":"Enable Jenny in Android Settings → Notifications → Notification access, then retry."}"""
        }
        return try {
            call(service)
        } catch (e: Exception) {
            Log.w(TAG, "delegate failed", e)
            """{"ok":false,"error":"bridge_failed:${e.message}"}"""
        }
    }
}
