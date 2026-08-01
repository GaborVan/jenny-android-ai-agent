package com.flagdizero.jenny

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.graphics.BitmapFactory
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlin.concurrent.thread

/**
 * Foreground service hosting the jenny gateway thread.
 *
 * Runs independently of MainActivity's lifecycle so the Python gateway (and
 * therefore the WebSocket session) survives the screen turning off or the
 * app being backgrounded. startForeground() must be called before any
 * potentially slow work (Chaquopy/Python startup) to avoid
 * ForegroundServiceDidNotStartInTimeException on Android 14+.
 */
class GatewayService : Service() {

    companion object {
        private const val TAG = "Jenny"
        private const val NOTIFICATION_CHANNEL_ID = "jenny_gateway"
        private const val NOTIFICATION_ID = 1
    }

    private var gatewayStarted = false

    override fun onCreate() {
        super.onCreate()
        startForegroundCompat(NOTIFICATION_ID, buildNotification())
        startGateway()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // Re-post on every (re)start so a notification permission granted
        // after the initial startForeground() call actually takes effect.
        startForegroundCompat(NOTIFICATION_ID, buildNotification())
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun startGateway() {
        if (gatewayStarted) return
        gatewayStarted = true

        // Chaquopy's first-run bootstrap (Python.start) unpacks the stdlib/
        // site-packages and can take several seconds; it must never run on the
        // main/UI Looper thread, or all touch input and WebView compositing
        // stalls for that entire window (same effect as an ANR).
        thread(name = "jenny-gateway") {
            try {
                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(applicationContext))
                }
                val py = Python.getInstance()
                val module = py.getModule("jenny.android_entry")
                module.callAttr("run_gateway", filesDir.absolutePath, applicationContext)
            } catch (e: Exception) {
                Log.e(TAG, "Gateway startup failed", e)
            }
        }
    }

    private fun buildNotification(): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        val channel = NotificationChannel(
            NOTIFICATION_CHANNEL_ID,
            "Jenny",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "Jenny resta accesa in sottofondo"
            setSound(null, null)
            enableVibration(false)
        }
        manager.createNotificationChannel(channel)

        return NotificationCompat.Builder(this, NOTIFICATION_CHANNEL_ID)
            .setContentTitle("Jenny ✦ online")
            .setContentText("Gateway attivo · sempre sul pezzo")
            .setSmallIcon(R.drawable.ic_stat_jenny)
            .setLargeIcon(BitmapFactory.decodeResource(resources, R.drawable.ic_notification_large))
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun startForegroundCompat(id: Int, notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            var type = ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            // Il tipo `location` va aggiunto SOLO se il permesso è concesso:
            // altrimenti Android 14 lancia SecurityException all'avvio di un
            // FGS di tipo location. Concede al service l'app-op location anche
            // a UI non in primo piano (schermo spento, Telegram, cron), senza
            // ACCESS_BACKGROUND_LOCATION. Il re-post in onStartCommand fa sì
            // che il tipo si aggiorni quando il permesso viene concesso dopo
            // l'avvio (MainActivity ri-avvia il service al grant).
            if (hasLocationPermission()) {
                type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
            }
            startForeground(id, notification, type)
        } else {
            startForeground(id, notification)
        }
    }

    private fun hasLocationPermission(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED
}
