package com.flagdizero.jenny

import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.util.Log

/**
 * Ponte verso [UiAutomationService], costruito da Python via Chaquopy
 * (stesso pattern di LocationBridge: ``jclass`` + costruttore con Context,
 * istanza cachata in ``runtime/ui_automation.py``).
 *
 * Il servizio di accessibilità è un componente di sistema: lo istanzia Android
 * quando l'utente lo abilita in Impostazioni → Accessibilità, quindi questo
 * ponte NON lo costruisce — delega all'istanza connessa
 * ([UiAutomationService.instance]). Quando il servizio non è abilitato ogni
 * metodo ritorna un JSON d'errore con ``error="service_not_enabled"`` e
 * l'hint su come abilitarlo; Python lo traduce in un messaggio per l'utente.
 *
 * Politica: qui vive solo l'accesso nativo (dump, gesture, testo, azioni
 * globali). La decisione "se e quando guardare/toccare lo schermo" vive in
 * Python (``jenny/runtime/ui_automation.py``), gated dal toggle utente
 * ``tools.ui_automation.enable``.
 */
class UiAutomationBridge(context: Context) {

    companion object {
        private const val TAG = "UiAutomationBridge"
    }

    private val appContext = context.applicationContext

    /** Apre le Impostazioni di Accessibilità del sistema. */
    fun openSettings(): String {
        return try {
            val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            appContext.startActivity(intent)
            """{"ok":true}"""
        } catch (e: Exception) {
            Log.w(TAG, "openSettings failed", e)
            """{"ok":false,"error":"settings_intent_failed"}"""
        }
    }

    /** True se il servizio di accessibilità è connesso. */
    fun isEnabled(): Boolean = UiAutomationService.instance != null

    fun screenDump(): String = delegateOrNotEnabled { it.screenDump() }

    fun tap(x: Int, y: Int): String = delegateOrNotEnabled { it.tap(x, y) }

    fun tapByText(text: String): String = delegateOrNotEnabled { it.tapByText(text) }

    fun swipe(x1: Int, y1: Int, x2: Int, y2: Int, durationMs: Int): String =
        delegateOrNotEnabled { it.swipe(x1, y1, x2, y2, durationMs) }

    fun typeText(text: String): String = delegateOrNotEnabled { it.typeText(text) }

    fun pressGlobal(key: String): String = delegateOrNotEnabled { it.pressGlobal(key) }

    fun captureScreenshot(path: String): String =
        delegateOrNotEnabled { it.captureScreenshot(path) }

    fun status(): String = delegateOrNotEnabled { it.status() }

    private fun delegateOrNotEnabled(call: (UiAutomationService) -> String): String {
        val service = UiAutomationService.instance
        if (service == null) {
            return """{"ok":false,"error":"service_not_enabled","hint":"Enable Jenny in Settings → Accessibility, then call ui_status to verify."}"""
        }
        return try {
            call(service)
        } catch (e: Exception) {
            Log.w(TAG, "delegate failed", e)
            """{"ok":false,"error":"bridge_failed:${e.message}"}"""
        }
    }
}
