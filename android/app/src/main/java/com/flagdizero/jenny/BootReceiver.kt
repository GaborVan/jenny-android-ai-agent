package com.flagdizero.jenny

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Riavvia il gateway dopo un reboot del dispositivo (o un ripristino post
 * blackout): il telefono-server nel cassetto torna online senza intervento.
 *
 * Il tipo FGS `specialUse` non è tra quelli vietati all'avvio da
 * BOOT_COMPLETED, quindi startForegroundService è consentito qui.
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action
        if (action != Intent.ACTION_BOOT_COMPLETED &&
            action != Intent.ACTION_LOCKED_BOOT_COMPLETED
        ) {
            return
        }
        Log.i("Jenny", "Boot completed: starting gateway service")
        try {
            context.startForegroundService(Intent(context, GatewayService::class.java))
        } catch (e: Exception) {
            Log.e("Jenny", "Failed to start gateway on boot", e)
        }
    }
}
