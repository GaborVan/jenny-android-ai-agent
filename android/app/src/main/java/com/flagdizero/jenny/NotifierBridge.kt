package com.flagdizero.jenny

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import android.util.Log
import androidx.core.app.NotificationCompat

/**
 * Bridge per gli alert di sistema (le notifiche che squillano), esposto a
 * Python via Chaquopy (`jclass("com.flagdizero.jenny.NotifierBridge")`), mai
 * istanziato da Kotlin — stesso pattern di InstalledAppsBridge.
 *
 * Usa un canale dedicato `jenny_alerts` (IMPORTANCE_HIGH, suono/vibrazione di
 * sistema) separato dal canale silenzioso del foreground service, così
 * l'utente può personalizzarne la suoneria dalle impostazioni Android senza
 * toccare la notifica persistente del gateway.
 *
 * Gate di visibilità: se MainActivity è in foreground l'alert viene soppresso
 * (il messaggio è già visibile in chat) — la policy "se squillare" vive qui,
 * quella "cosa dire" vive in Python (jenny/runtime/notifier.py).
 */
class NotifierBridge(context: Context) {

    companion object {
        private const val TAG = "NotifierBridge"
        private const val CHANNEL_ID = "jenny_alerts"
        // ID fisso + tag variabile: alert con lo stesso tag si sostituiscono
        // (niente pila infinita per la stessa sorgente), tag diversi convivono.
        private const val ALERT_ID = 2

        /** Cancella gli alert pendenti del canale (chiamato da MainActivity.onResume:
         *  una volta aperta la chat gli alert sono ormai stantii). Non tocca la
         *  notifica persistente del gateway, che vive su un altro canale. */
        fun clearAlerts(context: Context) {
            val manager = context.getSystemService(NotificationManager::class.java) ?: return
            manager.activeNotifications
                .filter { it.notification.channelId == CHANNEL_ID }
                .forEach { manager.cancel(it.tag, it.id) }
        }
    }

    private val appContext = context.applicationContext

    init {
        ensureChannel()
    }

    private fun ensureChannel() {
        val manager = appContext.getSystemService(NotificationManager::class.java) ?: return
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Jenny · avvisi",
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = "Promemoria e avvisi proattivi di Jenny"
        }
        manager.createNotificationChannel(channel)
    }

    /**
     * Posta un alert che squilla. Ritorna false quando soppresso (app in
     * foreground), permesso mancante o errore — il chiamante Python logga e
     * basta, il messaggio resta comunque in chat.
     */
    fun postAlert(title: String, body: String, tag: String): Boolean {
        if (MainActivity.isInForeground) {
            Log.d(TAG, "Alert suppressed: app in foreground")
            return false
        }
        return try {
            val intent = Intent(appContext, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            val pending = PendingIntent.getActivity(
                appContext,
                tag.hashCode(),
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            val notification = NotificationCompat.Builder(appContext, CHANNEL_ID)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(NotificationCompat.BigTextStyle().bigText(body))
                .setSmallIcon(R.drawable.ic_stat_jenny)
                .setLargeIcon(
                    BitmapFactory.decodeResource(appContext.resources, R.drawable.ic_notification_large)
                )
                .setAutoCancel(true)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_MESSAGE)
                .setContentIntent(pending)
                .build()
            val manager = appContext.getSystemService(NotificationManager::class.java)
                ?: return false
            manager.notify(tag, ALERT_ID, notification)
            true
        } catch (e: Exception) {
            // Include SecurityException su API 33+ senza POST_NOTIFICATIONS.
            Log.e(TAG, "Failed to post alert", e)
            false
        }
    }
}
