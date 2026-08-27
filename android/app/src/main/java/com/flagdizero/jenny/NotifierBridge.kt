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

        /** Cancella gli alert pendenti del canale.
         *
         *  La regola è una sola, e vale la pena scriverla per esteso perché è
         *  già stata sbagliata in due modi opposti: **si cancella quando la
         *  chat è a schermo**, perché è lì che il messaggio si legge, e mai
         *  perché l'app è tornata in primo piano.
         *
         *  Prima stava in ``onResume`` liscio: questa app è la home del
         *  telefono, ``onResume`` scatta a ogni ritorno alla schermata iniziale
         *  e su qualunque vista, quindi l'alert veniva cancellato comunque —
         *  letto o no. Poi solo sul tap dell'alert (``ACTION_OPEN_CHAT``), che
         *  è l'errore opposto: chi apriva la chat da sé si ritrovava in coda
         *  notifiche di messaggi che aveva davanti agli occhi.
         *
         *  I tre chiamanti sono quindi i tre modi in cui la chat arriva a
         *  schermo: il tap sull'alert (``onNewIntent`` e il suo gemello in
         *  ``onCreate`` per l'activity morta), il cambio vista dentro la SPA
         *  (``JennyNative.chatOpened``, da ``ChatController.activate``) e il
         *  rientro in primo piano a chat **già** attiva (``onResume``, dietro
         *  la domanda ``CHAT_ON_SCREEN_JS`` — che è ciò che lo distingue
         *  dall'``onResume`` liscio di allora).
         *
         *  Non tocca la notifica persistente del gateway, che vive su un altro
         *  canale. */
        fun clearAlerts(context: Context, trigger: String) {
            val manager = context.getSystemService(NotificationManager::class.java) ?: return
            val pending = manager.activeNotifications.filter {
                it.notification.channelId == CHANNEL_ID
            }
            pending.forEach { manager.cancel(it.tag, it.id) }
            // Il *trigger* è il motivo per cui questa riga esiste. Postare logga
            // solo i soppressi, e cancellare non loggava niente: da fuori
            // "l'alert non c'è più" ha quattro spiegazioni indistinguibili — uno
            // dei tre percorsi qui sotto, o il dito dell'utente sulla tendina.
            // Misurato il 27/08/2026: la prima verifica di questa funzione è
            // stata inconcludente esattamente per questo.
            Log.d(TAG, "clearAlerts($trigger): cancelled ${pending.size} alert(s)")
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
            appContext.getString(R.string.alerts_channel_name),
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = appContext.getString(R.string.alerts_channel_description)
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
            // L'action non è decorativa: è l'unica cosa che distingue questo
            // intent da un rilancio qualunque dell'activity. Senza,
            // MainActivity.onNewIntent — che instrada solo CATEGORY_HOME — non
            // aveva niente da riconoscere e il tap riportava l'app dov'era,
            // mini-app aperta compresa, invece che in chat.
            val intent = Intent(appContext, MainActivity::class.java)
                .setAction(MainActivity.ACTION_OPEN_CHAT)
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
