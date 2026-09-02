package com.flagdizero.jenny

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityService.ScreenshotResult
import android.accessibilityservice.AccessibilityService.TakeScreenshotCallback
import android.accessibilityservice.GestureDescription
import android.graphics.Bitmap
import android.graphics.Path
import android.graphics.Rect
import android.hardware.HardwareBuffer
import android.os.Build
import android.os.Bundle
import android.os.CountDownLatch
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.Executor
import java.util.concurrent.TimeUnit
import org.json.JSONArray
import org.json.JSONObject

/**
 * Servizio di accessibilità che dà a Jenny occhi e mani sugli altri app.
 *
 * NON è un bridge stile LocationBridge: è un componente di sistema che Android
 * istanzia e vincola quando l'utente lo abilita in Impostazioni → Accessibilità.
 * Il ponte verso Python è [UiAutomationBridge], che delega qui tramite
 * [instance] (null finché il servizio non è connesso).
 *
 * Le API di accessibilità (dump, gesture, azioni) vanno toccate dal main
 * thread: ogni metodo pubblico mar-shalla il lavoro su `Looper.getMainLooper()`
 * via [runOnMain] e blocca il chiamante con un latch — stesso pattern di
 * `LocationBridge` con `requestLocationUpdates`. Il chiamante è un thread di
 * lavoro Python (`asyncio.to_thread`), mai il main.
 *
 * Ritorno: JSON string. Azioni → `{"ok":true}` / `{"ok":false,"error":...}`;
 * dump → `{"ok":true,"package":...,"nodes":[...]}`.
 *
 * Confine di fiducia: chi abilita questo servizio dà all'app il permesso di
 * LEGGERE lo schermo e SIMULARE gesture su qualunque app — permesso di sistema
 * concesso a mano dall'utente. Il toggle Python (`tools.ui_automation.enable`)
 * è la seconda serratura lato agente.
 */
class UiAutomationService : AccessibilityService() {

    companion object {
        private const val TAG = "UiAutomationService"

        /** Istanza connessa; null finché l'utente non abilita il servizio. */
        @Volatile
        var instance: UiAutomationService? = null
            private set

        private const val MAX_TEXT_CHARS = 200
        private const val MAX_NODES = 400
        private const val GESTURE_TAP_MS = 80L
    }

    private val mainHandler = Handler(Looper.getMainLooper())

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        Log.i(TAG, "UiAutomationService connected")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // Servizio puramente on-demand: nessuna azione reattiva agli eventi.
    }

    override fun onInterrupt() {
        // Le gesture in corso vengono annullate dal sistema.
    }

    override fun onDestroy() {
        if (instance === this) {
            instance = null
        }
        super.onDestroy()
    }

    // ── API verso il ponte Python ──────────────────────────────────────────

    fun screenDump(): String = runOnMain {
        val root = rootInActiveWindow ?: return@runOnMain errorJson("no_active_window")
        try {
            val nodes = JSONArray()
            collectNodes(root, nodes, depth = 0)
            JSONObject()
                .put("ok", true)
                .put("package", root.packageName?.toString() ?: "")
                .put("nodes", nodes)
                .toString()
        } catch (e: Exception) {
            Log.w(TAG, "screenDump failed", e)
            errorJson("dump_failed: ${e.message}")
        } finally {
            root.recycle()
        }
    }

    /** Tocca un punto dello schermo (coordinate assolute in px). */
    fun tap(x: Int, y: Int): String = runOnMain {
        val gesture = tapGesture(x.toFloat(), y.toFloat())
        if (dispatchGesture(gesture, null, null)) okJson() else errorJson("gesture_rejected")
    }

    /** Trova un nodo cliccabile il cui testo contiene `text` e lo attiva. */
    fun tapByText(text: String): String = runOnMain {
        val root = rootInActiveWindow ?: return@runOnMain errorJson("no_active_window")
        try {
            val target = findNodeByText(root, text)
            if (target == null) {
                errorJson("text_not_found")
            } else {
                val ok = if (target.isClickable) {
                    target.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                } else {
                    val rect = Rect()
                    target.getBoundsInScreen(rect)
                    dispatchGesture(
                        tapGesture(rect.exactCenterX(), rect.exactCenterY()), null, null
                    )
                }
                if (ok) okJson() else errorJson("action_failed")
            }
        } catch (e: Exception) {
            Log.w(TAG, "tapByText failed", e)
            errorJson("tap_failed: ${e.message}")
        } finally {
            root.recycle()
        }
    }

    /** Swipe da (x1,y1) a (x2,y2) in `durationMs` ms. */
    fun swipe(x1: Int, y1: Int, x2: Int, y2: Int, durationMs: Int): String = runOnMain {
        val path = Path().apply {
            moveTo(x1.toFloat(), y1.toFloat())
            lineTo(x2.toFloat(), y2.toFloat())
        }
        val stroke = GestureDescription.StrokeDescription(path, 0, durationMs.coerceAtLeast(1).toLong())
        val gesture = GestureDescription.Builder().addStroke(stroke).build()
        if (dispatchGesture(gesture, null, null)) okJson() else errorJson("gesture_rejected")
    }

    /** Inserisce testo nel nodo focalizzato/editable (ACTION_SET_TEXT). */
    fun typeText(text: String): String = runOnMain {
        val root = rootInActiveWindow ?: return@runOnMain errorJson("no_active_window")
        try {
            val editable = findEditable(root)
            if (editable == null) {
                errorJson("no_editable_field")
            } else {
                val args = Bundle().apply {
                    putCharSequence(
                        AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text
                    )
                }
                val ok = editable.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
                if (ok) okJson() else errorJson("action_failed")
            }
        } catch (e: Exception) {
            Log.w(TAG, "typeText failed", e)
            errorJson("type_failed: ${e.message}")
        } finally {
            root.recycle()
        }
    }

    /** Azioni globali: back | home | recents | notifications. */
    fun pressGlobal(key: String): String = runOnMain {
        val action = when (key.lowercase()) {
            "back" -> GLOBAL_ACTION_BACK
            "home" -> GLOBAL_ACTION_HOME
            "recents" -> GLOBAL_ACTION_RECENTS
            "notifications" -> GLOBAL_ACTION_NOTIFICATIONS
            else -> return@runOnMain errorJson("unknown_key: $key")
        }
        if (performGlobalAction(action)) okJson() else errorJson("global_action_rejected")
    }

    /** Stato: connesso + package in primo piano (per diagnosi). */
    fun status(): String = runOnMain {
        JSONObject()
            .put("connected", true)
            .put("package", rootInActiveWindow?.packageName?.toString() ?: "")
            .toString()
    }

    /** Cattura uno screenshot della finestra attiva e lo salva in `path` (PNG).
     *
     * Richiede API 30+ (``AccessibilityService.takeScreenshot``) e fallisce su
     * finestre protette (secure flags). Ritorna JSON con path e dimensioni.
     */
    fun captureScreenshot(path: String): String = runOnMain {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            return@runOnMain errorJson("screenshot_requires_api_30")
        }
        val latch = CountDownLatch(1)
        val holder = arrayOfNulls<String>(1)
        val executor = Executor { runnable -> mainHandler.post(runnable) }
        val callback = object : TakeScreenshotCallback {
            override fun onSuccess(screenshot: ScreenshotResult) {
                holder[0] = try {
                    val buffer: HardwareBuffer = screenshot.hardwareBuffer
                    val bitmap = Bitmap.wrapHardwareBuffer(buffer, screenshot.colorSpace)
                    val file = File(path).apply { parentFile?.mkdirs() }
                    FileOutputStream(file).use { out ->
                        if (!bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)) {
                            errorJson("screenshot_compress_failed")
                        } else {
                            JSONObject()
                                .put("ok", true)
                                .put("path", file.absolutePath)
                                .put("width", bitmap.width)
                                .put("height", bitmap.height)
                                .toString()
                        }
                    }
                } catch (e: Exception) {
                    Log.w(TAG, "captureScreenshot save failed", e)
                    errorJson("screenshot_save_failed: ${e.message}")
                } finally {
                    latch.countDown()
                }
            }

            override fun onFailure(errorCode: Int) {
                holder[0] = errorJson("screenshot_failed_code:$errorCode")
                latch.countDown()
            }
        }
        try {
            takeScreenshot(Display.DEFAULT_DISPLAY, executor, callback)
            if (latch.await(5, TimeUnit.SECONDS)) holder[0] ?: errorJson("no_result")
            else errorJson("screenshot_timeout")
        } catch (e: Exception) {
            Log.w(TAG, "captureScreenshot failed", e)
            errorJson("screenshot_failed: ${e.message}")
        }
    }

    // ── Helpers ────────────────────────────────────────────────────────────

    /** Esegue `block` sul main thread e attende il risultato (timeout 5s). */
    private fun runOnMain(block: () -> String): String {
        val latch = CountDownLatch(1)
        val holder = arrayOfNulls<String>(1)
        mainHandler.post {
            try {
                holder[0] = block()
            } catch (e: Exception) {
                Log.w(TAG, "runOnMain failed", e)
                holder[0] = errorJson("bridge_failed: ${e.message}")
            } finally {
                latch.countDown()
            }
        }
        return try {
            if (latch.await(5, TimeUnit.SECONDS)) holder[0] ?: errorJson("no_result")
            else errorJson("main_thread_timeout")
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
            errorJson("interrupted")
        }
    }

    private fun tapGesture(x: Float, y: Float): GestureDescription {
        val path = Path().apply { moveTo(x, y) }
        val stroke = GestureDescription.StrokeDescription(path, 0, GESTURE_TAP_MS)
        return GestureDescription.Builder().addStroke(stroke).build()
    }

    private fun collectNodes(node: AccessibilityNodeInfo, out: JSONArray, depth: Int) {
        if (out.length() >= MAX_NODES || depth > 20) return
        val item = JSONObject()
        node.text?.toString()?.take(MAX_TEXT_CHARS)?.let { t ->
            if (t.isNotBlank()) item.put("text", t)
        }
        node.contentDescription?.toString()?.take(MAX_TEXT_CHARS)?.let { d ->
            if (d.isNotBlank()) item.put("desc", d)
        }
        node.viewIdResourceName?.let { item.put("id", it) }
        node.className?.toString()?.let { item.put("cls", it.substringAfterLast('.')) }
        item.put("clickable", node.isClickable)
        item.put("editable", node.isEditable)
        item.put("scrollable", node.isScrollable)
        item.put("focused", node.isFocused)
        item.put("depth", depth)
        val rect = Rect()
        node.getBoundsInScreen(rect)
        if (!rect.isEmpty) {
            item.put(
                "bounds",
                JSONArray().put(rect.left).put(rect.top).put(rect.right).put(rect.bottom)
            )
        }
        out.put(item)
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            collectNodes(child, out, depth + 1)
            child.recycle()
        }
    }

    /** Primo nodo (pre-order) con testo o descrizione che contiene `needle`. */
    private fun findNodeByText(node: AccessibilityNodeInfo, needle: String): AccessibilityNodeInfo? {
        val text = node.text?.toString()
        val desc = node.contentDescription?.toString()
        if (text?.contains(needle, ignoreCase = true) == true ||
            desc?.contains(needle, ignoreCase = true) == true
        ) {
            return node
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findNodeByText(child, needle)
            if (found != null) return found
            child.recycle()
        }
        return null
    }

    /** Nodo focalizzato o, in mancanza, il primo campo editabile visibile. */
    private fun findEditable(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isEditable && node.isVisibleToUser) return node
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findEditable(child)
            if (found != null) return found
            child.recycle()
        }
        return null
    }

    private fun okJson(): String = JSONObject().put("ok", true).toString()

    private fun errorJson(reason: String): String =
        JSONObject().put("ok", false).put("error", reason).toString()
}
